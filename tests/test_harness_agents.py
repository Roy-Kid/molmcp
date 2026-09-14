"""The two harness agents are the evaluator's fixed instruments.

An evaluation compares two harnesses, which only means something if
everything else holds still. Two things can move without anyone noticing.
A ``model:`` that resolves from the environment makes two runs a week apart
incomparable — the judge would have changed along with the defendant. And an
actor that can read the criteria optimises for them: the reading then measures
test-taking, not whether the harness leads a person to the right move on its
own.

Neither file is code, so nothing else in this repo would ever complain about
them. These are structural assertions on the text, in the manner of
``test_no_env_switches.py``: read the file by path, parse the frontmatter with
a few lines of string handling rather than a YAML dependency the repo does not
have, and say plainly what is wrong.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
AGENTS = REPO / ".claude" / "agents"

ACTOR = AGENTS / "harness-actor.md"
OBSERVER = AGENTS / "harness-observer.md"

#: The four frontmatter keys a subagent definition pins (ac-012).
_REQUIRED_KEYS = ("name", "description", "tools", "model")

#: A templated model id would let the subject drift with the environment.
_PLACEHOLDER = "{{"

#: Tools that would let one evaluation round change the repository it runs in.
_ACTOR_MUST_NOT_HOLD = ("Write", "Edit")

#: The vocabulary of the criteria. The actor is told the task, nothing else.
_CRITERIA_WORDS = ("expect", "forbid", "harness_cases")

#: Every key of the observation schema the observer is the sole source of.
_OBSERVATION_KEYS = (
    "case_id",
    "seed",
    "side",
    "contract_met",
    "tool_errors",
    "call_count",
)

#: Naming a side is proof the blind was broken before the observer wrote.
_SIDE_NAMES = ("champion", "challenger")

#: Readings that cannot be taken off a transcript, and so must not be invited.
_UNOBSERVABLE = ("tokens", "latency_s")


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _read(path: Path) -> str:
    """The file's text, or a readable failure instead of an OSError traceback."""
    assert path.is_file(), (
        f"{_rel(path)} does not exist. The harness evaluator needs both agent "
        f"definitions in the repository — an observer that lives in the tree "
        f"under test would change along with it."
    )
    return path.read_text(encoding="utf-8")


def _frontmatter(path: Path) -> dict[str, str]:
    """The ``key: value`` lines between the opening and closing ``---`` fences.

    Enough YAML for four scalar keys plus a tool list written inline
    (``Read, Grep``), bracketed (``[Read, Grep]``) or as a ``- Read`` block.
    The repo carries no YAML parser and this spec adds no dependency.
    """
    lines = _read(path).splitlines()

    assert [line.strip() for line in lines[:1]] == ["---"], (
        f"{_rel(path)} must open with a --- YAML frontmatter fence; its first "
        f"line is {lines[:1]!r}."
    )

    closing = next(
        (i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None
    )
    assert closing is not None, (
        f"{_rel(path)} opens a --- frontmatter fence that is never closed by a "
        f"second --- line."
    )

    fields: dict[str, str] = {}
    key: str | None = None
    for line in lines[1:closing]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and key is not None:
            item = stripped[2:].strip()
            fields[key] = ", ".join(part for part in (fields[key], item) if part)
            continue
        name, sep, value = stripped.partition(":")
        if not sep:
            continue
        key = name.strip()
        fields[key] = value.strip()
    return fields


def _tool_names(value: str) -> tuple[str, ...]:
    """Tool names out of whichever of the three list spellings was used."""
    parts = (part.strip().strip("\"'") for part in value.strip("[]").split(","))
    return tuple(part for part in parts if part)


def _cases() -> list[dict[str, object]]:
    """The case set, which is the only source of the strings the actor may not see."""
    try:
        module = importlib.import_module("harness_cases")
    except ImportError as exc:
        pytest.fail(
            f"cannot import `harness_cases` ({exc}). The criteria live only "
            f"there, so without it this guard would have nothing to look for "
            f"and would pass by vacuity. Expected scripts/harness_cases.py "
            f'with "scripts" on [tool.pytest.ini_options] pythonpath.'
        )

    raw = getattr(module, "CASES", None)
    assert isinstance(raw, list) and raw, (
        f"harness_cases.CASES must be a non-empty list of cases; got {raw!r}."
    )

    cases: list[dict[str, object]] = []
    for entry in raw:
        assert isinstance(entry, dict), f"CASES entry is not a dict: {entry!r}"
        cases.append(entry)
    return cases


def _criteria_strings() -> tuple[str, ...]:
    """Every ``expect`` and ``forbid`` string across the case set."""
    strings: list[str] = []
    for case in _cases():
        for key in ("expect", "forbid"):
            values = case.get(key)
            assert isinstance(values, list) and values, (
                f"case {case.get('id')!r} needs a non-empty {key!r} list "
                f"(tests/test_harness_cases.py owns that rule; this guard only "
                f"reads the strings)."
            )
            for value in values:
                assert isinstance(value, str), (
                    f"case {case.get('id')!r} has a non-string {key!r} entry: {value!r}"
                )
                strings.append(value)
    return tuple(strings)


class TestHarnessAgents:
    @pytest.mark.parametrize("path", (ACTOR, OBSERVER), ids=lambda p: p.stem)
    def test_frontmatter_carries_the_four_keys(self, path: Path):
        fields = _frontmatter(path)
        missing = [key for key in _REQUIRED_KEYS if key not in fields]

        assert missing == [], (
            f"{_rel(path)} frontmatter is missing {missing}. A subagent "
            f"definition pins {list(_REQUIRED_KEYS)}; anything left out is "
            f"resolved by the host, which is exactly the drift this evaluator "
            f"is measuring against."
        )

    @pytest.mark.parametrize(
        ("path", "expected"),
        ((ACTOR, "harness-actor"), (OBSERVER, "harness-observer")),
        ids=("actor", "observer"),
    )
    def test_each_definition_names_itself(self, path: Path, expected: str):
        assert _frontmatter(path).get("name") == expected, (
            f"{_rel(path)} must declare `name: {expected}`. The orchestrator "
            f"dispatches on that name, not on the filename."
        )

    @pytest.mark.parametrize("path", (ACTOR, OBSERVER), ids=lambda p: p.stem)
    def test_model_is_a_pinned_literal(self, path: Path):
        model = _frontmatter(path).get("model", "")

        assert model and _PLACEHOLDER not in model, (
            f"{_rel(path)} must pin `model` to a non-empty literal carrying no "
            f"{_PLACEHOLDER} placeholder; got {model!r}. The judge must not "
            f"drift with the environment, and neither must the subject — two "
            f"runs a week apart have to stay comparable."
        )

    def test_the_actor_cannot_write_or_edit(self):
        tools = _tool_names(_frontmatter(ACTOR).get("tools", ""))
        held = [tool for tool in _ACTOR_MUST_NOT_HOLD if tool in tools]

        assert held == [], (
            f"{_rel(ACTOR)} grants {held}. One evaluation round must not modify "
            f"the repository it runs in, and both sides run this single "
            f"definition, so any tool it holds is under test alongside the "
            f"harness. Declared tools: {list(tools)}."
        )

    def test_the_actor_never_repeats_a_criterion(self):
        body = _read(ACTOR).casefold()
        leaked = [text for text in _criteria_strings() if text.casefold() in body]

        assert leaked == [], (
            f"{_rel(ACTOR)} repeats {len(leaked)} criteria string(s) from the "
            f"case set, first {leaked[:1]!r}. An actor that knows the criteria "
            f"optimises for them, and the reading measures test-taking instead "
            f"of whether the harness leads to the right move on its own."
        )

    def test_the_actor_never_names_the_criteria_vocabulary(self):
        body = _read(ACTOR).casefold()
        named = [word for word in _CRITERIA_WORDS if word in body]

        assert named == [], (
            f"{_rel(ACTOR)} names {named}. The actor receives the task text and "
            f"the harness under test; the moment it can name where the criteria "
            f"live it can go and read them."
        )

    @pytest.mark.parametrize("label", ("A", "B"))
    def test_the_observer_names_the_blind_labels(self, label: str):
        body = _read(OBSERVER)
        forms = (f'"{label}"', f"'{label}'", f"`{label}`", f"{label}/", f"/{label}")

        assert any(form in body for form in forms), (
            f"{_rel(OBSERVER)} must name the blind label {label} as a label — "
            f"quoted, fenced, or as the pair A/B. The two transcripts reach the "
            f"observer under these labels and leave it under them; the manifest "
            f"is what maps them back."
        )

    @pytest.mark.parametrize("key", _OBSERVATION_KEYS)
    def test_the_observer_names_every_observation_key(self, key: str):
        assert key in _read(OBSERVER), (
            f"{_rel(OBSERVER)} never names the observation key {key!r}. The "
            f"observer is the sole source of this schema; a key it is not told "
            f"to emit is a cell the report cannot fill."
        )

    @pytest.mark.parametrize("name", _SIDE_NAMES)
    def test_the_observer_cannot_name_a_side(self, name: str):
        assert name not in _read(OBSERVER).casefold(), (
            f"{_rel(OBSERVER)} contains {name!r}. An observer that can name a "
            f"side has been told which is which; unblinding belongs to the "
            f"manifest, which the observer never sees."
        )

    @pytest.mark.parametrize("reading", _UNOBSERVABLE)
    def test_the_observer_cannot_name_an_unobservable_reading(self, reading: str):
        assert reading not in _read(OBSERVER).casefold(), (
            f"{_rel(OBSERVER)} contains {reading!r}. A reading that cannot be "
            f"taken off a transcript is one the observer would have to invent, "
            f"and invented telemetry is worse than the zero the report records."
        )
