#!/usr/bin/env python3
"""Regression example: risk-graded promotion, and the slot a rollback eats.

Standalone (no pytest dependency). Injects a fake pointer machine that
exposes three names and nothing else -- ``stage``, a nullary ``promote()``
and a nullary ``rollback()`` -- records the method-name sequence, and moves
``current`` / ``previous`` as one slot the way the real activation does.
``Promoter`` is driven through the ``molmcp.evolution`` package façade only,
and every file it writes lands in one ``tempfile.TemporaryDirectory`` that
is removed in a ``finally``.

The fake carries no ``bind``. That is the point: ``Promoter`` must never
call it, because whoever injected the pointer machine has already bound it,
and an implementation that reached for it would raise ``AttributeError``
here rather than pass.

Hard-coded goldens (in-repo, 2026-09-07, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-12-promote.md``, Testing
strategy -> 回归脚本, and acceptance AC-003 / AC-004 / AC-005 / AC-006 /
AC-010 / AC-014):

    owner + low + accepted, forty 'a' -> method sequence ('stage',
        'promote') exactly, ``promote`` handed nothing, fake current forty
        'a', history row activated, and no canary.json written
    owner + high + accepted, forty 'b' -> canary.json is exactly
        {"version": 1, "sha": forty 'b', "report_id": "report-provider-1"},
        history row canaried, zero further pointer calls, fake current
        still forty 'a'
    bot + high + accepted + path_allowed, over a pointer sitting on forty
        'f' -> the same canary document from an empty state directory,
        **zero** pointer calls in total, fake current still forty 'f'
    owner + accepted=False, forty 'c' -> GateDecision(allow=False,
        reason="failed-report"), history row rejected, zero further pointer
        calls, canary.json byte-for-byte the provider document
    low forty 'd' then low forty 'e' -> rollback("report-order-first")
        raises PromoterError("not-current") with the rollback count still 0
        and current forty 'e'; rollback("report-order-second") calls the
        nullary rollback() once and appends HistoryEntry(forty 'e',
        "report-order-second", "rolled_back"); rollback("report-order-first")
        again raises PromoterError("not-current"), the rollback count stays
        1, and current stays forty 'd'

No golden below is fed back in as an input. The requests further down spell
their own shas and report ids out, so editing a golden makes this script
fail instead of moving both sides of a comparison at once; each of the
thirty-two ``_GOLDEN_*`` constants was perturbed on its own and confirmed to
break the run.

*Why the rollback ordering is the golden worth the most.* A ``rolled_back``
row **consumes** the pointer machine's ``previous`` slot, so the current
activation is positional -- the last ``activated`` row with no
``rolled_back`` row after it anywhere in the log -- and never a pairing by
``report_id``. Pair by id and this sequence walks back a generation: after
apply A, apply B, rollback(B), report A's own ``activated`` row still looks
unpaired, the pointer really is sitting on A so even the published-sha guard
agrees, and the third call swaps B back in as a second generation of a
snapshot that was already withdrawn. That is why the first refusal is not
enough on its own: before rollback(B) the pointer is on B, so a per-id
implementation is still caught by the published-sha check and refuses for
the wrong reason. Only the refusal *after* a successful rollback separates
the two implementations, and it is asserted three ways -- the code, the
unchanged rollback count, and the fake's ``current``.

Public surface only: ``molmcp.evolution`` (the package façade), never
``molmcp.evolution.promote``. Deliberately absent: the module's private
``_current_activation`` / ``_read_document`` / ``_write_document`` helpers
and its ``_ActivationHandle`` protocol (the positional rule is proven by
behaviour; importing the helper would test the leaf against its own
opinion), ``molmcp.components`` and any real store, git, network,
subprocesses, environment variables, pytest, and any path outside the one
temporary directory.

Run directly::

    uv run python regressions/autonomous-harness-evolution-12-promote.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via
``test_autonomous_harness_evolution_12_promote``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from molmcp.evolution import (
    PROMOTER_STATE_VERSION,
    ApplyOutcome,
    AuthorKind,
    GatePolicy,
    HistoryEntry,
    Promoter,
    PromoterError,
    PromotionRequest,
    Risk,
)

# ---------------------------------------------------------------------------
# Goldens. In-repo, 2026-09-07, no third-party oracle. Every literal in this
# block is an *expectation* and is used nowhere as an input: the requests and
# the seeded pointer further down spell their own shas and ids out, so
# editing anything here makes the script fail instead of agreeing with
# itself.
# ---------------------------------------------------------------------------

#: The three shas of the worked example, written out again rather than read
#: off the requests that carry them. Full commit identities: forty lowercase
#: hexadecimal characters, never an abbreviation and never a tag.
_GOLDEN_SKILL_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_GOLDEN_PROVIDER_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_GOLDEN_REJECT_SHA = "cccccccccccccccccccccccccccccccccccccccc"

#: The report ids the results must carry back.
_GOLDEN_SKILL_REPORT_ID = "report-skill-1"
_GOLDEN_PROVIDER_REPORT_ID = "report-provider-1"
_GOLDEN_REJECT_REPORT_ID = "report-reject-1"

#: What a pointer already sitting on something must still read after a
#: high-risk apply parks a canary beside it.
_GOLDEN_PRESET_CURRENT_SHA = "ffffffffffffffffffffffffffffffffffffffff"

#: The two generations of the rollback-ordering case: A, then B.
_GOLDEN_FIRST_SHA = "dddddddddddddddddddddddddddddddddddddddd"
_GOLDEN_SECOND_SHA = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
_GOLDEN_FIRST_REPORT_ID = "report-order-first"
_GOLDEN_SECOND_REPORT_ID = "report-order-second"

#: The method-name sequence a low-risk apply records, in order. ``stage``
#: takes the sha; ``promote`` takes nothing, because the staged sha is
#: already the pointer machine's to read.
_GOLDEN_LOW_RISK_CALLS = ("stage", "promote")

#: What a canary and a refusal record: nothing at all, ``stage`` included.
_GOLDEN_NO_CALLS: tuple[str, ...] = ()

#: The four ledger actions, as the JSON file spells them.
_GOLDEN_ACTIVATED = "activated"
_GOLDEN_CANARIED = "canaried"
_GOLDEN_REJECTED = "rejected"
_GOLDEN_ROLLED_BACK = "rolled_back"

#: Gate reasons. ``failed-report`` is the one the owner gets no exemption
#: from, and ``not-current`` is the refusal the ordering case turns on.
_GOLDEN_ALLOWED = "allowed"
_GOLDEN_FAILED_REPORT = "failed-report"
_GOLDEN_NOT_CURRENT = "not-current"

#: The integer both private documents carry.
_GOLDEN_STATE_VERSION = 1

#: The two documents, and nothing else, under a state directory.
_GOLDEN_CANARY_NAME = "canary.json"
_GOLDEN_HISTORY_NAME = "history.json"
_GOLDEN_STATE_FILENAMES = ("canary.json", "history.json")
_GOLDEN_ORDERING_FILENAMES = ("history.json",)

#: The parked canary, whole. Exact equality on purpose: an implementation
#: that smuggled a fourth key past this file would be publishing a shape
#: nobody agreed to.
_GOLDEN_CANARY_DOCUMENT: dict[str, object] = {
    "version": 1,
    "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "report_id": "report-provider-1",
}

#: The worked example's ledger, oldest first. Each apply is checked against
#: the prefix it should have produced, so "history gained one row" is pinned
#: per step and not only at the end.
_GOLDEN_WORKED_HISTORY: tuple[dict[str, object], ...] = (
    {
        "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "report_id": "report-skill-1",
        "action": "activated",
    },
    {
        "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "report_id": "report-provider-1",
        "action": "canaried",
    },
    {
        "sha": "cccccccccccccccccccccccccccccccccccccccc",
        "report_id": "report-reject-1",
        "action": "rejected",
    },
)

#: The in-path bot's ledger: one canaried row from an empty directory.
_GOLDEN_BOT_HISTORY: tuple[dict[str, object], ...] = (
    {
        "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "report_id": "report-provider-1",
        "action": "canaried",
    },
)

#: The ordering case's ledger. The third row is B's, not A's: what was
#: withdrawn is the generation the log said was current.
_GOLDEN_ORDER_HISTORY: tuple[dict[str, object], ...] = (
    {
        "sha": "dddddddddddddddddddddddddddddddddddddddd",
        "report_id": "report-order-first",
        "action": "activated",
    },
    {
        "sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "report_id": "report-order-second",
        "action": "activated",
    },
    {
        "sha": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "report_id": "report-order-second",
        "action": "rolled_back",
    },
)

#: The row ``rollback`` hands back. Its sha comes from the ``activated``
#: record the Promoter looked up, never from what ``rollback()`` returned --
#: the fake returns a string that is not a sha at all.
_GOLDEN_ROLLED_BACK_ENTRY = HistoryEntry(
    sha="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    report_id="report-order-second",
    action="rolled_back",
)

#: How many times the pointer machine's ``rollback()`` may run: none while a
#: newer generation is current, exactly one across the whole ordering case.
_GOLDEN_ROLLBACK_CALLS_BEFORE = 0
_GOLDEN_ROLLBACK_CALLS_AFTER = 1

# ---------------------------------------------------------------------------
# Inputs. Literals, not references to the goldens above.
# ---------------------------------------------------------------------------

#: A low-risk skill snapshot filed by the owner on an accepted report.
_SKILL_REQUEST = PromotionRequest(
    sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    report_id="report-skill-1",
    author=AuthorKind.OWNER,
    risk=Risk.LOW,
    accepted=True,
)

#: The same provider snapshot filed twice, by the owner and by an in-path
#: bot. The gate treats them alike, so both must park the identical canary.
_PROVIDER_REQUEST_OWNER = PromotionRequest(
    sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    report_id="report-provider-1",
    author=AuthorKind.OWNER,
    risk=Risk.HIGH,
    accepted=True,
)
_PROVIDER_REQUEST_BOT = PromotionRequest(
    sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    report_id="report-provider-1",
    author=AuthorKind.BOT,
    risk=Risk.HIGH,
    accepted=True,
    path_allowed=True,
)

#: A failed report filed by the owner. Low risk, so nothing but ``accepted``
#: stands between it and the pointer -- which is the whole test.
_REJECT_REQUEST = PromotionRequest(
    sha="cccccccccccccccccccccccccccccccccccccccc",
    report_id="report-reject-1",
    author=AuthorKind.OWNER,
    risk=Risk.LOW,
    accepted=False,
)

#: Two low-risk generations, applied in this order.
_ORDER_FIRST_REQUEST = PromotionRequest(
    sha="dddddddddddddddddddddddddddddddddddddddd",
    report_id="report-order-first",
    author=AuthorKind.OWNER,
    risk=Risk.LOW,
    accepted=True,
)
_ORDER_SECOND_REQUEST = PromotionRequest(
    sha="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    report_id="report-order-second",
    author=AuthorKind.OWNER,
    risk=Risk.LOW,
    accepted=True,
)

#: What the bot scenario's pointer already reads before anything is applied.
_PRESET_POINTER_SHA = "ffffffffffffffffffffffffffffffffffffffff"

#: What the fake's ``rollback()`` hands back. Deliberately neither a sha nor
#: a report id: the Promoter must ignore it and use the record it looked up.
_ROLLBACK_RETURN = "whatever-the-pointer-felt-like-returning"


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


class _FakePointer:
    """A duck-typed pointer machine: one live sha and one slot behind it.

    Three names and no more. ``bind`` is absent so that an implementation
    calling it fails loudly, and ``promote`` / ``rollback`` are **nullary**
    so that one handed a sha raises ``TypeError`` instead of quietly
    writing the pointer twice.

    ``rollback`` swaps ``current`` with ``previous`` in a single slot, the
    way the real activation's one-level undo does, and returns something
    that is not a sha to pin that nobody reads it.

    Args:
        current: The sha the pointer already reads, or ``None`` for a
            machine that has promoted nothing yet.
    """

    def __init__(self, current: str | None = None) -> None:
        self.current = current
        self.previous: str | None = None
        self.staged: str | None = None
        self.calls: list[str] = []
        self.staged_shas: list[str] = []

    def stage(self, sha: str) -> None:
        """Park *sha* as the staged candidate."""
        self.calls.append("stage")
        self.staged_shas.append(sha)
        self.staged = sha

    def promote(self) -> None:
        """Make the staged sha live. Nullary: the sha is already here."""
        self.calls.append("promote")
        if self.staged is None:
            raise AssertionError("promote() ran with nothing staged")
        self.previous = self.current
        self.current = self.staged
        self.staged = None

    def rollback(self) -> str:
        """Swap the live sha with the one behind it. Nullary by contract."""
        self.calls.append("rollback")
        if self.previous is None:
            raise AssertionError("rollback() ran with nothing behind it")
        self.current, self.previous = self.previous, self.current
        return _ROLLBACK_RETURN

    def calls_since(self, mark: int) -> tuple[str, ...]:
        """Return the method names recorded after *mark* calls."""
        return tuple(self.calls[mark:])

    def rollback_count(self) -> int:
        """Return how many times ``rollback()`` has run."""
        return self.calls.count("rollback")


def _read_json(path: Path) -> dict[str, object]:
    """Return the JSON object at *path*, failing when it is not there.

    Args:
        path: Document to read.

    Returns:
        The decoded object.
    """
    _require(path.is_file(), f"{path} was not written")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(payload, dict), f"{path} does not hold a JSON object")
    return dict(payload)


def _filenames(state_dir: Path) -> tuple[str, ...]:
    """Return the names under *state_dir*, sorted.

    A ``.partial`` sibling left behind would show up here, and so would a
    wiki page or a lock file this layer has no business writing.
    """
    return tuple(sorted(entry.name for entry in state_dir.iterdir()))


def _history_rows(state_dir: Path) -> tuple[dict[str, object], ...]:
    """Return the ledger rows under *state_dir*, oldest first.

    Args:
        state_dir: The Promoter's private directory.

    Returns:
        One mapping per stored row.
    """
    document = _read_json(state_dir / _GOLDEN_HISTORY_NAME)
    version = document.get("version")
    _require(
        isinstance(version, int) and not isinstance(version, bool),
        f"history version {version!r} is not an integer",
    )
    _require(
        version == _GOLDEN_STATE_VERSION,
        f"history version {version!r} != {_GOLDEN_STATE_VERSION!r}",
    )
    stored = document.get("entries")
    _require(isinstance(stored, list), f"history entries is {stored!r}, not a list")
    rows: list[dict[str, object]] = []
    for row in stored if isinstance(stored, list) else []:
        _require(isinstance(row, dict), f"history row {row!r} is not an object")
        rows.append(dict(row))
    return tuple(rows)


def _check_low_risk(promoter: Promoter, pointer: _FakePointer, state_dir: Path) -> None:
    """Golden 1: low risk stages the sha, then promotes with nothing.

    Args:
        promoter: The promoter under test.
        pointer: The fake it was handed.
        state_dir: Its private directory.
    """
    decision = GatePolicy().decide(_SKILL_REQUEST)
    _require(decision.allow is True, f"the gate refused the owner: {decision!r}")
    _require(
        decision.reason == _GOLDEN_ALLOWED,
        f"gate reason {decision.reason!r} != {_GOLDEN_ALLOWED!r}",
    )

    try:
        result = promoter.apply(_SKILL_REQUEST)
    except TypeError as exc:
        raise AssertionError(
            f"apply passed an argument to a nullary pointer method: {exc}. "
            "promote() takes no sha -- the staged one is already the pointer "
            "machine's to read, and handing it over writes the pointer twice"
        ) from exc

    _require(
        result.outcome == ApplyOutcome.ACTIVATED,
        f"outcome {result.outcome!r} is not ACTIVATED",
    )
    _require(
        str(result.outcome) == _GOLDEN_ACTIVATED,
        f"outcome spells {str(result.outcome)!r}, not {_GOLDEN_ACTIVATED!r}",
    )
    _require(
        result.sha == _GOLDEN_SKILL_SHA,
        f"result sha {result.sha!r} != {_GOLDEN_SKILL_SHA!r}",
    )
    _require(
        result.report_id == _GOLDEN_SKILL_REPORT_ID,
        f"result report_id {result.report_id!r} != {_GOLDEN_SKILL_REPORT_ID!r}",
    )
    _require(
        result.reason == _GOLDEN_ALLOWED,
        f"result reason {result.reason!r} != {_GOLDEN_ALLOWED!r}",
    )

    _require(
        pointer.calls_since(0) == _GOLDEN_LOW_RISK_CALLS,
        f"the pointer recorded {pointer.calls_since(0)!r}, "
        f"not {_GOLDEN_LOW_RISK_CALLS!r}",
    )
    _require(
        tuple(pointer.staged_shas) == (_GOLDEN_SKILL_SHA,),
        f"stage was handed {tuple(pointer.staged_shas)!r}, "
        f"not {(_GOLDEN_SKILL_SHA,)!r}",
    )
    _require(
        pointer.current == _GOLDEN_SKILL_SHA,
        f"the pointer reads {pointer.current!r}, not {_GOLDEN_SKILL_SHA!r}",
    )

    _require(
        _history_rows(state_dir) == _GOLDEN_WORKED_HISTORY[:1],
        f"the ledger holds {_history_rows(state_dir)!r}, "
        f"not {_GOLDEN_WORKED_HISTORY[:1]!r}",
    )
    _require(
        not (state_dir / _GOLDEN_CANARY_NAME).exists(),
        "a low-risk activation wrote a canary; the sha went live, it is not parked",
    )

    print(f"low risk: calls={pointer.calls_since(0)!r} current={pointer.current!r}")


def _check_high_risk(
    promoter: Promoter, pointer: _FakePointer, state_dir: Path
) -> None:
    """Golden 2: high risk parks the sha and calls nothing at all.

    Args:
        promoter: The promoter under test, already holding an activation.
        pointer: The fake it was handed.
        state_dir: Its private directory.
    """
    mark = len(pointer.calls)

    result = promoter.apply(_PROVIDER_REQUEST_OWNER)

    _require(
        result.outcome == ApplyOutcome.CANARIED,
        f"outcome {result.outcome!r} is not CANARIED",
    )
    _require(
        str(result.outcome) == _GOLDEN_CANARIED,
        f"outcome spells {str(result.outcome)!r}, not {_GOLDEN_CANARIED!r}",
    )
    _require(
        result.sha == _GOLDEN_PROVIDER_SHA,
        f"result sha {result.sha!r} != {_GOLDEN_PROVIDER_SHA!r}",
    )
    _require(
        result.report_id == _GOLDEN_PROVIDER_REPORT_ID,
        f"result report_id {result.report_id!r} != {_GOLDEN_PROVIDER_REPORT_ID!r}",
    )

    _require(
        pointer.calls_since(mark) == _GOLDEN_NO_CALLS,
        f"the canary called {pointer.calls_since(mark)!r} on the pointer; a "
        "stage with no promote behind it is leftover state nobody owns",
    )
    _require(
        pointer.current == _GOLDEN_SKILL_SHA,
        f"the canary moved the live pointer to {pointer.current!r}; it must "
        f"still read {_GOLDEN_SKILL_SHA!r}",
    )

    canary = _read_json(state_dir / _GOLDEN_CANARY_NAME)
    version = canary.get("version")
    _require(
        isinstance(version, int) and not isinstance(version, bool),
        f"canary version {version!r} is not an integer",
    )
    _require(
        version == _GOLDEN_STATE_VERSION,
        f"canary version {version!r} != {_GOLDEN_STATE_VERSION!r}",
    )
    _require(
        canary == _GOLDEN_CANARY_DOCUMENT,
        f"canary.json holds {canary!r}, not {_GOLDEN_CANARY_DOCUMENT!r}",
    )
    _require(
        _history_rows(state_dir) == _GOLDEN_WORKED_HISTORY[:2],
        f"the ledger holds {_history_rows(state_dir)!r}, "
        f"not {_GOLDEN_WORKED_HISTORY[:2]!r}",
    )

    print(f"high risk: calls={pointer.calls_since(mark)!r} canary={canary!r}")


def _check_refusal(promoter: Promoter, pointer: _FakePointer, state_dir: Path) -> None:
    """Golden 3: a failed report is refused, the owner included.

    Args:
        promoter: The promoter under test.
        pointer: The fake it was handed.
        state_dir: Its private directory.
    """
    decision = GatePolicy().decide(_REJECT_REQUEST)
    _require(
        decision.allow is False,
        f"the gate let a failed report through: {decision!r}",
    )
    _require(
        decision.reason == _GOLDEN_FAILED_REPORT,
        f"gate reason {decision.reason!r} != {_GOLDEN_FAILED_REPORT!r}",
    )

    mark = len(pointer.calls)

    result = promoter.apply(_REJECT_REQUEST)

    _require(
        result.outcome == ApplyOutcome.REJECTED,
        f"outcome {result.outcome!r} is not REJECTED",
    )
    _require(
        str(result.outcome) == _GOLDEN_REJECTED,
        f"outcome spells {str(result.outcome)!r}, not {_GOLDEN_REJECTED!r}",
    )
    _require(
        result.sha == _GOLDEN_REJECT_SHA,
        f"result sha {result.sha!r} != {_GOLDEN_REJECT_SHA!r}",
    )
    _require(
        result.report_id == _GOLDEN_REJECT_REPORT_ID,
        f"result report_id {result.report_id!r} != {_GOLDEN_REJECT_REPORT_ID!r}",
    )
    _require(
        result.reason == _GOLDEN_FAILED_REPORT,
        f"result reason {result.reason!r} != {_GOLDEN_FAILED_REPORT!r}; an "
        "owner gets no exemption from a report that failed",
    )

    _require(
        pointer.calls_since(mark) == _GOLDEN_NO_CALLS,
        f"the refusal called {pointer.calls_since(mark)!r} on the pointer",
    )
    _require(
        pointer.current == _GOLDEN_SKILL_SHA,
        f"the refusal moved the live pointer to {pointer.current!r}",
    )
    _require(
        _read_json(state_dir / _GOLDEN_CANARY_NAME) == _GOLDEN_CANARY_DOCUMENT,
        "the refusal rewrote the parked canary",
    )
    _require(
        _history_rows(state_dir) == _GOLDEN_WORKED_HISTORY,
        f"the ledger holds {_history_rows(state_dir)!r}, "
        f"not {_GOLDEN_WORKED_HISTORY!r}",
    )
    _require(
        _filenames(state_dir) == _GOLDEN_STATE_FILENAMES,
        f"the state directory holds {_filenames(state_dir)!r}, "
        f"not {_GOLDEN_STATE_FILENAMES!r}",
    )

    print(f"refusal: reason={result.reason!r} calls={pointer.calls_since(mark)!r}")


def _check_worked_example(state_dir: Path) -> None:
    """Run the three-request worked example against one pointer.

    Args:
        state_dir: A directory that does not exist yet.
    """
    pointer = _FakePointer()
    promoter = Promoter(activation=pointer, state_dir=state_dir)

    _check_low_risk(promoter, pointer, state_dir)
    _check_high_risk(promoter, pointer, state_dir)
    _check_refusal(promoter, pointer, state_dir)


def _check_in_path_bot_canary(state_dir: Path) -> None:
    """Golden 4: an in-path bot parks the same canary, from zero calls.

    The pointer is seeded with a sha of its own so that "unchanged" is an
    observation rather than a pair of ``None``s, and the call list is
    checked from empty, so this is the reading where the canary branch makes
    **zero** pointer calls in total rather than zero further ones.

    Args:
        state_dir: A directory that does not exist yet.
    """
    pointer = _FakePointer(current=_PRESET_POINTER_SHA)
    promoter = Promoter(activation=pointer, state_dir=state_dir)

    result = promoter.apply(_PROVIDER_REQUEST_BOT)

    _require(
        str(result.outcome) == _GOLDEN_CANARIED,
        f"outcome spells {str(result.outcome)!r}, not {_GOLDEN_CANARIED!r}",
    )
    _require(
        pointer.calls_since(0) == _GOLDEN_NO_CALLS,
        f"the in-path bot's canary called {pointer.calls_since(0)!r}",
    )
    _require(
        pointer.current == _GOLDEN_PRESET_CURRENT_SHA,
        f"the pointer reads {pointer.current!r}, not {_GOLDEN_PRESET_CURRENT_SHA!r}",
    )
    _require(
        _read_json(state_dir / _GOLDEN_CANARY_NAME) == _GOLDEN_CANARY_DOCUMENT,
        "an in-path bot parked a different canary than the owner did",
    )
    _require(
        _history_rows(state_dir) == _GOLDEN_BOT_HISTORY,
        f"the ledger holds {_history_rows(state_dir)!r}, not {_GOLDEN_BOT_HISTORY!r}",
    )
    _require(
        _filenames(state_dir) == _GOLDEN_STATE_FILENAMES,
        f"the state directory holds {_filenames(state_dir)!r}, "
        f"not {_GOLDEN_STATE_FILENAMES!r}",
    )

    print(f"in-path bot: calls={pointer.calls_since(0)!r} current={pointer.current!r}")


def _refuse_rollback(promoter: Promoter, report_id: str, label: str) -> None:
    """Call ``rollback`` expecting ``not-current``, and nothing else.

    Args:
        promoter: The promoter under test.
        report_id: Report to try to withdraw.
        label: Which of the two refusals this is, for the message.
    """
    try:
        promoter.rollback(report_id)
    except PromoterError as error:
        _require(
            error.code == _GOLDEN_NOT_CURRENT,
            f"{label}: code {error.code!r} != {_GOLDEN_NOT_CURRENT!r}",
        )
        print(f"{label}: PromoterError(code={error.code!r})")
    else:
        raise AssertionError(f"{label}: rollback succeeded instead of refusing")


def _check_rollback_ordering(state_dir: Path) -> None:
    """Golden 5: a ``rolled_back`` row consumes the ``previous`` slot.

    Apply A, apply B, and the log says B is current: A is buried, so
    ``rollback(A)`` is refused with the pointer still on B and the pointer
    machine untouched. ``rollback(B)`` is the one call, and the row it
    appends carries B's sha and B's report id -- looked up from the
    ``activated`` record, never read off ``rollback()``'s return value,
    which the fake makes a non-sha string on purpose.

    Then ``rollback(A)`` again. This is the half that separates the two
    implementations. Pairing by ``report_id`` would find A's own
    ``activated`` row still unpaired, and the pointer really is sitting on A
    now, so even the published-sha guard agrees -- the swap would run and
    put B back as a second generation of a snapshot already withdrawn.
    Positionally there is nothing left to withdraw at all: the last
    ``rolled_back`` row consumed the slot, and no ``activated`` row follows
    it. So the refusal is asserted three ways at once -- the code, the
    unchanged rollback count, and the fake still reading A.

    Args:
        state_dir: A directory that does not exist yet.
    """
    pointer = _FakePointer()
    promoter = Promoter(activation=pointer, state_dir=state_dir)

    first = promoter.apply(_ORDER_FIRST_REQUEST)
    _require(
        first.report_id == _GOLDEN_FIRST_REPORT_ID,
        f"result report_id {first.report_id!r} != {_GOLDEN_FIRST_REPORT_ID!r}",
    )
    _require(
        pointer.current == _GOLDEN_FIRST_SHA,
        f"after A the pointer reads {pointer.current!r}, not {_GOLDEN_FIRST_SHA!r}",
    )

    second = promoter.apply(_ORDER_SECOND_REQUEST)
    _require(
        second.report_id == _GOLDEN_SECOND_REPORT_ID,
        f"result report_id {second.report_id!r} != {_GOLDEN_SECOND_REPORT_ID!r}",
    )
    _require(
        pointer.current == _GOLDEN_SECOND_SHA,
        f"after B the pointer reads {pointer.current!r}, not {_GOLDEN_SECOND_SHA!r}",
    )

    # A is buried under B. Nothing may move.
    _refuse_rollback(promoter, _ORDER_FIRST_REQUEST.report_id, "rollback(A) under B")
    _require(
        pointer.rollback_count() == _GOLDEN_ROLLBACK_CALLS_BEFORE,
        f"the buried rollback ran {pointer.rollback_count()!r} times, "
        f"not {_GOLDEN_ROLLBACK_CALLS_BEFORE!r}",
    )
    _require(
        pointer.current == _GOLDEN_SECOND_SHA,
        f"the refused rollback left the pointer on {pointer.current!r}, "
        f"not {_GOLDEN_SECOND_SHA!r}",
    )
    _require(
        _history_rows(state_dir) == _GOLDEN_ORDER_HISTORY[:2],
        f"the refused rollback wrote {_history_rows(state_dir)!r}",
    )

    # B is current. One nullary call, and a row that names B.
    entry = promoter.rollback(_ORDER_SECOND_REQUEST.report_id)
    _require(
        entry == _GOLDEN_ROLLED_BACK_ENTRY,
        f"the appended row is {entry!r}, not {_GOLDEN_ROLLED_BACK_ENTRY!r}; its "
        "sha comes from the activated record, not from rollback()'s return",
    )
    _require(
        entry.action == _GOLDEN_ROLLED_BACK,
        f"row action {entry.action!r} != {_GOLDEN_ROLLED_BACK!r}",
    )
    _require(
        entry.sha == _GOLDEN_SECOND_SHA,
        f"row sha {entry.sha!r} != {_GOLDEN_SECOND_SHA!r}",
    )
    _require(
        entry.report_id == _GOLDEN_SECOND_REPORT_ID,
        f"row report_id {entry.report_id!r} != {_GOLDEN_SECOND_REPORT_ID!r}",
    )
    _require(
        pointer.rollback_count() == _GOLDEN_ROLLBACK_CALLS_AFTER,
        f"rollback() ran {pointer.rollback_count()!r} times, "
        f"not {_GOLDEN_ROLLBACK_CALLS_AFTER!r}",
    )
    _require(
        pointer.current == _GOLDEN_FIRST_SHA,
        f"after withdrawing B the pointer reads {pointer.current!r}, "
        f"not {_GOLDEN_FIRST_SHA!r}",
    )
    _require(
        pointer.previous == _GOLDEN_SECOND_SHA,
        f"the withdrawn sha sits at {pointer.previous!r}, not {_GOLDEN_SECOND_SHA!r}",
    )

    # The half that catches per-id pairing: A is not current either.
    _refuse_rollback(promoter, _ORDER_FIRST_REQUEST.report_id, "rollback(A) after B")
    _require(
        pointer.rollback_count() == _GOLDEN_ROLLBACK_CALLS_AFTER,
        f"rollback() ran {pointer.rollback_count()!r} times, "
        f"not {_GOLDEN_ROLLBACK_CALLS_AFTER!r}; a rolled_back row consumes the "
        "previous slot, so there is no second generation to walk back to",
    )
    _require(
        pointer.current == _GOLDEN_FIRST_SHA,
        f"the second refusal put {pointer.current!r} back on the pointer; it "
        f"must still read {_GOLDEN_FIRST_SHA!r}, and B must not be re-activated",
    )
    _require(
        _history_rows(state_dir) == _GOLDEN_ORDER_HISTORY,
        f"the ledger holds {_history_rows(state_dir)!r}, not {_GOLDEN_ORDER_HISTORY!r}",
    )
    _require(
        _filenames(state_dir) == _GOLDEN_ORDERING_FILENAMES,
        f"the state directory holds {_filenames(state_dir)!r}, "
        f"not {_GOLDEN_ORDERING_FILENAMES!r}",
    )

    print(
        f"ordering: rollback calls={pointer.rollback_count()!r} "
        f"current={pointer.current!r} previous={pointer.previous!r}"
    )


def _check_state_version() -> None:
    """Golden 6: the version both private documents carry is an integer."""
    _require(
        isinstance(PROMOTER_STATE_VERSION, int)
        and not isinstance(PROMOTER_STATE_VERSION, bool),
        f"PROMOTER_STATE_VERSION {PROMOTER_STATE_VERSION!r} is not an integer",
    )
    _require(
        PROMOTER_STATE_VERSION == _GOLDEN_STATE_VERSION,
        f"PROMOTER_STATE_VERSION {PROMOTER_STATE_VERSION!r} "
        f"!= {_GOLDEN_STATE_VERSION!r}",
    )
    print(f"PROMOTER_STATE_VERSION={PROMOTER_STATE_VERSION!r}")


def main() -> int:
    workspace = tempfile.TemporaryDirectory(prefix="molmcp-promote-regression-")
    try:
        root = Path(workspace.name)
        _check_state_version()
        _check_worked_example(root / "worked")
        _check_in_path_bot_canary(root / "in-path-bot")
        _check_rollback_ordering(root / "ordering")
    finally:
        workspace.cleanup()

    print("\nOK: low risk goes live, high risk parks, and one rolled_back row")
    print("consumes the previous slot -- no generation is walked back to.")
    return 0


def test_autonomous_harness_evolution_12_promote() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
