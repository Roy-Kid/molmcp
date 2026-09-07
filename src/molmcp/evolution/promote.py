"""Local promotion request, identity gate, and risk-graded pointer motion.

One evaluated snapshot arrives here as a :class:`PromotionRequest`: a
sha, the id of the report that judged it, who filed it, how risky the
change is, and whether that report accepted it. ``sha`` is always a
**full commit identity** — forty lowercase hexadecimal characters naming
one commit outright. It is a name, not a measurement: it carries no
unit and no scale, and an abbreviation, a tag, or an uppercase spelling
is refused at construction rather than resolved later.

:class:`GatePolicy` is a decision table over four fields the caller
already filled in — ``accepted``, ``author``, ``approved``,
``path_allowed``. It asks nobody who anyone is: no network, no
credential, no forge. A report that was not accepted is refused whoever
filed it, the owner included.

:class:`Promoter` turns an allowed request into exactly one of three
observable results, and writes two private JSON documents under a
``state_dir`` of the caller's naming:

* **low risk** — ``stage(sha)`` then a **nullary** ``promote()`` on the
  injected pointer machine, so the live pointer becomes that sha. The
  ledger gains an ``activated`` row.
* **high risk** — the sha is parked in ``canary.json`` and the pointer
  machine is not called at all, ``stage`` included: a staged sha with no
  promote behind it would be leftover state this module has no
  compensation for. The ledger gains a ``canaried`` row.
* **refused** — nothing moves, no canary is written, and the ledger
  gains a ``rejected`` row. The narrative of *why* belongs to the
  pattern wiki, so only the returned :class:`ApplyResult` carries the
  gate's reason.

The pointer machine is a **seam**. It arrives through the constructor
and is duck typed: this module imports no pointer type, no store, and no
MCP machinery, and the only names it may call are ``stage``,
``promote()`` and ``rollback()``. ``bind`` is never called — whoever
injected the activation has already bound it. An exception whose class
is named ``ActivationUnboundError`` is re-raised as
:class:`PromoterError` with code ``unbound``; the match is on the class
*name* for the same reason, and it guards the seam rather than the real
pointer machine, which cannot be constructed unbound at all.

:meth:`Promoter.rollback` reads one rule off the ledger and no other:
**a** ``rolled_back`` **row consumes the previous slot.** The current
activation is the last ``activated`` row with no ``rolled_back`` row
after it anywhere in the log — never the newest ``activated`` left
unpaired by ``report_id``. Pairing by report id would, after apply A,
apply B, ``rollback(B)``, swap B back in as a second generation of a
snapshot that had already been withdrawn.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

#: Bumped when either private document changes shape incompatibly. Both
#: ``canary.json`` and ``history.json`` carry it as an integer; a history
#: *row* carries none, because one document has one version.
PROMOTER_STATE_VERSION = 1

#: The two private documents, inside the caller's ``state_dir``.
_CANARY_NAME = "canary.json"
_HISTORY_NAME = "history.json"

#: A full commit identity: forty lowercase hexadecimal characters, whole.
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")

#: The four things the ledger records. ``rolled_back`` is ledger-only:
#: :meth:`Promoter.apply` never produces one, which is why
#: :class:`ApplyOutcome` carries the other three and not this.
_ACTIVATED = "activated"
_CANARIED = "canaried"
_REJECTED = "rejected"
_ROLLED_BACK = "rolled_back"

#: The gate's stable reasons. ``allowed`` is the one that opens a door.
_ALLOWED = "allowed"
_FAILED_REPORT = "failed-report"
_NEEDS_APPROVAL = "needs-approval"
_PATH_NOT_ALLOWED = "path-not-allowed"

#: Class name an unbound pointer machine is expected to raise. Matched by
#: name, never imported: the activation is a duck type, and importing the
#: module that defines it is the coupling this leaf exists to avoid.
_UNBOUND_ERROR_NAME = "ActivationUnboundError"


class PromoterError(ValueError):
    """Raised when a promotion cannot proceed, with a stable ``code``.

    The codes are the vocabulary a caller may branch on:
    ``invalid-sha`` (the identity is not a whole commit sha),
    ``unknown-report`` (no report is named by that id),
    ``canary-occupied`` (a different sha already holds the canary),
    ``canaried`` / ``rejected`` (that report never moved the pointer),
    ``not-current`` (that report is not the current activation), and
    ``unbound`` (the injected pointer machine was never bound).

    Args:
        message: Human-readable detail. Defaults to the code itself.
        code: Stable machine-readable code, keyword-only and required.
    """

    def __init__(self, message: str = "", *, code: str) -> None:
        super().__init__(message or code)
        self.code = code


class AuthorKind(StrEnum):
    """Who filed a promotion request.

    Read as a literal. This module does not verify that an ``owner``
    really owns the repository; a named upstream fills the field in.
    """

    OWNER = "owner"
    BOT = "bot"
    OTHER = "other"


class Risk(StrEnum):
    """How much of the harness a change can move.

    ``low`` is prose, examples, pure knowledge patterns, a
    non-executing overlay. ``high`` is a provider, a script, tool
    routing, a shared behaviour rule, a dependency. The classification
    itself is made upstream; this module only reads it.
    """

    LOW = "low"
    HIGH = "high"


class ApplyOutcome(StrEnum):
    """What :meth:`Promoter.apply` did.

    Three values, not four: ``apply`` never rolls anything back, so
    ``rolled_back`` is a ledger action with no outcome beside it.
    """

    ACTIVATED = _ACTIVATED
    CANARIED = _CANARIED
    REJECTED = _REJECTED


@dataclass(frozen=True, slots=True)
class PromotionRequest:
    """One snapshot put forward for promotion, with its judgement.

    Frozen and slotted: a request is a value somebody hands over, not a
    record this module edits. Illegal shapes are refused at
    construction, so a request that exists is a request that can be
    decided.

    Args:
        sha: Full commit identity of the snapshot — forty lowercase
            hexadecimal characters, a whole commit and never a tag or
            an abbreviation. No unit; it names a commit, it does not
            measure one.
        report_id: Identity of the evaluation report that judged that
            sha. Opaque and non-blank.
        author: Who filed the request.
        risk: How far the change can reach.
        accepted: The report's verdict, carried in rather than
            recomputed. No default: a caller that forgot it is not
            granted a pass.
        approved: Whether an ``other`` author already has the owner's
            approval. Ignored for ``owner`` and ``bot``.
        path_allowed: Whether a ``bot`` stayed inside the paths it may
            write. Ignored for ``owner`` and ``other``.

    Raises:
        PromoterError: ``sha`` is not a full commit identity
            (``invalid-sha``), or ``report_id`` names no report
            (``unknown-report``).
    """

    sha: str
    report_id: str
    author: AuthorKind
    risk: Risk
    accepted: bool
    approved: bool = False
    path_allowed: bool = True

    def __post_init__(self) -> None:
        """Refuse an identity this layer cannot act on."""
        if not isinstance(self.sha, str) or not _SHA_PATTERN.fullmatch(self.sha):
            raise PromoterError(
                f"{self.sha!r} is not a full 40-character lowercase commit sha",
                code="invalid-sha",
            )
        if not isinstance(self.report_id, str) or not self.report_id.strip():
            raise PromoterError(
                "a promotion request needs the id of the report that judged it",
                code="unknown-report",
            )


@dataclass(frozen=True, slots=True)
class GateDecision:
    """The gate's answer: one flag and one stable reason.

    Args:
        allow: Whether the request may move a pointer at all.
        reason: One of ``allowed``, ``failed-report``,
            ``needs-approval``, ``path-not-allowed``.
    """

    allow: bool
    reason: str


@dataclass(frozen=True, slots=True)
class GatePolicy:
    """The owner / bot / other table, decided from the request alone.

    A table and not a lookup: :meth:`decide` opens no socket, reads no
    credential, and asks no forge who anyone is. Deciding the same
    request twice gives the same answer because nothing outside it was
    consulted.
    """

    def decide(self, request: PromotionRequest) -> GateDecision:
        """Rule on one request.

        The order is the contract. ``accepted`` is read first, so
        approval cannot buy a failed report in and the owner gets no
        exemption from one. After that each author kind answers to its
        own field: ``other`` to ``approved``, ``bot`` to
        ``path_allowed``, ``owner`` to neither.

        Args:
            request: The promotion request, carrying the full commit
                identity under judgement and the report that judged it.

        Returns:
            A frozen :class:`GateDecision`. ``allow`` is ``True`` only
            when the report was accepted and the author's own condition
            holds.
        """
        if not request.accepted:
            return GateDecision(allow=False, reason=_FAILED_REPORT)
        if request.author == AuthorKind.OTHER and not request.approved:
            return GateDecision(allow=False, reason=_NEEDS_APPROVAL)
        if request.author == AuthorKind.BOT and not request.path_allowed:
            return GateDecision(allow=False, reason=_PATH_NOT_ALLOWED)
        return GateDecision(allow=True, reason=_ALLOWED)


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One row of the promotion ledger.

    Args:
        sha: Full commit identity the row is about.
        report_id: Report that judged that sha. Every row carries the
            pair; neither half is optional.
        action: One of ``activated``, ``canaried``, ``rejected``,
            ``rolled_back``. The refusal's reason is deliberately
            absent — the ledger keeps three fields, the narrative lives
            in the wiki.
    """

    sha: str
    report_id: str
    action: str


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What one :meth:`Promoter.apply` did, and why.

    Args:
        outcome: Which of the three branches ran.
        sha: Full commit identity the branch acted on.
        report_id: Report that judged it.
        reason: The gate's reason, ``allowed`` when it opened. This is
            the only place a refusal's reason is returned; it is kept
            out of the ledger on purpose.
    """

    outcome: ApplyOutcome
    sha: str
    report_id: str
    reason: str


class _ActivationHandle(Protocol):
    """The three names a :class:`Promoter` may call on the pointer machine.

    Structural on purpose, so no pointer type is imported here.
    ``bind`` is absent because it is never called: binding happened
    before the activation was handed over.
    """

    def stage(self, sha: str, /) -> object:
        """Park *sha* as the staged candidate."""

    def promote(self) -> object:
        """Make the staged sha the live one. Nullary by contract."""

    def rollback(self) -> object:
        """Swap the live sha with the previous one. Nullary by contract."""


@contextmanager
def _unbound_guard() -> Iterator[None]:
    """Re-raise an unbound pointer machine as a :class:`PromoterError`.

    Matched on ``type(exc).__name__`` so this module imports no pointer
    type. Against the real activation the branch is unreachable — one
    cannot be constructed unbound — so what it guards is the injection
    seam, where anything duck typed may arrive.

    Yields:
        Nothing; the block runs inside the guard.

    Raises:
        PromoterError: Code ``unbound``, chained to the original.
    """
    try:
        yield
    except Exception as exc:
        if type(exc).__name__ == _UNBOUND_ERROR_NAME:
            raise PromoterError(
                f"the injected activation is not bound: {exc}",
                code="unbound",
            ) from exc
        raise


def _read_document(path: Path) -> dict[str, object]:
    """Return the JSON object at *path*, or ``{}`` when it is not there.

    The trust boundary for both private documents: bytes are decoded
    here and narrowed to a mapping before any caller sees them.

    Args:
        path: Document to read.

    Returns:
        The decoded object, or an empty mapping when the file is absent.

    Raises:
        ValueError: The file exists but holds no readable JSON object.
            A document nobody can read is not one this module may
            overwrite, and corruption is not a promotion decision, so it
            carries no :class:`PromoterError` code.
    """
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path} does not hold readable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return payload


def _write_document(path: Path, document: dict[str, object]) -> None:
    """Swap *document* into *path* whole.

    Written to a ``.partial`` sibling and moved with :func:`os.replace`,
    so a reader sees either the previous document or the new one and
    never a truncated file under the live name.

    Args:
        path: Live document path.
        document: Object to store, unknown keys already merged in.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.partial")
    partial.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(partial, path)


def _entry_of(row: object, path: Path) -> HistoryEntry:
    """Narrow one stored row into a :class:`HistoryEntry`.

    Args:
        row: Decoded ledger row.
        path: Document it came from, for the message.

    Returns:
        The row's sha / report id / action triple. Keys this module does
        not know are ignored here and kept verbatim on rewrite.

    Raises:
        ValueError: The row is not an object, or is missing one of the
            three fields every row must carry.
    """
    if not isinstance(row, dict):
        raise ValueError(f"{path} holds a history row that is not an object")
    sha = row.get("sha")
    report_id = row.get("report_id")
    action = row.get("action")
    if not (
        isinstance(sha, str) and isinstance(report_id, str) and isinstance(action, str)
    ):
        raise ValueError(f"{path} holds a history row without sha, report_id, action")
    return HistoryEntry(sha=sha, report_id=report_id, action=action)


def _current_activation(entries: Sequence[HistoryEntry]) -> HistoryEntry | None:
    """Return the activation a rollback may still pop, or ``None``.

    The mechanical rule, and the whole of it: find the last
    ``rolled_back`` row, then the last ``activated`` row after it. A
    ``rolled_back`` row *consumes* the pointer machine's previous slot,
    so an ``activated`` row with any ``rolled_back`` row behind it names
    a generation nobody can reach any more.

    Pairing by ``report_id`` instead would pass for one activation and
    then, after apply A, apply B, ``rollback(B)``, offer A as still
    rollable — swapping B back in as a second generation.

    Args:
        entries: The ledger, oldest first.

    Returns:
        The current activation row, or ``None`` when the last rollback
        left none behind.
    """
    last_rolled_back = -1
    for index, entry in enumerate(entries):
        if entry.action == _ROLLED_BACK:
            last_rolled_back = index
    for entry in reversed(entries[last_rolled_back + 1 :]):
        if entry.action == _ACTIVATED:
            return entry
    return None


class Promoter:
    """Moves one pointer, or parks one sha, per promotion request.

    Both seams are keyword-only and neither has a default. There is no
    fallback activation factory — building a real pointer machine here
    would drag its store into a leaf that exists to stay out of it — and
    no working-directory fallback for the state.

    The state directory is the Promoter's own, separate from any lock
    directory, and holds exactly two documents: ``canary.json`` and
    ``history.json``. Neither is created before the first write.

    Args:
        activation: The pointer machine, duck typed. Only ``stage``,
            ``promote()`` and ``rollback()`` are ever called; ``bind``
            is not, because the caller has already bound it.
        state_dir: Directory for the two private documents. Expanded
            and resolved at construction; created on first write.
    """

    def __init__(
        self,
        *,
        activation: _ActivationHandle,
        state_dir: Path | str,
    ) -> None:
        """Store the two seams.

        Args:
            activation: Duck-typed pointer machine, already bound.
            state_dir: Private state directory.
        """
        self._activation = activation
        self._state_dir = Path(state_dir).expanduser().resolve()
        self._gate = GatePolicy()

    # -- reading ----------------------------------------------------------

    @property
    def _canary_path(self) -> Path:
        return self._state_dir / _CANARY_NAME

    @property
    def _history_path(self) -> Path:
        return self._state_dir / _HISTORY_NAME

    def _entries(self) -> tuple[HistoryEntry, ...]:
        """Return the ledger, oldest first; ``()`` when there is none."""
        document = _read_document(self._history_path)
        stored = document.get("entries", [])
        if not isinstance(stored, list):
            raise ValueError(f"{self._history_path} entries is not a list")
        return tuple(_entry_of(row, self._history_path) for row in stored)

    # -- writing ----------------------------------------------------------

    def _append(self, entry: HistoryEntry) -> None:
        """Append one row, keeping every key this module does not own.

        Rows already stored are copied through untouched, so another
        writer's per-row keys survive; unknown top-level keys are merged
        back in the same way.
        """
        document = _read_document(self._history_path)
        stored = document.get("entries", [])
        rows = list(stored) if isinstance(stored, list) else []
        rows.append(
            {
                "sha": entry.sha,
                "report_id": entry.report_id,
                "action": entry.action,
            }
        )
        _write_document(
            self._history_path,
            {**document, "version": PROMOTER_STATE_VERSION, "entries": rows},
        )

    def _park_canary(self, request: PromotionRequest) -> None:
        """Write the canary pointer, refusing a sha that would evict another.

        Args:
            request: The high-risk request, carrying the full commit
                identity to park.

        Raises:
            PromoterError: Code ``canary-occupied`` when a different sha
                already holds it. The same sha may re-take its own.
        """
        document = _read_document(self._canary_path)
        parked = document.get("sha")
        if isinstance(parked, str) and parked != request.sha:
            raise PromoterError(
                f"the canary already holds {parked}; {request.sha} cannot take it",
                code="canary-occupied",
            )
        _write_document(
            self._canary_path,
            {
                **document,
                "version": PROMOTER_STATE_VERSION,
                "sha": request.sha,
                "report_id": request.report_id,
            },
        )

    # -- the two public moves ---------------------------------------------

    def apply(self, request: PromotionRequest) -> ApplyResult:
        """Decide one request and do the one thing it earns.

        The gate rules first. A refused request makes **zero** calls on
        the pointer machine and writes no canary; it is recorded as
        ``rejected`` and the reason comes back on the result.

        An allowed low-risk request calls ``stage(sha)`` and then the
        nullary ``promote()`` — in that order, and ``promote`` is never
        handed the sha, because the staged one is already the pointer
        machine's to read. It is recorded as ``activated``.

        An allowed high-risk request parks the sha in ``canary.json``
        and makes **zero** calls, ``stage`` included: a staged sha with
        no promote behind it is leftover state nothing here compensates
        for. It is recorded as ``canaried``, and the live pointer keeps
        the value it had.

        ``bind`` is never called on any branch.

        Args:
            request: The request to rule on, carrying the full commit
                identity and the id of the report that judged it.

        Returns:
            A frozen :class:`ApplyResult` naming the branch that ran,
            the sha and report it acted on, and the gate's reason.

        Raises:
            PromoterError: Code ``canary-occupied`` when a different sha
                already holds the canary — nothing is written and
                nothing is called. Code ``unbound`` when the injected
                pointer machine was never bound.
        """
        decision = self._gate.decide(request)
        if not decision.allow:
            return self._record(request, ApplyOutcome.REJECTED, decision.reason)
        if request.risk == Risk.HIGH:
            self._park_canary(request)
            return self._record(request, ApplyOutcome.CANARIED, decision.reason)
        with _unbound_guard():
            self._activation.stage(request.sha)
            self._activation.promote()
        return self._record(request, ApplyOutcome.ACTIVATED, decision.reason)

    def rollback(self, report_id: str) -> HistoryEntry:
        """Withdraw the activation *report_id* put in place, if it still is.

        Five refusals come before any call, and each makes none:

        1. No row names ``report_id`` — ``unknown-report``.
        2. Its most recent row is ``canaried`` or ``rejected`` — that
           report never moved the pointer, so the code is ``canaried``
           or ``rejected``. Neither clears ``canary.json``.
        3. There is no current activation left, because the last
           rollback consumed it — ``not-current``.
        4. The current activation is some other report — ``not-current``.
           A newer activation buries an older one.
        5. The pointer machine publishes a sha (as ``current``, or
           ``active``) that is not the one the ledger expects — somebody
           else promoted since, and this is not ours to pop.

        Only then is the nullary ``rollback()`` called. Its return value
        is ignored: the row appended carries the sha and report id of
        the ``activated`` record looked up in step 3, which is the
        identity actually being withdrawn.

        Args:
            report_id: Report whose activation should be withdrawn.

        Returns:
            The appended ``rolled_back`` row, carrying the full commit
            identity that was withdrawn.

        Raises:
            PromoterError: Codes ``unknown-report``, ``canaried``,
                ``rejected``, ``not-current`` as listed above, or
                ``unbound`` when the injected pointer machine was never
                bound.
        """
        entries = self._entries()
        mine = [entry for entry in entries if entry.report_id == report_id]
        if not mine:
            raise PromoterError(
                f"no promotion history names report {report_id!r}",
                code="unknown-report",
            )
        latest = mine[-1]
        if latest.action in (_CANARIED, _REJECTED):
            raise PromoterError(
                f"report {report_id!r} was {latest.action}; it moved no pointer",
                code=latest.action,
            )
        current = _current_activation(entries)
        if current is None or current.report_id != report_id:
            raise PromoterError(
                f"report {report_id!r} is not the current activation",
                code="not-current",
            )
        published = getattr(
            self._activation, "current", getattr(self._activation, "active", None)
        )
        if published is not None and published != current.sha:
            raise PromoterError(
                f"the pointer is on {published}, not on {current.sha}",
                code="not-current",
            )
        with _unbound_guard():
            self._activation.rollback()
        entry = HistoryEntry(
            sha=current.sha,
            report_id=current.report_id,
            action=_ROLLED_BACK,
        )
        self._append(entry)
        return entry

    # -- shared tail -------------------------------------------------------

    def _record(
        self,
        request: PromotionRequest,
        outcome: ApplyOutcome,
        reason: str,
    ) -> ApplyResult:
        """Append the row for *outcome* and return the matching result."""
        self._append(
            HistoryEntry(
                sha=request.sha,
                report_id=request.report_id,
                action=str(outcome),
            )
        )
        return ApplyResult(
            outcome=outcome,
            sha=request.sha,
            report_id=request.report_id,
            reason=reason,
        )
