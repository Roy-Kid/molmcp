"""ImmutableGitStore — fake GitTransport, no DiscoveryEngine."""

from __future__ import annotations

import inspect
import io
import json
import tarfile
from pathlib import Path

import pytest

from molmcp.components.git import extract_git_archive
from molmcp.components.store import (
    ImmutableGitStore,
    ShaConflictError,
    StoreError,
    UnknownShaError,
)

SHA_A = "a" * 40
_OWNER = "acme"
_REPO = "widgets"
_HARNESS_TOML = "# harness\n"
_STORE_PY = (
    Path(__file__).resolve().parents[2] / "src" / "molmcp" / "components" / "store.py"
)


def _github_tarball(repo: str, sha: str) -> bytes:
    """GitHub-style tar.gz whose inner directory is ``{repo}-{sha}/``."""
    prefix = f"{repo}-{sha}"
    members = {
        f"{prefix}/harness.toml": _HARNESS_TOML,
        f"{prefix}/dummy.txt": "dummy\n",
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _FakeGitTransport:
    """``fetch_archive`` only; ``resolve_commit`` raises if the store calls it."""

    def __init__(self, archive: bytes) -> None:
        self._archive = archive
        self.fetch_calls: list[tuple[str, str, str]] = []
        self.resolve_calls: list[tuple[str, str, str | None]] = []

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        self.fetch_calls.append((owner, repo, sha))
        return self._archive

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        self.resolve_calls.append((owner, repo, ref))
        raise AssertionError("ImmutableGitStore must not call resolve_commit")


def _new_store(tmp_path: Path) -> tuple[ImmutableGitStore, _FakeGitTransport]:
    transport = _FakeGitTransport(_github_tarball(_REPO, SHA_A))
    return ImmutableGitStore(tmp_path, transport), transport


def _published(tmp_path: Path) -> tuple[ImmutableGitStore, _FakeGitTransport]:
    store, transport = _new_store(tmp_path)
    store.publish(SHA_A, owner=_OWNER, repo=_REPO)
    return store, transport


def _plant_metadata_only(root: Path, sha: str) -> Path:
    sha_dir = root / "commits" / sha
    sha_dir.mkdir(parents=True)
    path = sha_dir / "metadata.json"
    path.write_text(
        json.dumps({"sha": sha, "owner": _OWNER, "repo": _REPO}),
        encoding="utf-8",
    )
    return path


def _plant_tree_only(root: Path, sha: str) -> Path:
    tree = root / "commits" / sha / "tree"
    tree.mkdir(parents=True)
    (tree / "harness.toml").write_text(_HARNESS_TOML, encoding="utf-8")
    return tree


def _store_source() -> str:
    return _STORE_PY.read_text(encoding="utf-8")


class TestImmutableGitStore:
    def test_constructs_without_arguments_raises_type_error(self):
        with pytest.raises(TypeError):
            ImmutableGitStore()  # type: ignore[call-arg]

    def test_constructs_without_root_raises_type_error(self):
        transport = _FakeGitTransport(_github_tarball(_REPO, SHA_A))
        with pytest.raises(TypeError):
            ImmutableGitStore(transport=transport)  # type: ignore[call-arg]

    def test_constructs_without_transport_raises_type_error(self, tmp_path):
        with pytest.raises(TypeError):
            ImmutableGitStore(tmp_path)  # type: ignore[call-arg]

    def test_constructs_with_root_none_raises_type_error(self):
        transport = _FakeGitTransport(_github_tarball(_REPO, SHA_A))
        with pytest.raises(TypeError):
            ImmutableGitStore(None, transport)  # type: ignore[arg-type]

    def test_constructs_with_transport_none_raises_type_error(self, tmp_path):
        with pytest.raises(TypeError):
            ImmutableGitStore(tmp_path, None)  # type: ignore[arg-type]

    def test_constructs_with_root_and_transport_positionally(self, tmp_path):
        params = inspect.signature(ImmutableGitStore).parameters
        assert list(params) == ["root", "transport"]
        assert params["root"].default is inspect.Parameter.empty
        assert params["transport"].default is inspect.Parameter.empty
        transport = _FakeGitTransport(_github_tarball(_REPO, SHA_A))
        store = ImmutableGitStore(tmp_path, transport)
        assert isinstance(store, ImmutableGitStore)

    def test_publish_writes_owner_and_repo_from_kwargs(self, tmp_path):
        store, _transport = _new_store(tmp_path)
        store.publish(SHA_A, owner=_OWNER, repo=_REPO)
        payload = json.loads(
            (tmp_path / "commits" / SHA_A / "metadata.json").read_text(encoding="utf-8")
        )
        assert payload["owner"] == _OWNER
        assert payload["repo"] == _REPO
        assert payload["owner"] != _REPO

    def test_publish_places_harness_toml_at_flattened_tree_path(self, tmp_path):
        store, _transport = _new_store(tmp_path)
        store.publish(SHA_A, owner=_OWNER, repo=_REPO)
        tree = store.tree_path(SHA_A)
        assert tree == tmp_path / "commits" / SHA_A / "tree"
        harness = tree / "harness.toml"
        assert harness.is_file()
        assert harness.read_text(encoding="utf-8") == _HARNESS_TOML
        assert not (tree / f"{_REPO}-{SHA_A}").exists()

    def test_has_is_true_after_complete_publish(self, tmp_path):
        store, _transport = _new_store(tmp_path)
        store.publish(SHA_A, owner=_OWNER, repo=_REPO)
        assert store.has(SHA_A) is True

    def test_publish_returns_path_where_tree_path_works(self, tmp_path):
        store, _transport = _new_store(tmp_path)
        returned = store.publish(SHA_A, owner=_OWNER, repo=_REPO)
        tree = store.tree_path(SHA_A)
        sha_dir = tmp_path / "commits" / SHA_A
        assert isinstance(returned, Path)
        assert returned in {tree, sha_dir}
        assert tree == sha_dir / "tree"
        assert tree.is_dir()

    def test_republish_same_provenance_does_not_fetch_archive(self, tmp_path):
        store, transport = _published(tmp_path)
        assert len(transport.fetch_calls) == 1
        store.publish(SHA_A, owner=_OWNER, repo=_REPO)
        assert len(transport.fetch_calls) == 1

    def test_republish_same_provenance_does_not_replace_tree(self, tmp_path):
        store, _transport = _published(tmp_path)
        sentinel = store.tree_path(SHA_A) / "sentinel.txt"
        sentinel.write_text("planted", encoding="utf-8")
        store.publish(SHA_A, owner=_OWNER, repo=_REPO)
        assert sentinel.is_file()
        assert sentinel.read_text(encoding="utf-8") == "planted"

    def test_publish_same_sha_different_owner_raises_sha_conflict_error(self, tmp_path):
        store, _transport = _published(tmp_path)
        with pytest.raises(ShaConflictError):
            store.publish(SHA_A, owner="other", repo=_REPO)

    def test_publish_same_sha_different_repo_raises_sha_conflict_error(self, tmp_path):
        store, _transport = _published(tmp_path)
        with pytest.raises(ShaConflictError):
            store.publish(SHA_A, owner=_OWNER, repo="gadgets")

    def test_publish_conflict_leaves_tree_unchanged(self, tmp_path):
        store, _transport = _published(tmp_path)
        sentinel = store.tree_path(SHA_A) / "sentinel.txt"
        sentinel.write_text("planted", encoding="utf-8")
        with pytest.raises(ShaConflictError):
            store.publish(SHA_A, owner="other", repo=_REPO)
        assert sentinel.read_text(encoding="utf-8") == "planted"

    def test_has_is_false_when_only_metadata_exists(self, tmp_path):
        _plant_metadata_only(tmp_path, SHA_A)
        store, _transport = _new_store(tmp_path)
        assert store.has(SHA_A) is False

    def test_tree_path_raises_unknown_sha_when_only_metadata_exists(self, tmp_path):
        _plant_metadata_only(tmp_path, SHA_A)
        store, _transport = _new_store(tmp_path)
        with pytest.raises(UnknownShaError):
            store.tree_path(SHA_A)

    def test_has_is_false_when_only_tree_exists(self, tmp_path):
        _plant_tree_only(tmp_path, SHA_A)
        store, _transport = _new_store(tmp_path)
        assert store.has(SHA_A) is False

    def test_tree_path_raises_unknown_sha_when_only_tree_exists(self, tmp_path):
        _plant_tree_only(tmp_path, SHA_A)
        store, _transport = _new_store(tmp_path)
        with pytest.raises(UnknownShaError):
            store.tree_path(SHA_A)

    def test_publish_completes_metadata_only_directory(self, tmp_path):
        _plant_metadata_only(tmp_path, SHA_A)
        store, transport = _new_store(tmp_path)
        store.publish(SHA_A, owner=_OWNER, repo=_REPO)
        assert len(transport.fetch_calls) == 1
        assert store.has(SHA_A) is True
        harness = store.tree_path(SHA_A) / "harness.toml"
        assert harness.is_file()
        assert harness.read_text(encoding="utf-8") == _HARNESS_TOML

    def test_has_is_false_for_never_published_sha(self, tmp_path):
        store, _transport = _new_store(tmp_path)
        assert store.has(SHA_A) is False

    def test_tree_path_raises_unknown_sha_for_never_published_sha(self, tmp_path):
        store, _transport = _new_store(tmp_path)
        with pytest.raises(UnknownShaError):
            store.tree_path(SHA_A)

    def test_store_source_does_not_contain_materialize(self):
        assert "materialize" not in _store_source()

    def test_store_source_does_not_contain_resolve_commit(self):
        assert "resolve_commit" not in _store_source()

    def test_store_source_uses_extract_git_archive(self):
        assert extract_git_archive.__name__ in _store_source()

    def test_publish_does_not_create_refs_directory(self, tmp_path):
        _published(tmp_path)
        assert not (tmp_path / "refs").exists()

    def test_publish_does_not_create_pointers_directory(self, tmp_path):
        _published(tmp_path)
        assert not (tmp_path / "pointers").exists()

    def test_publish_does_not_write_pointer_files(self, tmp_path):
        _published(tmp_path)
        assert {path.name for path in tmp_path.iterdir()} == {"commits"}

    def test_layer_errors_subclass_store_error(self):
        assert issubclass(StoreError, Exception)
        assert issubclass(UnknownShaError, StoreError)
        assert issubclass(ShaConflictError, StoreError)

    def test_publish_does_not_call_resolve_commit(self, tmp_path):
        _store, transport = _published(tmp_path)
        assert transport.resolve_calls == []
