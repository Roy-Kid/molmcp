"""EpisodeReceipt V1: redaction, a TTL'd local log, and default-off consent.

An *episode receipt* is what survives one evolution episode: six frozen
fields, no reasoning. The persistence contract is :data:`RECEIPT_FIELDS`
and nothing else — a payload's every other key (a chain of thought, a
pattern key a later spec may want) is dropped on the way in rather than
stored and forgotten. A receipt is kept for a fixed TTL (*time to
live*) of :data:`RECEIPT_TTL_DAYS` days and is then pruned.

Three disciplines hold this module together:

* *Redaction happens at construction.* :meth:`EpisodeReceipt.__post_init__`
  runs :func:`redact_text` over ``task`` and ``error_detail``, so a
  receipt carrying an unredacted home path or token is not a state this
  type can be in.
* *Redaction is handed its inputs.* :func:`redact_text` consults
  :meth:`~pathlib.Path.home` and nothing else. A redactor that went
  hunting through the process' variables would learn secrets nobody gave
  it — the discipline ``tests/test_no_env_switches.py`` enforces
  elsewhere, for the same reason.
* *The fence is for the LLM, not the disk.* ``fence_untrusted`` is
  imported inside :func:`upload_payload` alone. Bytes written by
  :meth:`ReceiptLog.append` are redacted plaintext, never fenced, and
  this package does not re-export the helper.

Leaf module: standard library only. The atomic write below copies the
shape of the adoption ledger's swap (a ``.partial`` sibling, then
``os.replace``) without importing it; that ledger is a resumable
single-file journal for one migration, which is not this.

Attributes:
    RECEIPT_VERSION: Persistence contract version stamped on receipts.
    RECEIPT_TTL_DAYS: Receipt lifetime in days (14). A module constant,
        not a settings knob.
    RECEIPT_FIELDS: The V1 field names, in order.
    SHARE_RECEIPTS_KEY: Name of the top-level consent setting a later
        spec adds to the schema. Nothing here reads settings.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: Version stamped into every receipt this module writes. A document
#: claiming a higher version is still read for V1 fields only.
RECEIPT_VERSION = 1

#: Receipt lifetime in **days** — the TTL (time to live) after which a
#: receipt is pruned by the next :meth:`ReceiptLog.append` or
#: :meth:`ReceiptLog.prune`. A module constant on purpose, not a
#: settings knob: the retention window is part of the contract a caller
#: accepts by writing here, and a per-machine value would make "how long
#: do you keep this?" unanswerable.
RECEIPT_TTL_DAYS = 14

#: Name of the top-level boolean consent setting (shape of
#: ``indexWorkspace``, not a nested ``evolution.shareReceipts``). Only a
#: string here: this module never reads settings, and a later spec owns
#: adding the key to the schema.
SHARE_RECEIPTS_KEY = "shareReceipts"

#: The V1 persistence contract: exactly these names, in this order.
#: :class:`EpisodeReceipt` declares the same six, and ``to_dict`` emits
#: the same six. One more or one fewer is a different format.
RECEIPT_FIELDS: tuple[str, ...] = (
    "version",
    "episode_id",
    "created_at",
    "outcome",
    "task",
    "error_detail",
)

#: The only outcomes a V1 receipt may carry.
_OUTCOMES: frozenset[str] = frozenset({"ok", "failed", "skipped"})

#: Every :data:`RECEIPT_FIELDS` entry that is text rather than a number.
_TEXT_FIELDS: tuple[str, ...] = tuple(
    name for name in RECEIPT_FIELDS if name != "version"
)

#: An episode id is one path segment: letters, digits, dot, underscore,
#: dash. No separator and no whitespace, so an id cannot climb out of
#: the log root, and the ``.json`` suffix means even ``..`` names a
#: file rather than the parent directory.
_EPISODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

_HOME_PLACEHOLDER = "~"
_USER_PLACEHOLDER = "[USER]"
_SECRET_PLACEHOLDER = "[REDACTED]"

#: Whole-token secret shapes, prefix included. ``Bearer`` is the odd one
#: out: its secret follows whitespace, so scheme and credential are
#: swallowed together. The lookbehind keeps ``sk-`` from firing inside a
#: word such as ``disk-usage``. Every prefix demands at least one body
#: character, so a bare ``sk-`` is left alone. No two prefixes can begin
#: at the same position, so the order inside the alternation is
#: presentation, not precedence.
_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:"
    r"(?:github_pat_|ghp_|gho_|sk-|xox[baprs]-)[A-Za-z0-9_-]+"
    r"|Bearer\s+[A-Za-z0-9._~+/=-]+"
    r")"
)


class ReceiptError(ValueError):
    """Raised when something is not a receipt this module will accept.

    Covers a payload that is not an object, a missing ``episode_id`` /
    ``created_at`` / ``outcome``, a field of the wrong type, an outcome
    outside ``ok`` / ``failed`` / ``skipped``, and an episode id that is
    not a single path segment. Owned here: reusing another subsystem's
    error would make a receipt problem look like that subsystem's.
    """


def redact_text(text: str) -> str:
    """Return *text* with home path, username, and secrets replaced.

    Three substitutions run in a fixed order, and the order is part of
    the contract:

    1. Every occurrence of ``str(Path.home())`` becomes ``~``, so
       ``/Users/alice/work`` reads ``~/work``.
    2. Every remaining ``Path.home().name`` becomes ``[USER]``.
    3. Every whole secret token becomes ``[REDACTED]``, prefix included:
       ``ghp_``, ``gho_``, ``github_pat_``, ``sk-``, ``Bearer`` (with the
       credential after it), and ``xox[baprs]-``.

    Step 1 must precede step 2. Replacing the bare username first would
    chop the home path into a shape step 1 could no longer match, and
    the result would name a directory layout it had already promised to
    hide.

    Only the home directory is consulted; this function is never told to
    go looking for secrets it was not handed.

    Args:
        text: Text that may embed a home path, a username, or a token.

    Returns:
        A new string. *text* is not modified.
    """
    home = Path.home()
    redacted = text.replace(str(home), _HOME_PLACEHOLDER)
    # A home of ``/`` has an empty name: no username to hide, and
    # ``str.replace("")`` would splice the placeholder between every
    # character of the text.
    if home.name:
        redacted = redacted.replace(home.name, _USER_PLACEHOLDER)
    return _TOKEN_PATTERN.sub(_SECRET_PLACEHOLDER, redacted)


@dataclass(frozen=True, slots=True, kw_only=True)
class EpisodeReceipt:
    """One episode reduced to the six redacted fields of V1.

    Field names and order are exactly :data:`RECEIPT_FIELDS`. There is
    no ``extras`` mapping and no ``pattern_key``: an escape hatch for
    "just one more key" is how a reasoning trace ends up on disk.

    Every field is keyword-only (the dataclass is ``kw_only=True``), so
    callers must construct by name — ``EpisodeReceipt("ep-001", ...)``
    is a ``TypeError``. That is what lets ``version`` carry a default
    while the required fields follow it, and it keeps the declaration
    order that pins the persistence contract from doubling as a
    call-site argument order.

    Construction redacts. :func:`redact_text` runs over ``task`` and
    ``error_detail`` in ``__post_init__``, which is the only way in —
    :meth:`from_dict` builds through the same constructor — so an
    unredacted receipt cannot exist.

    Attributes:
        version: Contract version; defaults to :data:`RECEIPT_VERSION`.
        episode_id: Caller-supplied single-segment episode identifier.
        created_at: UTC ISO-8601 instant, e.g.
            ``2026-09-04T00:00:00+00:00``.
        outcome: One of ``ok``, ``failed``, ``skipped``.
        task: Redacted user-visible task summary — a description of the
            work, never the reasoning that produced it.
        error_detail: Redacted plaintext error; ``""`` on success.

    Raises:
        ReceiptError: ``version`` is not an ``int``, one of the five
            text fields is not a ``str``, or ``outcome`` is not ``ok`` /
            ``failed`` / ``skipped``. Whether ``episode_id`` is a legal
            filename is :meth:`ReceiptLog.append`'s question, not this
            constructor's.

    Examples:
        >>> receipt = EpisodeReceipt(
        ...     episode_id="ep-001",
        ...     created_at="2026-09-04T00:00:00+00:00",
        ...     outcome="ok",
        ...     task="rebuild the catalog index",
        ... )
        >>> receipt.version
        1
        >>> receipt.error_detail
        ''
    """

    version: int = RECEIPT_VERSION
    episode_id: str
    created_at: str
    outcome: str
    task: str = ""
    error_detail: str = ""

    def __post_init__(self) -> None:
        """Validate the six fields, then redact the two free-text ones."""
        if not isinstance(self.version, int):
            raise ReceiptError(f"receipt version is not an integer: {self.version!r}")
        for name in _TEXT_FIELDS:
            if not isinstance(getattr(self, name), str):
                raise ReceiptError(f"receipt {name} is not a string")
        if self.outcome not in _OUTCOMES:
            raise ReceiptError(f"unknown receipt outcome: {self.outcome!r}")
        object.__setattr__(self, "task", redact_text(self.task))
        object.__setattr__(self, "error_detail", redact_text(self.error_detail))

    def to_dict(self) -> dict[str, str | int]:
        """Return exactly :data:`RECEIPT_FIELDS` as a new plain dict.

        Returns:
            A fresh mapping of the six V1 fields in
            :data:`RECEIPT_FIELDS` order, values already redacted and in
            plaintext (never fenced). :meth:`ReceiptLog.append`
            serializes this mapping, so the order here is also the key
            order on disk.
        """
        return {name: getattr(self, name) for name in RECEIPT_FIELDS}

    @classmethod
    def from_dict(cls, payload: object) -> EpisodeReceipt:
        """Build a receipt from *payload*, dropping every unknown key.

        Only :data:`RECEIPT_FIELDS` are read. Anything else the document
        carries — ``cot``, ``reasoning``, ``thought``,
        ``chain_of_thought``, ``pattern_key``, an ``extras`` mapping — is
        discarded here rather than reshaped into a field, so a future
        version of this format cannot smuggle a reasoning trace through
        an older reader. A document declaring a higher ``version`` is
        read for the V1 fields only; the number it carries is kept as
        found, not rewritten to :data:`RECEIPT_VERSION`.

        Args:
            payload: Decoded JSON document, expected to be an object.

        Returns:
            A new :class:`EpisodeReceipt` with ``task`` and
            ``error_detail`` already redacted.

        Raises:
            ReceiptError: *payload* is not a mapping, or lacks
                ``episode_id`` / ``created_at`` / ``outcome``, or fails
                the constructor's own checks.
        """
        if not isinstance(payload, dict):
            raise ReceiptError(
                f"receipt payload is not an object: {type(payload).__name__}"
            )
        kept = {name: payload[name] for name in RECEIPT_FIELDS if name in payload}
        for required in ("episode_id", "created_at", "outcome"):
            if required not in kept:
                raise ReceiptError(f"receipt payload has no {required}")
        return cls(**kept)


class ReceiptLog:
    """A directory of receipts, one JSON file each, pruned at the TTL.

    *Root* is required. There is no working-directory fallback and no
    cache-directory default: a log that guesses where to write would
    scatter receipts across whichever directory a process happened to
    start in. The caller names the directory or there is no log.

    Each receipt is ``<root>/<episode_id>.json``, written through a
    ``.partial`` sibling and swapped in with ``os.replace``, so a reader
    sees either the previous document or the whole new one. What lands
    on disk is redacted plaintext; the fence belongs to
    :func:`upload_payload`.

    :meth:`append` prunes after every write, which keeps the TTL from
    being a second call a caller can forget.

    Args:
        root: Directory holding the receipts. Expanded and resolved at
            construction; created on first :meth:`append`.

    Raises:
        TypeError: ``root`` is omitted or ``None``.
    """

    def __init__(self, root: Path | str) -> None:
        """Resolve and store *root*.

        Args:
            root: Receipt directory (string or path).

        Raises:
            TypeError: ``root`` is omitted (it has no default) or
                ``None``.
        """
        if root is None:
            raise TypeError("root is required")
        self._root = Path(root).expanduser().resolve()

    def append(self, receipt: EpisodeReceipt, *, now: datetime | None = None) -> Path:
        """Write *receipt* atomically, then prune what has expired.

        A second append under the same ``episode_id`` replaces the first
        document: an episode has one receipt, not a history.

        :meth:`prune` runs after the swap, with the same *now*, so the
        TTL is enforced by every write instead of by a second call a
        caller has to remember.

        Args:
            receipt: Receipt to persist. Its text is already redacted.
            now: Timezone-aware instant to prune against; defaults to
                the current UTC time. Injected by callers that must not
                read a clock, and forwarded unchanged to :meth:`prune`,
                which is where a naive value would fail.

        Returns:
            Path of the written ``<episode_id>.json``. A receipt whose
            own ``created_at`` is already past the TTL is written and
            then removed by this call's own prune, so the returned path
            can name a file that is no longer there.

        Raises:
            ReceiptError: ``receipt.episode_id`` is not one path
                segment.
            OSError: The directory could not be created or the document
                could not be written.
        """
        path = self._path_for(receipt.episode_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.partial")
        partial.write_text(
            json.dumps(receipt.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(partial, path)
        self.prune(now=now)
        return path

    def prune(self, *, now: datetime | None = None) -> int:
        """Delete receipts older than :data:`RECEIPT_TTL_DAYS`.

        The threshold is *now* minus the TTL. A file is deleted only
        when its ``created_at`` parses *and* falls strictly before that
        threshold, so a stamp sitting exactly on it survives. A stored
        ``created_at`` written without a UTC offset is read as UTC,
        which is what the field is defined to be.

        Anything undatable — malformed JSON, a document that is not an
        object, a missing or unparseable ``created_at`` — stays where it
        is and is not counted. A file nobody can date is not a file
        anybody may delete, and treating it as expired would silently
        destroy the one receipt whose problem is worth looking at. It is
        the same reason :meth:`list` skips what it cannot parse rather
        than raising: together, the two keep one corrupt document from
        wedging the log.

        Args:
            now: Timezone-aware instant the threshold is measured back
                from; defaults to the current UTC time.

        Returns:
            How many receipts were deleted. Zero when the root does not
            exist yet.

        Raises:
            TypeError: *now* is naive (has no UTC offset) and the root
                holds at least one datable receipt — a naive threshold
                cannot be compared with the aware stamps on disk.
        """
        if not self._root.is_dir():
            return 0
        threshold = (now or datetime.now(UTC)) - timedelta(days=RECEIPT_TTL_DAYS)
        removed = 0
        for path in sorted(self._root.glob("*.json")):
            payload = _load_object(path)
            created_at = None if payload is None else _created_at(payload)
            if created_at is None or created_at >= threshold:
                continue
            path.unlink()
            removed += 1
        return removed

    def list(self) -> tuple[EpisodeReceipt, ...]:
        """Return every readable receipt, oldest ``created_at`` first.

        Reads one level of ``*.json`` under the root, through
        :meth:`EpisodeReceipt.from_dict`, so unknown keys are already
        gone. A file that does not parse — bad JSON, no ``episode_id``,
        an outcome outside the three — is skipped, not raised on:
        :meth:`prune` deliberately never removes such a file, so one
        corrupt document must not make the whole log unreadable forever.

        Returns:
            Receipts sorted by the ``created_at`` string, which orders
            them chronologically because V1 stamps are UTC ISO-8601.
            Empty when the root does not exist yet.
        """
        if not self._root.is_dir():
            return ()
        found = (_read_receipt(path) for path in sorted(self._root.glob("*.json")))
        return tuple(
            sorted(
                (receipt for receipt in found if receipt is not None),
                key=lambda receipt: receipt.created_at,
            )
        )

    def _path_for(self, episode_id: str) -> Path:
        """Return ``<root>/<id>.json``, refusing an id with a separator."""
        if _EPISODE_ID_PATTERN.fullmatch(episode_id) is None:
            raise ReceiptError(f"episode id is not one path segment: {episode_id!r}")
        return self._root / f"{episode_id}.json"


@dataclass(frozen=True, slots=True)
class Consent:
    """Whether the caller agreed to send receipts anywhere.

    Default off, and the default is the whole point: an omitted consent
    and a refused one mean the same thing, so nothing leaves the machine
    because a caller forgot to decide.

    A value the caller constructs and passes to :func:`upload_payload`.
    :data:`SHARE_RECEIPTS_KEY` names the setting a later spec will read
    it from; nothing in this module reads settings today.

    Attributes:
        share_receipts: ``True`` only when sharing was asked for.
    """

    share_receipts: bool = False


def upload_payload(
    receipt: EpisodeReceipt, consent: Consent | None = None
) -> dict[str, str | int] | None:
    """Return an upload payload, or ``None`` when consent is not granted.

    Consent is a value the caller passes, not something read from
    settings here — :data:`SHARE_RECEIPTS_KEY` only names the key a
    later spec will add. Omitting the argument, passing ``Consent()``,
    and passing ``Consent(share_receipts=False)`` all mean the same
    ``None``.

    When granted, ``error_detail`` is wrapped by ``fence_untrusted`` —
    imported inside this function, the only place the fence belongs. The
    payload is headed for a model that must read an attacker-influenced
    error as data, not as instructions. The other fields stay redacted
    plaintext, and the copy on disk is never fenced.

    Args:
        receipt: Receipt to offer. Already redacted by construction.
        consent: Caller's decision; ``None`` means no.

    Returns:
        A new dict of exactly :data:`RECEIPT_FIELDS` with a fenced
        ``error_detail``, or ``None`` when consent was not granted.
    """
    if consent is None or not consent.share_receipts:
        return None

    from molmcp.helpers.text import fence_untrusted

    return {
        **receipt.to_dict(),
        "error_detail": fence_untrusted(
            receipt.error_detail, label="receipt error_detail"
        ),
    }


def _load_object(path: Path) -> dict[str, object] | None:
    """Return the JSON object at *path*, or ``None`` when unreadable.

    The trust boundary: bytes on disk are decoded here and narrowed to a
    mapping before any caller sees them.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _created_at(payload: dict[str, object]) -> datetime | None:
    """Return *payload*'s ``created_at``, or ``None`` when undatable.

    A stamp without an offset is read as UTC, which is what
    ``created_at`` is defined to be.
    """
    raw = payload.get("created_at")
    if not isinstance(raw, str):
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _read_receipt(path: Path) -> EpisodeReceipt | None:
    """Return the receipt stored at *path*, or ``None`` when it is not one."""
    payload = _load_object(path)
    if payload is None:
        return None
    try:
        return EpisodeReceipt.from_dict(payload)
    except ReceiptError:
        return None
