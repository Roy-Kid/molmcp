"""The harness case set is data, and the actor is never told the criteria.

``scripts/harness_cases.py`` is the only place this repo says what "a better
harness" means, so it is held to two rules that a reviewer cannot enforce by
reading.

The first is that it stays plain Python. A case set in YAML or JSON needs a
parser, a schema and a second place to look before anyone can tell what the
evaluator actually asserts; the same list written as a literal needs none of
them, which is why ``tests/discovery/golden_queries.py`` is shaped this way
too.

The second is the one the whole design rests on: an actor that can read the
criteria optimises for the criteria, and the report then measures exam
technique rather than whether the harness leads a real user to the right
move. "The actor never sees them" is a wish until something fails when a
criterion string turns up inside the task text that is handed over verbatim.

Structural guards read the module as text (the habit of
``tests/test_no_env_switches.py``); the behavioural ones import it flat --
``scripts`` is on pytest's ``pythonpath``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from _ast_checks import reads_environment
from harness_cases import CASES, case_by_id, graduated_ids, held_out_ids

#: The module under test, read as source for the structural guards.
_SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "harness_cases.py"

#: Every key a case carries, and nothing besides.
_KEYS = frozenset({"id", "graduated", "task", "expect", "forbid"})

#: Importing any of these would mean the case set had become a file format.
_SERIALISATION_MODULES = frozenset(
    {
        "configparser",
        "csv",
        "json",
        "pickle",
        "plistlib",
        "ruamel",
        "toml",
        "tomli",
        "tomllib",
        "xml",
        "yaml",
    }
)


def _imported_roots() -> frozenset[str]:
    """Top-level module names imported by ``scripts/harness_cases.py``.

    Returns:
        The first dotted segment of every ``import`` and ``from`` target. A
        relative import contributes its leading dots instead, which no
        standard-library check can accept -- ``scripts/`` is not a package.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                roots.add("." * node.level)
            elif node.module:
                roots.add(node.module.split(".")[0])
    return frozenset(roots)


class TestHarnessCases:
    def test_every_case_carries_exactly_the_five_keys(self):
        wrong = [
            (index, sorted(set(case) ^ _KEYS))
            for index, case in enumerate(CASES)
            if set(case) != _KEYS
        ]

        assert wrong == [], (
            f"each case is exactly {sorted(_KEYS)}; an extra key is a field "
            f"nothing reads and a missing one is a case the evaluator cannot "
            f"place, offenders (index, symmetric difference): {wrong}"
        )

    def test_every_field_has_the_declared_type(self):
        wrong: list[tuple[object, str]] = []
        for case in CASES:
            case_id = case.get("id")
            if not isinstance(case_id, str):
                wrong.append((case_id, "id must be a str"))
            if not isinstance(case.get("graduated"), bool):
                wrong.append((case_id, "graduated must be a bool"))
            if not isinstance(case.get("task"), str):
                wrong.append((case_id, "task must be a str"))
            for key in ("expect", "forbid"):
                value = case.get(key)
                if not isinstance(value, list) or not all(
                    isinstance(item, str) for item in value
                ):
                    wrong.append((case_id, f"{key} must be a list[str]"))

        assert wrong == [], f"case fields have declared types: {wrong}"

    def test_ids_are_unique(self):
        ids = [case["id"] for case in CASES]

        assert len(set(ids)) == len(ids), (
            f"case_by_id can only return one of two cases sharing an id, and "
            f"the loser is silently never run: {ids}"
        )

    def test_expect_and_forbid_are_both_non_empty(self):
        empty = [
            (case["id"], key)
            for case in CASES
            for key in ("expect", "forbid")
            if not case[key]
        ]

        assert empty == [], (
            f"a case with nothing to expect proves nothing and a case with no "
            f"negative control can never fail, so it is not a case: {empty}"
        )

    def test_both_a_graduated_and_a_held_out_case_exist(self):
        flags = {case["graduated"] for case in CASES}

        assert flags == {True, False}, (
            f"graduated cases feed evaluate's regression_cases and held-out "
            f"cases its held_out_cases, and evaluate raises on an empty "
            f"held_out_cases, so both kinds must exist: {sorted(flags)}"
        )

    def test_held_out_and_graduated_ids_are_disjoint(self):
        both = sorted(set(held_out_ids()) & set(graduated_ids()))

        assert both == [], (
            f"a case is a correctness contract or a reading, never both -- "
            f"counting it twice moves the mean it also gates: {both}"
        )

    def test_held_out_and_graduated_ids_cover_every_id(self):
        covered = set(held_out_ids()) | set(graduated_ids())

        assert covered == {case["id"] for case in CASES}, (
            f"a case in neither list is a case nothing runs: "
            f"{sorted({case['id'] for case in CASES} ^ covered)}"
        )

    def test_the_partition_follows_the_graduated_flag(self):
        assert set(graduated_ids()) == {
            case["id"] for case in CASES if case["graduated"] is True
        }
        assert set(held_out_ids()) == {
            case["id"] for case in CASES if case["graduated"] is False
        }

    def test_the_accessors_return_tuples_of_ids(self):
        assert isinstance(held_out_ids(), tuple)
        assert isinstance(graduated_ids(), tuple)
        assert all(isinstance(case_id, str) for case_id in held_out_ids())
        assert all(isinstance(case_id, str) for case_id in graduated_ids())

    def test_case_by_id_returns_the_entry_for_every_id(self):
        assert [case_by_id(case["id"]) for case in CASES] == list(CASES)

    def test_case_by_id_raises_key_error_for_an_unknown_id(self):
        with pytest.raises(KeyError):
            case_by_id("nope")

    def test_no_criterion_leaks_into_the_task_the_actor_is_handed(self):
        leaked = [
            (case["id"], criterion)
            for case in CASES
            for criterion in (*case["expect"], *case["forbid"])
            if criterion in case["task"]
        ]

        assert leaked == [], (
            f"task is handed to the actor verbatim: a criterion quoted in it "
            f"turns the run into an exam the actor can study for, and the "
            f"report then measures exam technique: {leaked}"
        )

    def test_the_module_imports_nothing_outside_the_standard_library(self):
        outside = sorted(_imported_roots() - sys.stdlib_module_names)

        assert outside == [], (
            f"the case set is the evaluator's only source of truth and must "
            f"import on a bare interpreter; scripts/ ships in no wheel and "
            f"has no dependencies to declare: {outside}"
        )

    def test_the_module_parses_no_serialisation_format(self):
        parsers = sorted(_imported_roots() & _SERIALISATION_MODULES)

        assert parsers == [], (
            f"cases are Python literals: a file format adds a parser, a "
            f"schema and a second place to read before anyone can tell what "
            f"is asserted: {parsers}"
        )

    def test_the_module_does_not_read_the_environment(self):
        tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))

        assert not reads_environment(tree), (
            "an evaluation whose cases depend on the shell is not "
            "reproducible; the case set takes no configuration at all"
        )
