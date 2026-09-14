"""Public-API lock for harness locator identity and operator-field JSON.

GitHub shorthand ``MolCrafts/harness`` and the clone URL
``https://github.com/MolCrafts/harness.git`` share one origin key. A
relative path is not a locator. The first
:func:`~molmcp.settings.set_harness_source` without an alias writes the
name ``origin``. Persisted JSON carries only ``name``, ``locator``, and
``enable``; ``enable`` is omitted when it is ``None``.

Hard-coded golden provenance:
    spec: harness-locator-host-adapters-01-locator
    date: 2026-09-11
    command: uv run python regressions/harness-locator-host-adapters-01-locator.py

No network, no :mod:`molmcp.discovery` import, no third-party subprocess.
Imports are this project plus the stdlib needed to write a settings file.

This script is standalone-runnable::

    uv run python regressions/harness-locator-host-adapters-01-locator.py
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
from pathlib import Path

from molmcp.components.locator import parse_harness_locator
from molmcp.settings import HarnessSource, load_settings, set_harness_source

# Independent of the locator texts passed into the API: do not derive one
# from the other (a mixed-case input must not become its own expected key).
GOLDEN_ORIGIN_KEY = "molcrafts/harness"
GOLDEN_DEFAULT_ALIAS = "origin"
GOLDEN_OPERATOR_KEYS = frozenset({"enable", "locator", "name"})
GOLDEN_RETIRED_KEYS = frozenset({"origin_key", "owner", "path", "ref", "repo"})


def main() -> int:
    """Run the locator identity scenario; return 0 on pass.

    Returns:
        ``0`` when every golden holds.

    Raises:
        AssertionError: A golden did not match.
        ValueError: Unexpected; a relative locator must raise, others must not.
    """
    shorthand = parse_harness_locator("MolCrafts/harness")
    clone_url = parse_harness_locator("https://github.com/MolCrafts/harness.git")
    assert shorthand.origin_key == GOLDEN_ORIGIN_KEY, shorthand.origin_key
    assert clone_url.origin_key == GOLDEN_ORIGIN_KEY, clone_url.origin_key
    assert (
        HarnessSource(name="official", locator="MolCrafts/harness").origin_key
        == GOLDEN_ORIGIN_KEY
    )
    assert (
        HarnessSource(
            name="official",
            locator="https://github.com/MolCrafts/harness.git",
        ).origin_key
        == GOLDEN_ORIGIN_KEY
    )

    try:
        parse_harness_locator("./checkout")
    except ValueError:
        pass
    else:
        raise AssertionError("./checkout must raise")

    assert {field.name for field in dataclasses.fields(HarnessSource)} == (
        GOLDEN_OPERATOR_KEYS
    )

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        home = root / "home"
        project = root / "project"
        home.mkdir()
        project.mkdir()
        # load_settings always reads ~/.molmcp; isolate so a developer file
        # cannot fail the scenario or inject extra harness entries.
        previous_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            settings_path = project / ".molmcp" / "settings.json"
            written = set_harness_source(settings_path, "MolCrafts/harness")
            _assert_operator_entry(written["harness"][0])
            on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
            _assert_operator_entry(on_disk["harness"][0])
            loaded = load_settings(project)
            assert len(loaded.harness) == 1
            source = loaded.harness[0]
            assert source.name == GOLDEN_DEFAULT_ALIAS, source.name
            assert source.origin_key == GOLDEN_ORIGIN_KEY, source.origin_key
            assert source.enable is None, source.enable
            assert source.locator == "MolCrafts/harness", source.locator
        finally:
            if previous_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous_home

    discovery = [
        name
        for name in sys.modules
        if name == "molmcp.discovery" or name.startswith("molmcp.discovery.")
    ]
    assert discovery == [], discovery

    print("harness-locator-host-adapters-01-locator: ok")
    return 0


def _assert_operator_entry(entry: dict[str, object]) -> None:
    """Check one persisted harness object against the operator-field golden.

    Args:
        entry: One object from the ``harness`` list, as written.

    Raises:
        AssertionError: Name, keys, or omitted ``enable`` did not match.
    """
    assert entry["name"] == GOLDEN_DEFAULT_ALIAS, entry
    assert "enable" not in entry, entry
    assert set(entry) <= GOLDEN_OPERATOR_KEYS, set(entry)
    assert GOLDEN_RETIRED_KEYS.isdisjoint(entry), entry


if __name__ == "__main__":
    raise SystemExit(main())
