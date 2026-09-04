"""GitHub source resolution tests (transport faked; no DiscoveryEngine)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from molmcp.components.git import GitError
from molmcp.discovery.cache.snapshotcache import SnapshotCache
from molmcp.discovery.config import DiscoveryConfig
from molmcp.discovery.source import SourceError, github
from molmcp.discovery.source.github import latest_commit, resolve_github

_SHA = "a" * 40
_FILES = {"calc.py": "def add(a, b):\n    return a + b\n"}
_GITHUB_PY = Path(github.__file__).resolve()


def _make_tarball(top: str, files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=f"{top}/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _FakeTransport:
    """GitTransport stand-in: resolve_commit + fetch_archive, no sockets."""

    def __init__(
        self,
        sha: str = _SHA,
        files: dict[str, str] | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.sha = sha
        self.files = dict(_FILES if files is None else files)
        self.archive = _make_tarball(f"repo-{sha}", self.files)
        self.error = error
        self.resolve_calls: list[tuple[str, str, str | None]] = []
        self.fetch_calls: list[tuple[str, str, str]] = []

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        self.resolve_calls.append((owner, repo, ref))
        if self.error is not None:
            raise self.error
        return self.sha

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        self.fetch_calls.append((owner, repo, sha))
        return self.archive


def _config(tmp_path: Path) -> DiscoveryConfig:
    return DiscoveryConfig(cache_dir=tmp_path / "cache")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeTransport) -> _FakeTransport:
    monkeypatch.setattr(github, "_transport", lambda _config: fake)
    return fake


class TestResolveGithub:
    def test_snapshot_identity_inner_tree_and_extracted_marker(
        self, monkeypatch, tmp_path
    ):
        config = _config(tmp_path)
        _install(monkeypatch, _FakeTransport())
        snapshot = resolve_github("github:owner/repo", config)

        assert snapshot.snapshot_id == "github:commit:" + _SHA
        assert snapshot.commit == _SHA
        assert snapshot.origin == "github"
        assert snapshot.root_dir.name == f"repo-{_SHA}"
        assert snapshot.root_dir.is_dir()
        assert any(f.rel_path == "calc.py" for f in snapshot.files)

        marker = SnapshotCache(config).raw_dir(snapshot.snapshot_id) / ".extracted"
        assert marker.is_file()
        assert marker.read_text(encoding="utf-8").strip() == str(snapshot.root_dir)

    def test_ref_in_spec_is_passed_to_resolve_commit(self, monkeypatch, tmp_path):
        fake = _install(monkeypatch, _FakeTransport())
        snapshot = resolve_github("github:owner/repo@dev", _config(tmp_path))
        assert snapshot.ref == "dev"
        assert fake.resolve_calls
        assert fake.resolve_calls[0] == ("owner", "repo", "dev")

    def test_invalid_spec_does_not_call_transport(self, monkeypatch, tmp_path):
        fake = _install(monkeypatch, _FakeTransport())
        with pytest.raises(SourceError):
            resolve_github("github:not-a-valid-spec", _config(tmp_path))
        assert fake.resolve_calls == []
        assert fake.fetch_calls == []

    def test_git_error_is_mapped_to_source_error(self, monkeypatch, tmp_path):
        _install(
            monkeypatch,
            _FakeTransport(
                error=GitError("GitHub request failed (404) for https://example")
            ),
        )
        with pytest.raises(SourceError, match="GitHub request failed") as caught:
            resolve_github("github:owner/repo", _config(tmp_path))
        assert isinstance(caught.value, SourceError)
        assert not isinstance(caught.value, GitError)

    def test_second_resolve_skips_fetch_archive(self, monkeypatch, tmp_path):
        config = _config(tmp_path)
        fake = _install(monkeypatch, _FakeTransport())
        resolve_github("github:owner/repo", config)
        assert len(fake.fetch_calls) == 1
        resolve_github("github:owner/repo", config)
        assert len(fake.fetch_calls) == 1


class TestLatestCommit:
    def test_returns_same_sha_as_resolve_github(self, monkeypatch, tmp_path):
        config = _config(tmp_path)
        _install(monkeypatch, _FakeTransport())
        snapshot = resolve_github("github:owner/repo", config)
        assert latest_commit("github:owner/repo", config) == snapshot.commit
        assert latest_commit("github:owner/repo", config) == _SHA


class TestGithubModuleSource:
    def test_does_not_import_urllib(self):
        source = _GITHUB_PY.read_text(encoding="utf-8")
        assert "import urllib" not in source
        assert "urllib." not in source

    def test_drops_legacy_http_and_extract_names(self):
        source = _GITHUB_PY.read_text(encoding="utf-8")
        for needle in (
            "_http_get",
            "resolve_ref",
            "_safe_extract",
            "tarfile.extractall",
        ):
            assert needle not in source, needle
