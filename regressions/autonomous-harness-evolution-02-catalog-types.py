#!/usr/bin/env python3
"""Regression example: public harness catalog through ``load_harness_catalog``.

Standalone (no pytest dependency). Writes the spec's canonical ``harness.toml``
(no ``sha`` key) into a ``tempfile.TemporaryDirectory``, loads it through the
public ``molmcp.components`` surface with three positionals, and asserts the
hard-coded goldens below.

Hard-coded goldens (in-repo, 2026-09-04, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-02-catalog-types.md``, Testing
strategy -> Regression):

    catalog.sha == "0123456789abcdef0123456789abcdef01234567"
    resolve_bundle("daily").members ids ==
        ("skill.daily", "rule.safety", "provider.molvis", "overlay.molpy")
    resolve_bundle("dev").members ids ==
        ("skill.daily", "agent.reviewer", "rule.safety", "provider.molvis")
    get("daily") raises CatalogError; message contains "unknown-id"

Imports are this project only (``load_harness_catalog``, ``CatalogError``).
No live third-party oracle.

Run directly::

    uv run python regressions/autonomous-harness-evolution-02-catalog-types.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via ``test_autonomous_harness_evolution_02_catalog_types``.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from molmcp.components import CatalogError, load_harness_catalog

# In-repo goldens, 2026-09-04, no third-party oracle.
_EXPECTED_SHA = "0123456789abcdef0123456789abcdef01234567"
_EXPECTED_DAILY_IDS = (
    "skill.daily",
    "rule.safety",
    "provider.molvis",
    "overlay.molpy",
)
_EXPECTED_DEV_IDS = (
    "skill.daily",
    "agent.reviewer",
    "rule.safety",
    "provider.molvis",
)

# Canonical wire TOML from the spec Design/Wire section (no sha key).
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


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


def _member_ids(bundle: object) -> tuple[str, ...]:
    members = getattr(bundle, "members")
    return tuple(spec.id for spec in members)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="molmcp-catalog-regression-") as tmp:
        root = Path(tmp)
        (root / "harness.toml").write_text(_CANONICAL_TOML, encoding="utf-8")

        catalog = load_harness_catalog(
            root,
            "0123456789abcdef0123456789abcdef01234567",
            frozenset({"provider-sdk", "harness-catalog"}),
        )

        _require(
            catalog.sha == _EXPECTED_SHA,
            f"catalog.sha {catalog.sha!r} != {_EXPECTED_SHA!r}",
        )

        daily_ids = _member_ids(catalog.resolve_bundle("daily"))
        _require(
            daily_ids == _EXPECTED_DAILY_IDS,
            f"daily member ids {daily_ids} != {_EXPECTED_DAILY_IDS}",
        )

        dev_ids = _member_ids(catalog.resolve_bundle("dev"))
        _require(
            dev_ids == _EXPECTED_DEV_IDS,
            f"dev member ids {dev_ids} != {_EXPECTED_DEV_IDS}",
        )

        try:
            catalog.get("daily")
        except CatalogError as exc:
            message = str(exc)
            _require(
                "unknown-id" in message,
                f"get('daily') message {message!r} does not contain 'unknown-id'",
            )
        else:
            raise AssertionError("get('daily') did not raise CatalogError")

        print(f"sha={catalog.sha}")
        print(f"daily.members={daily_ids}")
        print(f"dev.members={dev_ids}")
        print("get('daily') -> CatalogError containing 'unknown-id'")

    print("\nOK: public harness catalog goldens match.")
    return 0


def test_autonomous_harness_evolution_02_catalog_types() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
