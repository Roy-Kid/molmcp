#!/usr/bin/env python3
"""Regression example: one `molmcp init` wires MCP, daily, adapter, and dev.

Standalone (no pytest dependency). Builds a throwaway checkout holding one
daily skill and one dev command, points ``Path.home()`` at a second throwaway
directory, and runs the public entry point
``molmcp.cli.main(["init", "grok", "--source", str(checkout)])``. What the host
is left holding afterwards is the whole subject: this script never calls a
``molmcp.host`` write primitive itself.

Hard-coded goldens (in-repo, 2026-09-07, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-07-host-adapter.md``, Testing
strategy -> Regression example, and acceptance AC-011):

    tuple(json.loads((home/".mcp.json").read_text())["mcpServers"])
        == ("molcrafts",)
    "DAILY-SKILL-BODY" in (home/".grok/skills/daily-demo/SKILL.md")
    (home/".grok/skills/spec").exists() is False
    "/mol:spec" in (home/".grok/commands/spec.md"), which does not
        contain "DEV-SKILL-BODY"
    "pointer, not a constitution" in (home/".grok/molmcp-adapter.md"),
        which does not contain "DEV-SKILL-BODY"
    some file under home/".grok/molmcp-dev/" does contain "DEV-SKILL-BODY"
    (home/".grok/skills/molcrafts/SKILL.md").exists() is True

Four of those exist for one reason: the dev body reaches ``molmcp-dev/`` and
nowhere else. The checkout puts a ``spec`` skill under ``dev/``, so the host's
daily ``skills/`` must not gain it; the ``commands/`` entry must stay a stub
naming ``/mol:spec``; the adapter must stay a pointer rather than become a
second constitution.

Public surface only: ``molmcp.cli.main`` plus stdlib ``json`` / ``tempfile`` /
``pathlib``. Deliberately absent: ``molmcp.host`` (``layout_for``,
``install_skill``, ``materialize_daily``, ``write_adapter``,
``materialize_dev_index``, ``activate_dev``) and ``molmcp.client_config`` —
asserting against the primitives would prove the primitives, not the wiring —
plus pytest, network, environment variables, and any third-party import or
subprocess at runtime.

``Path.home`` is patched by hand, because a standalone script has no pytest
monkeypatch, and is restored in a ``finally`` beside both temporary
directories. The real ``~/.mcp.json`` and ``~/.grok`` are therefore never read
or written, and no absolute machine path is printed: paths are reported
relative to the throwaway home.

Run directly::

    uv run python regressions/autonomous-harness-evolution-07-host-adapter.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via
``test_autonomous_harness_evolution_07_host_adapter``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from molmcp.cli import main as molmcp_init

# In-repo goldens, 2026-09-07, no third-party oracle.
_HOST = "grok"
_EXPECTED_SERVERS = ("molcrafts",)
_DAILY_BODY = "DAILY-SKILL-BODY"
_DEV_BODY = "DEV-SKILL-BODY"
_DEV_STUB_MARKER = "/mol:spec"
_ADAPTER_MARKER = "pointer, not a constitution"

_HOST_ROOT = ".grok"
_MCP_JSON = ".mcp.json"
_DAILY_SKILL = "daily-demo"
_DEV_STEM = "spec"
_USAGE_SKILL = "molcrafts"

# The fake checkout, exactly the shape `--source` promises: one daily skill,
# one dev command, and one dev skill that must never reach daily `skills/`.
_DAILY_SKILL_MD = f"""---
name: {_DAILY_SKILL}
---

{_DAILY_BODY}
"""

_DEV_COMMAND_MD = f"""---
name: {_DEV_STEM}
---

{_DEV_BODY}
"""

_DEV_SKILL_MD = f"""---
name: {_DEV_STEM}
---

{_DEV_BODY}
"""


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


def _patch_home(home: Path) -> Callable[[], None]:
    """Point every ``Path.home()`` at *home* until the returned undo runs.

    Args:
        home: Throwaway directory to stand in for the user's home.

    Returns:
        A no-argument callable restoring the original ``Path.home``.
    """
    original = vars(Path).get("home")
    Path.home = classmethod(lambda cls: home)

    def restore() -> None:
        if original is None:  # pragma: no cover - stdlib always defines it
            delattr(Path, "home")
        else:
            Path.home = original

    return restore


def _write(dest: Path, text: str) -> Path:
    """Create *dest*'s parent, write *text* as UTF-8, and return *dest*."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


def _build_checkout(root: Path) -> Path:
    """Fill *root* with the daily/dev bundle shape ``--source`` expects.

    Args:
        root: Empty throwaway directory to populate.

    Returns:
        *root*, ready to pass to ``molmcp init --source``.
    """
    _write(root / "daily" / "skills" / _DAILY_SKILL / "SKILL.md", _DAILY_SKILL_MD)
    _write(root / "dev" / "commands" / f"{_DEV_STEM}.md", _DEV_COMMAND_MD)
    _write(root / "dev" / "skills" / _DEV_STEM / "SKILL.md", _DEV_SKILL_MD)
    return root


def _check_mcp_json(home: Path) -> None:
    """Golden 1: one host config, one server in it, named ``molcrafts``.

    Args:
        home: The throwaway home ``molmcp init`` just wrote into.
    """
    config = home / _MCP_JSON
    _require(config.is_file(), f"{_MCP_JSON} was not written")

    document = json.loads(config.read_text(encoding="utf-8"))
    _require(
        isinstance(document, dict),
        f"{_MCP_JSON} holds a {type(document).__name__}, not an object",
    )
    servers = document.get("mcpServers")
    _require(
        isinstance(servers, dict),
        f"mcpServers is a {type(servers).__name__}, not an object",
    )
    names = tuple(servers)
    _require(
        names == _EXPECTED_SERVERS,
        f"mcpServers keys {names} != {_EXPECTED_SERVERS}",
    )

    print(f"{_MCP_JSON} mcpServers={list(names)}")


def _check_skills(home: Path) -> None:
    """Goldens 2, 3, and 7: daily lands, dev does not, constitution exists.

    Args:
        home: The throwaway home ``molmcp init`` just wrote into.
    """
    skills = home / _HOST_ROOT / "skills"

    daily = skills / _DAILY_SKILL / "SKILL.md"
    _require(daily.is_file(), f"daily skill {_DAILY_SKILL}/SKILL.md was not written")
    _require(
        _DAILY_BODY in daily.read_text(encoding="utf-8"),
        f"daily skill body lacks {_DAILY_BODY!r}",
    )

    leaked = skills / _DEV_STEM
    _require(
        not leaked.exists(),
        f"dev skill {_DEV_STEM!r} leaked into the host's daily skills/",
    )

    usage = skills / _USAGE_SKILL / "SKILL.md"
    _require(usage.is_file(), f"usage constitution {_USAGE_SKILL}/SKILL.md is missing")

    print(f"skills/ -> {sorted(path.name for path in skills.iterdir())}")


def _check_dev_bundle(home: Path) -> None:
    """Goldens 4 and 6: ``commands/`` holds a stub, ``molmcp-dev/`` the body.

    Args:
        home: The throwaway home ``molmcp init`` just wrote into.
    """
    stub = home / _HOST_ROOT / "commands" / f"{_DEV_STEM}.md"
    _require(stub.is_file(), f"dev command stub {_DEV_STEM}.md was not written")
    stub_text = stub.read_text(encoding="utf-8")
    _require(
        _DEV_STUB_MARKER in stub_text,
        f"command stub {stub_text!r} lacks {_DEV_STUB_MARKER!r}",
    )
    _require(
        _DEV_BODY not in stub_text,
        f"command stub carries the dev body {_DEV_BODY!r}",
    )

    dev_root = home / _HOST_ROOT / "molmcp-dev"
    _require(dev_root.is_dir(), "molmcp-dev/ was not activated")
    carriers = tuple(
        path
        for path in sorted(dev_root.rglob("*"))
        if path.is_file() and _DEV_BODY in path.read_text(encoding="utf-8")
    )
    _require(
        bool(carriers),
        f"no file under molmcp-dev/ carries {_DEV_BODY!r}",
    )

    print(f"commands/{_DEV_STEM}.md -> stub naming {_DEV_STUB_MARKER}")
    print(
        "molmcp-dev/ bodies -> "
        f"{[str(path.relative_to(dev_root)) for path in carriers]}"
    )


def _check_adapter(home: Path) -> None:
    """Golden 5: the adapter is a pointer, not a second constitution.

    Args:
        home: The throwaway home ``molmcp init`` just wrote into.
    """
    adapter = home / _HOST_ROOT / "molmcp-adapter.md"
    _require(adapter.is_file(), "molmcp-adapter.md was not written")
    text = adapter.read_text(encoding="utf-8")
    _require(
        _ADAPTER_MARKER in text,
        f"adapter {text!r} lacks the pointer sentence {_ADAPTER_MARKER!r}",
    )
    _require(
        _DEV_BODY not in text,
        f"adapter carries the dev body {_DEV_BODY!r}",
    )

    print(f"molmcp-adapter.md -> {_ADAPTER_MARKER!r}, no {_DEV_BODY}")


def main() -> int:
    home_dir = tempfile.TemporaryDirectory(prefix="molmcp-host-regression-home-")
    source_dir = tempfile.TemporaryDirectory(prefix="molmcp-host-regression-src-")
    home = Path(home_dir.name)
    checkout = _build_checkout(Path(source_dir.name))

    restore_home = _patch_home(home)
    try:
        code = molmcp_init(["init", _HOST, "--source", str(checkout)])
        _require(code == 0, f"molmcp init {_HOST} exited {code}, not 0")

        _check_mcp_json(home)
        _check_skills(home)
        _check_dev_bundle(home)
        _check_adapter(home)
    finally:
        restore_home()
        source_dir.cleanup()
        home_dir.cleanup()

    print("\nOK: one init wired MCP, daily, adapter, and dev; goldens match.")
    return 0


def test_autonomous_harness_evolution_07_host_adapter() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
