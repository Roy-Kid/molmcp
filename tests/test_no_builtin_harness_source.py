"""No harness source is built in, and ``components/`` never hears of one.

molmcp serves components from the harness repositories its operator named,
and from no others. The guarantee worth testing is not that some list of
literals is absent from the source — it is that an install which names
nothing gets nothing. Hence the leading assertion here: ``load_settings``
over an empty settings tree resolves ``harness`` to the empty tuple.
``tests/test_stack.py`` (the ``harness=()`` arms) is the other half of that
criterion, recording that the empty tuple binds no store, reads no catalog
and contributes ``extras == ()``; it is not duplicated here.

**There is deliberately no AST lint in this module.** A scan for
``HarnessSource(...)`` calls carrying string constants is defeated by
``HarnessSource(**_DEFAULT)``, by a module-level constant, and most
realistically by a module-level list of plain dicts poured through the same
``_harness_sources`` path that file data takes — which never calls
``HarnessSource(...)`` with a literal at all. A gate that cannot catch the
case it exists for is worse than none, so the behavioural assertion leads
and nothing lints behind it. A bare literal blocklist on ``"molcrafts"`` is
refused for a second reason: that string is the core plane id and appears
throughout ``server.py`` for unrelated reasons.

The second guard is a boundary. ``components/`` is a shared stdlib leaf,
admitted only when an inner layer needs it; a harness source is a
``settings.py`` concept and nothing in ``components/`` has any reason to
know one exists. ``ComponentSpec``'s id grammar is deliberately *not*
re-asserted here — ``tests/test_components/test_models.py``
``TestComponentSpec.test_rejects_id_mismatch`` owns "an id that is not
``f'{kind}.{name}'`` raises ``CatalogError``", and cross-source namespacing
is out of scope for the spec that added this module.

Both guards are expected to be green on arrival: their job is to fail
*later*, if someone builds an official coordinate in or teaches
``components/`` about settings. This lives in a module of its own rather
than inside ``tests/test_settings.py`` because it reads other modules'
source as data, which is not ``settings.py`` behaviour;
``tests/test_no_env_switches.py`` is the repo's existing pattern for a
repo-wide structural assertion housed this way.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from molmcp import settings as st

SRC = Path(__file__).resolve().parents[1] / "src" / "molmcp"

#: The components layer, which must not learn that a harness source exists.
_COMPONENT_MODULES = (
    SRC / "components" / "models.py",
    SRC / "components" / "catalog.py",
    SRC / "components" / "locator.py",
)

#: Naming either of these in ``components/`` means the boundary moved.
_HARNESS_NAMES = ("HarnessSource", "harness_source")


def test_an_empty_settings_tree_names_no_harness_source(home: Path, tmp_path: Path):
    """No file, no source: the empty tuple is the un-harnessed install."""
    assert st.load_settings(tmp_path / "repo").harness == ()


@pytest.mark.parametrize("name", _HARNESS_NAMES)
@pytest.mark.parametrize("path", _COMPONENT_MODULES, ids=lambda p: p.name)
def test_the_components_layer_never_names_a_harness_source(path: Path, name: str):
    assert name not in path.read_text(encoding="utf-8"), (
        f"{path.relative_to(SRC)} names {name}. A harness source is a settings "
        f"concept; components/ is a shared leaf that must not depend on it. "
        f"Cross-source namespacing belongs to the resolution layer, keyed by a "
        f"(source_name, component_id) pair, and never enters ComponentSpec.id."
    )


@pytest.mark.parametrize("path", _COMPONENT_MODULES, ids=lambda p: p.name)
def test_a_guarded_module_is_still_a_live_module_under_src(path: Path):
    """A renamed or deleted file would make the text guard pass vacuously."""
    assert path in set(SRC.rglob("*.py"))

    ast.parse(path.read_text(encoding="utf-8"))
