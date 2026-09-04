"""GitHub ref-freshness tests (transport faked)."""

from __future__ import annotations

import io
import tarfile

import pytest

from molmcp.discovery import DiscoveryConfig, DiscoveryEngine
from molmcp.discovery.source import github

_SHA1 = "a" * 40
_SHA2 = "b" * 40
_FILES = {"calc.py": "def add(a, b):\n    return a + b\n"}
_MUL = {"calc.py": "def mul(a, b):\n    return a * b\n"}


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

    def __init__(self, sha: str, files: dict[str, str] | None = None) -> None:
        self.sha = sha
        self.files = dict(_FILES if files is None else files)
        self.archive = _make_tarball(f"repo-{sha}", self.files)

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        return self.sha

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        return self.archive


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeTransport) -> _FakeTransport:
    monkeypatch.setattr(github, "_transport", lambda _config: fake)
    return fake


def _engine(tmp_path) -> DiscoveryEngine:
    return DiscoveryEngine(DiscoveryConfig(cache_dir=tmp_path / "cache"))


def test_freshness_unknown_when_not_indexed(tmp_path):
    assert _engine(tmp_path).check_freshness("github:owner/repo") == "unknown"


def test_freshness_fresh_after_index(monkeypatch, tmp_path):
    _install(monkeypatch, _FakeTransport(_SHA1))
    engine = _engine(tmp_path)
    engine.index("github:owner/repo")
    assert engine.check_freshness("github:owner/repo") == "fresh"


def test_freshness_stale_when_remote_moves(monkeypatch, tmp_path):
    _install(monkeypatch, _FakeTransport(_SHA1))
    engine = _engine(tmp_path)
    engine.index("github:owner/repo")

    _install(monkeypatch, _FakeTransport(_SHA2))
    assert engine.check_freshness("github:owner/repo") == "stale"


def test_refresh_picks_up_new_commit(monkeypatch, tmp_path):
    _install(monkeypatch, _FakeTransport(_SHA1))
    engine = _engine(tmp_path)
    first = engine.index("github:owner/repo")
    assert first.snapshot.commit == _SHA1

    _install(monkeypatch, _FakeTransport(_SHA2, _MUL))
    result = engine.refresh("github:owner/repo")
    assert result.snapshot.commit == _SHA2
    assert result.freshness == "fresh"
    assert "calc.mul" in {n.qualname for n in result.graph.nodes}


def test_local_source_is_always_fresh(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("x = 1\n", encoding="utf-8")
    assert _engine(tmp_path).check_freshness(str(repo)) == "fresh"
