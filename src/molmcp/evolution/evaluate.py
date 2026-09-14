"""Held-out gate: four readings compared one by one, never summed.

A *challenger* is a checkout of the harness that wants to replace the
current *champion*. :func:`evaluate` decides whether it may and says why,
in one frozen :class:`EvaluationReport`: the graduated regression cases
must still pass, and the held-out cases are then replayed on both sides
under the frozen seeds :data:`DEFAULT_SEEDS` and compared on four
readings — ``tool_errors``, ``call_count``, ``tokens`` and ``latency_s``
(seconds). All four are lower-is-better, and each is compared on its own
against its own ``DROP_*`` threshold. There is no weighted total, so a
gain in one reading can never buy a regression in another, and the
verdict is a literal reason rather than a number.

Two disciplines are easy to lose:

* *The float mean decides; the rounded mean is only reported.* Three of
  the four readings are ints and the report stores ``round(mean)``, but
  the worse/better comparison runs on the un-rounded seed mean. Rounding
  first would let a real regression of a third of a call per seed vanish
  into a tie — and let a real gain of the same size vanish with it.
* *The report is a verdict, not a promotion.* It carries no pointer, no
  stage and no score, and :func:`evaluate` reads and writes no
  active/previous pointer and no session lock. Moving the champion
  pointer belongs to a later leaf; this one only says accepted or not,
  and why.

Leaf module: the standard library and its own types. It imports no
FastMCP, no MCP and nothing
from the runtime that composes planes — which is why the two seams it
needs, :class:`ContractRunner` and :class:`ReplayFn`, are keyword-only
parameters with no default at all. A default would have to be a real
host, and the host is exactly what this module is kept away from. Tests
inject fakes; the production pair is injected by the CI gate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

#: The challenger replaces the champion. The only reason an accepted
#: report may carry, and the only one a rejection may not.
ACCEPTED = "accepted"

#: A graduated regression case failed on the challenger tree. Decided
#: before any held-out replay, so it says nothing about the readings.
REGRESSION_FAILED = "regression_failed"

#: The challenger made more tool errors than the champion.
WORSE_TOOL_ERRORS = "worse_tool_errors"

#: The challenger needed more tool calls than the champion.
WORSE_CALL_COUNT = "worse_call_count"

#: The challenger spent more tokens than the champion.
WORSE_TOKENS = "worse_tokens"

#: The challenger took longer than the champion.
WORSE_LATENCY = "worse_latency"

#: Nothing got worse, but nothing got better either. A tie is a
#: rejection: the champion keeps the seat it already holds.
NO_PRACTICAL_GAIN = "no_practical_gain"

#: The seeds every held-out replay uses unless a caller names its own.
#: Frozen at three so two runs of the same pair of trees compare the same
#: way; a report always records the seeds it actually used.
DEFAULT_SEEDS: tuple[int, ...] = (1, 2, 3)

#: How much worse than the champion each reading may get before it is a
#: regression, in that reading's own unit (errors, calls, tokens,
#: seconds). All zero: there is no noise band here, because the replay is
#: seeded and any move in the wrong direction is a real one.
DROP_TOOL_ERRORS = 0
DROP_CALL_COUNT = 0
DROP_TOKENS = 0
DROP_LATENCY_S = 0.0


class EvaluationError(ValueError):
    """A gate that cannot be run, or a verdict that cannot be held.

    Raised for an empty seed list, an empty held-out suite, and any
    :class:`EvaluationReport` whose ``accepted`` and ``reason`` disagree.
    A ``ValueError`` because every case is a bad value handed to this
    layer, not a failure of something it called.
    """


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One case a runner or a replay is asked to work through.

    Identity only. What the case *does* lives with whoever executes it —
    this module hands cases across a seam and never reads inside one.

    Attributes:
        id: Stable identity of the case, as the executing side knows it.
    """

    id: str


@dataclass(frozen=True, slots=True)
class Metrics:
    """One side's readings from a held-out replay.

    Exactly four numbers, all lower-is-better, all compared
    independently. There is deliberately no score, total or rank here: a
    single number would let a token saving pay for an extra tool error,
    and this gate does not make that trade.

    Attributes:
        tool_errors: Count of tool calls that returned an error.
        call_count: Count of tool calls made.
        tokens: Count of tokens spent.
        latency_s: Wall-clock duration in **seconds**. The only float of
            the four, and the only reading the report keeps un-rounded.
    """

    tool_errors: int
    call_count: int
    tokens: int
    latency_s: float


#: What both sides read when the regression contract short-circuits: the
#: replay never ran, so there is nothing to report but zeroes.
_ZERO_METRICS = Metrics(tool_errors=0, call_count=0, tokens=0, latency_s=0.0)


@dataclass(frozen=True, slots=True)
class ContractOutcome:
    """What a :class:`ContractRunner` says about the graduated suite.

    Attributes:
        passed: Whether every case given to the runner passed.
        failed_case_ids: Ids of the cases that did not, empty when
            ``passed``. Carried for the report's readers, not read here:
            one failure is already the whole verdict.
    """

    passed: bool
    failed_case_ids: tuple[str, ...]


class Challenger(Protocol):
    """The checkout under evaluation, read for three names only.

    Duck-typed on purpose, and named for what it is: a tree someone has
    already built, not a patch someone has proposed. The report field is
    still ``candidate_sha``.

    :func:`evaluate` reads ``sha`` and nothing else; ``component`` and
    ``affected_paths`` are here because callers pass one object around,
    and this module neither interprets a path nor checks one against a
    whitelist.
    """

    @property
    def sha(self) -> str:
        """Full commit sha of the challenger checkout."""

    @property
    def component(self) -> str:
        """Id of the harness component the challenger changes."""

    @property
    def affected_paths(self) -> Sequence[str]:
        """Repository paths the challenger touches."""


class ContractRunner(Protocol):
    """Runs the graduated regression cases against a checked-out tree.

    The production runner starts a real host; this module only ever
    holds the seam, which is why it has no default.
    """

    def run(self, tree: Path, cases: Sequence[EvalCase]) -> ContractOutcome:
        """Run *cases* against the tree at *tree* and report the outcome."""
        ...


class ReplayFn(Protocol):
    """Replays the held-out cases against one side under one seed.

    The two sides are named differently on purpose: the champion by its
    full sha, because resolving it to a tree belongs behind this seam,
    and the challenger by the tree a caller already checked out.
    """

    def __call__(
        self, target: str | Path, cases: Sequence[EvalCase], seed: int
    ) -> Metrics:
        """Replay *cases* against *target* under *seed* and read the metrics."""
        ...


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """The whole verdict on one challenger, and the only home for it.

    A verdict, not a promotion: there is no pointer, stage or score field
    here, and constructing one whose ``accepted`` disagrees with its
    ``reason`` or with ``regression_passed`` raises rather than
    producing a report a later reader would have to second-guess.

    Attributes:
        accepted: Whether the challenger may replace the champion. True
            only together with ``reason == ACCEPTED`` and
            ``regression_passed``.
        reason: One of the seven frozen literals in this module.
        candidate_sha: Full sha of the challenger that was evaluated.
        champion_sha: Full sha of the champion it was compared against.
        seeds: The seeds the replay actually used, in call order.
        regression_passed: Whether the graduated suite still passed. True
            when there were no graduated cases to run.
        champion_metrics: The champion's seed means — the three counts
            rounded to ints, ``latency_s`` the mean in seconds. Zeroed
            when the regression contract short-circuited the replay.
        challenger_metrics: The challenger's seed means, same shape.

    Raises:
        EvaluationError: ``accepted`` is True while ``regression_passed``
            is False, ``accepted`` is True under any reason other than
            ``ACCEPTED``, or ``accepted`` is False under ``ACCEPTED``.
    """

    accepted: bool
    reason: str
    candidate_sha: str
    champion_sha: str
    seeds: tuple[int, ...]
    regression_passed: bool
    champion_metrics: Metrics
    challenger_metrics: Metrics

    def __post_init__(self) -> None:
        if self.accepted and not self.regression_passed:
            raise EvaluationError(
                "accepted report cannot carry a failed regression suite"
            )
        if self.accepted and self.reason != ACCEPTED:
            raise EvaluationError(f"accepted report cannot read {self.reason!r}")
        if not self.accepted and self.reason == ACCEPTED:
            raise EvaluationError(f"rejected report cannot read {ACCEPTED!r}")


#: One reading's reason literal, champion mean, challenger mean and drop
#: threshold — everything a single comparison needs, in one place.
_Comparison = tuple[str, float, float, float]


@dataclass(frozen=True, slots=True)
class _Means:
    """The un-rounded seed means, before the report rounds three of them."""

    tool_errors: float
    call_count: float
    tokens: float
    latency_s: float


def _means(readings: Sequence[Metrics]) -> _Means:
    """Arithmetic mean of *readings*, field by field, without rounding."""
    count = len(readings)
    return _Means(
        tool_errors=sum(reading.tool_errors for reading in readings) / count,
        call_count=sum(reading.call_count for reading in readings) / count,
        tokens=sum(reading.tokens for reading in readings) / count,
        latency_s=sum(reading.latency_s for reading in readings) / count,
    )


def _reported(means: _Means) -> Metrics:
    """The reportable form: counts rounded, ``latency_s`` kept in seconds."""
    return Metrics(
        tool_errors=round(means.tool_errors),
        call_count=round(means.call_count),
        tokens=round(means.tokens),
        latency_s=means.latency_s,
    )


def _comparisons(champion: _Means, challenger: _Means) -> tuple[_Comparison, ...]:
    """The four comparisons, in the order the worse-field scan must use."""
    return (
        (
            WORSE_TOOL_ERRORS,
            champion.tool_errors,
            challenger.tool_errors,
            DROP_TOOL_ERRORS,
        ),
        (WORSE_CALL_COUNT, champion.call_count, challenger.call_count, DROP_CALL_COUNT),
        (WORSE_TOKENS, champion.tokens, challenger.tokens, DROP_TOKENS),
        (WORSE_LATENCY, champion.latency_s, challenger.latency_s, DROP_LATENCY_S),
    )


def _first_worse(comparisons: Sequence[_Comparison]) -> str | None:
    """The reason named by the first regressing reading, or ``None``."""
    for reason, champion, challenger, drop in comparisons:
        if challenger > champion + drop:
            return reason
    return None


def _has_gain(comparisons: Sequence[_Comparison]) -> bool:
    """Whether any single reading improved beyond its drop threshold."""
    return any(
        challenger < champion - drop for _, champion, challenger, drop in comparisons
    )


def evaluate(
    challenger: Challenger,
    challenger_tree: Path,
    champion_sha: str,
    held_out_cases: Sequence[EvalCase],
    regression_cases: Sequence[EvalCase],
    *,
    runner: ContractRunner,
    replay: ReplayFn,
    seeds: Sequence[int] = DEFAULT_SEEDS,
) -> EvaluationReport:
    """Decide whether *challenger* may replace the champion, and say why.

    The gate short-circuits in four steps:

    1. A graduated regression case fails on ``challenger_tree`` →
       rejected as ``REGRESSION_FAILED``, ``replay`` is never called and
       both sides' metrics are zero. An empty ``regression_cases`` is
       legal — nothing has graduated yet — and passes.
    2. Otherwise the held-out cases are replayed once per seed on each
       side and the seed means compared reading by reading. The first
       reading worse than the champion by more than its ``DROP_*``
       threshold names the reason, in the order ``tool_errors`` →
       ``call_count`` → ``tokens`` → ``latency_s``. A gain elsewhere
       never offsets it: there is no total to trade in.
    3. Nothing worse but nothing better either → ``NO_PRACTICAL_GAIN``.
    4. Otherwise accepted.

    Both comparisons run on the un-rounded means; the report rounds the
    three counts only on the way in, and keeps ``latency_s`` as the mean
    in seconds.

    Args:
        challenger: The checkout under evaluation. Only its ``sha`` is
            read, and only to fill ``candidate_sha``.
        challenger_tree: Tree the challenger is checked out in. Handed to
            ``runner`` and ``replay``; never opened or stat'd here.
        champion_sha: Full sha of the champion. Handed to ``replay``,
            which owns resolving it to a tree.
        held_out_cases: Cases replayed on both sides. Must not be empty.
        regression_cases: Graduated cases run against the challenger
            tree only. May be empty.
        runner: Seam that runs ``regression_cases``. Keyword-only with no
            default: the production runner needs a host.
        replay: Seam that replays ``held_out_cases`` for one side under
            one seed. Keyword-only with no default, for the same reason.
        seeds: Seeds to replay under, once each per side. Defaults to
            :data:`DEFAULT_SEEDS`; must not be empty.

    Returns:
        One :class:`EvaluationReport` carrying the verdict, its reason,
        the seeds used and both sides' mean metrics. No pointer is read
        or written: promotion is a later leaf's job.

    Raises:
        EvaluationError: ``seeds`` or ``held_out_cases`` is empty. Raised
            before ``runner`` or ``replay`` is called, so no report and
            no side effect comes of it.
    """
    if not seeds:
        raise EvaluationError("evaluate needs at least one seed")
    if not held_out_cases:
        raise EvaluationError("evaluate needs at least one held-out case")

    if regression_cases and not runner.run(challenger_tree, regression_cases).passed:
        return EvaluationReport(
            accepted=False,
            reason=REGRESSION_FAILED,
            candidate_sha=challenger.sha,
            champion_sha=champion_sha,
            seeds=tuple(seeds),
            regression_passed=False,
            champion_metrics=_ZERO_METRICS,
            challenger_metrics=_ZERO_METRICS,
        )

    champion_means = _means(
        tuple(replay(champion_sha, held_out_cases, seed) for seed in seeds)
    )
    challenger_means = _means(
        tuple(replay(challenger_tree, held_out_cases, seed) for seed in seeds)
    )

    comparisons = _comparisons(champion_means, challenger_means)
    reason = _first_worse(comparisons)
    if reason is None:
        reason = ACCEPTED if _has_gain(comparisons) else NO_PRACTICAL_GAIN

    return EvaluationReport(
        accepted=reason == ACCEPTED,
        reason=reason,
        candidate_sha=challenger.sha,
        champion_sha=champion_sha,
        seeds=tuple(seeds),
        regression_passed=True,
        champion_metrics=_reported(champion_means),
        challenger_metrics=_reported(challenger_means),
    )
