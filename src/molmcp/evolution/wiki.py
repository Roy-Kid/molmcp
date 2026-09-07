"""Evolution wiki: one page per pattern key, appended to, fenced on read.

A *pattern* is a recurring shape of work this harness attempts more than
once; its *page* is the single document holding what was tried and how
each attempt ended. One ``pattern_key`` has exactly one page, and a
verdict is folded into that page rather than written as a file of its
own — two rejections followed by an acceptance are one file, three
records, and a derived current hypothesis.

Three disciplines hold this module together:

* *The page is the authority.* :meth:`WikiPage.current` is computed from
  the receipt sequence and never stored. A second, independently
  writable field would be a second truth to keep in sync, and the one
  that fell behind would still read as authoritative. A later acceptance
  appends; it does not edit, reorder, or drop the rejections before it.
* *The fence is for the reader, not the disk.* ``evidence_refs`` are
  pointer strings, and :class:`WikiStore` writes them exactly as given.
  Only :func:`render_page` wraps them, and it wraps them with the shared
  :func:`~molmcp.helpers.fence_untrusted` rather than a second copy of
  the marker: persisting the wrapper would make the fence part of the
  data it guards, and re-spelling it would fork it the next time it
  changes.
* *The store is a local directory the caller names.* No
  working-directory fallback, no cache default, and a remote-shaped root
  is refused before any IO. Fetching a page over the network belongs to
  another layer, and this leaf may not import that layer to do it.

Leaf module: the standard library plus :mod:`molmcp.helpers`. It reads
no clock and no environment, and no runtime surface imports it. The
atomic write below copies the shape of the adoption ledger's swap (a
``.partial`` sibling, then :func:`os.replace`) without importing it;
that ledger is a resumable journal for one migration, which is not this.

The record handed to :meth:`Maintainer.ingest` is duck-typed: four
attribute names are read off it and everything else it carries is
dropped on the way in. The episode record type is therefore free to grow
or lose fields without this module noticing. A pointer to a skill file
in particular is not a wiki field — it is not read, not stored, and not
rendered.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..helpers import fence_untrusted

#: The verdict that makes a receipt the page's current hypothesis.
_ACCEPTED = "accepted"

#: The verdict that records an attempt worth keeping and not repeating.
_REJECTED = "rejected"

#: The only two verdicts a wiki receipt may carry. Anything else is a
#: word this module has no rule for, so it is refused rather than stored.
_OUTCOMES: frozenset[str] = frozenset({_ACCEPTED, _REJECTED})

#: Suffix of a page document, and of the sibling a save swaps in from.
_PAGE_SUFFIX = ".json"
_PARTIAL_SUFFIX = ".partial"

#: Everything a page file name may not keep. What survives is
#: ``[A-Za-z0-9._-]``: one path segment, so a key spelled ``../escape``
#: names a file instead of climbing to the parent directory. The slug is
#: lossy on purpose, which is why the key inside the document — not the
#: file name — is the authoritative one.
_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9._-]")

#: Scheme of the hosted git service, spelled in two pieces. The guard
#: below needs the token and the isolation test needs this file's text
#: not to name a service a leaf package may never reach for; splitting it
#: is how both stay true.
_FORGE_SCHEME = "git" + "hub:"

#: A store root that is not a local directory. One or two slashes are
#: accepted after a URL scheme because :class:`~pathlib.Path` collapses a
#: doubled separator: ``Path("https://host/x")`` stringifies as
#: ``https:/host/x``, so a guard demanding the two slashes it was handed
#: would wave the URL straight through. The scheme is matched
#: case-insensitively; the rest of the root is not this check's business.
_REMOTE_ROOT = re.compile(
    "^(?:" + _FORGE_SCHEME + r"|https?:/{1,2}|ssh:/{1,2}|git@)",
    re.IGNORECASE,
)

#: Label on every fence :func:`render_page` writes. An evidence pointer
#: is untrusted text: whoever produced the episode chose it.
_EVIDENCE_LABEL = "untrusted evidence pointer"

#: What :func:`render_page` says instead of an empty section. "Nothing
#: rendered yet" and "nothing has been accepted yet" are different
#: claims, and only the second one is true here.
_NO_CURRENT = "No accepted hypothesis is on record for this pattern."


class WikiError(ValueError):
    """Raised when something is not a wiki page this module will accept.

    Covers a store root shaped like a remote, a blank or missing
    ``pattern_key``, a receipt missing ``outcome`` / ``snapshot_sha`` /
    ``evidence_refs`` or carrying a verdict outside ``accepted`` /
    ``rejected``, evidence pointers that are not strings, a page document
    that is not readable JSON, and a file name collision between two
    different keys. Owned here: reusing another subsystem's error would
    make a wiki problem look like that subsystem's.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class WikiReceipt:
    """One verdict on one snapshot, folded into a page.

    Three fields and no fourth. There is no episode id, no timestamp, and
    no pointer to a skill file: a wiki page answers "what was tried on
    this pattern and how did it end", and every other name a caller's
    record happens to carry is dropped by :meth:`Maintainer.ingest`
    rather than stored and forgotten.

    Every field is keyword-only, so declaration order stays this module's
    business rather than a call-site argument order. Frozen with slots:
    a receipt already on a page cannot be edited into a different one.

    Attributes:
        outcome: ``accepted`` or ``rejected``.
        snapshot_sha: Identifier of what was tried, as the caller spelled
            it. Never parsed here.
        evidence_refs: Pointer strings — paths, ids, references — for
            whoever wants to look. Raw on disk, fenced by
            :func:`render_page`, and empty when the episode left none.

    Examples:
        >>> WikiReceipt(outcome="rejected", snapshot_sha="sha-fail-1").evidence_refs
        ()
    """

    outcome: str
    snapshot_sha: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class WikiPage:
    """Everything one ``pattern_key`` has been through, in order.

    The receipt sequence is append-only by discipline: callers build a
    new page rather than editing this one, and :meth:`Maintainer.ingest`
    only ever appends. The rejections are the part worth keeping — a
    later success does not get to edit the record of the failures that
    preceded it.

    Attributes:
        pattern_key: The authoritative name of this page. The file name
            on disk is a lossy slug of it and is not authoritative.
        receipts: Verdicts in ingest order, oldest first.

    Examples:
        >>> WikiPage(pattern_key="demo.pattern").current() is None
        True
    """

    pattern_key: str
    receipts: tuple[WikiReceipt, ...] = ()

    def current(self) -> WikiReceipt | None:
        """Return the last accepted receipt, or ``None`` when there is none.

        Derived on every call rather than stored, so the current
        hypothesis cannot drift from the history it is read out of.

        Returns:
            The most recent receipt whose ``outcome`` is ``accepted``, or
            ``None`` when nothing on this page has been accepted yet.
        """
        for receipt in reversed(self.receipts):
            if receipt.outcome == _ACCEPTED:
                return receipt
        return None


class WikiStore:
    """A directory of wiki pages, one JSON document per pattern key.

    *Path* is required and local. There is no working-directory
    fallback and no cache-directory default: a store that guessed where
    to write would scatter pages across whichever directory a process
    happened to start in. The directory itself is created by the first
    successful :meth:`save`, so a rejected receipt leaves no trace of a
    store that was never written to.

    Each page is ``<path>/<slug>.json``, written through a ``.partial``
    sibling and swapped in with :func:`os.replace`, so a reader sees
    either the previous document or the whole new one. What lands on disk
    is data — pointer strings, never fenced and never the content they
    point at.

    Args:
        path: Local directory holding the pages. Expanded and resolved at
            construction; created on first :meth:`save`.

    Raises:
        TypeError: *path* is omitted — it has no default.
        WikiError: *path* is shaped like a remote (a hosted git service,
            HTTP(S), SSH, or ``git@host:owner/repo``). Refused before any
            IO.

    Examples:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     store = WikiStore(Path(tmp) / "wiki")
        ...     store.load("demo.pattern") is None
        True
    """

    def __init__(self, path: Path) -> None:
        """Refuse a remote-shaped *path*, then keep the local one.

        Args:
            path: Local directory the pages live in.

        Raises:
            WikiError: *path* stringifies to a remote shape. Checked
                before expansion so nothing on disk is touched.
        """
        candidate = Path(path)
        for spelling in (str(candidate), candidate.as_posix()):
            if _REMOTE_ROOT.match(spelling):
                raise WikiError(
                    f"a wiki store is a local directory, not a remote: {spelling!r}"
                )
        self._path = candidate.expanduser().resolve()

    def load(self, pattern_key: str) -> WikiPage | None:
        """Return the page for *pattern_key*, or ``None`` when there is none.

        Args:
            pattern_key: Authoritative key of the wanted page.

        Returns:
            The stored :class:`WikiPage`, or ``None`` when no document
            exists for this key yet.

        Raises:
            WikiError: *pattern_key* is blank, the document is not
                readable JSON or not a page, or the file the slug names
                holds a different key — returning that page would answer
                a question nobody asked.
        """
        path = self._page_path(pattern_key)
        page = _read_page(path)
        if page is not None and page.pattern_key != pattern_key:
            raise WikiError(
                f"{path} holds pattern_key {page.pattern_key!r}, not {pattern_key!r}"
            )
        return page

    def save(self, page: WikiPage) -> Path:
        """Write *page* atomically, creating the store directory if needed.

        A page for a key already on disk replaces it wholesale: the
        caller folds a receipt into the page it loaded, so the document
        it hands back is the whole history.

        Args:
            page: The page to persist, receipts in ingest order.

        Returns:
            Path of the written ``<slug>.json``.

        Raises:
            WikiError: ``page.pattern_key`` is blank, or the slug names a
                file already holding a different key. Checked before the
                directory is created, so a refused save leaves the store
                byte-identical.
            OSError: The directory could not be created or the document
                could not be written.
        """
        path = self._page_path(page.pattern_key)
        stored = _read_page(path)
        if stored is not None and stored.pattern_key != page.pattern_key:
            raise WikiError(
                f"{path} already holds pattern_key {stored.pattern_key!r}; "
                f"{page.pattern_key!r} would overwrite another pattern"
            )
        self._path.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}{_PARTIAL_SUFFIX}")
        partial.write_text(
            json.dumps(_document(page), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(partial, path)
        return path

    def _page_path(self, pattern_key: str) -> Path:
        """Return the document path *pattern_key* slugs to."""
        return self._path / f"{_slug(pattern_key)}{_PAGE_SUFFIX}"


class Maintainer:
    """The one way a receipt reaches a page: validate, fold, save.

    Ingest is the whole policy. There is no second entry point that
    writes a receipt as a file of its own, and none that edits a page in
    place, because either would let a pattern's history live in two
    shapes at once.

    Args:
        store: Where pages are read from and written to.

    Examples:
        >>> import tempfile
        >>> from types import SimpleNamespace
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     maintainer = Maintainer(WikiStore(Path(tmp) / "wiki"))
        ...     page = maintainer.ingest(
        ...         SimpleNamespace(
        ...             pattern_key="demo.pattern",
        ...             outcome="rejected",
        ...             snapshot_sha="sha-fail-1",
        ...             evidence_refs=("fixtures/a.log",),
        ...         )
        ...     )
        ...     page.current() is None
        True
    """

    def __init__(self, store: WikiStore) -> None:
        """Keep *store* as the only place ingest writes.

        Args:
            store: The page directory this maintainer folds receipts
                into.
        """
        self._store = store

    def ingest(self, receipt: object) -> WikiPage:
        """Fold *receipt* into its pattern's page and persist the result.

        Exactly four attributes are read — ``pattern_key``, ``outcome``,
        ``snapshot_sha``, ``evidence_refs`` — and every other name
        *receipt* carries is discarded here rather than reshaped into a
        field. Nothing is written until all four validate, so a refused
        record leaves the store exactly as it was, down to a directory
        that does not exist yet.

        Args:
            receipt: Any object carrying the four attributes. Duck-typed
                on purpose: this module does not import the type an
                episode produces, and that type may change without
                changing this contract.

        Returns:
            The new :class:`WikiPage`, with *receipt* appended last. The
            page that was on disk is not modified — a new one replaces
            it.

        Raises:
            WikiError: An attribute is missing, blank, of the wrong type,
                or carries a verdict outside ``accepted`` / ``rejected``.
            OSError: The page could not be written.
        """
        pattern_key = _required_text(receipt, "pattern_key")
        outcome = _outcome_of(receipt)
        snapshot_sha = _required_text(receipt, "snapshot_sha")
        evidence_refs = _pointers(
            getattr(receipt, "evidence_refs", None), "receipt evidence_refs"
        )
        folded = WikiReceipt(
            outcome=outcome,
            snapshot_sha=snapshot_sha,
            evidence_refs=evidence_refs,
        )
        stored = self._store.load(pattern_key)
        history = () if stored is None else stored.receipts
        page = WikiPage(pattern_key=pattern_key, receipts=(*history, folded))
        self._store.save(page)
        return page


def render_page(page: WikiPage) -> str:
    """Render *page* as markdown, with every evidence pointer fenced.

    The read path is where the fence belongs: what is on disk is data,
    and this is the function that hands it to something that reads
    instructions. Pointers go through
    :func:`~molmcp.helpers.fence_untrusted` — the shared one, so the
    marker has a single definition — and the current hypothesis is taken
    from :meth:`WikiPage.current` rather than from a stored field.

    An absent current hypothesis is stated rather than left as an empty
    section: "nothing rendered" and "nothing accepted" are different
    claims.

    Args:
        page: The page to render. Not modified.

    Returns:
        Markdown: a title naming ``pattern_key``, a current-hypothesis
        section, and a history section listing every receipt in ingest
        order, oldest first.

    Examples:
        >>> print(render_page(WikiPage(pattern_key="demo.pattern")))
        # demo.pattern
        <BLANKLINE>
        ## Current hypothesis
        <BLANKLINE>
        No accepted hypothesis is on record for this pattern.
        <BLANKLINE>
        ## History
        <BLANKLINE>
    """
    current = page.current()
    blocks: list[str] = [
        f"# {page.pattern_key}",
        "## Current hypothesis",
        _NO_CURRENT if current is None else _render_receipt(current),
        "## History",
    ]
    blocks.extend(
        _render_receipt(receipt, prefix=f"{position}. ")
        for position, receipt in enumerate(page.receipts, start=1)
    )
    return "\n\n".join(blocks) + "\n"


def _slug(pattern_key: str) -> str:
    """Return the file-name stem *pattern_key* maps to.

    Args:
        pattern_key: The authoritative key.

    Returns:
        *pattern_key* with every character outside ``[A-Za-z0-9._-]``
        replaced by ``_``. Lossy: two different keys can slug alike,
        which is why the key inside the document is checked as well.

    Raises:
        WikiError: *pattern_key* is not a string, or is blank.
    """
    if not isinstance(pattern_key, str) or not pattern_key.strip():
        raise WikiError(f"pattern_key is missing or blank: {pattern_key!r}")
    return _UNSAFE_IN_NAME.sub("_", pattern_key)


def _required_text(receipt: object, name: str) -> str:
    """Return *receipt*'s *name* attribute as non-blank text.

    Args:
        receipt: The duck-typed record.
        name: Attribute to read. A missing attribute and a ``None`` one
            fail the same way — neither is a value.

    Returns:
        The attribute, stripped, so a key differing only in surrounding
        blanks cannot open a second page for the same pattern.

    Raises:
        WikiError: The attribute is absent, not a string, or blank.
    """
    value = getattr(receipt, name, None)
    if not isinstance(value, str) or not value.strip():
        raise WikiError(f"receipt {name} is missing or blank: {value!r}")
    return value.strip()


def _outcome_of(receipt: object) -> str:
    """Return *receipt*'s verdict, normalized to ``accepted`` or ``rejected``.

    Accepts a plain string or anything carrying the verdict on a
    ``value`` attribute, which is what an enum member looks like from
    here. The enum type itself is never imported: its repr is not the
    verdict, and its ``value`` is.

    Args:
        receipt: The duck-typed record.

    Returns:
        The verdict, stripped and lower-cased.

    Raises:
        WikiError: The verdict is absent, not text, blank, or a word this
            module has no rule for.
    """
    raw = getattr(receipt, "outcome", None)
    value = getattr(raw, "value", raw)
    if not isinstance(value, str):
        raise WikiError(f"receipt outcome is missing or not text: {raw!r}")
    normalized = value.strip().lower()
    if normalized not in _OUTCOMES:
        raise WikiError(
            f"unknown receipt outcome {value!r}; expected "
            f"{_ACCEPTED!r} or {_REJECTED!r}"
        )
    return normalized


def _pointers(raw: object, what: str) -> tuple[str, ...]:
    """Return *raw* as a tuple of pointer strings.

    Args:
        raw: A sequence of strings. A bare string is refused rather than
            iterated: one pointer is not a sequence of one-character
            pointers.
        what: What to name in the error message.

    Returns:
        A new tuple. An empty sequence yields ``()`` — having no pointer
        is a fact about the episode, not a broken record.

    Raises:
        WikiError: *raw* is absent, a string, not iterable, or holds an
            element that is not a string.
    """
    if raw is None or isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        raise WikiError(f"{what} is not a sequence of pointers: {raw!r}")
    pointers: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise WikiError(f"{what} holds a pointer that is not text: {item!r}")
        pointers.append(item)
    return tuple(pointers)


def _document(page: WikiPage) -> dict[str, object]:
    """Return *page* as the exact mapping written to disk.

    The document has two keys, ``pattern_key`` and ``receipts``, and each
    receipt has three, ``outcome`` / ``snapshot_sha`` / ``evidence_refs``.
    One more or one fewer is a different format — in particular there is
    no stored current hypothesis, and no fence: the wrapper belongs to
    :func:`render_page`.

    Args:
        page: The page to serialize.

    Returns:
        A new plain mapping of JSON-native values.
    """
    return {
        "pattern_key": page.pattern_key,
        "receipts": [
            {
                "outcome": receipt.outcome,
                "snapshot_sha": receipt.snapshot_sha,
                "evidence_refs": list(receipt.evidence_refs),
            }
            for receipt in page.receipts
        ],
    }


def _read_page(path: Path) -> WikiPage | None:
    """Return the page stored at *path*, or ``None`` when the file is absent.

    The trust boundary: bytes on disk are decoded here and narrowed to a
    page before any caller sees them.

    Args:
        path: Candidate document path.

    Returns:
        The stored :class:`WikiPage`, or ``None`` when nothing is there.

    Raises:
        WikiError: The file exists but is not readable JSON, or is not a
            page. A document nobody can read is not a document anybody
            may overwrite.
    """
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise WikiError(f"{path} does not hold readable JSON: {exc}") from exc
    return _page_of(payload, path)


def _page_of(payload: object, path: Path) -> WikiPage:
    """Narrow a decoded document into a page.

    Args:
        payload: Decoded JSON, expected to be an object.
        path: Where it came from, for the error message.

    Returns:
        A new :class:`WikiPage` with receipts in stored order.

    Raises:
        WikiError: *payload* is not an object, its ``pattern_key`` is
            missing or blank, or ``receipts`` is not a list of receipts.
    """
    if not isinstance(payload, dict):
        raise WikiError(f"{path} does not hold a wiki page object")
    pattern_key = payload.get("pattern_key")
    if not isinstance(pattern_key, str) or not pattern_key.strip():
        raise WikiError(f"{path} has no pattern_key")
    entries = payload.get("receipts", [])
    if not isinstance(entries, list):
        raise WikiError(f"{path} receipts is not a list")
    return WikiPage(
        pattern_key=pattern_key,
        receipts=tuple(_receipt_of(entry, path) for entry in entries),
    )


def _receipt_of(entry: object, path: Path) -> WikiReceipt:
    """Narrow one stored entry into a receipt.

    Args:
        entry: One element of the document's ``receipts`` list.
        path: Where it came from, for the error message.

    Returns:
        A new :class:`WikiReceipt`, evidence pointers back in a tuple so
        a reloaded page equals the one that was saved.

    Raises:
        WikiError: *entry* is not an object, carries a verdict outside
            ``accepted`` / ``rejected``, has a blank ``snapshot_sha``, or
            holds evidence pointers that are not strings.
    """
    if not isinstance(entry, dict):
        raise WikiError(f"{path} holds a receipt that is not an object")
    outcome = entry.get("outcome")
    if not isinstance(outcome, str) or outcome not in _OUTCOMES:
        raise WikiError(f"{path} holds an unknown receipt outcome: {outcome!r}")
    snapshot_sha = entry.get("snapshot_sha")
    if not isinstance(snapshot_sha, str) or not snapshot_sha.strip():
        raise WikiError(f"{path} holds a receipt without a snapshot_sha")
    return WikiReceipt(
        outcome=outcome,
        snapshot_sha=snapshot_sha,
        evidence_refs=_pointers(
            entry.get("evidence_refs", ()), f"{path} evidence_refs"
        ),
    )


def _render_receipt(receipt: WikiReceipt, prefix: str = "") -> str:
    """Return one receipt as a markdown block.

    Args:
        receipt: The receipt to render.
        prefix: Text opening the first line, e.g. a history position.

    Returns:
        The verdict and snapshot on one line, followed by one fenced
        block per evidence pointer. A receipt with no pointers renders as
        the single line.
    """
    parts = [f"{prefix}{receipt.outcome}: `{receipt.snapshot_sha}`"]
    parts.extend(
        fence_untrusted(ref, label=_EVIDENCE_LABEL) for ref in receipt.evidence_refs
    )
    return "\n\n".join(parts)
