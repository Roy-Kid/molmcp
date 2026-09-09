"""The two GitTransport implementations and extract_git_archive.

``GitHubTransport`` is driven against a fake ``urlopen``: no socket is
opened here. ``LocalGitTransport`` is the opposite kind of leaf — it shells
out to ``git`` against a checkout this module builds in ``tmp_path``, so it
is driven against a *real* repository rather than a mock. Neither reaches
the network, and no ``DiscoveryEngine`` is involved in either.
"""

from __future__ import annotations

import inspect
import io
import json
import subprocess
import tarfile
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import NamedTuple

import pytest

from molmcp.components import git as git_mod
from molmcp.components.git import (
    GitError,
    GitHubTransport,
    GitTransport,
    extract_git_archive,
)

_OWNER = "owner"
_REPO = "repo"
_SHA = "a" * 40
_API = "https://api.github.com"
_CODELOAD = "https://codeload.github.com"
_REPO_URL = f"{_API}/repos/{_OWNER}/{_REPO}"
_COMMITS_DEV = f"{_API}/repos/{_OWNER}/{_REPO}/commits/dev"
_COMMITS_MAIN = f"{_API}/repos/{_OWNER}/{_REPO}/commits/main"
_ARCHIVE_URL = f"{_CODELOAD}/{_OWNER}/{_REPO}/tar.gz/{_SHA}"
_GIT_PY = (
    Path(__file__).resolve().parents[2] / "src" / "molmcp" / "components" / "git.py"
)


def _json_body(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _headers(request: urllib.request.Request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}


def _make_tarball(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeUrlOpen:
    """Stand-in for ``urllib.request.urlopen``; never opens a socket."""

    def __init__(
        self,
        body_for: dict[str, bytes] | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.body_for = body_for or {}
        self.error = error
        self.calls: list[tuple[urllib.request.Request, float | None]] = []

    def __call__(
        self,
        request: urllib.request.Request,
        timeout: float | None = None,
    ) -> _FakeResponse:
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        url = request.full_url
        if url not in self.body_for:
            raise AssertionError(f"unexpected urlopen url: {url}")
        return _FakeResponse(self.body_for[url])


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeUrlOpen) -> _FakeUrlOpen:
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


class TestGitHubTransport:
    def test_protocol_declares_resolve_commit_and_fetch_archive(self):
        assert callable(getattr(GitTransport, "resolve_commit", None))
        assert callable(getattr(GitTransport, "fetch_archive", None))

    def test_git_error_is_runtime_error(self):
        assert issubclass(GitError, RuntimeError)

    def test_constructs_without_arguments(self):
        GitHubTransport()

    def test_constructs_with_token_none(self):
        GitHubTransport(token=None)

    def test_constructor_takes_only_token(self):
        params = inspect.signature(GitHubTransport).parameters
        assert list(params) == ["token"]
        assert params["token"].default is None
        with pytest.raises(TypeError):
            GitHubTransport(timeout=30)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            GitHubTransport(config=None)  # type: ignore[call-arg]

    def test_resolve_commit_with_ref_hits_commits_url_and_returns_sha(
        self, monkeypatch
    ):
        fake = _install(
            monkeypatch,
            _FakeUrlOpen({_COMMITS_DEV: _json_body({"sha": _SHA})}),
        )
        sha = GitHubTransport().resolve_commit(_OWNER, _REPO, ref="dev")
        assert sha == _SHA
        assert len(fake.calls) == 1
        request, _timeout = fake.calls[0]
        assert request.full_url == _COMMITS_DEV

    def test_resolve_commit_without_ref_resolves_default_branch_first(
        self, monkeypatch
    ):
        fake = _install(
            monkeypatch,
            _FakeUrlOpen(
                {
                    _REPO_URL: _json_body({"default_branch": "main"}),
                    _COMMITS_MAIN: _json_body({"sha": _SHA}),
                }
            ),
        )
        sha = GitHubTransport().resolve_commit(_OWNER, _REPO, ref=None)
        assert sha == _SHA
        assert [request.full_url for request, _timeout in fake.calls] == [
            _REPO_URL,
            _COMMITS_MAIN,
        ]

    def test_fetch_archive_hits_codeload_and_returns_bytes(self, monkeypatch):
        payload = b"tarball-bytes"
        fake = _install(monkeypatch, _FakeUrlOpen({_ARCHIVE_URL: payload}))
        data = GitHubTransport().fetch_archive(_OWNER, _REPO, _SHA)
        assert data == payload
        assert len(fake.calls) == 1
        request, _timeout = fake.calls[0]
        assert request.full_url == _ARCHIVE_URL

    def test_user_agent_is_exactly_molmcp(self, monkeypatch):
        fake = _install(
            monkeypatch,
            _FakeUrlOpen(
                {
                    _REPO_URL: _json_body({"default_branch": "main"}),
                    _COMMITS_MAIN: _json_body({"sha": _SHA}),
                    _ARCHIVE_URL: b"tarball-bytes",
                }
            ),
        )
        transport = GitHubTransport()
        transport.resolve_commit(_OWNER, _REPO, ref=None)
        transport.fetch_archive(_OWNER, _REPO, _SHA)
        assert fake.calls, "expected urlopen to be called"
        for request, _timeout in fake.calls:
            assert _headers(request)["user-agent"] == "molmcp"

    def test_token_sends_authorization_bearer(self, monkeypatch):
        fake = _install(
            monkeypatch,
            _FakeUrlOpen({_COMMITS_DEV: _json_body({"sha": _SHA})}),
        )
        GitHubTransport(token="test-token").resolve_commit(_OWNER, _REPO, ref="dev")
        request, _timeout = fake.calls[0]
        assert _headers(request)["authorization"] == "Bearer test-token"

    def test_token_none_omits_authorization(self, monkeypatch):
        fake = _install(
            monkeypatch,
            _FakeUrlOpen({_COMMITS_DEV: _json_body({"sha": _SHA})}),
        )
        GitHubTransport(token=None).resolve_commit(_OWNER, _REPO, ref="dev")
        request, _timeout = fake.calls[0]
        assert "authorization" not in _headers(request)

    def test_urlopen_timeout_is_thirty_seconds(self, monkeypatch):
        fake = _install(
            monkeypatch,
            _FakeUrlOpen({_COMMITS_DEV: _json_body({"sha": _SHA})}),
        )
        GitHubTransport().resolve_commit(_OWNER, _REPO, ref="dev")
        _request, timeout = fake.calls[0]
        assert timeout == 30

    @pytest.mark.parametrize("code", [404, 503])
    def test_http_error_raises_git_error(self, monkeypatch, code):
        error = urllib.error.HTTPError(_COMMITS_DEV, code, "error", Message(), None)
        _install(monkeypatch, _FakeUrlOpen(error=error))
        with pytest.raises(GitError):
            GitHubTransport().resolve_commit(_OWNER, _REPO, ref="dev")

    def test_url_error_raises_git_error(self, monkeypatch):
        _install(
            monkeypatch,
            _FakeUrlOpen(error=urllib.error.URLError("connection refused")),
        )
        with pytest.raises(GitError):
            GitHubTransport().resolve_commit(_OWNER, _REPO, ref="dev")

    def test_json_payload_without_sha_raises_git_error(self, monkeypatch):
        _install(
            monkeypatch,
            _FakeUrlOpen({_COMMITS_DEV: _json_body({"message": "ok"})}),
        )
        with pytest.raises(GitError):
            GitHubTransport().resolve_commit(_OWNER, _REPO, ref="dev")

    def test_source_does_not_read_the_environment(self):
        text = _GIT_PY.read_text(encoding="utf-8")
        assert "os.environ" not in text
        assert "getenv" not in text


class TestExtractGitArchive:
    def test_extracts_inner_root_and_file(self, tmp_path):
        dest = tmp_path / "raw"
        dest.mkdir()
        data = _make_tarball({"repo-sha/calc.py": "x = 1"})
        root = extract_git_archive(data, dest)
        assert root == dest / "repo-sha"
        inner = dest / "repo-sha" / "calc.py"
        assert inner.is_file()
        assert inner.read_text(encoding="utf-8") == "x = 1"

    def test_empty_bytes_raises_git_error(self, tmp_path):
        dest = tmp_path / "raw"
        dest.mkdir()
        with pytest.raises(GitError):
            extract_git_archive(b"", dest)

    def test_corrupt_bytes_raises_git_error(self, tmp_path):
        dest = tmp_path / "raw"
        dest.mkdir()
        with pytest.raises(GitError):
            extract_git_archive(b"this is not a tar.gz", dest)

    def test_tarball_with_no_directory_entry_raises_git_error(self, tmp_path):
        dest = tmp_path / "raw"
        dest.mkdir()
        data = _make_tarball({"calc.py": "x = 1"})
        with pytest.raises(GitError):
            extract_git_archive(data, dest)


_BRANCH = "dev"
_TAG = "v1"
_ANNOTATED_TAG = "v1-signed-off"
_MANIFEST = '[harness]\nname = "mine"\n'
_SKILL = "# greet\n"
_SCRATCH = "still being edited\n"
_IDENTITY = (
    "-c",
    "user.name=molmcp tests",
    "-c",
    "user.email=tests@molmcp.invalid",
)


class _Checkout(NamedTuple):
    """A real git repository built under ``tmp_path``.

    Two commits, so a ref that is not ``HEAD`` has somewhere else to point:
    ``tagged`` carries only ``harness.toml`` and is what ``dev``, the
    lightweight ``v1`` and the annotated ``v1-signed-off`` all name;
    ``head`` adds ``skills/greet.md`` on ``main``. One more file —
    ``scratch.txt`` — sits in the working tree, committed by nothing.
    """

    root: Path
    head: str
    tagged: str


def _git(root: Path, *args: str) -> str:
    """Run one git command inside ``root`` and return its stripped stdout."""
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init(root: Path) -> None:
    """Create ``root`` as an empty repository on ``main`` with 40-hex SHAs."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "--initial-branch=main", "--object-format=sha1")


def _commit(root: Path, message: str) -> str:
    """Commit everything currently in ``root`` and return the new SHA."""
    _git(root, "add", "-A")
    _git(root, *_IDENTITY, "commit", "--no-gpg-sign", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(root: Path) -> set[str]:
    """Every file under ``root``, as slash-separated relative paths."""
    return {
        item.relative_to(root).as_posix() for item in root.rglob("*") if item.is_file()
    }


def _extract(data: bytes, tmp_path: Path, name: str) -> Path:
    """Extract ``data`` into a fresh directory and return the inner tree."""
    dest = tmp_path / name
    dest.mkdir()
    return extract_git_archive(data, dest)


@pytest.fixture
def checkout(tmp_path: Path) -> _Checkout:
    root = tmp_path / "harness"
    _init(root)
    _write(root / "harness.toml", _MANIFEST)
    tagged = _commit(root, "first")
    _git(root, "tag", _TAG)
    _git(
        root,
        *_IDENTITY,
        "-c",
        "tag.gpgSign=false",
        "tag",
        "-a",
        _ANNOTATED_TAG,
        "-m",
        "release one",
    )
    _git(root, "branch", _BRANCH)
    _write(root / "skills" / "greet.md", _SKILL)
    head = _commit(root, "second")
    _write(root / "scratch.txt", _SCRATCH)
    return _Checkout(root=root, head=head, tagged=tagged)


class TestLocalGitTransport:
    """A harness source that is a checkout on disk rather than a coordinate.

    Same two primitives as :class:`GitHubTransport` — resolve a ref to a
    commit SHA, hand back that commit's gzip tarball — read out of a local
    repository instead of over HTTP. ``owner`` and ``repo`` are accepted
    because the ``GitTransport`` protocol passes them, and are *ignored*:
    the ``root`` this was constructed with is the whole repository
    selection, which is the one difference worth pinning.

    The property that makes a local source a *source* rather than a
    directory read is that ``fetch_archive`` archives the committed tree at
    a SHA — never the working tree. Without it, "pinned to a commit" would
    mean "whatever the operator had unsaved at the moment we looked", and
    there would be no reason to go through git at all instead of copying
    the directory.

    The class is reached through ``git_mod`` rather than imported by name
    at module scope on purpose: while it does not exist, every test here
    fails on its own ``AttributeError`` instead of one collection error
    taking :class:`TestGitHubTransport` down with it.
    """

    def test_constructor_takes_only_root(self) -> None:
        params = inspect.signature(git_mod.LocalGitTransport).parameters
        assert list(params) == ["root"]

    def test_the_methods_take_the_protocol_parameters(self) -> None:
        for method in ("resolve_commit", "fetch_archive"):
            assert list(
                inspect.signature(getattr(git_mod.LocalGitTransport, method)).parameters
            ) == list(inspect.signature(getattr(GitTransport, method)).parameters)

    def test_resolve_commit_of_head_returns_a_forty_hex_sha(
        self, checkout: _Checkout
    ) -> None:
        sha = git_mod.LocalGitTransport(checkout.root).resolve_commit(
            _OWNER, _REPO, "HEAD"
        )

        assert sha == checkout.head
        assert len(sha) == 40
        assert set(sha) <= set("0123456789abcdef")

    def test_resolve_commit_of_the_checked_out_branch_is_the_head_commit(
        self, checkout: _Checkout
    ) -> None:
        sha = git_mod.LocalGitTransport(checkout.root).resolve_commit(
            _OWNER, _REPO, "main"
        )

        assert sha == checkout.head

    def test_resolve_commit_of_another_branch_is_that_branchs_tip(
        self, checkout: _Checkout
    ) -> None:
        sha = git_mod.LocalGitTransport(checkout.root).resolve_commit(
            _OWNER, _REPO, _BRANCH
        )

        assert sha == checkout.tagged
        assert sha != checkout.head

    def test_resolve_commit_of_a_tag_is_the_tagged_commit(
        self, checkout: _Checkout
    ) -> None:
        sha = git_mod.LocalGitTransport(checkout.root).resolve_commit(
            _OWNER, _REPO, _TAG
        )

        assert sha == checkout.tagged

    def test_resolve_commit_of_an_annotated_tag_is_the_commit_not_the_tag_object(
        self, checkout: _Checkout
    ) -> None:
        """``git rev-parse`` on an annotated tag yields the *tag object*.

        The protocol promises a commit SHA, and a tag object's SHA is not
        one — an activation pinned to it would name something ``git log``
        cannot walk. ``git tag -a`` is how a harness release gets cut, so
        this is the ordinary case rather than an exotic one.
        """
        sha = git_mod.LocalGitTransport(checkout.root).resolve_commit(
            _OWNER, _REPO, _ANNOTATED_TAG
        )

        assert sha == checkout.tagged

    def test_resolve_commit_of_a_sha_is_that_same_sha(
        self, checkout: _Checkout
    ) -> None:
        transport = git_mod.LocalGitTransport(checkout.root)

        assert transport.resolve_commit(_OWNER, _REPO, checkout.tagged) == (
            checkout.tagged
        )

    def test_resolve_commit_without_a_ref_takes_the_default_branch(
        self, checkout: _Checkout
    ) -> None:
        sha = git_mod.LocalGitTransport(checkout.root).resolve_commit(
            _OWNER, _REPO, None
        )

        assert sha == checkout.head

    @pytest.mark.parametrize(
        ("owner", "repo"),
        [("", ""), ("acme", "somewhere-else"), ("MolCrafts", "harness")],
    )
    def test_owner_and_repo_do_not_select_the_repository(
        self, checkout: _Checkout, owner: str, repo: str
    ) -> None:
        sha = git_mod.LocalGitTransport(checkout.root).resolve_commit(
            owner, repo, "HEAD"
        )

        assert sha == checkout.head

    def test_the_root_is_what_selects_the_repository(
        self, tmp_path: Path, checkout: _Checkout
    ) -> None:
        other = tmp_path / "other"
        _init(other)
        _write(other / "harness.toml", '[harness]\nname = "other"\n')
        other_head = _commit(other, "only")

        assert other_head != checkout.head
        assert (
            git_mod.LocalGitTransport(checkout.root).resolve_commit(_OWNER, _REPO, None)
            == checkout.head
        )
        assert (
            git_mod.LocalGitTransport(other).resolve_commit(_OWNER, _REPO, None)
            == other_head
        )

    def test_fetch_archive_returns_bytes_extract_git_archive_accepts(
        self, tmp_path: Path, checkout: _Checkout
    ) -> None:
        data = git_mod.LocalGitTransport(checkout.root).fetch_archive(
            _OWNER, _REPO, checkout.head
        )

        assert isinstance(data, bytes)
        inner = _extract(data, tmp_path, "raw")
        assert inner.is_dir()

    def test_the_archived_tree_holds_the_committed_files(
        self, tmp_path: Path, checkout: _Checkout
    ) -> None:
        data = git_mod.LocalGitTransport(checkout.root).fetch_archive(
            _OWNER, _REPO, checkout.head
        )

        inner = _extract(data, tmp_path, "raw")
        assert _tree(inner) == {"harness.toml", "skills/greet.md"}
        assert (inner / "harness.toml").read_text(encoding="utf-8") == _MANIFEST

    def test_the_archive_is_the_committed_tree_not_the_working_tree(
        self, tmp_path: Path, checkout: _Checkout
    ) -> None:
        assert (checkout.root / "scratch.txt").is_file(), "fixture wrote no scratch"

        data = git_mod.LocalGitTransport(checkout.root).fetch_archive(
            _OWNER, _REPO, checkout.head
        )

        inner = _extract(data, tmp_path, "raw")
        assert "scratch.txt" not in _tree(inner)

    def test_an_earlier_sha_archives_that_commits_tree(
        self, tmp_path: Path, checkout: _Checkout
    ) -> None:
        data = git_mod.LocalGitTransport(checkout.root).fetch_archive(
            _OWNER, _REPO, checkout.tagged
        )

        inner = _extract(data, tmp_path, "raw")
        assert _tree(inner) == {"harness.toml"}

    def test_the_inner_directory_names_the_commit_it_was_taken_at(
        self, tmp_path: Path, checkout: _Checkout
    ) -> None:
        data = git_mod.LocalGitTransport(checkout.root).fetch_archive(
            _OWNER, _REPO, checkout.head
        )

        inner = _extract(data, tmp_path, "raw")
        assert checkout.head in inner.name

    def test_an_unknown_ref_raises_git_error(self, checkout: _Checkout) -> None:
        with pytest.raises(GitError) as excinfo:
            git_mod.LocalGitTransport(checkout.root).resolve_commit(
                _OWNER, _REPO, "no-such"
            )

        assert not isinstance(excinfo.value, subprocess.CalledProcessError)

    def test_an_unknown_sha_raises_git_error(self, checkout: _Checkout) -> None:
        with pytest.raises(GitError) as excinfo:
            git_mod.LocalGitTransport(checkout.root).fetch_archive(_OWNER, _REPO, _SHA)

        assert not isinstance(excinfo.value, subprocess.CalledProcessError)

    def test_a_root_that_is_not_a_repository_raises_git_error(
        self, tmp_path: Path
    ) -> None:
        plain = tmp_path / "plain"
        _write(plain / "harness.toml", _MANIFEST)

        with pytest.raises(GitError):
            git_mod.LocalGitTransport(plain).resolve_commit(_OWNER, _REPO, "HEAD")

    def test_fetching_from_a_root_that_is_not_a_repository_raises_git_error(
        self, tmp_path: Path
    ) -> None:
        plain = tmp_path / "plain"
        _write(plain / "harness.toml", _MANIFEST)

        with pytest.raises(GitError):
            git_mod.LocalGitTransport(plain).fetch_archive(_OWNER, _REPO, _SHA)

    def test_a_root_that_does_not_exist_raises_git_error(self, tmp_path: Path) -> None:
        with pytest.raises(GitError):
            git_mod.LocalGitTransport(tmp_path / "missing").resolve_commit(
                _OWNER, _REPO, "HEAD"
            )
