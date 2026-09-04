#!/usr/bin/env python3
"""Regression example: public Activation through ImmutableGitStore.

Standalone (no pytest dependency). Builds in-memory GitHub-style ``tar.gz``
archives whose inner trees contain a catalog-eligible ``harness.toml``,
publishes both SHAs through a fake ``GitTransport.fetch_archive`` (real
``extract_git_archive`` inside ``publish``), binds an ``Activation`` pointer,
and drives ``stage`` / ``promote`` / ``rollback``. Asserts the hard-coded
goldens below. Properties are read-only; JSON keys are not read.

Hard-coded goldens (in-repo fake, 2026-09-04, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-04-sha-activate.md``, Testing
strategy -> Regression):

    SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    stage(SHA_A)+promote -> {current: SHA_A, staged: None, previous: None}
    stage(SHA_B)+promote -> {current: SHA_B, staged: None, previous: SHA_A}
    rollback             -> {current: SHA_A, staged: None, previous: None}

Imports are this project plus stdlib (``io``, ``tarfile``, ``tempfile``).
No urllib, no network, no env vars, no DiscoveryEngine, no live
third-party oracle.

Run directly::

    uv run python regressions/autonomous-harness-evolution-04-sha-activate.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via ``test_autonomous_harness_evolution_04_sha_activate``.
"""

from __future__ import annotations

import io
import sys
import tarfile
import tempfile
from pathlib import Path

from molmcp.components import Activation, ImmutableGitStore

# In-repo goldens, 2026-09-04, no third-party oracle.
SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_OWNER = "owner"
_REPO = "repo"
_CAPABILITIES = frozenset({"provider-sdk", "harness-catalog"})
_AFTER_A = {"current": SHA_A, "staged": None, "previous": None}
_AFTER_B = {"current": SHA_B, "staged": None, "previous": SHA_A}
_AFTER_ROLLBACK = {"current": SHA_A, "staged": None, "previous": None}

# Canonical TOML from tests/test_components/test_catalog.py (daily+dev
# bundles, provider-sdk + harness-catalog). Must pass load_harness_catalog.
_CANONICAL_TOML = """\
requires = ["provider-sdk", "harness-catalog"]

[[component]]
kind = "skill"
name = "daily"
path = "skills/daily/SKILL.md"

[[component]]
kind = "rule"
name = "safety"
path = "rules/safety.md"

[[component]]
kind = "provider"
name = "molvis"
path = "providers/molvis/provider.py"
entrypoint = "molmcp.providers.molvis:MolvisProvider"

[[component]]
kind = "overlay"
name = "molpy"
path = "overlays/molpy/overlay.py"
entrypoint = "molpy.overlay:MolpyOverlay"

[[component]]
kind = "agent"
name = "reviewer"
path = "agents/reviewer/AGENT.md"

[[component]]
kind = "bundle"
name = "daily"
members = ["skill.daily", "rule.safety", "provider.molvis", "overlay.molpy"]

[[component]]
kind = "bundle"
name = "dev"
members = ["skill.daily", "agent.reviewer", "rule.safety", "provider.molvis"]
"""


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
    """GitTransport stand-in: fetch_archive only, no sockets."""

    def __init__(self) -> None:
        self._archives = {
            SHA_A: _make_tarball(f"{_REPO}-{SHA_A}", {"harness.toml": _CANONICAL_TOML}),
            SHA_B: _make_tarball(f"{_REPO}-{SHA_B}", {"harness.toml": _CANONICAL_TOML}),
        }

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        try:
            return self._archives[sha]
        except KeyError:
            raise AssertionError(f"unexpected fetch_archive sha {sha!r}") from None


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


def _state(activation: Activation) -> dict[str, str | None]:
    return {
        "current": activation.current,
        "staged": activation.staged,
        "previous": activation.previous,
    }


def main() -> int:
    fake = _FakeTransport()
    with tempfile.TemporaryDirectory(prefix="molmcp-sha-activate-regression-") as tmp:
        root = Path(tmp)
        store = ImmutableGitStore(root / "store", fake)
        store.publish(SHA_A, owner=_OWNER, repo=_REPO)
        store.publish(SHA_B, owner=_OWNER, repo=_REPO)

        activation = Activation.bind(
            root / "pointer.json",
            store=store,
            supported_capabilities=_CAPABILITIES,
        )

        activation.stage(SHA_A)
        activation.promote()
        after_a = _state(activation)
        _require(
            after_a == _AFTER_A,
            f"after SHA_A stage+promote: {after_a} != {_AFTER_A}",
        )

        activation.stage(SHA_B)
        activation.promote()
        after_b = _state(activation)
        _require(
            after_b == _AFTER_B,
            f"after SHA_B stage+promote: {after_b} != {_AFTER_B}",
        )

        activation.rollback()
        after_rollback = _state(activation)
        _require(
            after_rollback == _AFTER_ROLLBACK,
            f"after rollback: {after_rollback} != {_AFTER_ROLLBACK}",
        )

        print(f"after SHA_A promote={after_a}")
        print(f"after SHA_B promote={after_b}")
        print(f"after rollback={after_rollback}")

    print("\nOK: public SHA activation goldens match.")
    return 0


def test_autonomous_harness_evolution_04_sha_activate() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
