"""Held-out challenger gate — four independent metrics, never a total score.

Mirrors ``src/molmcp/evolution/evaluate.py``; one class per public behaviour
(``Metrics`` and ``EvaluationReport`` the value objects, ``evaluate`` the
function). ``EvalCase``, ``ContractOutcome`` and the ``Challenger`` /
``ContractRunner`` / ``ReplayFn`` protocols are exercised *through* those
three: they are literals and seams a caller builds, and a test that only
constructed them would pin no behaviour.

Note the naming this module inherits. The duck-typed protocol for "the
checkout under evaluation" is ``Challenger``, because ``Candidate`` is
already a dataclass in :mod:`molmcp.evolution` (spec 10: a proposed patch).
The *report field* is still ``candidate_sha`` — only the protocol was
renamed.

Four disciplines are pinned here that no single assertion makes obvious.

*The float mean decides, the rounded mean is only stored.* Three of the four
metrics are ints, and the report keeps ``round(mean)``; the worse/better
comparison happens on the un-rounded mean. The rounding-trap tests build
sides whose float means differ while their rounded ints are equal, and each
one pairs that hidden move with a visible move in another field, so an
implementation that compares the rounded ints returns a *different verdict*
rather than the same one by luck.

*The regression contract short-circuits.* When ``runner`` says no, ``replay``
is never called and both sides' metrics are zero. The fixture for that test
hands the fake replay a strictly better challenger, so an implementation that
runs the replay anyway accepts instead of rejecting.

*Worse beats better, and the first worse field names the reason.* The four
fields are compared in order ``tool_errors`` → ``call_count`` → ``tokens`` →
``latency_s``; a gain in one field never offsets a regression in another,
because there is no total to trade them in.

*The seams have no default.* ``runner`` and ``replay`` are keyword-only with
no default at all — a default would have to be a real host, which would drag
MCP into this leaf. The fakes here decide which side they were asked about
from the *type* of ``target``: a ``str`` is the champion sha, a ``Path`` is
the challenger tree, so passing the wrong one returns the wrong numbers.

Nothing here reads a clock, the environment, the network, or the filesystem.
``_TREE`` is a literal path that is never created: ``evaluate`` hands it to
the seams, it does not stat it. The only file read is ``evaluate.py`` itself,
and only to prove what it does not import.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from molmcp.evolution.evaluate import (
    ACCEPTED,
    DEFAULT_SEEDS,
    DROP_CALL_COUNT,
    DROP_LATENCY_S,
    DROP_TOKENS,
    DROP_TOOL_ERRORS,
    NO_PRACTICAL_GAIN,
    REGRESSION_FAILED,
    WORSE_CALL_COUNT,
    WORSE_LATENCY,
    WORSE_TOKENS,
    WORSE_TOOL_ERRORS,
    Challenger,
    ContractOutcome,
    ContractRunner,
    EvalCase,
    EvaluationError,
    EvaluationReport,
    Metrics,
    ReplayFn,
    evaluate,
)

_REPO = Path(__file__).resolve().parents[2]
_EVALUATE = _REPO / "src" / "molmcp" / "evolution" / "evaluate.py"

#: The dotted package the module under test lives in, used to resolve the
#: relative imports its purity check has to see through.
_PACKAGE_PARTS: tuple[str, ...] = ("molmcp", "evolution")

#: The two shas the report echoes back, and the challenger's own identity.
#: Full shas: the spec says ``champion_sha`` is the complete string.
_CHAMPION_SHA = "a" * 40
_CHALLENGER_SHA = "b" * 40
_COMPONENT = "daily-pack-skill"

#: Never created on disk. ``evaluate`` passes it to ``runner`` and ``replay``
#: and must not touch it, so a test run needs no ``tmp_path`` at all.
_TREE = Path("/challenger/tree")

#: Held-out cases go to ``replay``; regression cases go to ``runner``. Two
#: distinct sequences, so a swap shows up as the wrong ids on the wrong seam.
_HELD_OUT: tuple[EvalCase, ...] = (EvalCase(id="held-1"), EvalCase(id="held-2"))
_REGRESSION: tuple[EvalCase, ...] = (EvalCase(id="reg-1"),)
_HELD_OUT_IDS: tuple[str, ...] = ("held-1", "held-2")
_REGRESSION_IDS: tuple[str, ...] = ("reg-1",)

#: ``Metrics`` fields, in the order the spec's value-object table lists them —
#: which is also the order the worse-field scan must use.
_METRICS_FIELDS: tuple[str, ...] = (
    "tool_errors",
    "call_count",
    "tokens",
    "latency_s",
)

#: ``EvaluationReport`` fields, in the spec's order.
_REPORT_FIELDS: tuple[str, ...] = (
    "accepted",
    "reason",
    "candidate_sha",
    "champion_sha",
    "seeds",
    "regression_passed",
    "champion_metrics",
    "challenger_metrics",
)

#: Names that would turn four independent metrics back into one number.
#: Forbidden as attributes, not merely unused.
_ABSENT_ON_METRICS: tuple[str, ...] = (
    "score",
    "total",
    "weighted_sum",
    "composite",
    "f1",
    "rank",
)

#: Names that would make the report a pointer writer. Promotion is spec 12;
#: this report is a verdict and nothing else.
_ABSENT_ON_REPORT: tuple[str, ...] = (
    "pointer",
    "active",
    "previous",
    "stage",
    "score",
)

#: The seven frozen reason literals, spelling included. 12-promote and
#: 13-ci-gate match on these strings; a synonym is a break.
_REASONS: tuple[tuple[str, str], ...] = (
    (ACCEPTED, "accepted"),
    (REGRESSION_FAILED, "regression_failed"),
    (WORSE_TOOL_ERRORS, "worse_tool_errors"),
    (WORSE_CALL_COUNT, "worse_call_count"),
    (WORSE_TOKENS, "worse_tokens"),
    (WORSE_LATENCY, "worse_latency"),
    (NO_PRACTICAL_GAIN, "no_practical_gain"),
)

#: Layers this leaf may not reach for. The first four are the runtime it must
#: stay out of; the last two are the MCP machinery a default seam would drag
#: in.
_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "molmcp.cli",
    "molmcp.server",
    "molmcp.providers",
    "molmcp.collection",
    "fastmcp",
    "mcp",
)


def _metrics(
    *,
    tool_errors: int = 2,
    call_count: int = 10,
    tokens: int = 100,
    latency_s: float = 1.0,
) -> Metrics:
    """The baseline reading, with at most one field swapped out."""
    return Metrics(
        tool_errors=tool_errors,
        call_count=call_count,
        tokens=tokens,
        latency_s=latency_s,
    )


#: What both sides read when a test is not saying anything about a field.
_BASELINE = _metrics()

#: Strictly better than ``_BASELINE`` on one field, equal on the rest.
_BETTER = _metrics(tool_errors=1)

#: What the report must carry when the regression contract short-circuits.
_ZERO = _metrics(tool_errors=0, call_count=0, tokens=0, latency_s=0.0)

#: Arithmetic-mean comparison tolerance. ``latency_s`` is a mean of literal
#: floats, not a measured quantity, so only representation error is allowed.
_LATENCY_TOL = 1e-12


class FakeChallenger:
    """Stand-in for the ``Challenger`` protocol — three read-only attributes.

    Deliberately not a subclass: the protocol is duck-typed, and a fake that
    inherited it would hide a rename of any of the three names.
    """

    def __init__(
        self,
        sha: str = _CHALLENGER_SHA,
        component: str = _COMPONENT,
        affected_paths: Sequence[str] = ("skills/daily/pack.md",),
    ) -> None:
        self.sha: str = sha
        self.component: str = component
        self.affected_paths: tuple[str, ...] = tuple(affected_paths)


class FakeRunner:
    """Stand-in for ``ContractRunner`` — records ``run``, replays one outcome."""

    def __init__(self, outcome: ContractOutcome) -> None:
        self._outcome = outcome
        self.calls: list[tuple[Path, tuple[str, ...]]] = []

    def run(self, tree: Path, cases: Sequence[EvalCase]) -> ContractOutcome:
        self.calls.append((tree, tuple(case.id for case in cases)))
        return self._outcome


class FakeReplay:
    """Stand-in for ``ReplayFn`` — one metrics table per side, keyed by seed.

    ``target`` decides the side: a ``str`` is the champion sha, a ``Path`` is
    the challenger tree. An implementation that hands over the wrong type
    reads the wrong side's numbers, so the seam's types are pinned by every
    verdict here as well as by ``test_replay_gets_a_sha_then_a_tree``.
    """

    def __init__(
        self,
        champion: Mapping[int, Metrics],
        challenger: Mapping[int, Metrics],
    ) -> None:
        self._champion = dict(champion)
        self._challenger = dict(challenger)
        self.calls: list[tuple[str | Path, tuple[str, ...], int]] = []

    def __call__(
        self, target: str | Path, cases: Sequence[EvalCase], seed: int
    ) -> Metrics:
        self.calls.append((target, tuple(case.id for case in cases), seed))
        table = self._challenger if isinstance(target, Path) else self._champion
        if seed not in table:
            raise AssertionError(
                f"replay called with unexpected seed {seed!r}; "
                f"table has {sorted(table)}"
            )
        return table[seed]

    @property
    def seeds_seen(self) -> list[int]:
        return [seed for _, _, seed in self.calls]


def _challenger(sha: str = _CHALLENGER_SHA) -> Challenger:
    return FakeChallenger(sha=sha)


def _runner(passed: bool = True, failed_case_ids: Sequence[str] = ()) -> FakeRunner:
    return FakeRunner(
        ContractOutcome(passed=passed, failed_case_ids=tuple(failed_case_ids))
    )


def _flat(metrics: Metrics, seeds: Sequence[int] = DEFAULT_SEEDS) -> dict[int, Metrics]:
    """One reading repeated for every seed."""
    return {seed: metrics for seed in seeds}


def _per_seed(
    readings: Sequence[Metrics], seeds: Sequence[int] = DEFAULT_SEEDS
) -> dict[int, Metrics]:
    """One reading per seed, paired positionally."""
    return dict(zip(seeds, readings, strict=True))


def _replay(
    champion: Mapping[int, Metrics] | None = None,
    challenger: Mapping[int, Metrics] | None = None,
) -> FakeReplay:
    """Both sides flat on ``_BASELINE`` unless a test says otherwise."""
    return FakeReplay(
        _flat(_BASELINE) if champion is None else champion,
        _flat(_BASELINE) if challenger is None else challenger,
    )


def _evaluate(
    runner: FakeRunner,
    replay: FakeReplay,
    *,
    seeds: Sequence[int] | None = None,
    held_out_cases: Sequence[EvalCase] = _HELD_OUT,
    regression_cases: Sequence[EvalCase] = _REGRESSION,
    challenger_tree: Path = _TREE,
) -> EvaluationReport:
    """Call ``evaluate`` by keyword, omitting ``seeds`` entirely when ``None``."""
    if seeds is None:
        return evaluate(
            challenger=_challenger(),
            challenger_tree=challenger_tree,
            champion_sha=_CHAMPION_SHA,
            held_out_cases=held_out_cases,
            regression_cases=regression_cases,
            runner=runner,
            replay=replay,
        )
    return evaluate(
        challenger=_challenger(),
        challenger_tree=challenger_tree,
        champion_sha=_CHAMPION_SHA,
        held_out_cases=held_out_cases,
        regression_cases=regression_cases,
        runner=runner,
        replay=replay,
        seeds=seeds,
    )


def _kwargs_without(
    seam: str, runner: FakeRunner, replay: FakeReplay
) -> dict[str, object]:
    """Every argument ``evaluate`` needs, minus one injected seam."""
    kwargs: dict[str, object] = {
        "challenger": _challenger(),
        "challenger_tree": _TREE,
        "champion_sha": _CHAMPION_SHA,
        "held_out_cases": _HELD_OUT,
        "regression_cases": _REGRESSION,
        "runner": runner,
        "replay": replay,
    }
    del kwargs[seam]
    return kwargs


def _valid_report(
    *,
    accepted: bool = True,
    reason: str = ACCEPTED,
    candidate_sha: str = _CHALLENGER_SHA,
    champion_sha: str = _CHAMPION_SHA,
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
    regression_passed: bool = True,
    champion_metrics: Metrics = _BASELINE,
    challenger_metrics: Metrics = _BETTER,
) -> EvaluationReport:
    """An accepted report, with at most one invariant swapped out."""
    return EvaluationReport(
        accepted=accepted,
        reason=reason,
        candidate_sha=candidate_sha,
        champion_sha=champion_sha,
        seeds=seeds,
        regression_passed=regression_passed,
        champion_metrics=champion_metrics,
        challenger_metrics=challenger_metrics,
    )


def _evaluate_source() -> str:
    assert _EVALUATE.is_file(), f"{_EVALUATE} does not exist yet"
    return _EVALUATE.read_text(encoding="utf-8")


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


#: One field worse than ``_BASELINE``, the rest equal → the reason it names.
_WORSE_CASES = (
    pytest.param(_metrics(tool_errors=3), WORSE_TOOL_ERRORS, id="tool_errors"),
    pytest.param(_metrics(call_count=11), WORSE_CALL_COUNT, id="call_count"),
    pytest.param(_metrics(tokens=101), WORSE_TOKENS, id="tokens"),
    pytest.param(_metrics(latency_s=1.5), WORSE_LATENCY, id="latency_s"),
)

#: Several fields worse at once → the *first* in field order names the reason.
#: An implementation scanning in any other order fails at least one of these.
_PRECEDENCE_CASES = (
    pytest.param(
        _metrics(tool_errors=3, call_count=11),
        WORSE_TOOL_ERRORS,
        id="tool_errors-before-call_count",
    ),
    pytest.param(
        _metrics(tool_errors=3, latency_s=1.5),
        WORSE_TOOL_ERRORS,
        id="tool_errors-before-latency_s",
    ),
    pytest.param(
        _metrics(call_count=11, tokens=101),
        WORSE_CALL_COUNT,
        id="call_count-before-tokens",
    ),
    pytest.param(
        _metrics(tokens=101, latency_s=1.5),
        WORSE_TOKENS,
        id="tokens-before-latency_s",
    ),
    pytest.param(
        _metrics(tool_errors=3, call_count=11, tokens=101, latency_s=1.5),
        WORSE_TOOL_ERRORS,
        id="all-four-worse",
    ),
)

#: One field better than ``_BASELINE``, the rest equal → accepted.
_BETTER_CASES = (
    pytest.param(_metrics(tool_errors=1), id="tool_errors"),
    pytest.param(_metrics(call_count=9), id="call_count"),
    pytest.param(_metrics(tokens=99), id="tokens"),
    pytest.param(_metrics(latency_s=0.5), id="latency_s"),
)

#: Report shapes the ``__post_init__`` must make unrepresentable.
_ILLEGAL_REPORTS = (
    pytest.param(
        {"accepted": True, "regression_passed": False},
        id="accepted-while-regression-failed",
    ),
    pytest.param(
        {"accepted": True, "reason": REGRESSION_FAILED},
        id="accepted-reading-regression_failed",
    ),
    pytest.param(
        {"accepted": True, "reason": WORSE_TOOL_ERRORS},
        id="accepted-reading-worse_tool_errors",
    ),
    pytest.param(
        {"accepted": True, "reason": NO_PRACTICAL_GAIN},
        id="accepted-reading-no_practical_gain",
    ),
    pytest.param(
        {"accepted": False, "reason": ACCEPTED},
        id="rejected-reading-accepted",
    ),
)

#: Report shapes that must stay representable — in particular a rejection
#: whose regression suite *passed*, which is every step-2 and step-3 verdict.
_LEGAL_REPORTS = (
    pytest.param(
        {"accepted": True, "reason": ACCEPTED, "regression_passed": True},
        id="accepted",
    ),
    pytest.param(
        {"accepted": False, "reason": REGRESSION_FAILED, "regression_passed": False},
        id="regression-failed",
    ),
    pytest.param(
        {"accepted": False, "reason": WORSE_TOKENS, "regression_passed": True},
        id="worse-though-regression-passed",
    ),
    pytest.param(
        {"accepted": False, "reason": NO_PRACTICAL_GAIN, "regression_passed": True},
        id="no-practical-gain",
    ),
)


class TestMetrics:
    def test_carries_the_four_readings_it_was_given(self) -> None:
        metrics = _metrics(tool_errors=3, call_count=12, tokens=345, latency_s=2.5)

        assert metrics.tool_errors == 3
        assert metrics.call_count == 12
        assert metrics.tokens == 345
        assert metrics.latency_s == pytest.approx(2.5, abs=_LATENCY_TOL)

    def test_field_names_are_the_value_object_table_in_order(self) -> None:
        names = tuple(field.name for field in dataclasses.fields(Metrics))

        assert names == _METRICS_FIELDS

    def test_has_exactly_four_fields(self) -> None:
        assert len(dataclasses.fields(Metrics)) == 4

    @pytest.mark.parametrize("name", _ABSENT_ON_METRICS)
    def test_carries_no_composite_number(self, name: str) -> None:
        """Four independent metrics, never summed, weighted, or ranked."""
        assert not hasattr(Metrics, name)
        assert not hasattr(_metrics(), name)

    def test_the_class_carries_no_helper_beyond_its_four_fields(self) -> None:
        """A weighted-sum method would show up here as a fifth public name."""
        public = {name for name in vars(Metrics) if not name.startswith("_")}

        assert public == set(_METRICS_FIELDS)

    @pytest.mark.parametrize("field_name", _METRICS_FIELDS)
    def test_is_frozen(self, field_name: str) -> None:
        metrics = _metrics()

        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(metrics, field_name, 99)

    def test_uses_slots(self) -> None:
        assert hasattr(Metrics, "__slots__")
        assert not hasattr(_metrics(), "__dict__")

    def test_equal_readings_compare_equal(self) -> None:
        """Verdicts are read off equality; a reading is its four numbers."""
        assert _metrics() == _metrics()
        assert _metrics() != _metrics(tokens=101)


class TestEvaluationReport:
    def test_carries_the_eight_fields_it_was_given(self) -> None:
        report = _valid_report()

        assert report.accepted is True
        assert report.reason == ACCEPTED
        assert report.candidate_sha == _CHALLENGER_SHA
        assert report.champion_sha == _CHAMPION_SHA
        assert report.seeds == DEFAULT_SEEDS
        assert report.regression_passed is True
        assert report.champion_metrics == _BASELINE
        assert report.challenger_metrics == _BETTER

    def test_field_names_are_the_value_object_table_in_order(self) -> None:
        """The protocol was renamed to ``Challenger``; the field was not."""
        names = tuple(field.name for field in dataclasses.fields(EvaluationReport))

        assert names == _REPORT_FIELDS

    def test_has_exactly_eight_fields(self) -> None:
        assert len(dataclasses.fields(EvaluationReport)) == 8

    @pytest.mark.parametrize("name", _ABSENT_ON_REPORT)
    def test_carries_no_pointer_or_score(self, name: str) -> None:
        """A verdict, not a promotion: moving the pointer is spec 12's job."""
        assert not hasattr(EvaluationReport, name)
        assert not hasattr(_valid_report(), name)
        assert name not in _REPORT_FIELDS

    @pytest.mark.parametrize("overrides", _LEGAL_REPORTS)
    def test_consistent_verdicts_are_representable(
        self, overrides: dict[str, object]
    ) -> None:
        report = _valid_report(**overrides)

        assert report.accepted is overrides["accepted"]
        assert report.reason == overrides["reason"]
        assert report.regression_passed is overrides["regression_passed"]

    @pytest.mark.parametrize("overrides", _ILLEGAL_REPORTS)
    def test_inconsistent_verdicts_are_unrepresentable(
        self, overrides: dict[str, object]
    ) -> None:
        with pytest.raises(EvaluationError):
            _valid_report(**overrides)

    @pytest.mark.parametrize("field_name", _REPORT_FIELDS)
    def test_is_frozen(self, field_name: str) -> None:
        report = _valid_report()

        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(report, field_name, "mutated")

    def test_uses_slots(self) -> None:
        assert hasattr(EvaluationReport, "__slots__")
        assert not hasattr(_valid_report(), "__dict__")


class TestEvaluate:
    # -- constants ---------------------------------------------------------

    def test_the_default_seeds_are_frozen_at_one_two_three(self) -> None:
        assert DEFAULT_SEEDS == (1, 2, 3)
        assert isinstance(DEFAULT_SEEDS, tuple)

    def test_the_drop_thresholds_are_zero(self) -> None:
        """No noise band: any move in the wrong direction is a regression."""
        assert DROP_TOOL_ERRORS == 0
        assert DROP_CALL_COUNT == 0
        assert DROP_TOKENS == 0
        assert DROP_LATENCY_S == 0.0
        assert isinstance(DROP_LATENCY_S, float)

    @pytest.mark.parametrize(
        ("literal", "expected"), _REASONS, ids=[text for _, text in _REASONS]
    )
    def test_the_reason_literals_are_frozen(self, literal: str, expected: str) -> None:
        assert literal == expected

    def test_the_reasons_are_seven_distinct_strings(self) -> None:
        assert len({literal for literal, _ in _REASONS}) == 7

    def test_the_error_is_a_value_error(self) -> None:
        assert issubclass(EvaluationError, ValueError)

    # -- the injected seams ------------------------------------------------

    def test_the_fakes_satisfy_the_injected_seams(self) -> None:
        """The seam shapes this module injects, stated as types once."""
        runner: ContractRunner = _runner()
        replay: ReplayFn = _replay()

        assert runner.run(_TREE, _REGRESSION).passed is True
        assert replay(_CHAMPION_SHA, _HELD_OUT, 1) == _BASELINE

    def test_runner_and_replay_are_keyword_only_without_a_default(self) -> None:
        """A default seam would have to be a real host — that is 13's job."""
        params = inspect.signature(evaluate).parameters

        for name in ("runner", "replay"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert params[name].default is inspect.Parameter.empty
        assert params["seeds"].kind is inspect.Parameter.KEYWORD_ONLY

    @pytest.mark.parametrize("seam", ["runner", "replay"])
    def test_omitting_a_seam_is_a_type_error(self, seam: str) -> None:
        runner = _runner()
        replay = _replay()

        with pytest.raises(TypeError):
            evaluate(**_kwargs_without(seam, runner, replay))

        assert runner.calls == []
        assert replay.calls == []

    # -- refusals ----------------------------------------------------------

    def test_empty_seeds_raises_before_anything_runs(self) -> None:
        """Immediately means immediately: no contract run, and no report."""
        runner = _runner()
        replay = _replay()

        with pytest.raises(EvaluationError):
            _evaluate(runner, replay, seeds=())

        assert runner.calls == []
        assert replay.calls == []

    def test_empty_held_out_cases_raises_before_anything_runs(self) -> None:
        """This is the held-out gate, not a contract-only channel."""
        runner = _runner()
        replay = _replay()

        with pytest.raises(EvaluationError):
            _evaluate(runner, replay, held_out_cases=())

        assert runner.calls == []
        assert replay.calls == []

    def test_no_regression_cases_yet_is_legal_and_passes(self) -> None:
        replay = _replay(challenger=_flat(_BETTER))

        report = _evaluate(_runner(), replay, regression_cases=())

        assert report.regression_passed is True
        assert report.accepted is True
        assert report.reason == ACCEPTED

    # -- step 1: the regression contract short-circuits ---------------------

    def test_a_failed_contract_rejects_without_replaying(self) -> None:
        """The challenger table here is strictly better, and never read."""
        runner = _runner(passed=False, failed_case_ids=("reg-1",))
        replay = _replay(challenger=_flat(_BETTER))

        report = _evaluate(runner, replay)

        assert report.accepted is False
        assert report.reason == REGRESSION_FAILED
        assert report.regression_passed is False
        assert report.champion_metrics == _ZERO
        assert report.challenger_metrics == _ZERO
        assert replay.calls == []
        assert report.candidate_sha == _CHALLENGER_SHA
        assert report.champion_sha == _CHAMPION_SHA

    def test_the_contract_runs_on_the_tree_with_the_regression_cases(self) -> None:
        runner = _runner()

        _evaluate(runner, _replay(challenger=_flat(_BETTER)))

        assert runner.calls == [(_TREE, _REGRESSION_IDS)]

    # -- step 2: the replay ------------------------------------------------

    def test_replay_runs_once_per_seed_for_each_side(self) -> None:
        replay = _replay(challenger=_flat(_BETTER))

        _evaluate(_runner(), replay)

        assert len(replay.calls) == 2 * len(DEFAULT_SEEDS)
        assert sorted(replay.seeds_seen) == sorted(DEFAULT_SEEDS + DEFAULT_SEEDS)

    def test_replay_gets_a_sha_then_a_tree(self) -> None:
        """Champion by full sha, challenger by checked-out tree."""
        replay = _replay(challenger=_flat(_BETTER))

        _evaluate(_runner(), replay)

        targets = [target for target, _, _ in replay.calls]
        champion_targets = [t for t in targets if isinstance(t, str)]
        challenger_targets = [t for t in targets if isinstance(t, Path)]

        assert champion_targets == [_CHAMPION_SHA] * len(DEFAULT_SEEDS)
        assert challenger_targets == [_TREE] * len(DEFAULT_SEEDS)
        assert len(targets) == len(champion_targets) + len(challenger_targets)

    def test_replay_gets_the_held_out_cases_not_the_regression_ones(self) -> None:
        replay = _replay(challenger=_flat(_BETTER))

        _evaluate(_runner(), replay)

        assert [ids for _, ids, _ in replay.calls] == [_HELD_OUT_IDS] * 6

    @pytest.mark.parametrize(("challenger_metrics", "reason"), _WORSE_CASES)
    def test_one_worse_field_rejects_and_names_itself(
        self, challenger_metrics: Metrics, reason: str
    ) -> None:
        replay = _replay(challenger=_flat(challenger_metrics))

        report = _evaluate(_runner(), replay)

        assert report.accepted is False
        assert report.reason == reason

    @pytest.mark.parametrize(("challenger_metrics", "reason"), _PRECEDENCE_CASES)
    def test_the_first_worse_field_in_order_names_the_reason(
        self, challenger_metrics: Metrics, reason: str
    ) -> None:
        replay = _replay(challenger=_flat(challenger_metrics))

        report = _evaluate(_runner(), replay)

        assert report.accepted is False
        assert report.reason == reason

    def test_a_gain_never_offsets_a_regression(self) -> None:
        """No total score means no trade: the worse field still decides."""
        replay = _replay(challenger=_flat(_metrics(tool_errors=1, latency_s=1.5)))

        report = _evaluate(_runner(), replay)

        assert report.accepted is False
        assert report.reason == WORSE_LATENCY

    # -- steps 3 and 4: the verdict ----------------------------------------

    def test_no_move_at_all_is_not_a_gain(self) -> None:
        replay = _replay()

        report = _evaluate(_runner(), replay)

        assert report.accepted is False
        assert report.reason == NO_PRACTICAL_GAIN
        assert report.regression_passed is True

    @pytest.mark.parametrize("challenger_metrics", _BETTER_CASES)
    def test_one_better_field_and_no_worse_one_accepts(
        self, challenger_metrics: Metrics
    ) -> None:
        replay = _replay(challenger=_flat(challenger_metrics))

        report = _evaluate(_runner(), replay)

        assert report.accepted is True
        assert report.reason == ACCEPTED
        assert report.regression_passed is True
        assert report.candidate_sha == _CHALLENGER_SHA
        assert report.champion_sha == _CHAMPION_SHA

    # -- the report --------------------------------------------------------

    def test_the_report_records_the_default_seeds_when_none_are_given(self) -> None:
        report = _evaluate(_runner(), _replay(challenger=_flat(_BETTER)))

        assert report.seeds == DEFAULT_SEEDS

    def test_the_report_records_the_seeds_actually_used(self) -> None:
        seeds = [7, 11]
        replay = FakeReplay(_flat(_BASELINE, seeds), _flat(_BETTER, seeds))

        report = _evaluate(_runner(), replay, seeds=seeds)

        assert report.seeds == (7, 11)
        assert isinstance(report.seeds, tuple)
        assert sorted(replay.seeds_seen) == [7, 7, 11, 11]
        assert report.accepted is True

    def test_the_reports_metrics_are_the_seed_means(self) -> None:
        """Int fields keep ``round(mean)``; ``latency_s`` keeps the mean."""
        replay = _replay(
            champion=_per_seed(
                (
                    _metrics(tool_errors=2, call_count=10, tokens=100, latency_s=1.0),
                    _metrics(tool_errors=2, call_count=11, tokens=100, latency_s=1.0),
                    _metrics(tool_errors=3, call_count=11, tokens=101, latency_s=1.3),
                )
            ),
            challenger=_flat(
                _metrics(tool_errors=0, call_count=5, tokens=50, latency_s=0.5)
            ),
        )

        report = _evaluate(_runner(), replay)

        assert report.champion_metrics.tool_errors == 2  # mean 2.333…
        assert report.champion_metrics.call_count == 11  # mean 10.667…
        assert report.champion_metrics.tokens == 100  # mean 100.333…
        assert report.champion_metrics.latency_s == pytest.approx(1.1, abs=_LATENCY_TOL)
        assert report.challenger_metrics == _metrics(
            tool_errors=0, call_count=5, tokens=50, latency_s=0.5
        )
        assert report.accepted is True

    # -- the rounding trap -------------------------------------------------

    def test_a_regression_hidden_by_rounding_still_rejects(self) -> None:
        """``call_count`` means 10.0 vs 10.333…; both round to 10.

        ``tokens`` improves, so an implementation that compares the *rounded*
        ints sees one gain and no regression and accepts this fixture.
        """
        replay = _replay(
            champion=_flat(_metrics(call_count=10, tokens=100)),
            challenger=_per_seed(
                (
                    _metrics(call_count=10, tokens=90),
                    _metrics(call_count=10, tokens=90),
                    _metrics(call_count=11, tokens=90),
                )
            ),
        )

        report = _evaluate(_runner(), replay)

        assert report.accepted is False
        assert report.reason == WORSE_CALL_COUNT
        assert report.champion_metrics.call_count == 10
        assert report.challenger_metrics.call_count == 10

    def test_a_tool_error_regression_hidden_by_rounding_still_rejects(self) -> None:
        """``tool_errors`` means 0.0 vs 0.333…; both round to 0."""
        replay = _replay(
            champion=_flat(_metrics(tool_errors=0, tokens=100)),
            challenger=_per_seed(
                (
                    _metrics(tool_errors=0, tokens=90),
                    _metrics(tool_errors=0, tokens=90),
                    _metrics(tool_errors=1, tokens=90),
                )
            ),
        )

        report = _evaluate(_runner(), replay)

        assert report.accepted is False
        assert report.reason == WORSE_TOOL_ERRORS
        assert report.champion_metrics.tool_errors == 0
        assert report.challenger_metrics.tool_errors == 0

    def test_a_token_regression_hidden_by_rounding_still_rejects(self) -> None:
        """``tokens`` means 100.0 vs 100.333…; both round to 100."""
        replay = _replay(
            champion=_flat(_metrics(tool_errors=2, tokens=100)),
            challenger=_per_seed(
                (
                    _metrics(tool_errors=1, tokens=100),
                    _metrics(tool_errors=1, tokens=100),
                    _metrics(tool_errors=1, tokens=101),
                )
            ),
        )

        report = _evaluate(_runner(), replay)

        assert report.accepted is False
        assert report.reason == WORSE_TOKENS
        assert report.champion_metrics.tokens == 100
        assert report.challenger_metrics.tokens == 100

    def test_a_gain_hidden_by_rounding_still_accepts(self) -> None:
        """The trap in the other direction: 10.333… → 10.0 is a real gain.

        Nothing else moves, so an implementation comparing the rounded ints
        sees 10 against 10 and calls it ``no_practical_gain``.
        """
        replay = _replay(
            champion=_per_seed(
                (
                    _metrics(call_count=10),
                    _metrics(call_count=10),
                    _metrics(call_count=11),
                )
            ),
            challenger=_flat(_metrics(call_count=10)),
        )

        report = _evaluate(_runner(), replay)

        assert report.accepted is True
        assert report.reason == ACCEPTED
        assert report.champion_metrics.call_count == 10
        assert report.challenger_metrics.call_count == 10

    # -- isolation ---------------------------------------------------------

    def test_the_source_imports_no_runtime_or_mcp(self) -> None:
        modules = _module_level_imports(ast.parse(_evaluate_source()))

        offenders = sorted(
            name
            for name in modules
            if any(
                name == prefix or name.startswith(f"{prefix}.")
                for prefix in _FORBIDDEN_IMPORT_PREFIXES
            )
        )

        assert offenders == []

    def test_the_gate_is_not_an_mcp_tool(self) -> None:
        """Imported here rather than at module level: the leaf owes it nothing."""
        import molmcp

        for name in ("evaluate", "EvaluationReport", "ContractRunner", "Metrics"):
            assert name not in molmcp.__all__
