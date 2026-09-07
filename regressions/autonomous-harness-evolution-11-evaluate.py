#!/usr/bin/env python3
"""Regression example: the held-out gate, four readings, never summed.

Standalone (no pytest dependency). Builds six held-out fixtures as literal
``(seed -> Metrics)`` replay tables, hands each to ``evaluate`` behind a fake
``ContractRunner`` and a fake ``ReplayFn``, and pins the verdict, the reason
literal and both sides' reported means. Nothing is read from disk: the
challenger tree is a ``Path`` that is never created, stat'd or opened, and
the champion is a sha string that is never resolved.

Hard-coded goldens (in-repo, 2026-09-07, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-11-evaluate.md``, Testing
strategy -> 回归脚本, and acceptance AC-005 / AC-006 / AC-007 / AC-010):

    DEFAULT_SEEDS == (1, 2, 3), and an omitted `seeds` is recorded as (1, 2, 3)
    a gain on tool_errors with the other three tied -> accepted is True,
        reason == "accepted", champion mean Metrics(3, 12, 900, 2.5) and
        challenger mean Metrics(1, 12, 900, 2.5)
    a failing graduated suite -> accepted is False, reason ==
        "regression_failed", regression_passed is False, both sides
        Metrics(0, 0, 0, 0.0), and the replay recorded zero calls
    two readings worse at once -> the earlier one names the reason, in the
        order "worse_tool_errors" -> "worse_call_count" -> "worse_tokens" ->
        "worse_latency"
    nothing worse and nothing better -> reason == "no_practical_gain"
    a float mean that regresses under a rounded mean that ties -> rejected,
        reason == "worse_call_count", both reported call_count 10
    seeds=() and held_out_cases=() each raise EvaluationError before the
        runner or the replay is touched

No golden is reused as an input. Every replay table below spells its own
numbers out, and every expectation is a separate literal — editing a golden
makes this script fail rather than quietly move both sides of a comparison
at once. The two shas are written twice on purpose, once as the value handed
to ``evaluate`` and once as the value the report must carry back.

Three properties are checked because they are the ones most likely to rot
into something that still looks right:

*The rounding trap.* This is the golden worth the most. The champion reads
10 calls under every seed; the challenger reads 10, 10, 11 — a float mean of
10.333... against 10.0, which is a real regression, while ``round()`` gives
10 against 10, which is a tie. The challenger is also a clear 100 tokens
cheaper. So an implementation that rounded *before* comparing would see a
tie plus a gain and accept; only one that compares the un-rounded means
rejects. The report is asserted to carry the tie (both sides call_count 10)
while the verdict rejects on that very reading, which no rounding-first
implementation can produce.

*The mean is really the mean.* In the accepted fixture no single seed's
reading equals its own field mean on either side — 5/2/2 errors mean 3, and
11/11/14 calls mean 12. An implementation that reported the first seed would
reject on call_count instead of accepting, and one that reported the last
would accept with the wrong numbers and fail the Metrics goldens.

*The order is really the order.* Precedence is pinned with three adjacent
pairs and one singleton — errors+calls, calls+tokens, tokens+latency, then
latency alone — which is enough to fix the total order. The first pair also
improves tokens, so a gain elsewhere is shown not to buy off a regression:
there is no total to trade in.

Public surface only: ``molmcp.evolution`` (the package facade), never
``molmcp.evolution.evaluate``. Deliberately absent: the module's private
``_means`` / ``_reported`` / ``_first_worse`` helpers and ``_ZERO_METRICS``
(the rounding rule and the short-circuit are proven by behaviour; importing
them would test the leaf against its own opinion), every runtime surface
including ``create_stack`` and ``create_plane``, git, network, subprocesses,
environment variables, pytest, and any filesystem access at all — a gate
that opened the challenger tree would be the bug this file exists to make
impossible.

Run directly::

    uv run python regressions/autonomous-harness-evolution-11-evaluate.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via
``test_autonomous_harness_evolution_11_evaluate``.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from molmcp.evolution import (
    ACCEPTED,
    DEFAULT_SEEDS,
    NO_PRACTICAL_GAIN,
    REGRESSION_FAILED,
    WORSE_CALL_COUNT,
    WORSE_LATENCY,
    WORSE_TOKENS,
    WORSE_TOOL_ERRORS,
    Challenger,
    ContractOutcome,
    EvalCase,
    EvaluationError,
    EvaluationReport,
    Metrics,
    evaluate,
)

# ---------------------------------------------------------------------------
# Goldens. In-repo, 2026-09-07, no third-party oracle. Every literal in this
# block is an *expectation* and is used nowhere as an input: the fixtures
# further down spell their own numbers and strings out, so editing anything
# here makes the script fail instead of agreeing with itself.
# ---------------------------------------------------------------------------

#: The frozen seed triple, and what an omitted ``seeds`` must be recorded as.
_GOLDEN_SEEDS = (1, 2, 3)

#: The seven reason literals, as 12-promote and 13-ci-gate will read them.
_GOLDEN_ACCEPTED = "accepted"
_GOLDEN_REGRESSION_FAILED = "regression_failed"
_GOLDEN_WORSE_TOOL_ERRORS = "worse_tool_errors"
_GOLDEN_WORSE_CALL_COUNT = "worse_call_count"
_GOLDEN_WORSE_TOKENS = "worse_tokens"
_GOLDEN_WORSE_LATENCY = "worse_latency"
_GOLDEN_NO_PRACTICAL_GAIN = "no_practical_gain"

#: The shas the report must carry back, written out again rather than
#: referenced from the inputs below.
_GOLDEN_CANDIDATE_SHA = "7e2a06c4d1b83f95ea27c60d4b18f3a95c07e2d1"
_GOLDEN_CHAMPION_SHA = "1b9d4f0c2a7e5834bd61c0f2a94e7d3c8501fa62"

#: The accepted fixture's seed means. Not one of these numbers is a reading
#: any single seed produced; see the module docstring.
_GOLDEN_ACCEPTED_CHAMPION = Metrics(
    tool_errors=3, call_count=12, tokens=900, latency_s=2.5
)
_GOLDEN_ACCEPTED_CHALLENGER = Metrics(
    tool_errors=1, call_count=12, tokens=900, latency_s=2.5
)

#: What both sides read when the graduated suite short-circuits the replay.
_GOLDEN_ZERO = Metrics(tool_errors=0, call_count=0, tokens=0, latency_s=0.0)

#: The tie fixture's means, identical on both sides by construction.
_GOLDEN_TIED = Metrics(tool_errors=2, call_count=10, tokens=500, latency_s=1.5)

#: The rounding trap: both sides *report* ten calls while the challenger's
#: un-rounded mean is 10.333..., and the challenger is 100 tokens cheaper.
_GOLDEN_TRAP_CALL_COUNT = 10
_GOLDEN_TRAP_CHAMPION_TOKENS = 500
_GOLDEN_TRAP_CHALLENGER_TOKENS = 400

#: What the report must record when a caller names its own seeds, to show
#: ``seeds`` is recorded rather than echoed from ``DEFAULT_SEEDS``. Written
#: out separately from the ``_CUSTOM_SEEDS`` that are actually passed in,
#: because one constant feeding both sides would pin nothing.
_GOLDEN_CUSTOM_SEEDS = (7, 11)

#: Report fields that must not exist. A verdict is not a promotion.
_FORBIDDEN_REPORT_FIELDS = ("score", "pointer", "active", "previous", "stage")

#: Seconds. Latency is a mean of exactly representable halves here, so this
#: only absorbs the last bit of the division, never a real difference.
_LATENCY_TOL_S = 1e-9

# ---------------------------------------------------------------------------
# Inputs. Literals, not references to the goldens above.
# ---------------------------------------------------------------------------

_CHAMPION_SHA = "1b9d4f0c2a7e5834bd61c0f2a94e7d3c8501fa62"

#: Never created, never opened, never stat'd — only handed across the seams.
_CHALLENGER_TREE = Path("/nonexistent/molmcp-challenger-11-evaluate")

#: Seeds a caller names for itself. An input, never an expectation.
_CUSTOM_SEEDS = (7, 11)

_HELD_OUT_CASES = (EvalCase(id="held-out-a"), EvalCase(id="held-out-b"))
_REGRESSION_CASES = (EvalCase(id="graduated-a"),)

_PASSING = ContractOutcome(passed=True, failed_case_ids=())
_FAILING = ContractOutcome(passed=False, failed_case_ids=("graduated-a",))


@dataclass(frozen=True, slots=True)
class _FakeChallenger:
    """A checkout under evaluation; ``evaluate`` reads its ``sha`` only."""

    sha: str
    component: str
    affected_paths: tuple[str, ...]


_CHALLENGER: Challenger = _FakeChallenger(
    sha="7e2a06c4d1b83f95ea27c60d4b18f3a95c07e2d1",
    component="daily-pack-skill",
    affected_paths=("skills/daily/pack.md",),
)


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


class _FakeRunner:
    """Answers the graduated suite from one literal outcome, and counts.

    Args:
        outcome: What every ``run`` returns.
    """

    def __init__(self, outcome: ContractOutcome) -> None:
        self._outcome = outcome
        self.calls: list[tuple[Path, tuple[str, ...]]] = []

    def run(self, tree: Path, cases: Sequence[EvalCase]) -> ContractOutcome:
        """Record the request and answer from the literal outcome."""
        self.calls.append((tree, tuple(case.id for case in cases)))
        return self._outcome


class _FakeReplay:
    """A ``seed -> Metrics`` table per side, dispatched as the seam is typed.

    The champion arrives as a ``str`` sha and the challenger as a ``Path``,
    so this fake dispatches on exactly that. A gate that handed the champion
    a path, or the challenger a sha, would read the wrong table and change
    the verdict rather than pass quietly.

    Args:
        champion: The champion's reading under each seed.
        challenger: The challenger's reading under each seed.
    """

    def __init__(
        self,
        champion: Mapping[int, Metrics],
        challenger: Mapping[int, Metrics],
    ) -> None:
        self._champion = dict(champion)
        self._challenger = dict(challenger)
        self.calls: list[tuple[str | Path, int]] = []

    def __call__(
        self, target: str | Path, cases: Sequence[EvalCase], seed: int
    ) -> Metrics:
        """Record the request and read the seeded row for *target*'s side."""
        self.calls.append((target, seed))
        is_challenger = isinstance(target, Path)
        table = self._challenger if is_challenger else self._champion
        _require(
            bool(cases),
            f"replay was asked for no cases on {target!r}",
        )
        _require(
            seed in table,
            f"replay was asked for unseeded {seed!r} on {target!r}",
        )
        return table[seed]


def _check_metrics(actual: Metrics, expected: Metrics, label: str) -> None:
    """Pin the three counts exactly and ``latency_s`` within tolerance.

    Args:
        actual: The metrics the report carried.
        expected: The golden mean.
        label: Which side is being checked, for the failure message.
    """
    _require(
        actual.tool_errors == expected.tool_errors,
        f"{label} tool_errors {actual.tool_errors!r} != {expected.tool_errors!r}",
    )
    _require(
        actual.call_count == expected.call_count,
        f"{label} call_count {actual.call_count!r} != {expected.call_count!r}",
    )
    _require(
        actual.tokens == expected.tokens,
        f"{label} tokens {actual.tokens!r} != {expected.tokens!r}",
    )
    _require(
        abs(actual.latency_s - expected.latency_s) <= _LATENCY_TOL_S,
        f"{label} latency_s {actual.latency_s!r} != {expected.latency_s!r} "
        f"within {_LATENCY_TOL_S!r} s",
    )


def _evaluate(
    replay: _FakeReplay,
    runner: _FakeRunner,
    *,
    seeds: Sequence[int] | None = None,
) -> EvaluationReport:
    """Run the gate over the shared challenger with the given fakes.

    Args:
        replay: The seeded held-out table.
        runner: The graduated-suite answer.
        seeds: Seeds to pass explicitly, or ``None`` to omit the argument
            and let the default stand.

    Returns:
        The report ``evaluate`` produced.
    """
    if seeds is None:
        return evaluate(
            _CHALLENGER,
            _CHALLENGER_TREE,
            _CHAMPION_SHA,
            _HELD_OUT_CASES,
            _REGRESSION_CASES,
            runner=runner,
            replay=replay,
        )
    return evaluate(
        _CHALLENGER,
        _CHALLENGER_TREE,
        _CHAMPION_SHA,
        _HELD_OUT_CASES,
        _REGRESSION_CASES,
        runner=runner,
        replay=replay,
        seeds=seeds,
    )


# ---------------------------------------------------------------------------
# Fixtures. Each table spells its own numbers out; none is derived from a
# golden, and none is shared between two scenarios that assert different
# verdicts.
# ---------------------------------------------------------------------------

#: Accepted: the challenger halves the errors and ties the rest on the mean.
#: Per seed it does neither, which is the point.
_ACCEPTED_CHAMPION_TABLE = {
    1: Metrics(tool_errors=5, call_count=11, tokens=870, latency_s=2.0),
    2: Metrics(tool_errors=2, call_count=11, tokens=870, latency_s=2.0),
    3: Metrics(tool_errors=2, call_count=14, tokens=960, latency_s=3.5),
}
_ACCEPTED_CHALLENGER_TABLE = {
    1: Metrics(tool_errors=3, call_count=14, tokens=960, latency_s=3.5),
    2: Metrics(tool_errors=0, call_count=11, tokens=870, latency_s=2.0),
    3: Metrics(tool_errors=0, call_count=11, tokens=870, latency_s=2.0),
}

#: The champion every precedence and tie fixture is measured against:
#: means of 2 errors, 10 calls, 500 tokens, 1.5 s.
_BASE_CHAMPION_TABLE = {
    1: Metrics(tool_errors=1, call_count=9, tokens=480, latency_s=1.0),
    2: Metrics(tool_errors=2, call_count=10, tokens=500, latency_s=1.5),
    3: Metrics(tool_errors=3, call_count=11, tokens=520, latency_s=2.0),
}

#: Errors *and* calls regress while tokens improve: the earlier reading must
#: name the reason, and the gain must not buy either regression off.
_WORSE_ERRORS_AND_CALLS_TABLE = {
    1: Metrics(tool_errors=3, call_count=11, tokens=400, latency_s=1.0),
    2: Metrics(tool_errors=3, call_count=11, tokens=400, latency_s=1.5),
    3: Metrics(tool_errors=3, call_count=11, tokens=400, latency_s=2.0),
}

#: Calls *and* tokens regress; errors tie.
_WORSE_CALLS_AND_TOKENS_TABLE = {
    1: Metrics(tool_errors=1, call_count=11, tokens=520, latency_s=1.0),
    2: Metrics(tool_errors=2, call_count=11, tokens=520, latency_s=1.5),
    3: Metrics(tool_errors=3, call_count=11, tokens=520, latency_s=2.0),
}

#: Tokens *and* latency regress; errors and calls tie.
_WORSE_TOKENS_AND_LATENCY_TABLE = {
    1: Metrics(tool_errors=1, call_count=9, tokens=520, latency_s=2.0),
    2: Metrics(tool_errors=2, call_count=10, tokens=520, latency_s=2.0),
    3: Metrics(tool_errors=3, call_count=11, tokens=520, latency_s=2.0),
}

#: Latency alone regresses — the last reading in the order, checked on its
#: own so the three pairs above fix a total order rather than a prefix.
_WORSE_LATENCY_ONLY_TABLE = {
    1: Metrics(tool_errors=1, call_count=9, tokens=480, latency_s=2.0),
    2: Metrics(tool_errors=2, call_count=10, tokens=500, latency_s=2.0),
    3: Metrics(tool_errors=3, call_count=11, tokens=520, latency_s=2.0),
}

#: The champion's own readings, seed order reversed: every mean is identical,
#: so nothing is worse and nothing is better.
_TIED_CHALLENGER_TABLE = {
    1: Metrics(tool_errors=3, call_count=11, tokens=520, latency_s=2.0),
    2: Metrics(tool_errors=2, call_count=10, tokens=500, latency_s=1.5),
    3: Metrics(tool_errors=1, call_count=9, tokens=480, latency_s=1.0),
}

#: The rounding trap. Champion call_count mean 10.0; challenger 31/3 =
#: 10.333..., which rounds to the same 10. The challenger is also 100 tokens
#: cheaper, so a gate that rounded before comparing would see a tie plus a
#: gain and accept. Comparing the un-rounded means rejects, and the report
#: still shows the tie.
_TRAP_CHAMPION_TABLE = {
    1: Metrics(tool_errors=1, call_count=10, tokens=500, latency_s=1.0),
    2: Metrics(tool_errors=1, call_count=10, tokens=500, latency_s=1.0),
    3: Metrics(tool_errors=1, call_count=10, tokens=500, latency_s=1.0),
}
_TRAP_CHALLENGER_TABLE = {
    1: Metrics(tool_errors=1, call_count=10, tokens=400, latency_s=1.0),
    2: Metrics(tool_errors=1, call_count=10, tokens=400, latency_s=1.0),
    3: Metrics(tool_errors=1, call_count=11, tokens=400, latency_s=1.0),
}

#: Two caller-named seeds, to show the report records the seeds it used.
_CUSTOM_SEED_CHAMPION_TABLE = {
    7: Metrics(tool_errors=2, call_count=10, tokens=500, latency_s=1.0),
    11: Metrics(tool_errors=2, call_count=10, tokens=500, latency_s=2.0),
}
_CUSTOM_SEED_CHALLENGER_TABLE = {
    7: Metrics(tool_errors=1, call_count=10, tokens=500, latency_s=1.0),
    11: Metrics(tool_errors=1, call_count=10, tokens=500, latency_s=2.0),
}


def _check_frozen_literals() -> None:
    """Golden 1: the seven reasons and the seed triple, as 12/13 read them."""
    pairs = (
        ("ACCEPTED", ACCEPTED, _GOLDEN_ACCEPTED),
        ("REGRESSION_FAILED", REGRESSION_FAILED, _GOLDEN_REGRESSION_FAILED),
        ("WORSE_TOOL_ERRORS", WORSE_TOOL_ERRORS, _GOLDEN_WORSE_TOOL_ERRORS),
        ("WORSE_CALL_COUNT", WORSE_CALL_COUNT, _GOLDEN_WORSE_CALL_COUNT),
        ("WORSE_TOKENS", WORSE_TOKENS, _GOLDEN_WORSE_TOKENS),
        ("WORSE_LATENCY", WORSE_LATENCY, _GOLDEN_WORSE_LATENCY),
        ("NO_PRACTICAL_GAIN", NO_PRACTICAL_GAIN, _GOLDEN_NO_PRACTICAL_GAIN),
    )
    for name, exported, golden in pairs:
        _require(exported == golden, f"{name} is {exported!r}, not {golden!r}")

    _require(
        DEFAULT_SEEDS == _GOLDEN_SEEDS,
        f"DEFAULT_SEEDS {DEFAULT_SEEDS!r} != {_GOLDEN_SEEDS!r}",
    )

    print(f"seven frozen reasons pinned; DEFAULT_SEEDS={DEFAULT_SEEDS!r}")


def _check_accepted() -> None:
    """Golden 2: one reading better, three tied on the three-seed mean."""
    replay = _FakeReplay(_ACCEPTED_CHAMPION_TABLE, _ACCEPTED_CHALLENGER_TABLE)
    runner = _FakeRunner(_PASSING)

    report = _evaluate(replay, runner)

    _require(report.accepted is True, f"accepted is {report.accepted!r}, not True")
    _require(
        report.reason == _GOLDEN_ACCEPTED,
        f"reason {report.reason!r} != {_GOLDEN_ACCEPTED!r}",
    )
    _require(
        report.regression_passed is True,
        f"regression_passed is {report.regression_passed!r}, not True",
    )
    _require(
        report.seeds == _GOLDEN_SEEDS,
        f"seeds {report.seeds!r} != {_GOLDEN_SEEDS!r}",
    )
    _require(
        report.candidate_sha == _GOLDEN_CANDIDATE_SHA,
        f"candidate_sha {report.candidate_sha!r} != {_GOLDEN_CANDIDATE_SHA!r}",
    )
    _require(
        report.champion_sha == _GOLDEN_CHAMPION_SHA,
        f"champion_sha {report.champion_sha!r} != {_GOLDEN_CHAMPION_SHA!r}",
    )

    _check_metrics(report.champion_metrics, _GOLDEN_ACCEPTED_CHAMPION, "champion")
    _check_metrics(report.challenger_metrics, _GOLDEN_ACCEPTED_CHALLENGER, "challenger")

    for field in _FORBIDDEN_REPORT_FIELDS:
        _require(
            not hasattr(report, field),
            f"the report carries a {field!r} field; a verdict is not a promotion",
        )

    champion_seeds = tuple(
        seed for target, seed in replay.calls if not isinstance(target, Path)
    )
    challenger_seeds = tuple(
        seed for target, seed in replay.calls if isinstance(target, Path)
    )
    _require(
        champion_seeds == _GOLDEN_SEEDS,
        f"the champion was replayed under {champion_seeds!r}, not {_GOLDEN_SEEDS!r}",
    )
    _require(
        challenger_seeds == _GOLDEN_SEEDS,
        f"the challenger was replayed under {challenger_seeds!r}, "
        f"not {_GOLDEN_SEEDS!r}",
    )
    _require(
        len(replay.calls) == 6,
        f"replay ran {len(replay.calls)} times, not 2 sides x 3 seeds",
    )

    print(f"accepted={report.accepted!r} reason={report.reason!r}")
    print(f"champion mean {report.champion_metrics!r}")
    print(f"challenger mean {report.challenger_metrics!r}")
    print(f"seeds={report.seeds!r}, replay calls={len(replay.calls)}")


def _check_regression_failed() -> None:
    """Golden 3: a failing graduated suite rejects before any replay runs."""
    replay = _FakeReplay(_ACCEPTED_CHAMPION_TABLE, _ACCEPTED_CHALLENGER_TABLE)
    runner = _FakeRunner(_FAILING)

    report = _evaluate(replay, runner)

    _require(report.accepted is False, f"accepted is {report.accepted!r}, not False")
    _require(
        report.reason == _GOLDEN_REGRESSION_FAILED,
        f"reason {report.reason!r} != {_GOLDEN_REGRESSION_FAILED!r}",
    )
    _require(
        report.regression_passed is False,
        f"regression_passed is {report.regression_passed!r}, not False",
    )

    _check_metrics(report.champion_metrics, _GOLDEN_ZERO, "champion")
    _check_metrics(report.challenger_metrics, _GOLDEN_ZERO, "challenger")

    _require(
        replay.calls == [],
        f"the replay ran {replay.calls!r} after the graduated suite failed",
    )
    _require(
        len(runner.calls) == 1,
        f"the graduated suite ran {len(runner.calls)} times, not once",
    )

    print(f"accepted={report.accepted!r} reason={report.reason!r}")
    print(f"zeroed metrics, replay calls={len(replay.calls)}")


def _check_worse_precedence() -> None:
    """Golden 4: with two readings worse at once, the earlier one wins."""
    cases = (
        ("errors+calls", _WORSE_ERRORS_AND_CALLS_TABLE, _GOLDEN_WORSE_TOOL_ERRORS),
        ("calls+tokens", _WORSE_CALLS_AND_TOKENS_TABLE, _GOLDEN_WORSE_CALL_COUNT),
        ("tokens+latency", _WORSE_TOKENS_AND_LATENCY_TABLE, _GOLDEN_WORSE_TOKENS),
        ("latency alone", _WORSE_LATENCY_ONLY_TABLE, _GOLDEN_WORSE_LATENCY),
    )
    for label, challenger_table, golden in cases:
        replay = _FakeReplay(_BASE_CHAMPION_TABLE, challenger_table)
        report = _evaluate(replay, _FakeRunner(_PASSING))

        _require(
            report.accepted is False,
            f"{label}: accepted is {report.accepted!r}, not False",
        )
        _require(
            report.reason == golden,
            f"{label}: reason {report.reason!r} != {golden!r}",
        )
        _require(
            report.regression_passed is True,
            f"{label}: regression_passed is {report.regression_passed!r}, not True",
        )
        print(f"{label} -> {report.reason!r}")


def _check_no_practical_gain() -> None:
    """Golden 5: nothing worse and nothing better is still a rejection."""
    replay = _FakeReplay(_BASE_CHAMPION_TABLE, _TIED_CHALLENGER_TABLE)

    report = _evaluate(replay, _FakeRunner(_PASSING))

    _require(report.accepted is False, f"accepted is {report.accepted!r}, not False")
    _require(
        report.reason == _GOLDEN_NO_PRACTICAL_GAIN,
        f"reason {report.reason!r} != {_GOLDEN_NO_PRACTICAL_GAIN!r}",
    )

    _check_metrics(report.champion_metrics, _GOLDEN_TIED, "champion")
    _check_metrics(report.challenger_metrics, _GOLDEN_TIED, "challenger")

    print(f"accepted={report.accepted!r} reason={report.reason!r}")
    print(f"both sides {report.challenger_metrics!r}")


def _check_rounding_trap() -> None:
    """Golden 6: the float mean decides; the rounded mean only reports.

    The champion reads ten calls under every seed and the challenger reads
    ten, ten, eleven — 10.333... against 10.0. Both round to ten, and the
    challenger is a hundred tokens cheaper, so a gate that rounded first
    would find a tie plus a gain and accept. This is the only fixture that
    separates the two implementations, which is why the report is checked
    for the tie *and* the verdict for the rejection: no rounding-first gate
    can produce that pair.
    """
    replay = _FakeReplay(_TRAP_CHAMPION_TABLE, _TRAP_CHALLENGER_TABLE)

    report = _evaluate(replay, _FakeRunner(_PASSING))

    _require(report.accepted is False, f"accepted is {report.accepted!r}, not False")
    _require(
        report.reason == _GOLDEN_WORSE_CALL_COUNT,
        f"reason {report.reason!r} != {_GOLDEN_WORSE_CALL_COUNT!r}; a gate that "
        "rounded before comparing would read a tie here and accept",
    )
    _require(
        report.champion_metrics.call_count == _GOLDEN_TRAP_CALL_COUNT,
        f"champion call_count {report.champion_metrics.call_count!r} "
        f"!= {_GOLDEN_TRAP_CALL_COUNT!r}",
    )
    _require(
        report.challenger_metrics.call_count == _GOLDEN_TRAP_CALL_COUNT,
        f"challenger call_count {report.challenger_metrics.call_count!r} "
        f"!= {_GOLDEN_TRAP_CALL_COUNT!r}; the rounded means must tie while the "
        "verdict rejects",
    )
    _require(
        report.champion_metrics.tokens == _GOLDEN_TRAP_CHAMPION_TOKENS,
        f"champion tokens {report.champion_metrics.tokens!r} "
        f"!= {_GOLDEN_TRAP_CHAMPION_TOKENS!r}",
    )
    _require(
        report.challenger_metrics.tokens == _GOLDEN_TRAP_CHALLENGER_TOKENS,
        f"challenger tokens {report.challenger_metrics.tokens!r} "
        f"!= {_GOLDEN_TRAP_CHALLENGER_TOKENS!r}; the token gain is what a "
        "rounding-first gate would have accepted on",
    )

    print(f"accepted={report.accepted!r} reason={report.reason!r}")
    print(
        f"reported call_count {report.champion_metrics.call_count!r} vs "
        f"{report.challenger_metrics.call_count!r} (a tie), tokens "
        f"{report.champion_metrics.tokens!r} vs "
        f"{report.challenger_metrics.tokens!r} (a gain)"
    )


def _check_seed_gate() -> None:
    """Golden 7: caller seeds are recorded; empty seeds and cases raise."""
    replay = _FakeReplay(_CUSTOM_SEED_CHAMPION_TABLE, _CUSTOM_SEED_CHALLENGER_TABLE)
    report = _evaluate(replay, _FakeRunner(_PASSING), seeds=_CUSTOM_SEEDS)

    _require(report.accepted is True, f"accepted is {report.accepted!r}, not True")
    _require(
        report.seeds == _GOLDEN_CUSTOM_SEEDS,
        f"seeds {report.seeds!r} != {_GOLDEN_CUSTOM_SEEDS!r}",
    )
    _require(
        len(replay.calls) == 4,
        f"replay ran {len(replay.calls)} times, not 2 sides x 2 seeds",
    )

    empty_seed_replay = _FakeReplay(_BASE_CHAMPION_TABLE, _TIED_CHALLENGER_TABLE)
    empty_seed_runner = _FakeRunner(_PASSING)
    try:
        _evaluate(empty_seed_replay, empty_seed_runner, seeds=())
    except EvaluationError as error:
        print(f"seeds=() -> EvaluationError({str(error)!r})")
    else:
        raise AssertionError("seeds=() produced a report instead of raising")
    _require(
        empty_seed_replay.calls == [] and empty_seed_runner.calls == [],
        "seeds=() touched a seam before raising",
    )

    empty_case_replay = _FakeReplay(_BASE_CHAMPION_TABLE, _TIED_CHALLENGER_TABLE)
    empty_case_runner = _FakeRunner(_PASSING)
    try:
        evaluate(
            _CHALLENGER,
            _CHALLENGER_TREE,
            _CHAMPION_SHA,
            (),
            _REGRESSION_CASES,
            runner=empty_case_runner,
            replay=empty_case_replay,
        )
    except EvaluationError as error:
        print(f"held_out_cases=() -> EvaluationError({str(error)!r})")
    else:
        raise AssertionError("held_out_cases=() produced a report instead of raising")
    _require(
        empty_case_replay.calls == [] and empty_case_runner.calls == [],
        "held_out_cases=() touched a seam before raising",
    )

    print(f"caller seeds recorded as {report.seeds!r}, replay calls={4}")


def main() -> int:
    _check_frozen_literals()
    _check_accepted()
    _check_regression_failed()
    _check_worse_precedence()
    _check_no_practical_gain()
    _check_rounding_trap()
    _check_seed_gate()

    print("\nOK: four readings compared one at a time, on un-rounded seed means.")
    return 0


def test_autonomous_harness_evolution_11_evaluate() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
