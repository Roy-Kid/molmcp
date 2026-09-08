"""The observation adapter: a blind transcript reading turned into a verdict.

Mirrors ``scripts/harness_eval.py`` — the one thin entry that hands an
observer subagent's structured output to the already-shipped
:func:`molmcp.evolution.evaluate`. ``scripts/`` is flat rather than a
package, so the mirrored unit path is this single ``tests/`` module; one
class per behaviour the adapter owns, and nothing here starts an agent,
a host or a process.

Four disciplines are pinned here that no single assertion makes obvious.

*The manifest unblinds, the observer never does.* The observation carries
only the blind labels ``A`` / ``B``; which one is the challenger comes
from ``manifest["sides"]``. An observation that so much as names a side
is refused before the store is touched, and the positive proof is
``test_swapping_the_sides_flips_the_verdict``: one observation read twice
under swapped manifests must come out ``ACCEPTED`` one way and
``WORSE_CALL_COUNT`` the other. A reader who assigned sides from the
payload would get the same verdict twice.

*Giving up must not read cheap.* An unfinished round makes fewer calls
and fewer errors, so averaging a held-out reading with ``contract_met``
false in would make abandonment look like a gain. The guard test builds
exactly that shape — the abandoned cell also carries the lowest
``call_count`` in the whole observation — and the negative control flips
that one field to true and gets ``ACCEPTED``, which is precisely the
false win the refusal exists to stop.

*Two readings cannot be read off a transcript.* ``tokens`` and
``latency_s`` are refused on the way in and pinned to zero on the way
out: under ``evaluate``'s independent comparisons, 0 against 0 is the
only value that neither convicts nor acquits. Permitting the observer to
write them invites the next one to guess a number.

*The readings are sums of held-out cases only.* A graduated case is a
correctness contract, not a reading; its counts are loud here (a
``call_count`` of ``_GRADUATED_CALL_COUNT``) so an implementation that
summed them in cannot land on the expected number by luck.

The case ids come from ``harness_cases.CASES`` rather than literals: the
suite is data that may still grow, and only the deliberately unknown id
is spelled out. Everything outbound is a fake — a store that records its
``tree_path`` calls, a spy that stands in for ``ObservedReplay`` and
counts every dispatch. ``report`` must build its replay by looking up the
module-global ``ObservedReplay``, which is what lets the short-circuit
test prove zero calls. No network, no git, no subprocess, no environment
variable, and the trees are literal paths that are never created.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import harness_eval
import pytest
from harness_cases import CASES
from harness_eval import (
    ObservedChallenger,
    ObservedReplay,
    ObservedRunner,
    main,
    report,
)

from molmcp.components import UnknownShaError
from molmcp.evolution import (
    ACCEPTED,
    REGRESSION_FAILED,
    WORSE_CALL_COUNT,
    EvalCase,
    EvaluationError,
    EvaluationReport,
    Metrics,
)

_REPO = Path(__file__).resolve().parents[1]
_SOURCE = _REPO / "scripts" / "harness_eval.py"

#: Read from the case set, never spelled out: which ids are held-out and
#: which have graduated is the case module's decision, not this module's.
_HELD_OUT_IDS: tuple[str, ...] = tuple(
    str(case["id"]) for case in CASES if not case["graduated"]
)
_GRADUATED_IDS: tuple[str, ...] = tuple(
    str(case["id"]) for case in CASES if case["graduated"]
)

#: How many held-out cases one side's per-seed reading sums over.
_HELD_OUT_COUNT = len(_HELD_OUT_IDS)

#: The one id that must *not* resolve. A typo silently averaged into the
#: mean is worse than a refusal, so this is the only literal id here.
_UNKNOWN_CASE_ID = "no-such-case"

#: The blind labels the observer is allowed to use, and nothing else.
_LABELS: tuple[str, ...] = ("A", "B")

_SCHEMA = "harness-eval/1"

#: Full shas: the manifest carries complete 40-character strings.
_CHAMPION_SHA = "a" * 40
_CHALLENGER_SHA = "b" * 40
_COMPONENT = "daily-pack-skill"
_AFFECTED_PATHS: tuple[str, ...] = ("skills/daily/pack.md",)

#: Never created on disk. The store hands them over, ``evaluate`` passes
#: them to the seams, and nothing stats them.
_CHAMPION_TREE = Path("/published/champion/tree")
_CHALLENGER_TREE = Path("/published/challenger/tree")
_TREES: Mapping[str, Path] = {
    _CHAMPION_SHA: _CHAMPION_TREE,
    _CHALLENGER_SHA: _CHALLENGER_TREE,
}

#: Repeat rounds, not random seeds: the same prompt run three times.
_SEEDS: tuple[int, ...] = (1, 2, 3)

#: Two rounds whose numbers are not ``DEFAULT_SEEDS``, so a report that
#: echoes them back cannot have fallen through to the default.
_ODD_SEEDS: tuple[int, ...] = (2, 5)

_SIDES: Mapping[str, str] = {"A": "champion", "B": "challenger"}
_SIDES_SWAPPED: Mapping[str, str] = {"A": "challenger", "B": "champion"}

#: Per held-out reading. One side cheaper than the other by one call per
#: case is the whole difference in the accepted fixture.
_TIED_CALLS = 6
_CHEAPER_CALLS = 5
_TIED_ERRORS = 1

#: What an abandoned round reads like: the cheapest cell in the payload.
_ABANDONED_CALLS = 1

#: Graduated rows are loud on purpose. Summing them into a reading would
#: move the reported mean by hundreds, not by a rounding step.
_GRADUATED_CALL_COUNT = 1000
_GRADUATED_TOOL_ERRORS = 50

#: The two per-seed held-out counts of the seeds fixture, and the mean
#: written out on its own rather than derived from them.
_SLOW_SEED_CALLS = 8
_MEAN_CALLS = 7
_FEW_ERRORS = 2
_MANY_ERRORS = 4
_MEAN_ERRORS = 3

#: Distinct readings for the dispatch test: whichever table
#: ``ObservedReplay`` picks is visible in the numbers it returns.
_CHAMPION_READING = Metrics(tool_errors=3, call_count=13, tokens=0, latency_s=0.0)
_CHALLENGER_READING = Metrics(tool_errors=1, call_count=7, tokens=0, latency_s=0.0)
_CHAMPION_TABLE: Mapping[int, Metrics] = {seed: _CHAMPION_READING for seed in _SEEDS}
_CHALLENGER_TABLE: Mapping[int, Metrics] = {
    seed: _CHALLENGER_READING for seed in _SEEDS
}
_REPLAY_CASES: tuple[EvalCase, ...] = tuple(
    EvalCase(id=case_id) for case_id in _HELD_OUT_IDS
)

#: The seven frozen reason literals in their *quoted* form. A bare scan
#: for ``accepted`` would ban ``report.accepted``, which is a field read,
#: and push a legitimate ``main`` into ``getattr`` to pass this check.
_REASON_WORDS: tuple[str, ...] = (
    "accepted",
    "worse_tool_errors",
    "worse_call_count",
    "worse_tokens",
    "worse_latency",
    "no_practical_gain",
    "regression_failed",
)
_QUOTED_REASONS: tuple[str, ...] = tuple(f'"{word}"' for word in _REASON_WORDS) + tuple(
    f"'{word}'" for word in _REASON_WORDS
)

#: A second threshold, a second telemetry source, or a second way to get
#: a tree. Each one would make two runs of this evaluator incomparable.
_FORBIDDEN_FRAGMENTS: tuple[str, ...] = (
    "DROP_",
    "os.environ",
    "getenv",
    "anthropic",
    "subprocess",
    "shutil",
    "tarfile",
)

#: Scanned as words, not substrings: ``digit`` must not read as ``git``
#: and ``underscore`` must not read as ``score``.
_FORBIDDEN_WORDS: tuple[str, ...] = (r"\bgit\b", r"(?i)\bscores?\b")

#: The three flags ``main`` must require, none of them defaulted.
_MAIN_FLAGS: tuple[str, ...] = ("--observation", "--manifest", "--store-root")


class FakeStore:
    """Stand-in for ``ImmutableGitStore`` — one table lookup, recorded.

    Deliberately not a subclass: the adapter reads exactly one method,
    and a fake that inherited the real store would hide a rename of it.
    """

    def __init__(self, trees: Mapping[str, Path]) -> None:
        self._trees = dict(trees)
        self.calls: list[str] = []

    def tree_path(self, sha: str) -> Path:
        self.calls.append(sha)
        if sha not in self._trees:
            raise UnknownShaError(sha)
        return self._trees[sha]


class SpyReplay:
    """One ``ObservedReplay`` wrapped so every dispatch is recorded."""

    def __init__(
        self,
        inner: ObservedReplay,
        calls: list[tuple[str | Path, tuple[str, ...], int]],
    ) -> None:
        self._inner = inner
        self._calls = calls

    def __call__(
        self, target: str | Path, cases: Sequence[EvalCase], seed: int
    ) -> Metrics:
        self._calls.append((target, tuple(case.id for case in cases), seed))
        return self._inner(target, cases, seed)

    @property
    def calls(self) -> list[tuple[str | Path, tuple[str, ...], int]]:
        return list(self._calls)


class SpyReplayFactory:
    """Stands in for the ``ObservedReplay`` class inside ``report``.

    Every instance it hands out delegates to the real ``ObservedReplay``
    and appends to one shared ``calls`` list, so a test can prove the
    replay was never reached at all.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str | Path, tuple[str, ...], int]] = []

    def __call__(self, *args: object, **kwargs: object) -> SpyReplay:
        return SpyReplay(ObservedReplay(*args, **kwargs), self.calls)


def _per_seed(value: Mapping[int, int] | int, seeds: Sequence[int]) -> dict[int, int]:
    """One count per seed, from either a table or a single number."""
    if isinstance(value, Mapping):
        return {seed: value[seed] for seed in seeds}
    return {seed: value for seed in seeds}


def _reading(
    case_id: str,
    seed: int,
    side: str,
    *,
    contract_met: bool = True,
    tool_errors: int = 0,
    call_count: int = _TIED_CALLS,
) -> dict[str, object]:
    """One observer row: a blind side, a round, and what it counted."""
    return {
        "case_id": case_id,
        "seed": seed,
        "side": side,
        "contract_met": contract_met,
        "tool_errors": tool_errors,
        "call_count": call_count,
    }


def _readings(
    *,
    calls: Mapping[str, Mapping[int, int] | int],
    errors: Mapping[str, Mapping[int, int] | int] | None = None,
    seeds: Sequence[int] = _SEEDS,
    failed: frozenset[tuple[str, int, str]] = frozenset(),
) -> list[dict[str, object]]:
    """The complete grid: every ``(label, seed, case)`` cell exactly once.

    ``calls`` and ``errors`` are per *held-out reading*, so one side's
    reading for one seed is that number times the held-out case count.
    Graduated rows carry the loud counts on both sides — the observer
    does not know which case graduated either.
    """
    errors = {label: 0 for label in _LABELS} if errors is None else errors
    rows: list[dict[str, object]] = []
    for label in _LABELS:
        call_table = _per_seed(calls[label], seeds)
        error_table = _per_seed(errors[label], seeds)
        for seed in seeds:
            for case_id in _HELD_OUT_IDS:
                rows.append(
                    _reading(
                        case_id,
                        seed,
                        label,
                        contract_met=(label, seed, case_id) not in failed,
                        tool_errors=error_table[seed],
                        call_count=call_table[seed],
                    )
                )
            for case_id in _GRADUATED_IDS:
                rows.append(
                    _reading(
                        case_id,
                        seed,
                        label,
                        contract_met=(label, seed, case_id) not in failed,
                        tool_errors=_GRADUATED_TOOL_ERRORS,
                        call_count=_GRADUATED_CALL_COUNT,
                    )
                )
    return rows


def _tied_readings(**kwargs: object) -> list[dict[str, object]]:
    """Both labels reading exactly the same, so one edit decides."""
    return _readings(calls={label: _TIED_CALLS for label in _LABELS}, **kwargs)


def _cheaper_on_b(
    *, failed: frozenset[tuple[str, int, str]] = frozenset()
) -> list[dict[str, object]]:
    """Label ``B`` one call per case cheaper, everything else tied."""
    return _readings(
        calls={"A": _TIED_CALLS, "B": _CHEAPER_CALLS},
        errors={label: _TIED_ERRORS for label in _LABELS},
        failed=failed,
    )


def _with_cell(
    rows: Sequence[Mapping[str, object]],
    *,
    case_id: str,
    seed: int,
    side: str,
    **fields: object,
) -> list[dict[str, object]]:
    """A copy of *rows* with one cell's fields replaced; nothing mutated."""
    cell = (case_id, seed, side)
    return [
        {**row, **fields}
        if (row["case_id"], row["seed"], row["side"]) == cell
        else dict(row)
        for row in rows
    ]


def _observation(
    rows: Sequence[Mapping[str, object]], **extra: object
) -> dict[str, object]:
    """What the observer writes: a schema tag, rows, and no side names."""
    return {"schema": _SCHEMA, "readings": [dict(row) for row in rows], **extra}


def _manifest(**overrides: object) -> dict[str, object]:
    """What the orchestrator wrote *before* the run, and never showed."""
    manifest: dict[str, object] = {
        "champion_sha": _CHAMPION_SHA,
        "challenger_sha": _CHALLENGER_SHA,
        "component": _COMPONENT,
        "affected_paths": list(_AFFECTED_PATHS),
        "seeds": list(_SEEDS),
        "sides": dict(_SIDES),
    }
    return {**manifest, **overrides}


def _store(published: Mapping[str, Path] | None = None) -> FakeStore:
    """Both shas published unless a test says one of them is not."""
    return FakeStore(_TREES if published is None else published)


def _source() -> str:
    assert _SOURCE.is_file(), f"{_SOURCE} does not exist yet"
    return _SOURCE.read_text(encoding="utf-8")


def _import_pairs(tree: ast.Module) -> set[tuple[str, str]]:
    """Every ``(module, name)`` the source imports with ``from``."""
    return {
        (node.module or "", alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


#: Observation keys that only an unblinded observer could have written.
_LEAKED_KEYS = (
    pytest.param("sides", dict(_SIDES), id="sides"),
    pytest.param("champion", "A", id="champion"),
    pytest.param("challenger", "B", id="challenger"),
    pytest.param("champion_sha", _CHAMPION_SHA, id="champion_sha"),
    pytest.param("challenger_sha", _CHALLENGER_SHA, id="challenger_sha"),
)

#: ``sides`` maps that are not a bijection onto the two roles. Each one
#: leaves at least one reading with no side, or two readings with one.
_BROKEN_SIDES = (
    pytest.param({"A": "champion", "B": "champion"}, id="both-champion"),
    pytest.param({"A": "challenger", "B": "challenger"}, id="both-challenger"),
    pytest.param({"A": "champion"}, id="missing-a-side"),
    pytest.param(
        {"A": "champion", "B": "challenger", "C": "champion"},
        id="a-third-label",
    ),
    pytest.param({"A": "champion", "B": "observer"}, id="a-third-role"),
)

#: Readings the transcript cannot support. Permitting either invites the
#: next observer to guess a number and call it telemetry.
_UNOBSERVABLE = (
    pytest.param("tokens", 900, id="tokens"),
    pytest.param("latency_s", 12.5, id="latency_s"),
)


class TestReport:
    def test_a_cheaper_challenger_is_accepted(self) -> None:
        result = report(_observation(_cheaper_on_b()), _manifest(), store=_store())

        assert result.accepted is True
        assert result.reason == ACCEPTED
        assert result.candidate_sha == _CHALLENGER_SHA
        assert result.champion_sha == _CHAMPION_SHA
        assert result.regression_passed is True

    def test_the_seeds_are_recorded_as_the_manifest_gave_them(self) -> None:
        """Two rounds that are not ``DEFAULT_SEEDS``, echoed back in order."""
        rows = _readings(
            calls={"A": _TIED_CALLS, "B": _CHEAPER_CALLS},
            seeds=_ODD_SEEDS,
        )

        result = report(
            _observation(rows),
            _manifest(seeds=list(_ODD_SEEDS)),
            store=_store(),
        )

        assert result.seeds == _ODD_SEEDS

    def test_a_reading_sums_that_rounds_held_out_cases(self) -> None:
        """Per ``(side, seed)``: the sum over held-out cases, then the mean.

        The champion reads 6 calls per case in one round and 8 in the
        other, so its mean is 7 per case; an implementation that averaged
        the cases instead of summing them would report 7, not 7 times the
        held-out count.
        """
        rows = _readings(
            calls={"A": {2: _TIED_CALLS, 5: _SLOW_SEED_CALLS}, "B": _CHEAPER_CALLS},
            errors={"A": {2: _FEW_ERRORS, 5: _MANY_ERRORS}, "B": _MEAN_ERRORS},
            seeds=_ODD_SEEDS,
        )

        result = report(
            _observation(rows),
            _manifest(seeds=list(_ODD_SEEDS)),
            store=_store(),
        )

        assert result.champion_metrics.call_count == _MEAN_CALLS * _HELD_OUT_COUNT
        assert result.champion_metrics.tool_errors == _MEAN_ERRORS * _HELD_OUT_COUNT
        assert result.challenger_metrics.call_count == _CHEAPER_CALLS * _HELD_OUT_COUNT
        assert result.challenger_metrics.tool_errors == _MEAN_ERRORS * _HELD_OUT_COUNT

    def test_a_graduated_case_never_reaches_the_readings(self) -> None:
        """Its counts are loud; a reading that summed them in shows it."""
        result = report(_observation(_cheaper_on_b()), _manifest(), store=_store())

        for metrics in (result.champion_metrics, result.challenger_metrics):
            assert metrics.call_count < _GRADUATED_CALL_COUNT
            assert metrics.tool_errors < _GRADUATED_TOOL_ERRORS

    def test_a_failed_graduated_case_short_circuits_before_any_replay(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The challenger is strictly cheaper here, so skipping the
        contract would accept it rather than reject it."""
        factory = SpyReplayFactory()
        monkeypatch.setattr(harness_eval, "ObservedReplay", factory)
        failed = frozenset({("B", _SEEDS[0], _GRADUATED_IDS[0])})

        result = report(
            _observation(_cheaper_on_b(failed=failed)),
            _manifest(),
            store=_store(),
        )

        assert result.reason == REGRESSION_FAILED
        assert result.accepted is False
        assert result.regression_passed is False
        for metrics in (result.champion_metrics, result.challenger_metrics):
            assert metrics.tool_errors == 0
            assert metrics.call_count == 0
            assert metrics.tokens == 0
            assert metrics.latency_s == 0.0
        assert factory.calls == []

    def test_a_failed_graduated_case_on_the_champion_side_is_discarded(
        self,
    ) -> None:
        """Both sides run the graduated case; only the challenger's counts.

        ``evaluate`` runs the contract on the challenger tree alone, so a
        champion-side failure must not reject anything.
        """
        failed = frozenset({("A", _SEEDS[0], _GRADUATED_IDS[0])})

        result = report(
            _observation(_cheaper_on_b(failed=failed)),
            _manifest(),
            store=_store(),
        )

        assert result.reason == ACCEPTED
        assert result.regression_passed is True

    def test_an_unknown_case_id_is_refused_by_name(self) -> None:
        rows = [*_cheaper_on_b(), _reading(_UNKNOWN_CASE_ID, _SEEDS[0], "A")]

        with pytest.raises(EvaluationError, match=_UNKNOWN_CASE_ID):
            report(_observation(rows), _manifest(), store=_store())

    def test_a_missing_cell_is_refused(self) -> None:
        """One cell short silently changes the denominator of the mean."""
        rows = _cheaper_on_b()

        with pytest.raises(EvaluationError):
            report(_observation(rows[1:]), _manifest(), store=_store())

    def test_a_duplicated_cell_is_refused(self) -> None:
        rows = _cheaper_on_b()

        with pytest.raises(EvaluationError):
            report(
                _observation([*rows, dict(rows[0])]),
                _manifest(),
                store=_store(),
            )


class TestBlindnessGuard:
    @pytest.mark.parametrize(("key", "value"), _LEAKED_KEYS)
    def test_an_observation_that_names_a_side_is_refused(
        self, key: str, value: object
    ) -> None:
        """An observer that can name a side was told which one it was."""
        store = _store()

        with pytest.raises(EvaluationError):
            report(
                _observation(_cheaper_on_b(), **{key: value}),
                _manifest(),
                store=store,
            )

        assert store.calls == []

    @pytest.mark.parametrize("sides", _BROKEN_SIDES)
    def test_sides_must_be_a_bijection_onto_the_two_roles(
        self, sides: Mapping[str, str]
    ) -> None:
        with pytest.raises(EvaluationError):
            report(
                _observation(_cheaper_on_b()),
                _manifest(sides=dict(sides)),
                store=_store(),
            )

    def test_swapping_the_sides_flips_the_verdict(self) -> None:
        """The positive proof: the manifest assigns sides, not the reader.

        One observation, read twice. With ``B`` as the challenger the
        cheaper side is the challenger and the report accepts; with the
        manifest swapped the very same numbers are a regression.
        """
        observation = _observation(_cheaper_on_b())

        accepted = report(observation, _manifest(sides=dict(_SIDES)), store=_store())
        rejected = report(
            observation, _manifest(sides=dict(_SIDES_SWAPPED)), store=_store()
        )

        assert accepted.reason == ACCEPTED
        assert accepted.accepted is True
        assert rejected.reason == WORSE_CALL_COUNT
        assert rejected.accepted is False

    def test_an_abandoned_held_out_run_is_refused_by_case_side_and_seed(
        self,
    ) -> None:
        """The cheapest cell in the payload is the one that gave up."""
        rows = _with_cell(
            _tied_readings(),
            case_id=_HELD_OUT_IDS[0],
            seed=_SEEDS[1],
            side="B",
            contract_met=False,
            call_count=_ABANDONED_CALLS,
        )

        with pytest.raises(EvaluationError) as excinfo:
            report(_observation(rows), _manifest(), store=_store())

        message = str(excinfo.value)
        assert _HELD_OUT_IDS[0] in message
        assert str(_SEEDS[1]) in message
        assert re.search(r"\bB\b|challenger", message), (
            f"the refusal must name the side it read, blind label or "
            f"unblinded role; got {message!r}"
        )

    def test_the_same_input_reports_once_that_run_finished(self) -> None:
        """The negative control for the refusal above.

        One field differs: the abandoned round is marked finished. Its
        single call now reads as the cheapest round anyone ran, and the
        verdict is ``ACCEPTED`` — the false win the refusal prevents.
        """
        rows = _with_cell(
            _tied_readings(),
            case_id=_HELD_OUT_IDS[0],
            seed=_SEEDS[1],
            side="B",
            contract_met=True,
            call_count=_ABANDONED_CALLS,
        )

        result = report(_observation(rows), _manifest(), store=_store())

        assert isinstance(result, EvaluationReport)
        assert result.reason == ACCEPTED


class TestObservedSeams:
    @pytest.mark.parametrize(("key", "value"), _UNOBSERVABLE)
    def test_a_reading_the_transcript_cannot_carry_is_refused(
        self, key: str, value: object
    ) -> None:
        rows = _cheaper_on_b()
        rows[0] = {**rows[0], key: value}

        with pytest.raises(EvaluationError):
            report(_observation(rows), _manifest(), store=_store())

    def test_both_sides_read_zero_tokens_and_zero_latency(self) -> None:
        """0 against 0 is the only pair that decides nothing at all."""
        result = report(_observation(_cheaper_on_b()), _manifest(), store=_store())

        for metrics in (result.champion_metrics, result.challenger_metrics):
            assert metrics.tokens == 0
            assert metrics.latency_s == 0.0

    def test_replay_reads_the_champion_table_for_a_str_target(self) -> None:
        """``ReplayFn``'s frozen convention: the champion arrives as a sha."""
        replay = ObservedReplay(_CHAMPION_TABLE, _CHALLENGER_TABLE)

        assert replay(_CHAMPION_SHA, _REPLAY_CASES, _SEEDS[0]) == _CHAMPION_READING

    def test_replay_reads_the_challenger_table_for_a_path_target(self) -> None:
        """And the challenger as a tree someone already checked out."""
        replay = ObservedReplay(_CHAMPION_TABLE, _CHALLENGER_TABLE)

        assert replay(_CHALLENGER_TREE, _REPLAY_CASES, _SEEDS[0]) == _CHALLENGER_READING

    def test_both_shas_are_resolved_through_the_store(self) -> None:
        store = _store()

        report(_observation(_cheaper_on_b()), _manifest(), store=store)

        assert set(store.calls) == {_CHAMPION_SHA, _CHALLENGER_SHA}

    @pytest.mark.parametrize("missing", ["champion_sha", "challenger_sha"])
    def test_an_unpublished_sha_propagates_rather_than_being_swallowed(
        self, missing: str
    ) -> None:
        """A report on an unpublished tree could never be reproduced."""
        manifest = _manifest()
        published = {
            sha: tree for sha, tree in _TREES.items() if sha != manifest[missing]
        }

        with pytest.raises(UnknownShaError):
            report(
                _observation(_cheaper_on_b()),
                manifest,
                store=_store(published),
            )

    def test_the_challenger_carries_the_three_protocol_names(self) -> None:
        names = tuple(field.name for field in dataclasses.fields(ObservedChallenger))

        assert names == ("sha", "component", "affected_paths")

    def test_the_challenger_is_frozen(self) -> None:
        challenger = ObservedChallenger(
            sha=_CHALLENGER_SHA,
            component=_COMPONENT,
            affected_paths=_AFFECTED_PATHS,
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            challenger.sha = _CHAMPION_SHA  # type: ignore[misc]

    def test_the_runner_keeps_the_contract_runner_signature(self) -> None:
        params = tuple(inspect.signature(ObservedRunner.run).parameters)

        assert params == ("self", "tree", "cases")

    def test_main_takes_argv_and_defaults_to_nothing_else(self) -> None:
        params = inspect.signature(main).parameters

        assert tuple(params) == ("argv",)
        assert params["argv"].default is None

    @pytest.mark.parametrize("flag", _MAIN_FLAGS)
    def test_main_names_all_three_paths_on_the_command_line(self, flag: str) -> None:
        assert flag in _source()

    @pytest.mark.parametrize("literal", _QUOTED_REASONS)
    def test_the_source_copies_no_reason_literal(self, literal: str) -> None:
        """A local copy of a reason is a second comparator in waiting."""
        assert literal not in _source()

    @pytest.mark.parametrize("fragment", _FORBIDDEN_FRAGMENTS)
    def test_the_source_carries_no_second_mechanism(self, fragment: str) -> None:
        """No threshold, no model call, no environment, no second checkout."""
        assert fragment not in _source()

    @pytest.mark.parametrize("pattern", _FORBIDDEN_WORDS)
    def test_the_source_names_no_tool_of_its_own(self, pattern: str) -> None:
        found = re.search(pattern, _source())

        assert found is None, (
            f"{pattern} appears in {_SOURCE.name}: the tree comes from the "
            f"store and the verdict from molmcp.evolution"
        )

    def test_the_verdict_comes_from_the_upstream_gate(self) -> None:
        pairs = _import_pairs(ast.parse(_source()))

        assert ("molmcp.evolution", "evaluate") in pairs
