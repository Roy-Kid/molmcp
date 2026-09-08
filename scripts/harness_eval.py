#!/usr/bin/env python
r"""Turn one blind observation of two harness runs into a verdict.

An evaluation has three parts and only the last one is Python. Two
*actor* subagents work the same case in clean contexts, each handed one
harness as prompt text; one *observer* subagent reads both transcripts
under the blind labels ``A`` and ``B``, holding criteria neither actor
ever sees, and writes down only what it can count off the transcript.
This module is the seam between that observation and the gate that
already exists: :func:`molmcp.evolution.evaluate` owns the short-circuit
order, the four independent comparisons and every reason a report may
carry. Nothing here compares two numbers.

Which label was the challenger lives in the *manifest*, written before
the run and never shown to the observer, which is why the two payloads
are two files:

* manifest (the orchestrator's): ``champion_sha``, ``challenger_sha``,
  ``component``, ``affected_paths``, ``seeds``, and ``sides`` mapping
  each blind label onto one role.
* observation (the observer's): ``schema`` and ``readings``, one row per
  ``(side, round, case)`` carrying ``contract_met``, ``tool_errors`` and
  ``call_count`` -- and no side name anywhere.

Every refusal below is a check rather than a convention, because each
one protects a number that would otherwise still look plausible: an
observation that can name a side was told which side it read; a reading
carrying ``tokens`` or ``latency_s`` invented telemetry no transcript
carries; a held-out round that gave up reads cheaper than one that
finished, so averaging it in would make abandonment look like a gain.

Two costs are taken openly. ``tokens`` and ``latency_s`` are read as
zero on both sides, and under the gate's independent comparisons that is
the one pair which neither convicts nor acquits. And the gate's drop
thresholds are all zero, which assumes a repeatable replay; three model
runs are not repeatable. A report from here is evidence, not a
promotion -- moving the champion pointer stays an operator's own action.

Usage::

    uv run python scripts/harness_eval.py \
        --observation runs/2026-09-07/observation.json \
        --manifest runs/2026-09-07/manifest.json \
        --store-root ~/.cache/molmcp/discovery/harness

Exits 0 whenever a report was produced -- a rejection is a successful
evaluation -- and 1 only when the observation could not be turned into
one. Developer-side and advisory: this is not wired into CI, because a
CI runner has no subagent to start.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from harness_cases import graduated_ids, held_out_ids

from molmcp.components import (
    SHA_PATTERN,
    GitHubTransport,
    ImmutableGitStore,
    UnknownShaError,
)
from molmcp.evolution import (
    ContractOutcome,
    EvalCase,
    EvaluationError,
    EvaluationReport,
    Metrics,
    evaluate,
)

#: The only observation payload version this adapter reads.
_SCHEMA = "harness-eval/1"

#: The blind labels an observer may use, and nothing else.
_LABELS: tuple[str, ...] = ("A", "B")

#: The two roles a label may be unblinded into.
_CHAMPION = "champion"
_CHALLENGER = "challenger"
_ROLES = frozenset({_CHAMPION, _CHALLENGER})

#: Observation keys only an unblinded observer could have written.
_LEAKING_KEYS = frozenset(
    {"sides", _CHAMPION, _CHALLENGER, "champion_sha", "challenger_sha"}
)

#: The closed observation schema. Closed rather than merely checked for
#: the banned keys above: the next leak would arrive under a name no
#: list here anticipated.
_OBSERVATION_KEYS = frozenset({"schema", "readings"})

#: The closed reading schema, for the same reason.
_READING_KEYS = frozenset(
    {"case_id", "seed", "side", "contract_met", "tool_errors", "call_count"}
)

#: Readings no transcript can support. Permitting the key invites the
#: next observer to guess a number and call it telemetry.
_UNOBSERVABLE_KEYS: tuple[str, ...] = ("tokens", "latency_s")

#: What the manifest must carry before anything is unblinded.
_MANIFEST_KEYS = frozenset(
    {
        "champion_sha",
        "challenger_sha",
        "component",
        "affected_paths",
        "seeds",
        "sides",
    }
)

#: What both sides read for the two unobservable readings. Equal on both
#: sides is the point: the gate compares each reading on its own, so an
#: equal pair can neither reject nor accept a challenger.
_UNREAD_TOKENS = 0
_UNREAD_LATENCY_S = 0.0

#: The case set, split the way the gate's two arguments split it.
_HELD_OUT: tuple[EvalCase, ...] = tuple(EvalCase(id=name) for name in held_out_ids())
_GRADUATED: tuple[EvalCase, ...] = tuple(EvalCase(id=name) for name in graduated_ids())
_HELD_OUT_IDS = frozenset(case.id for case in _HELD_OUT)
_KNOWN_IDS = _HELD_OUT_IDS | frozenset(case.id for case in _GRADUATED)


class TreeStore(Protocol):
    """The one thing :func:`report` asks a component store for.

    Structural on purpose: the adapter reads a single method, and a
    parameter typed as the concrete store would hide a rename of it.
    """

    def tree_path(self, sha: str) -> Path:
        """Return the published tree for *sha*, or raise if there is none."""
        ...


@dataclass(frozen=True, slots=True)
class ObservedChallenger:
    """The checkout under evaluation, as the manifest describes it.

    Implements :class:`molmcp.evolution.Challenger`: three names the
    gate carries into its report without interpreting any of them.

    Attributes:
        sha: Full commit sha of the challenger checkout.
        component: Id of the harness component it changes.
        affected_paths: Repository paths it touches.
    """

    sha: str
    component: str
    affected_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservedRunner:
    """The graduated contract, already settled by the observer.

    Implements :class:`molmcp.evolution.ContractRunner`. The transcripts
    were read before this module ran, so ``run`` opens nothing: it looks
    up what the observer recorded for the challenger side and reports
    it. A case counts as met only when every round met it.

    Attributes:
        met: Whether each graduated case was met on the challenger side,
            keyed by case id.
    """

    met: Mapping[str, bool]

    def run(self, tree: Path, cases: Sequence[EvalCase]) -> ContractOutcome:
        """Report the observed outcome of *cases*.

        Args:
            tree: The challenger checkout. Named by the protocol and
                never opened here -- the run it describes is already
                over, and re-reading the tree would be a second source.
            cases: The graduated cases to report on.

        Returns:
            One :class:`molmcp.evolution.ContractOutcome`, failing ids
            included so the report's readers can name them.
        """
        failed = tuple(case.id for case in cases if not self.met[case.id])
        return ContractOutcome(passed=not failed, failed_case_ids=failed)


@dataclass(frozen=True, slots=True)
class ObservedReplay:
    """The held-out readings, already counted, looked up by side and round.

    Implements :class:`molmcp.evolution.ReplayFn` and keeps that
    protocol's frozen convention for telling the sides apart: the
    champion arrives as a sha string, the challenger as the tree a
    caller already resolved, so ``isinstance(target, Path)`` is the
    whole dispatch. No side argument is added -- a second way to say
    which side is a second way to get it wrong.

    Attributes:
        champion: The champion's reading for each round, keyed by seed.
        challenger: The challenger's reading, same shape.
    """

    champion: Mapping[int, Metrics]
    challenger: Mapping[int, Metrics]

    def __call__(
        self, target: str | Path, cases: Sequence[EvalCase], seed: int
    ) -> Metrics:
        """Return one side's reading for one round.

        Args:
            target: The champion's sha, or the challenger's tree.
            cases: The held-out cases. Named by the protocol; the
                reading was summed over them before this module ran.
            seed: The round to read.

        Returns:
            That side's :class:`molmcp.evolution.Metrics` for *seed*.
        """
        table = self.challenger if isinstance(target, Path) else self.champion
        return table[seed]


@dataclass(frozen=True, slots=True)
class _Reading:
    """One observer row: a blind side, a round, and what it counted."""

    case_id: str
    seed: int
    side: str
    contract_met: bool
    tool_errors: int
    call_count: int


@dataclass(frozen=True, slots=True)
class _Plan:
    """The manifest, validated: everything the observer was not told."""

    champion_sha: str
    challenger_sha: str
    component: str
    affected_paths: tuple[str, ...]
    seeds: tuple[int, ...]
    sides: Mapping[str, str]


def _mapping(value: object, what: str) -> Mapping[str, object]:
    """Narrow *value* to a string-keyed mapping or refuse it."""
    if not isinstance(value, Mapping):
        raise EvaluationError(f"{what} must be an object, not {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def _sequence(value: object, what: str) -> Sequence[object]:
    """Narrow *value* to a list-like sequence or refuse it."""
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise EvaluationError(f"{what} must be a list, not {type(value).__name__}")
    return tuple(value)


def _text(value: object, what: str) -> str:
    """Narrow *value* to a string or refuse it."""
    if not isinstance(value, str):
        raise EvaluationError(f"{what} must be text, not {type(value).__name__}")
    return value


def _whole(value: object, what: str) -> int:
    """Narrow *value* to a non-negative integer or refuse it."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationError(
            f"{what} must be a whole number, not {type(value).__name__}"
        )
    if value < 0:
        raise EvaluationError(f"{what} must not be negative, got {value}")
    return value


def _flag(value: object, what: str) -> bool:
    """Narrow *value* to a boolean or refuse it."""
    if not isinstance(value, bool):
        raise EvaluationError(
            f"{what} must be true or false, not {type(value).__name__}"
        )
    return value


def _sha(value: object, what: str) -> str:
    """Narrow *value* to a full commit sha or refuse it."""
    sha = _text(value, what)
    if SHA_PATTERN.fullmatch(sha) is None:
        raise EvaluationError(f"{what} must be a full commit sha, got {sha!r}")
    return sha


def _cell(cell: tuple[str, int, str]) -> str:
    """Name one ``(side, round, case)`` cell the way a refusal should."""
    side, seed, case_id = cell
    return f"case {case_id!r} on side {side} in round {seed}"


def _cells(cells: Sequence[tuple[str, int, str]]) -> str:
    """Name several cells in one refusal."""
    return "; ".join(_cell(cell) for cell in cells)


def _reading_of(entry: Mapping[str, object], position: int) -> _Reading:
    """Validate and narrow one observation row.

    Args:
        entry: The row as the observer wrote it.
        position: Index of the row, so a refusal can point at it.

    Returns:
        The row as a :class:`_Reading`.

    Raises:
        EvaluationError: The row carries a reading no transcript can
            show, is not the closed row schema, names a case that is in
            no case set entry, or names a side that is not a blind label.
    """
    where = f"reading {position}"
    unobservable = [key for key in _UNOBSERVABLE_KEYS if key in entry]
    if unobservable:
        raise EvaluationError(
            f"{where} carries {', '.join(unobservable)}, which cannot be "
            f"counted off a transcript. Both sides are read as zero here so "
            f"that a guessed number can never decide a verdict."
        )
    unknown = sorted(set(entry) - _READING_KEYS)
    if unknown:
        raise EvaluationError(f"{where} carries unknown keys: {', '.join(unknown)}")
    missing = sorted(_READING_KEYS - set(entry))
    if missing:
        raise EvaluationError(f"{where} is missing keys: {', '.join(missing)}")

    case_id = _text(entry["case_id"], f"{where} case_id")
    if case_id not in _KNOWN_IDS:
        raise EvaluationError(
            f"{where} names case {case_id!r}, which is in no case set entry. "
            f"A mistyped id averaged into a mean is worse than a refusal."
        )
    side = _text(entry["side"], f"{where} side")
    if side not in _LABELS:
        raise EvaluationError(
            f"{where} names side {side!r}; only the blind labels "
            f"{', '.join(_LABELS)} may appear in an observation."
        )
    return _Reading(
        case_id=case_id,
        seed=_whole(entry["seed"], f"{where} seed"),
        side=side,
        contract_met=_flag(entry["contract_met"], f"{where} contract_met"),
        tool_errors=_whole(entry["tool_errors"], f"{where} tool_errors"),
        call_count=_whole(entry["call_count"], f"{where} call_count"),
    )


def _observed_readings(observation: Mapping[str, object]) -> tuple[_Reading, ...]:
    """Validate the observation and narrow its rows.

    Args:
        observation: What the observer wrote, straight from its file.

    Returns:
        Every row, validated, in the order the observer wrote them.

    Raises:
        EvaluationError: The observation names a side, is not the closed
            observation schema, carries another schema version, or holds
            a row that does not validate.
    """
    named = sorted(_LEAKING_KEYS & set(observation))
    if named:
        raise EvaluationError(
            f"the observation names a side: {', '.join(named)}. An observer "
            f"that can say which checkout it read was told which one it was, "
            f"and the whole reading rests on it not knowing."
        )
    unknown = sorted(set(observation) - _OBSERVATION_KEYS)
    if unknown:
        raise EvaluationError(
            f"the observation carries unknown keys: {', '.join(unknown)}"
        )
    missing = sorted(_OBSERVATION_KEYS - set(observation))
    if missing:
        raise EvaluationError(f"the observation is missing keys: {', '.join(missing)}")
    schema = _text(observation["schema"], "observation schema")
    if schema != _SCHEMA:
        raise EvaluationError(
            f"the observation reads {schema!r}; this adapter reads {_SCHEMA!r}"
        )
    rows = _sequence(observation["readings"], "observation readings")
    return tuple(
        _reading_of(_mapping(row, f"reading {position}"), position)
        for position, row in enumerate(rows)
    )


def _unblinded_sides(value: object) -> Mapping[str, str]:
    """Read the manifest's label-to-role assignment.

    Args:
        value: The manifest's ``sides`` entry.

    Returns:
        Each blind label mapped onto its role.

    Raises:
        EvaluationError: The assignment is not a bijection from the two
            blind labels onto the two roles. Anything else leaves a
            reading with no side, or two readings with the same one.
    """
    sides = _mapping(value, "manifest sides")
    roles = {
        label: _text(role, f"manifest side {label!r}") for label, role in sides.items()
    }
    if set(roles) != set(_LABELS) or set(roles.values()) != _ROLES:
        raise EvaluationError(
            f"manifest sides must assign each of {', '.join(_LABELS)} exactly "
            f"one of {', '.join(sorted(_ROLES))}, got {roles!r}. Unblinding "
            f"comes from the manifest alone, so it must be unambiguous."
        )
    return roles


def _plan_of(manifest: Mapping[str, object]) -> _Plan:
    """Validate the manifest the orchestrator wrote before the run.

    Args:
        manifest: The manifest, straight from its file.

    Returns:
        The validated :class:`_Plan`.

    Raises:
        EvaluationError: A key is missing, a sha is not a full commit
            sha, a round is repeated, or ``sides`` is not a bijection.
    """
    missing = sorted(_MANIFEST_KEYS - set(manifest))
    if missing:
        raise EvaluationError(f"the manifest is missing keys: {', '.join(missing)}")
    seeds = tuple(
        _whole(seed, "manifest seed")
        for seed in _sequence(manifest["seeds"], "manifest seeds")
    )
    if len(set(seeds)) != len(seeds):
        raise EvaluationError(
            f"the manifest repeats a round: {list(seeds)}. A round counted "
            f"twice weights itself twice in the mean."
        )
    return _Plan(
        champion_sha=_sha(manifest["champion_sha"], "manifest champion_sha"),
        challenger_sha=_sha(manifest["challenger_sha"], "manifest challenger_sha"),
        component=_text(manifest["component"], "manifest component"),
        affected_paths=tuple(
            _text(path, "manifest affected path")
            for path in _sequence(manifest["affected_paths"], "manifest affected_paths")
        ),
        seeds=seeds,
        sides=_unblinded_sides(manifest["sides"]),
    )


def _refuse_incomplete_grid(readings: Sequence[_Reading], seeds: Sequence[int]) -> None:
    """Refuse unless every ``(side, round, case)`` appears exactly once.

    Args:
        readings: The validated rows.
        seeds: The rounds the manifest asked for.

    Raises:
        EvaluationError: A cell is repeated, missing, or not one the
            manifest asked for. Any of the three silently changes the
            denominator of a mean.
    """
    counted = Counter((row.side, row.seed, row.case_id) for row in readings)
    expected = {
        (label, seed, case_id)
        for label in _LABELS
        for seed in seeds
        for case_id in _KNOWN_IDS
    }
    repeated = sorted(cell for cell, times in counted.items() if times > 1)
    if repeated:
        raise EvaluationError(f"the observation reads twice: {_cells(repeated)}")
    absent = sorted(expected - set(counted))
    if absent:
        raise EvaluationError(f"the observation never reads: {_cells(absent)}")
    extra = sorted(set(counted) - expected)
    if extra:
        raise EvaluationError(
            f"the observation reads a cell the manifest never asked for: "
            f"{_cells(extra)}"
        )


def _refuse_abandoned(readings: Sequence[_Reading]) -> None:
    """Refuse a held-out round that did not finish.

    Args:
        readings: The validated rows.

    Raises:
        EvaluationError: A held-out reading has ``contract_met`` false,
            named by case, side and round. An unfinished round reads
            cheaper than a finished one -- fewer calls, fewer errors --
            so averaging it in would make giving up look like a gain.
    """
    for row in readings:
        if row.case_id in _HELD_OUT_IDS and not row.contract_met:
            raise EvaluationError(
                f"{_cell((row.side, row.seed, row.case_id))} did not finish, "
                f"and an unfinished round reads cheaper than a finished one. "
                f"Graduate the case or fix the harness; do not let it lower "
                f"the mean."
            )


def _seed_metrics(
    readings: Sequence[_Reading], label: str, seeds: Sequence[int]
) -> Mapping[int, Metrics]:
    """Sum one side's held-out readings, round by round.

    Graduated cases are left out on purpose: a graduated case is a
    correctness contract, not a reading, and letting its cost into the
    sum would let the length of a contract case decide a promotion.

    Args:
        readings: The validated rows.
        label: The blind label of the side to read.
        seeds: The rounds to read.

    Returns:
        One :class:`molmcp.evolution.Metrics` per round, with both
        unobservable readings pinned to zero.
    """
    return {
        seed: _summed(
            [
                row
                for row in readings
                if row.side == label
                and row.seed == seed
                and row.case_id in _HELD_OUT_IDS
            ]
        )
        for seed in seeds
    }


def _summed(rows: Sequence[_Reading]) -> Metrics:
    """One round's reading: the sum over that round's held-out cases."""
    return Metrics(
        tool_errors=sum(row.tool_errors for row in rows),
        call_count=sum(row.call_count for row in rows),
        tokens=_UNREAD_TOKENS,
        latency_s=_UNREAD_LATENCY_S,
    )


def _contract_met(readings: Sequence[_Reading], label: str) -> Mapping[str, bool]:
    """Read one side's graduated outcome, case by case.

    Both sides run the graduated cases -- neither actor knows which case
    graduated, and the observer does not know which side is which -- but
    the gate runs the contract on the challenger tree alone, so only the
    challenger's rows are ever read here.

    Args:
        readings: The validated rows.
        label: The blind label of the challenger side.

    Returns:
        Whether each graduated case was met in every round.
    """
    return {
        case.id: all(
            row.contract_met
            for row in readings
            if row.side == label and row.case_id == case.id
        )
        for case in _GRADUATED
    }


def report(
    observation: Mapping[str, object],
    manifest: Mapping[str, object],
    *,
    store: TreeStore,
) -> EvaluationReport:
    """Turn one blind observation into one verdict.

    The observation is checked before the store is touched, because a
    payload that names a side is not a blind reading and no amount of
    later care recovers one. The manifest then unblinds the labels, the
    held-out rows become the readings and the challenger's graduated
    rows become the contract; the verdict itself comes wholly from
    :func:`molmcp.evolution.evaluate`.

    Args:
        observation: What the observer wrote: a schema tag and one row
            per ``(side, round, case)``, under blind labels only.
        manifest: What the orchestrator wrote before the run and never
            showed the observer, including the label-to-role assignment.
        store: Component store, asked for both published trees. The only
            checkout mechanism here: a report on a tree that was never
            published could not be reproduced.

    Returns:
        The :class:`molmcp.evolution.EvaluationReport` for this pair.

    Raises:
        EvaluationError: The observation names a side; ``sides`` is not
            a bijection; a reading carries an unobservable key; a case
            id is unknown; a cell is missing, repeated or unasked for;
            or a held-out round did not finish.
        molmcp.components.UnknownShaError: Either sha is unpublished.
            Left to propagate: it is a different failure from a payload
            this layer refuses, and swallowing it would report a verdict
            on a tree nobody can check out.
    """
    readings = _observed_readings(observation)
    plan = _plan_of(manifest)
    _refuse_incomplete_grid(readings, plan.seeds)
    _refuse_abandoned(readings)

    label_of = {role: label for label, role in plan.sides.items()}
    store.tree_path(plan.champion_sha)
    challenger_tree = store.tree_path(plan.challenger_sha)

    return evaluate(
        ObservedChallenger(
            sha=plan.challenger_sha,
            component=plan.component,
            affected_paths=plan.affected_paths,
        ),
        challenger_tree,
        plan.champion_sha,
        _HELD_OUT,
        _GRADUATED,
        runner=ObservedRunner(_contract_met(readings, label_of[_CHALLENGER])),
        replay=ObservedReplay(
            _seed_metrics(readings, label_of[_CHAMPION], plan.seeds),
            _seed_metrics(readings, label_of[_CHALLENGER], plan.seeds),
        ),
        seeds=plan.seeds,
    )


def _rendered(result: EvaluationReport) -> str:
    """Lay the report out for a terminal, adding nothing to it."""
    rounds = ", ".join(str(seed) for seed in result.seeds)
    contract = "passed" if result.regression_passed else "failed"
    return "\n".join(
        (
            f"reason:        {result.reason}",
            f"accepted:      {result.accepted}",
            f"candidate sha: {result.candidate_sha}",
            f"champion sha:  {result.champion_sha}",
            f"rounds:        {rounds}",
            f"contract:      {contract}",
            _readings_line(_CHAMPION, result.champion_metrics),
            _readings_line(_CHALLENGER, result.challenger_metrics),
        )
    )


def _readings_line(side: str, metrics: Metrics) -> str:
    """One side's four readings, in the order the gate compares them."""
    return (
        f"{side + ':':15}tool_errors={metrics.tool_errors} "
        f"call_count={metrics.call_count} tokens={metrics.tokens} "
        f"latency_s={metrics.latency_s}"
    )


def _loaded(path: Path, what: str) -> Mapping[str, object]:
    """Read one JSON payload from disk.

    Args:
        path: File to read.
        what: How to name it in a refusal.

    Returns:
        The payload as a mapping.

    Raises:
        EvaluationError: The file is not JSON, or is not a JSON object.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as broken:
        raise EvaluationError(f"{what} at {path} is not JSON: {broken}") from broken
    return _mapping(payload, f"{what} at {path}")


def _parser() -> argparse.ArgumentParser:
    """Build the command line: three paths, all required, no defaults."""
    parser = argparse.ArgumentParser(
        prog="harness_eval",
        description=(
            "Turn a blind observation of two harness runs into a verdict "
            "from molmcp.evolution.evaluate."
        ),
    )
    parser.add_argument(
        "--observation",
        required=True,
        type=Path,
        help="Observer payload: blind labels and counted readings.",
    )
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="Orchestrator payload: both shas, the rounds, and the sides.",
    )
    parser.add_argument(
        "--store-root",
        required=True,
        type=Path,
        help="Component store root holding both published trees.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Read both payloads, print the report, and say whether it was produced.

    Every path is named on the command line and none of them defaults:
    a run whose inputs came from somewhere the command line does not
    show is a run nobody else can repeat.

    Args:
        argv: Command line arguments, or ``None`` to read the process's.

    Returns:
        0 whenever a report was produced -- a rejection is a successful
        evaluation, and the reason it carries is the result. 1 only when
        the observation could not be turned into a report at all.
    """
    args = _parser().parse_args(argv)
    store = ImmutableGitStore(args.store_root, GitHubTransport())
    try:
        result = report(
            _loaded(args.observation, "the observation"),
            _loaded(args.manifest, "the manifest"),
            store=store,
        )
    except EvaluationError as refusal:
        print(f"no report: {refusal}")
        return 1
    except UnknownShaError as unpublished:
        print(
            f"no report: the store has published no tree for {unpublished}, "
            f"so a report on it could not be reproduced."
        )
        return 1
    print(_rendered(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
