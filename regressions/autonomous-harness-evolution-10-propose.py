#!/usr/bin/env python3
"""Regression example: one receipt-backed pattern becomes one Candidate.

Standalone (no pytest dependency). Builds the spec's worked example as three
frozen view literals — one open pattern, one skill component, one receipt
binding them — hands them to ``propose``, and pins every field of the single
``Candidate`` that comes back. Nothing is read from disk: the component's
body is a string in this file, and its path is a name that only ever appears
in the patch header.

Hard-coded goldens (in-repo, 2026-09-07, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-10-propose.md``, Testing strategy
-> 回归例子, and acceptance AC-008):

    pattern_id == "skill-missing-warning"
    component_id == "daily-pack-skill"
    path == "skills/daily/pack.md"
    rationale_refs == ("run-42",)
    unified_diff carries the whole line "+Always call packages before coding"
        and equals _EXPECTED_DIFF, headers included
    human_gate is False

The added line is checked as a *line*, not a substring: it is compared
against the members of ``unified_diff.splitlines()``, so a patch that folded
the insert into some longer line would fail here rather than pass on
containment. The whole patch is pinned beside it, spelled out rather than
rebuilt with ``difflib``, so that a change to the header or the hunk range
fails here instead of agreeing with itself.

No golden is reused as an input. The views below spell their own strings
out, so editing ``_PATTERN_ID`` or ``_COMPONENT_ID`` changes only what is
expected and the script fails; a shared constant would have moved both sides
of every comparison at once and pinned nothing.

Two further properties are checked because they are the ones most likely to
rot into something that still looks right:

*The substring trap.* One run over two open patterns, in wiki order: first
an insert whose added line is ``def pack(items):``, then one whose added line
is ``Always call def name( before coding``. The definition must be skipped
and the prose must not, so the returned candidate cites the *second*
pattern. An implementation that searched for the substring ``def name(``
would skip both and return ``None``; one that dropped the skip entirely
would return the first. Only the anchored rule returns what is asserted
here, and the definition-only wiki is then run on its own to show the skip
in isolation rather than by inference.

*Atomicity.* The return is one ``Candidate`` or ``None``, never a sequence.
The success path asserts the value is not a ``list`` or ``tuple``, and the
empty-wiki path asserts ``is None`` rather than falsiness — an empty tuple
is falsy too, and a leaf that started batching would slip past a truthiness
check.

Public surface only: ``molmcp.evolution`` (the package facade), never
``molmcp.evolution.propose``. Deliberately absent: the module's private
``_FUNCTION_DEF_PATTERN`` and ``_HUMAN_GATE_BY_KIND`` (the anchoring and the
gate are proven by behaviour; importing them would test the leaf against its
own opinion), ``difflib``, every runtime surface including ``create_stack``,
git, network, subprocesses, environment variables, pytest, and any
filesystem access at all — this leaf is a pure function, and opening
``skills/daily/pack.md`` is the bug it is written to make impossible.

Run directly::

    uv run python regressions/autonomous-harness-evolution-10-propose.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via
``test_autonomous_harness_evolution_10_propose``.
"""

from __future__ import annotations

import sys

from molmcp.evolution import (
    BundleView,
    Candidate,
    Component,
    Pattern,
    Receipt,
    ReceiptsView,
    WikiView,
    propose,
)

# In-repo goldens, 2026-09-07, no third-party oracle. Every literal below
# is an *expectation*. None of them is reused to build an input: the views
# further down spell their own strings out, so editing a golden here makes
# this script fail rather than quietly agree with itself.
_PATTERN_ID = "skill-missing-warning"
_COMPONENT_ID = "daily-pack-skill"
_PATH = "skills/daily/pack.md"
_RATIONALE_REFS = ("run-42",)
_ADDED_LINE = "+Always call packages before coding"
_HUMAN_GATE = False

#: The whole patch, written out rather than rebuilt from ``difflib`` so that
#: a change to the headers or the hunk range fails here. Both headers are
#: ``component.path`` verbatim — never an absolute or resolved path.
_EXPECTED_DIFF = (
    "--- skills/daily/pack.md\n"
    "+++ skills/daily/pack.md\n"
    "@@ -1 +1,2 @@\n"
    " # daily pack\n"
    "+Always call packages before coding\n"
)

#: Goldens for the substring trap: the pattern that must win, the receipt it
#: must cite, the line it must add, and the two the skipped definition would
#: have contributed.
_PROSE_PATTERN_ID = "skill-prose-mention"
_PROSE_REFS = ("run-44",)
_PROSE_ADDED_LINE = "+Always call def name( before coding"
_SKIPPED_PATTERN_ID = "skill-helper-def"
_SKIPPED_LINE = "+def pack(items):"

# Inputs, as the spec's happy path describes them: the insert lives on the
# pattern, the body on the component, and the binding on the receipt. These
# are literals, not references to the goldens above.
_BUNDLE = BundleView(
    components=(
        Component(
            component_id="daily-pack-skill",
            kind="skill",
            path="skills/daily/pack.md",
            text="# daily pack\n",
        ),
    ),
)
_WIKI = WikiView(
    open_patterns=(
        Pattern(
            pattern_id="skill-missing-warning",
            insert="Always call packages before coding",
        ),
    ),
)
_RECEIPTS = ReceiptsView(
    receipts=(
        Receipt(
            receipt_id="run-42",
            pattern_id="skill-missing-warning",
            component_id="daily-pack-skill",
        ),
    ),
)

#: No open pattern at all. The receipts and the bundle stay non-empty, so a
#: ``None`` here is the empty wiki talking and nothing else.
_EMPTY_WIKI = WikiView(open_patterns=())

#: The substring trap, in wiki order: a real definition first, then prose
#: that merely mentions one. The second must win.
_TRAP_WIKI = WikiView(
    open_patterns=(
        Pattern(pattern_id="skill-helper-def", insert="def pack(items):"),
        Pattern(
            pattern_id="skill-prose-mention",
            insert="Always call def name( before coding",
        ),
    ),
)

#: The same definition pattern with nothing behind it, so the skip is shown
#: on its own rather than inferred from which pattern won.
_DEF_ONLY_WIKI = WikiView(open_patterns=_TRAP_WIKI.open_patterns[:1])

_TRAP_RECEIPTS = ReceiptsView(
    receipts=(
        Receipt(
            receipt_id="run-43",
            pattern_id="skill-helper-def",
            component_id="daily-pack-skill",
        ),
        Receipt(
            receipt_id="run-44",
            pattern_id="skill-prose-mention",
            component_id="daily-pack-skill",
        ),
    ),
)


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


def _proposed(wiki: WikiView, receipts: ReceiptsView, bundle: BundleView) -> Candidate:
    """Return the candidate for *wiki*, or fail when there is none.

    Args:
        wiki: The open patterns to propose from.
        receipts: The evidence binding patterns to components.
        bundle: The components the patterns may touch.

    Returns:
        The single candidate ``propose`` returned.
    """
    candidate = propose(wiki, receipts, bundle)
    _require(
        candidate is not None,
        f"propose returned None for patterns "
        f"{[pattern.pattern_id for pattern in wiki.open_patterns]}",
    )
    if candidate is None:  # pragma: no cover - _require already raised
        raise AssertionError("unreachable")
    return candidate


def _check_happy_path() -> Candidate:
    """Goldens 1-6: the receipt-backed skill patch, field for field.

    Returns:
        The candidate, for the atomicity check to inspect.
    """
    candidate = _proposed(_WIKI, _RECEIPTS, _BUNDLE)

    _require(
        candidate.pattern_id == _PATTERN_ID,
        f"pattern_id {candidate.pattern_id!r} != {_PATTERN_ID!r}",
    )
    _require(
        candidate.component_id == _COMPONENT_ID,
        f"component_id {candidate.component_id!r} != {_COMPONENT_ID!r}",
    )
    _require(
        candidate.path == _PATH,
        f"path {candidate.path!r} != {_PATH!r}",
    )
    _require(
        candidate.rationale_refs == _RATIONALE_REFS,
        f"rationale_refs {candidate.rationale_refs!r} != {_RATIONALE_REFS!r}",
    )
    _require(
        candidate.human_gate is _HUMAN_GATE,
        f"human_gate {candidate.human_gate!r} is not {_HUMAN_GATE!r}",
    )

    lines = candidate.unified_diff.splitlines()
    _require(
        _ADDED_LINE in lines,
        f"the patch has no whole line {_ADDED_LINE!r}; it holds {lines!r}",
    )
    _require(
        candidate.unified_diff == _EXPECTED_DIFF,
        f"unified_diff {candidate.unified_diff!r} != {_EXPECTED_DIFF!r}",
    )

    print(f"propose(...) -> {candidate.pattern_id!r} on {candidate.component_id!r}")
    print(f"path={candidate.path!r}, rationale_refs={candidate.rationale_refs!r}")
    print(f"human_gate={candidate.human_gate!r}, added line {_ADDED_LINE!r}")
    return candidate


def _check_atomicity(candidate: Candidate) -> None:
    """Golden 7: one candidate or ``None``, never a sequence.

    Args:
        candidate: What the happy path returned.
    """
    _require(
        isinstance(candidate, Candidate),
        f"propose returned a {type(candidate).__name__}, not a Candidate",
    )
    _require(
        not isinstance(candidate, list | tuple),
        f"propose returned a {type(candidate).__name__}, which is a sequence",
    )

    nothing = propose(_EMPTY_WIKI, _RECEIPTS, _BUNDLE)
    _require(
        nothing is None,
        f"an empty wiki proposed {nothing!r}; an empty sequence is falsy too, "
        "so this is checked with `is None`",
    )

    print(f"one {type(candidate).__name__}, not a sequence")
    print(f"empty open_patterns -> {nothing!r} (identity, not falsiness)")


def _check_substring_trap() -> None:
    """Golden 8: ``def pack(`` is skipped, ``def name(`` in prose is not."""
    candidate = _proposed(_TRAP_WIKI, _TRAP_RECEIPTS, _BUNDLE)

    _require(
        candidate.pattern_id != _SKIPPED_PATTERN_ID,
        f"the function-definition pattern {_SKIPPED_PATTERN_ID!r} was proposed",
    )
    _require(
        candidate.pattern_id == _PROSE_PATTERN_ID,
        f"pattern_id {candidate.pattern_id!r} != {_PROSE_PATTERN_ID!r}",
    )
    _require(
        candidate.rationale_refs == _PROSE_REFS,
        f"rationale_refs {candidate.rationale_refs!r} != {_PROSE_REFS!r}",
    )

    lines = candidate.unified_diff.splitlines()
    _require(
        _PROSE_ADDED_LINE in lines,
        f"the patch has no whole line {_PROSE_ADDED_LINE!r}; it holds {lines!r}",
    )
    _require(
        _SKIPPED_LINE not in lines,
        f"the patch adds the skipped definition {_SKIPPED_LINE!r}",
    )

    skipped = propose(_DEF_ONLY_WIKI, _TRAP_RECEIPTS, _BUNDLE)
    _require(
        skipped is None,
        f"a skill insert adding {_SKIPPED_LINE!r} proposed {skipped!r}, not None",
    )

    print(f"two patterns -> {candidate.pattern_id!r}")
    print(f"rationale_refs={candidate.rationale_refs!r}")
    print(f"{_SKIPPED_LINE!r} alone on a skill -> {skipped!r}")


def main() -> int:
    candidate = _check_happy_path()
    _check_atomicity(candidate)
    _check_substring_trap()

    print("\nOK: one evidenced Candidate, anchored def skip, atomic return.")
    return 0


def test_autonomous_harness_evolution_10_propose() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
