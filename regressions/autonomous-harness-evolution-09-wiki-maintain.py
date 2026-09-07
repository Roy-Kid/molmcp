#!/usr/bin/env python3
"""Regression example: one page per pattern, append-only, fenced only on read.

Standalone (no pytest dependency). Folds three duck-typed episode stubs into
one ``WikiStore`` rooted in a throwaway directory — two rejections and then an
acceptance, all under a single ``pattern_key`` — and reads the result back
three ways: the page object ``load`` returns, the JSON text on disk, and the
markdown ``render_page`` produces.

Hard-coded goldens (in-repo, 2026-09-07, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-09-wiki-maintain.md``, Testing
strategy -> Regression example, and acceptance AC-010):

    two rejected stubs sharing "demo.pattern" -> the store directory holds
        exactly ["demo.pattern.json"], that document is
        _DOCUMENT_AFTER_REJECTIONS, the reloaded history is
        _HISTORY_AFTER_REJECTIONS, and current() is None
    one accepted stub after them -> still exactly ["demo.pattern.json"],
        that document is _DOCUMENT_AFTER_ACCEPTANCE, its first two receipt
        records equal the two read a step earlier, the reloaded history is
        _HISTORY_AFTER_ACCEPTANCE, and current() is the "sha-ok-1" receipt
    render_page(page) == _EXPECTED_MARKDOWN, which carries "<!-- BEGIN",
        while the page JSON carries neither "<!-- BEGIN" nor "<!-- END"
        and carries every pointer string raw
    "skill_pointer" and "skills/demo-pattern/SKILL.md" appear in none of
        the page object, the JSON text, and the markdown
    WikiStore(Path(root)) raises WikiError for each of the seven spellings
        in _REMOTE_ROOTS and creates nothing on disk, while a local root
        merely *containing* "https" constructs

The compaction golden is a *file listing*, never a file count: a stray
``demo.pattern.json.partial`` left behind by a half-finished swap is also
"more than one entry", and comparing the listing catches it while
``len(...) == 1`` would not.

The append-only golden is checked twice on purpose. Against the literal
above, so the records are what this file says they are; and against the two
records read from disk one ingest earlier, so "unmodified" is measured
against bytes that actually existed rather than against a second copy of the
same literal. Rewriting a rejection into something equally well-formed would
pass the first check and fail the second.

The stubs are a local frozen dataclass, not spec 06's ``EpisodeReceipt``.
``Maintainer.ingest`` reads four attribute names off whatever it is handed,
and importing the real record type here would quietly test the coupling that
duck typing exists to avoid — this script is the demonstration that a
stranger's object is enough. Each stub carries a fifth attribute,
``skill_pointer``, which the wiki has to read straight past.

The remote roots are handed over as ``Path``, which is the parameter's
declared type, and that is where the trap is: ``pathlib`` collapses a
doubled separator, so ``Path("https://example.invalid/x").as_posix()`` is
``"https:/example.invalid/x"`` — pinned below as _COLLAPSED_HTTPS. A guard
written against the ``"https://"`` it was handed would wave the URL through,
so the spellings include collapsed and mixed-case forms, and one local
directory whose name merely starts with ``https`` proves the guard still
discriminates. ``WikiStore.__init__`` does no IO at all, so the accepted
lookalike's directory must not exist either.

Public surface only: ``molmcp.evolution`` (the package facade), never
``molmcp.evolution.wiki``, plus stdlib ``json`` / ``tempfile`` /
``dataclasses``. Deliberately absent: ``fence_untrusted`` (the fenced blocks
are spelled out below instead of imported, so a change to the marker is a
failure here rather than a rename that agrees with itself), spec 06's
receipt types, ``create_stack`` and every runtime surface, git, network,
subprocesses, environment variables, and pytest. Nothing is written outside
the temporary directory, and no absolute machine path is printed.

Run directly::

    uv run python regressions/autonomous-harness-evolution-09-wiki-maintain.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via
``test_autonomous_harness_evolution_09_wiki_maintain``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from molmcp.evolution import Maintainer, WikiError, WikiPage, WikiStore, render_page


@dataclass(frozen=True, slots=True)
class _Episode:
    """An episode record shaped only by what this script hands over.

    Deliberately not spec 06's ``EpisodeReceipt``: ingest duck-types the
    four names below, so a local stub is the honest way to show that any
    object carrying them is enough.

    Attributes:
        pattern_key: The page this verdict belongs to.
        outcome: ``accepted`` or ``rejected``.
        snapshot_sha: What was tried.
        evidence_refs: Pointer strings, stored raw and fenced on render.
        skill_pointer: The fifth attribute, which the wiki must ignore.
    """

    pattern_key: str
    outcome: str
    snapshot_sha: str
    evidence_refs: tuple[str, ...]
    skill_pointer: str


# In-repo goldens, 2026-09-07, no third-party oracle.
_PATTERN_KEY = "demo.pattern"
_PAGE_NAME = "demo.pattern.json"
_STORE_DIR_NAME = "wiki"

_SKILL_POINTER_FIELD = "skill_pointer"
_SKILL_POINTER = "skills/demo-pattern/SKILL.md"

_FENCE_OPEN = "<!-- BEGIN"
_FENCE_CLOSE = "<!-- END"

#: The three stubs, in ingest order: fail, fail again, then succeed.
_REJECTED_ONE = _Episode(
    pattern_key=_PATTERN_KEY,
    outcome="rejected",
    snapshot_sha="sha-fail-1",
    evidence_refs=("fixtures/a.log",),
    skill_pointer=_SKILL_POINTER,
)
_REJECTED_TWO = _Episode(
    pattern_key=_PATTERN_KEY,
    outcome="rejected",
    snapshot_sha="sha-fail-2",
    evidence_refs=("fixtures/b.log",),
    skill_pointer=_SKILL_POINTER,
)
_ACCEPTED = _Episode(
    pattern_key=_PATTERN_KEY,
    outcome="accepted",
    snapshot_sha="sha-ok-1",
    evidence_refs=("fixtures/ok.log",),
    skill_pointer=_SKILL_POINTER,
)

#: Every pointer string that must reach disk unwrapped.
_POINTERS = ("fixtures/a.log", "fixtures/b.log", "fixtures/ok.log")

#: The reloaded history, verdict / snapshot / pointers, oldest first.
_HISTORY_AFTER_REJECTIONS = (
    ("rejected", "sha-fail-1", ("fixtures/a.log",)),
    ("rejected", "sha-fail-2", ("fixtures/b.log",)),
)
_HISTORY_AFTER_ACCEPTANCE = (
    ("rejected", "sha-fail-1", ("fixtures/a.log",)),
    ("rejected", "sha-fail-2", ("fixtures/b.log",)),
    ("accepted", "sha-ok-1", ("fixtures/ok.log",)),
)
_ACCEPTED_SHA = "sha-ok-1"

#: The document on disk after the two rejections, spelled out whole. No
#: current hypothesis is stored — it is derived — and no fifth attribute
#: of the stub survives the fold.
_DOCUMENT_AFTER_REJECTIONS: dict[str, object] = {
    "pattern_key": "demo.pattern",
    "receipts": [
        {
            "outcome": "rejected",
            "snapshot_sha": "sha-fail-1",
            "evidence_refs": ["fixtures/a.log"],
        },
        {
            "outcome": "rejected",
            "snapshot_sha": "sha-fail-2",
            "evidence_refs": ["fixtures/b.log"],
        },
    ],
}

#: The same document after the acceptance: one record longer, the two
#: rejections untouched and still first.
_DOCUMENT_AFTER_ACCEPTANCE: dict[str, object] = {
    "pattern_key": "demo.pattern",
    "receipts": [
        {
            "outcome": "rejected",
            "snapshot_sha": "sha-fail-1",
            "evidence_refs": ["fixtures/a.log"],
        },
        {
            "outcome": "rejected",
            "snapshot_sha": "sha-fail-2",
            "evidence_refs": ["fixtures/b.log"],
        },
        {
            "outcome": "accepted",
            "snapshot_sha": "sha-ok-1",
            "evidence_refs": ["fixtures/ok.log"],
        },
    ],
}

#: The whole rendered page. Written out rather than assembled from
#: ``fence_untrusted`` so that a change to the marker fails here instead of
#: agreeing with itself.
_EXPECTED_MARKDOWN = """\
# demo.pattern

## Current hypothesis

accepted: `sha-ok-1`

<!-- BEGIN untrusted evidence pointer -->
fixtures/ok.log
<!-- END untrusted evidence pointer -->

## History

1. rejected: `sha-fail-1`

<!-- BEGIN untrusted evidence pointer -->
fixtures/a.log
<!-- END untrusted evidence pointer -->

2. rejected: `sha-fail-2`

<!-- BEGIN untrusted evidence pointer -->
fixtures/b.log
<!-- END untrusted evidence pointer -->

3. accepted: `sha-ok-1`

<!-- BEGIN untrusted evidence pointer -->
fixtures/ok.log
<!-- END untrusted evidence pointer -->
"""

#: Roots a wiki store may not have. Two are mixed-case, three survive
#: ``pathlib``'s collapse of the doubled slash, and one is the ``git@``
#: shorthand that carries no scheme at all.
_REMOTE_ROOTS = (
    "github:x/y",
    "GitHub:owner/repo",
    "https://example.invalid/x",
    "HTTPS://Example.INVALID/x",
    "http://example.invalid/x",
    "ssh://example.invalid/x",
    "git@example.invalid:owner/repo",
)

#: What ``pathlib`` makes of a URL, pinned so the trap above is visible.
_COLLAPSED_HTTPS = "https:/example.invalid/x"

#: A local directory name that starts with the letters of a scheme but is
#: not one. It must be accepted, and it must not be created.
_LOCAL_LOOKALIKE = "https-not-a-remote"


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


def _entries(directory: Path) -> list[str]:
    """Return every name in *directory*, sorted, or ``[]`` when absent.

    Args:
        directory: Candidate store root.

    Returns:
        The sorted directory listing. Partial files count as entries,
        which is the point of comparing listings instead of counts.
    """
    if not directory.is_dir():
        return []
    return sorted(item.name for item in directory.iterdir())


def _read_document(path: Path) -> dict[str, object]:
    """Return the JSON object at *path*.

    Args:
        path: The page file ``save`` swapped into place.

    Returns:
        The decoded document as a plain mapping.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(
            f"{path.name} holds a {type(payload).__name__}, not a JSON object"
        )
    return dict(payload)


def _records(document: dict[str, object]) -> list[object]:
    """Return the ``receipts`` list of *document*.

    Args:
        document: A decoded page document.

    Returns:
        The stored receipt records, in stored order.
    """
    entries = document.get("receipts")
    if not isinstance(entries, list):
        raise AssertionError(f"receipts is a {type(entries).__name__}, not a JSON list")
    return list(entries)


def _history(page: WikiPage) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Return *page*'s receipts as comparable triples.

    Args:
        page: The page just loaded from disk.

    Returns:
        One ``(outcome, snapshot_sha, evidence_refs)`` triple per receipt,
        in ingest order.
    """
    return tuple(
        (receipt.outcome, receipt.snapshot_sha, receipt.evidence_refs)
        for receipt in page.receipts
    )


def _reload(store_dir: Path) -> WikiPage:
    """Load the page through a fresh store, so disk is the only source.

    Args:
        store_dir: The store root the maintainer wrote to.

    Returns:
        The page for the one pattern key this script uses.
    """
    page = WikiStore(store_dir).load(_PATTERN_KEY)
    _require(page is not None, f"no page was stored for {_PATTERN_KEY!r}")
    if page is None:  # pragma: no cover - _require already raised
        raise AssertionError("unreachable")
    _require(
        page.pattern_key == _PATTERN_KEY,
        f"the stored page names {page.pattern_key!r}, not {_PATTERN_KEY!r}",
    )
    return page


def _check_two_rejection_compaction(store_dir: Path) -> list[object]:
    """Golden 1: two rejections on one key are one file and two records.

    Args:
        store_dir: Throwaway store root, not yet created.

    Returns:
        The two receipt records as they sit on disk, for the append-only
        check to compare against later.
    """
    _require(
        _entries(store_dir) == [],
        "the store root already exists before the first ingest",
    )

    maintainer = Maintainer(WikiStore(store_dir))
    maintainer.ingest(_REJECTED_ONE)
    maintainer.ingest(_REJECTED_TWO)

    listing = _entries(store_dir)
    _require(
        listing == [_PAGE_NAME],
        f"the store holds {listing}, not exactly [{_PAGE_NAME!r}]",
    )

    document = _read_document(store_dir / _PAGE_NAME)
    _require(
        document == _DOCUMENT_AFTER_REJECTIONS,
        f"the page document {document!r} is not the two-rejection golden",
    )

    page = _reload(store_dir)
    history = _history(page)
    _require(
        history == _HISTORY_AFTER_REJECTIONS,
        f"the reloaded history {history!r} != {_HISTORY_AFTER_REJECTIONS!r}",
    )
    _require(
        page.current() is None,
        f"two rejections produced a current hypothesis: {page.current()!r}",
    )

    print(f"two rejected stubs -> {listing}")
    print(f"history={[record[1] for record in history]}, current()=None")
    return _records(document)


def _check_append_only_history(store_dir: Path, before: list[object]) -> WikiPage:
    """Golden 2: the acceptance appends and edits nothing behind it.

    Args:
        store_dir: Store root holding the two-rejection page.
        before: The receipt records read one ingest ago.

    Returns:
        The reloaded page carrying all three receipts.
    """
    Maintainer(WikiStore(store_dir)).ingest(_ACCEPTED)

    listing = _entries(store_dir)
    _require(
        listing == [_PAGE_NAME],
        f"the acceptance left {listing}, not exactly [{_PAGE_NAME!r}]",
    )

    document = _read_document(store_dir / _PAGE_NAME)
    _require(
        document == _DOCUMENT_AFTER_ACCEPTANCE,
        f"the page document {document!r} is not the acceptance golden",
    )

    records = _records(document)
    _require(
        records[: len(before)] == before,
        f"the rejections were rewritten: {records[: len(before)]!r} != {before!r}",
    )
    _require(
        len(records) == len(before) + 1,
        f"the page holds {len(records)} records, not {len(before) + 1}",
    )

    page = _reload(store_dir)
    history = _history(page)
    _require(
        history == _HISTORY_AFTER_ACCEPTANCE,
        f"the reloaded history {history!r} != {_HISTORY_AFTER_ACCEPTANCE!r}",
    )

    current = page.current()
    _require(
        current is not None and current.snapshot_sha == _ACCEPTED_SHA,
        f"current() is {current!r}, not the {_ACCEPTED_SHA!r} receipt",
    )

    print(f"after the acceptance -> {listing}")
    print(f"history={[record[1] for record in history]}, current()={_ACCEPTED_SHA!r}")
    return page


def _check_fence_on_render(store_dir: Path, page: WikiPage) -> str:
    """Golden 3: the fence is on the read path and nowhere on disk.

    Args:
        store_dir: Store root holding the page file.
        page: The page to render.

    Returns:
        The rendered markdown, for the ``skill_pointer`` check.
    """
    markdown = render_page(page)
    _require(
        markdown == _EXPECTED_MARKDOWN,
        f"render_page returned {markdown!r}, not the markdown golden",
    )
    _require(
        _FENCE_OPEN in markdown,
        f"the rendered page carries no {_FENCE_OPEN!r}",
    )

    text = (store_dir / _PAGE_NAME).read_text(encoding="utf-8")
    _require(
        _FENCE_OPEN not in text,
        f"the page JSON persists the fence marker {_FENCE_OPEN!r}",
    )
    _require(
        _FENCE_CLOSE not in text,
        f"the page JSON persists the fence marker {_FENCE_CLOSE!r}",
    )
    for pointer in _POINTERS:
        _require(
            pointer in text,
            f"the page JSON lost the raw evidence pointer {pointer!r}",
        )

    fences = markdown.count(_FENCE_OPEN)
    print(f"render_page -> {fences} fenced pointer(s), matches the markdown golden")
    print(f"{_PAGE_NAME} carries the raw pointers and no fence marker")
    return markdown


def _check_skill_pointer_absent(store_dir: Path, page: WikiPage, markdown: str) -> None:
    """Golden 4: the fifth stub attribute reaches nothing the wiki owns.

    Args:
        store_dir: Store root holding the page file.
        page: The reloaded page object.
        markdown: What ``render_page`` returned for it.
    """
    _require(
        _REJECTED_ONE.skill_pointer == _SKILL_POINTER,
        "the ingested stub never carried a skill pointer to drop",
    )
    _require(
        not hasattr(page, _SKILL_POINTER_FIELD),
        f"the page object grew a {_SKILL_POINTER_FIELD!r} attribute",
    )
    for receipt in page.receipts:
        _require(
            not hasattr(receipt, _SKILL_POINTER_FIELD),
            f"a receipt grew a {_SKILL_POINTER_FIELD!r} attribute",
        )

    text = (store_dir / _PAGE_NAME).read_text(encoding="utf-8")
    for haystack, where in (
        (repr(page), "the page object"),
        (text, _PAGE_NAME),
        (markdown, "the rendered page"),
    ):
        _require(
            _SKILL_POINTER not in haystack,
            f"{where} carries the skill pointer {_SKILL_POINTER!r}",
        )
        _require(
            _SKILL_POINTER_FIELD not in haystack,
            f"{where} names the field {_SKILL_POINTER_FIELD!r}",
        )

    print(
        f"{_SKILL_POINTER_FIELD!r} absent from the page object, "
        f"{_PAGE_NAME}, and the markdown"
    )


def _check_remote_roots(root: Path) -> None:
    """Golden 5: remote-shaped roots are refused, and touch nothing.

    Args:
        root: The throwaway directory whose listing must not change.
    """
    collapsed = Path("https://example.invalid/x").as_posix()
    _require(
        collapsed == _COLLAPSED_HTTPS,
        f"pathlib now spells the URL {collapsed!r}, not {_COLLAPSED_HTTPS!r}",
    )

    before = _entries(root)
    for spelling in _REMOTE_ROOTS:
        candidate = Path(spelling)
        try:
            WikiStore(candidate)
        except WikiError:
            pass
        else:
            raise AssertionError(f"WikiStore accepted the remote root {spelling!r}")
        _require(
            not candidate.exists(),
            f"the refused root {spelling!r} was created on disk",
        )
        _require(
            not Path(candidate.parts[0]).exists(),
            f"the refused root {spelling!r} created {candidate.parts[0]!r}",
        )

    lookalike = root / _LOCAL_LOOKALIKE
    WikiStore(lookalike)
    _require(
        not lookalike.exists(),
        f"constructing a store created {_LOCAL_LOOKALIKE!r} before any save",
    )

    after = _entries(root)
    _require(
        after == before,
        f"the refused roots changed the workspace: {before} -> {after}",
    )

    print(f"{len(_REMOTE_ROOTS)} remote spellings -> WikiError, nothing created")
    print(f"local root {_LOCAL_LOOKALIKE!r} accepted and not created")


def main() -> int:
    workspace = tempfile.TemporaryDirectory(prefix="molmcp-wiki-regression-")
    try:
        root = Path(workspace.name)
        store_dir = root / _STORE_DIR_NAME

        rejections = _check_two_rejection_compaction(store_dir)
        page = _check_append_only_history(store_dir, rejections)
        markdown = _check_fence_on_render(store_dir, page)
        _check_skill_pointer_absent(store_dir, page, markdown)
        _check_remote_roots(root)
    finally:
        workspace.cleanup()

    print("\nOK: one page per pattern, rejections kept, fence only on render.")
    return 0


def test_autonomous_harness_evolution_09_wiki_maintain() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
