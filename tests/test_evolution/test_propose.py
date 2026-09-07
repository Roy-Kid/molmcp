"""Evidence-triggered atomic Candidate proposal — one pair, one patch, or None.

Mirrors ``src/molmcp/evolution/propose.py``; one class per public behaviour
(``Candidate`` the value object, ``propose`` the pure function). The six view
types are exercised through ``propose`` rather than given classes of their own:
they are literals a caller builds, and a test that only constructed them would
pin no behaviour.

Three disciplines are pinned here that no single assertion makes obvious.

*Receipts trigger, patterns do not.* A pair is eligible only when some receipt
names both the pattern and the component. Every fixture therefore carries its
receipt explicitly, and the no-receipt case is a receipt for a *different*
component rather than an empty tuple — an implementation that fires whenever
``receipts`` is non-empty has to fail somewhere.

*Selection is wiki order, not search.* The outer loop is
``wiki.open_patterns``; the inner loop is ``bundle.components``. The order test
puts the winning pattern's component last in the bundle so that a bundle-first
implementation returns the wrong pair rather than the right one by luck.

*The skill function-def skip is anchored, not a substring search.* An added
line is a definition only when ``^def\\s+[A-Za-z_][A-Za-z0-9_]*\\(`` matches it
after ``lstrip``. ``Always call def name( before coding`` contains ``def name(``
and must still be proposed, so ``"def " in line`` fails this module by
construction.

Nothing here touches disk, a clock, the environment, or MCP. Views are frozen
literals; the only file read is ``propose.py`` itself, and only to prove what it
does not say.
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Sequence
from pathlib import Path

import pytest

from molmcp.evolution.propose import (
    BundleView,
    Candidate,
    Component,
    Pattern,
    Receipt,
    ReceiptsView,
    WikiView,
    propose,
)

_REPO = Path(__file__).resolve().parents[2]
_PROPOSE = _REPO / "src" / "molmcp" / "evolution" / "propose.py"

#: The dotted package the module under test lives in, used to resolve the
#: relative imports its purity check has to see through.
_PACKAGE_PARTS: tuple[str, ...] = ("molmcp", "evolution")

#: The worked example the spec names, field for field. Every other fixture is
#: this one with a single field swapped, so a failure names the swap.
_PATTERN_ID = "skill-missing-warning"
_INSERT = "Always call packages before coding"
_COMPONENT_ID = "daily-pack-skill"
_PATH = "skills/daily/pack.md"
_TEXT = "# daily pack\n"
_RECEIPT_ID = "run-42"

#: The diff header and the added line the happy path must produce. The header
#: is ``component.path`` verbatim: the patch names the component's own path,
#: never a temporary or a resolved absolute one.
_DIFF_FROM = f"--- {_PATH}"
_ADDED_LINE = f"+{_INSERT}"

#: The six kind literals this leaf knows. ``skill``/``rule``/``agent`` ship
#: without a human in the loop; ``overlay``/``provider`` do not; ``controller``
#: is not proposed at all.
_UNGATED_KINDS: tuple[str, ...] = ("skill", "rule", "agent")
_GATED_KINDS: tuple[str, ...] = ("overlay", "provider")

#: Inserts whose added line *is* a Python definition. Leading whitespace and a
#: parameter list are both in scope; the tab case is why the check must
#: ``lstrip`` rather than test for a literal four spaces.
_FUNCTION_DEF_INSERTS: tuple[str, ...] = (
    "def pack(",
    "    def pack(",
    "def pack():",
    "\tdef pack(self):",
)

#: Deliberately out of the skip's scope. These are proposed, not skipped: the
#: spec pins ``def <ident>(`` and nothing wider, so widening the regex to
#: ``async def`` or ``class`` breaks here rather than silently in a year.
_UNSKIPPED_INSERTS: tuple[str, ...] = (
    "async def pack(",
    "class Pack(",
    "def pack (",
)

#: An added line that merely *contains* a definition-shaped substring. Load
#: bearing: a substring search would skip it, an anchored regex would not.
_INSERT_MENTIONING_A_DEF = "Always call def name( before coding"

#: Text the module may not contain at all. ``fastmcp`` is checked in its import
#: spelling, so prose may still say "FastMCP" while ``import fastmcp`` cannot
#: hide. ``os.environ``/``getenv`` would make a pure function configurable;
#: ``write_text`` would make it a writer; ``mcp.tool`` would make it a plane.
_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "write_text",
    "os.environ",
    "getenv",
    "fastmcp",
    "mcp.tool",
)

#: Packages this leaf may not reach for. ``kind`` is data on the view; probing
#: for it by importing the layer that owns it is the failure this forbids.
_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "molmcp.providers",
    "molmcp.discovery",
    "molmcp.skill",
)

#: ``Candidate`` fields, in the order the spec's value-object table lists them.
_CANDIDATE_FIELDS: tuple[str, ...] = (
    "pattern_id",
    "component_id",
    "path",
    "unified_diff",
    "rationale_refs",
    "human_gate",
)


def _pattern(pattern_id: str = _PATTERN_ID, insert: str = _INSERT) -> Pattern:
    return Pattern(pattern_id=pattern_id, insert=insert)


def _component(
    component_id: str = _COMPONENT_ID,
    kind: str = "skill",
    path: str = _PATH,
    text: str = _TEXT,
) -> Component:
    return Component(component_id=component_id, kind=kind, path=path, text=text)


def _receipt(
    receipt_id: str = _RECEIPT_ID,
    pattern_id: str = _PATTERN_ID,
    component_id: str = _COMPONENT_ID,
) -> Receipt:
    return Receipt(
        receipt_id=receipt_id,
        pattern_id=pattern_id,
        component_id=component_id,
    )


def _views(
    kind: str = "skill",
    insert: str = _INSERT,
    text: str = _TEXT,
) -> tuple[WikiView, ReceiptsView, BundleView]:
    """The worked example, with at most one field swapped out."""
    return (
        WikiView(open_patterns=(_pattern(insert=insert),)),
        ReceiptsView(receipts=(_receipt(),)),
        BundleView(components=(_component(kind=kind, text=text),)),
    )


def _candidate() -> Candidate:
    return Candidate(
        pattern_id=_PATTERN_ID,
        component_id=_COMPONENT_ID,
        path=_PATH,
        unified_diff=f"{_DIFF_FROM}\n+++ {_PATH}\n@@ -1 +1,2 @@\n {_ADDED_LINE}\n",
        rationale_refs=(_RECEIPT_ID,),
        human_gate=False,
    )


def _propose_source() -> str:
    assert _PROPOSE.is_file(), f"{_PROPOSE} does not exist yet"
    return _PROPOSE.read_text(encoding="utf-8")


def _resolved_module(node: ast.ImportFrom) -> str:
    """The dotted module *node* names, with a relative import made absolute."""
    if not node.level:
        return node.module or ""
    kept = len(_PACKAGE_PARTS) - node.level + 1
    base = ".".join(_PACKAGE_PARTS[:kept]) if kept > 0 else ""
    if not node.module:
        return base
    return f"{base}.{node.module}" if base else node.module


def _module_level_imports(tree: ast.Module) -> set[str]:
    """Modules imported at module level — not inside a function or a block."""
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_module(node)
            modules.add(module)
            modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


class TestCandidate:
    def test_carries_the_six_fields_it_was_given(self) -> None:
        candidate = _candidate()

        assert candidate.pattern_id == _PATTERN_ID
        assert candidate.component_id == _COMPONENT_ID
        assert candidate.path == _PATH
        assert candidate.unified_diff.startswith(_DIFF_FROM)
        assert candidate.rationale_refs == (_RECEIPT_ID,)
        assert candidate.human_gate is False

    def test_field_names_are_the_value_object_table_in_order(self) -> None:
        names = tuple(field.name for field in dataclasses.fields(Candidate))

        assert names == _CANDIDATE_FIELDS

    @pytest.mark.parametrize("field_name", _CANDIDATE_FIELDS)
    def test_is_frozen(self, field_name: str) -> None:
        candidate = _candidate()

        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(candidate, field_name, "mutated")

    def test_uses_slots(self) -> None:
        assert hasattr(Candidate, "__slots__")
        assert not hasattr(_candidate(), "__dict__")


class TestPropose:
    def test_proposes_the_evidenced_pair(self) -> None:
        candidate = propose(*_views())

        assert candidate is not None
        assert candidate.pattern_id == _PATTERN_ID
        assert candidate.component_id == _COMPONENT_ID
        assert candidate.path == _PATH
        assert candidate.human_gate is False
        assert candidate.rationale_refs == (_RECEIPT_ID,)
        assert _DIFF_FROM in candidate.unified_diff
        assert _ADDED_LINE in candidate.unified_diff.splitlines()

    def test_no_open_patterns_proposes_nothing(self) -> None:
        """Receipts and a bundle are not evidence on their own."""
        _, receipts, bundle = _views()

        assert propose(WikiView(open_patterns=()), receipts, bundle) is None

    def test_a_rejected_pattern_id_is_skipped(self) -> None:
        wiki = WikiView(
            open_patterns=(
                _pattern(pattern_id="rejected-pattern"),
                _pattern(pattern_id="second-pattern"),
            )
        )
        receipts = ReceiptsView(
            receipts=(
                _receipt(receipt_id="run-1", pattern_id="rejected-pattern"),
                _receipt(
                    receipt_id="run-2",
                    pattern_id="second-pattern",
                    component_id="second-skill",
                ),
            )
        )
        bundle = BundleView(
            components=(
                _component(),
                _component(component_id="second-skill", path="skills/second.md"),
            )
        )

        candidate = propose(wiki, receipts, bundle, rejected_ids=("rejected-pattern",))

        assert candidate is not None
        assert candidate.pattern_id == "second-pattern"
        assert candidate.component_id == "second-skill"
        assert candidate.rationale_refs == ("run-2",)

    def test_wiki_order_beats_bundle_order(self) -> None:
        """``open_patterns[0]`` wins even with its component last in the bundle."""
        wiki = WikiView(
            open_patterns=(
                _pattern(pattern_id="first-pattern"),
                _pattern(pattern_id="second-pattern"),
            )
        )
        receipts = ReceiptsView(
            receipts=(
                _receipt(
                    receipt_id="run-1",
                    pattern_id="first-pattern",
                    component_id="late-skill",
                ),
                _receipt(
                    receipt_id="run-2",
                    pattern_id="second-pattern",
                    component_id="early-skill",
                ),
            )
        )
        bundle = BundleView(
            components=(
                _component(component_id="early-skill", path="skills/early.md"),
                _component(component_id="late-skill", path="skills/late.md"),
            )
        )

        candidate = propose(wiki, receipts, bundle)

        assert candidate is not None
        assert candidate.pattern_id == "first-pattern"
        assert candidate.component_id == "late-skill"
        assert candidate.path == "skills/late.md"

    def test_a_controller_is_never_proposed(self) -> None:
        assert propose(*_views(kind="controller")) is None

    def test_a_controller_is_passed_over_for_the_next_component(self) -> None:
        wiki, _, _ = _views()
        receipts = ReceiptsView(
            receipts=(
                _receipt(receipt_id="run-1", component_id="the-controller"),
                _receipt(receipt_id="run-2", component_id="the-skill"),
            )
        )
        bundle = BundleView(
            components=(
                _component(
                    component_id="the-controller",
                    kind="controller",
                    path="controllers/main.py",
                ),
                _component(component_id="the-skill", path="skills/next.md"),
            )
        )

        candidate = propose(wiki, receipts, bundle)

        assert candidate is not None
        assert candidate.component_id == "the-skill"
        assert candidate.rationale_refs == ("run-2",)

    @pytest.mark.parametrize("kind", _GATED_KINDS)
    def test_overlay_and_provider_need_a_human(self, kind: str) -> None:
        candidate = propose(*_views(kind=kind))

        assert candidate is not None
        assert candidate.human_gate is True

    @pytest.mark.parametrize("kind", _UNGATED_KINDS)
    def test_skill_rule_and_agent_do_not(self, kind: str) -> None:
        candidate = propose(*_views(kind=kind))

        assert candidate is not None
        assert candidate.human_gate is False

    def test_an_unknown_kind_is_never_proposed(self) -> None:
        """No silent default ``human_gate`` for a kind this leaf cannot rank."""
        assert propose(*_views(kind="widget")) is None

    def test_a_pattern_without_a_matching_receipt_proposes_nothing(self) -> None:
        wiki, _, bundle = _views()
        receipts = ReceiptsView(
            receipts=(_receipt(component_id="some-other-component"),)
        )

        assert propose(wiki, receipts, bundle) is None

    def test_an_insert_already_on_its_own_line_proposes_nothing(self) -> None:
        assert propose(*_views(text=f"# daily pack\n{_INSERT}\n")) is None

    def test_an_insert_inside_a_longer_line_is_still_proposed(self) -> None:
        """Containment is not presence: the check compares whole lines."""
        candidate = propose(*_views(text=f"# daily pack\nSee: {_INSERT} first.\n"))

        assert candidate is not None
        assert _ADDED_LINE in candidate.unified_diff.splitlines()

    def test_an_empty_insert_proposes_nothing(self) -> None:
        assert propose(*_views(insert="")) is None

    @pytest.mark.parametrize("insert", _FUNCTION_DEF_INSERTS)
    def test_a_skill_patch_adding_a_function_def_is_skipped(self, insert: str) -> None:
        assert propose(*_views(insert=insert)) is None

    def test_a_line_merely_mentioning_a_def_is_still_proposed(self) -> None:
        """Anchored after ``lstrip``; a substring search would skip this."""
        candidate = propose(*_views(insert=_INSERT_MENTIONING_A_DEF))

        assert candidate is not None
        assert f"+{_INSERT_MENTIONING_A_DEF}" in candidate.unified_diff.splitlines()

    @pytest.mark.parametrize("insert", _UNSKIPPED_INSERTS)
    def test_definitions_outside_the_pinned_shape_are_proposed(
        self, insert: str
    ) -> None:
        candidate = propose(*_views(insert=insert))

        assert candidate is not None
        assert f"+{insert}" in candidate.unified_diff.splitlines()

    def test_returns_one_candidate_rather_than_a_sequence(self) -> None:
        wiki = WikiView(
            open_patterns=(
                _pattern(pattern_id="first-pattern"),
                _pattern(pattern_id="second-pattern"),
            )
        )
        receipts = ReceiptsView(
            receipts=(
                _receipt(receipt_id="run-1", pattern_id="first-pattern"),
                _receipt(
                    receipt_id="run-2",
                    pattern_id="second-pattern",
                    component_id="second-skill",
                ),
            )
        )
        bundle = BundleView(
            components=(
                _component(),
                _component(component_id="second-skill", path="skills/second.md"),
            )
        )

        candidate = propose(wiki, receipts, bundle)

        assert isinstance(candidate, Candidate)
        assert not isinstance(candidate, Sequence)
        assert not isinstance(candidate, list | tuple)

    @pytest.mark.parametrize("token", _FORBIDDEN_TOKENS)
    def test_the_source_never_writes_configures_or_registers(self, token: str) -> None:
        assert token not in _propose_source()

    def test_the_source_imports_no_provider_discovery_or_skill(self) -> None:
        modules = _module_level_imports(ast.parse(_propose_source()))

        offenders = sorted(
            name
            for name in modules
            if any(
                name == prefix or name.startswith(f"{prefix}.")
                for prefix in _FORBIDDEN_IMPORT_PREFIXES
            )
        )

        assert offenders == []
