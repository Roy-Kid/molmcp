#!/usr/bin/env python3
"""Regression example: public ``resolve_github`` through a fake GitTransport.

Standalone (no pytest dependency). Builds an in-memory ``tar.gz`` whose
inner tree contains ``calc.py``, patches the private
``molmcp.discovery.source.github._transport`` seam with a stdlib
``unittest.mock.patch`` (not pytest), and drives the public
``resolve_github`` surface. Asserts the hard-coded goldens below.

Hard-coded goldens (in-repo fake, 2026-09-04, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-03-git-fetch.md``, Testing
strategy -> Regression example):

    sha == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    snapshot.snapshot_id == "github:commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    snapshot.commit equal to that SHA
    any(f.rel_path == "calc.py" for f in snapshot.files)
    SnapshotCache(config).raw_dir(snapshot.snapshot_id) / ".extracted" is a file

Imports are this project plus stdlib (``io``, ``tarfile``,
``tempfile``, ``unittest.mock``). No urllib, no DiscoveryEngine, no live
third-party oracle.

Run directly::

    uv run python regressions/autonomous-harness-evolution-03-git-fetch.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via ``test_autonomous_harness_evolution_03_git_fetch``.
"""

from __future__ import annotations

import io
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

from molmcp.discovery.cache.snapshotcache import SnapshotCache
from molmcp.discovery.config import DiscoveryConfig
from molmcp.discovery.source.github import resolve_github

# In-repo goldens, 2026-09-04, no third-party oracle.
_EXPECTED_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_EXPECTED_SNAPSHOT_ID = "github:commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_EXPECTED_REL_PATH = "calc.py"


def _make_tarball(top: str, files: dict[str, str]) -> bytes:
    """In-memory GitHub-style tar.gz (BytesIO + tarfile; no network)."""
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

    def __init__(self) -> None:
        self.archive = _make_tarball(
            f"repo-{_EXPECTED_SHA}",
            {_EXPECTED_REL_PATH: "def add(a, b):\n    return a + b\n"},
        )

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        return _EXPECTED_SHA

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        return self.archive


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


def main() -> int:
    fake = _FakeTransport()
    with tempfile.TemporaryDirectory(prefix="molmcp-git-fetch-regression-") as tmp:
        config = DiscoveryConfig(cache_dir=Path(tmp))
        with patch(
            "molmcp.discovery.source.github._transport",
            lambda _config: fake,
        ):
            snapshot = resolve_github("github:owner/repo", config)

        _require(
            snapshot.snapshot_id == _EXPECTED_SNAPSHOT_ID,
            f"snapshot.snapshot_id {snapshot.snapshot_id!r} "
            f"!= {_EXPECTED_SNAPSHOT_ID!r}",
        )
        _require(
            snapshot.commit == _EXPECTED_SHA,
            f"snapshot.commit {snapshot.commit!r} != {_EXPECTED_SHA!r}",
        )
        has_calc = any(f.rel_path == _EXPECTED_REL_PATH for f in snapshot.files)
        _require(
            has_calc,
            f"snapshot.files {[f.rel_path for f in snapshot.files]!r} "
            f"has no {_EXPECTED_REL_PATH!r}",
        )

        marker = SnapshotCache(config).raw_dir(snapshot.snapshot_id) / ".extracted"
        _require(marker.is_file(), f"{marker} is not a file")

        print(f"sha={snapshot.commit}")
        print(f"snapshot_id={snapshot.snapshot_id}")
        print(f"calc.py present={has_calc}")
        print(f".extracted is file={marker.is_file()}")

    print("\nOK: public resolve_github goldens match.")
    return 0


def test_autonomous_harness_evolution_03_git_fetch() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
