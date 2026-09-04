"""GitHubTransport and extract_git_archive — network mocked, no DiscoveryEngine."""

from __future__ import annotations

import inspect
import io
import json
import tarfile
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

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
