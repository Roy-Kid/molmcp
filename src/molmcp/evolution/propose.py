"""Evidence-triggered atomic proposal: one pattern, one component, one patch.

A *pattern* is a shape of work the wiki has left open, carrying the
literal text it wants appended to some component. A *receipt* is the
evidence binding one pattern to one component: with no receipt naming
both, there is no proposal. :func:`propose` walks the open patterns in
wiki order and, under each, the bundle's components in bundle order,
returning the first pair that survives every filter as one frozen
:class:`Candidate` — or ``None`` when no pair does.

Three disciplines hold this module together:

* *Receipts trigger, patterns do not.* A pattern nothing has a receipt
  for is skipped in silence. Firing on the pattern alone would turn the
  wiki into a queue of edits rather than a record of what happened.
* *``kind`` is data on the view, never a probe.* The gate a candidate
  carries is derived from :attr:`Component.kind` alone — no import of
  the layer that owns the vocabulary, no path sniffing, no file opened.
  A kind this module cannot rank (``controller``, or anything unlisted)
  is not proposed at all rather than proposed with a quietly defaulted
  gate.
* *The skill function-def skip is anchored, not a substring search.* An
  added line counts as a definition only when it matches
  ``^def <ident>(`` after its ``+`` is dropped and the rest is
  left-stripped. Prose mentioning ``def name(`` mid-sentence is still
  proposed.

Leaf module: standard library only (:mod:`difflib` for the patch,
:mod:`re` for the definition shape). Pure and in memory — it opens no
file, writes nothing, reads nothing from the process, and registers
nothing on a plane. ``path`` is copied into the :class:`Candidate` and
into the patch header; it is never resolved against a filesystem.
Applying a candidate belongs to a later leaf, as does scoring: the
choice here *is* wiki order.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass

#: Whether a proposed change to a component of this kind needs a human
#: before it ships, keyed by :attr:`Component.kind`. ``controller`` is
#: absent on purpose, and so is every kind nobody has ranked yet: a
#: missing key means *do not propose*, which is why this maps to the
#: gate rather than defaulting to one. A plain ``str`` key keeps the
#: kind vocabulary in one place — the component views — instead of
#: giving it a second home here.
_HUMAN_GATE_BY_KIND: dict[str, bool] = {
    "agent": False,
    "overlay": True,
    "provider": True,
    "rule": False,
    "skill": False,
}

#: The one kind whose patches are also read for Python definitions.
_SKILL = "skill"

#: A Python function definition at the start of a line. Anchored, so a
#: line that merely contains ``def name(`` does not match, and the
#: identifier must touch its parenthesis: ``def pack (`` is out of
#: scope, as are ``async def`` and ``class``.
_FUNCTION_DEF_PATTERN = re.compile(r"^def\s+[A-Za-z_][A-Za-z0-9_]*\(")


@dataclass(frozen=True, slots=True)
class Pattern:
    """One still-open evolution pattern from the wiki.

    The insert lives here and only here. A receipt is evidence that a
    pattern applies to a component; it never carries the patch body, so
    two receipts for one pattern cannot disagree about what to write.

    Attributes:
        pattern_id: Stable identity of the pattern. What
            ``rejected_ids`` matches and what a candidate cites.
        insert: Literal text to append to a component's body. Empty
            text proposes nothing.
    """

    pattern_id: str
    insert: str


@dataclass(frozen=True, slots=True)
class WikiView:
    """The open patterns, in the order the wiki lists them.

    Order is the whole selection policy: the first pattern that yields
    an eligible pair wins. There is no ranking pass.

    Attributes:
        open_patterns: Open patterns, wiki order. Empty proposes
            nothing, whatever the receipts and bundle hold.
    """

    open_patterns: tuple[Pattern, ...]


@dataclass(frozen=True, slots=True)
class Receipt:
    """Evidence that one pattern was met on one component.

    Attributes:
        receipt_id: Identity of the episode this came from; collected
            into :attr:`Candidate.rationale_refs`.
        pattern_id: The pattern this receipt is evidence for.
        component_id: The component this receipt is evidence about.
    """

    receipt_id: str
    pattern_id: str
    component_id: str


@dataclass(frozen=True, slots=True)
class ReceiptsView:
    """The receipts one run left behind, in the order it left them.

    Attributes:
        receipts: Receipts in run order. A candidate's rationale is
            collected in this order, so two runs over the same evidence
            cite it the same way.
    """

    receipts: tuple[Receipt, ...]


@dataclass(frozen=True, slots=True)
class Component:
    """One component of the current harness bundle, as data.

    Every field is given, never discovered. The body is text the caller
    already has, not a file this module goes and reads, and the kind is
    a string the caller already knows, not something inferred from the
    path or from what imports.

    Attributes:
        component_id: Identity a receipt names.
        kind: One of ``skill``, ``rule``, ``agent``, ``overlay``,
            ``provider``, ``controller``. A plain string: the
            vocabulary's home is elsewhere.
        path: POSIX path of the component, used verbatim in the patch
            header and copied onto the candidate. Never opened.
        text: The component's current body.
    """

    component_id: str
    kind: str
    path: str
    text: str


@dataclass(frozen=True, slots=True)
class BundleView:
    """The components of the harness bundle, in bundle order.

    Attributes:
        components: Components in bundle order. Scanned under each open
            pattern, so bundle order breaks ties only within one
            pattern — never across patterns.
    """

    components: tuple[Component, ...]


@dataclass(frozen=True, slots=True)
class Candidate:
    """One proposed change: one pattern, one component, one patch.

    The only thing :func:`propose` returns, and never more than one of
    them. :attr:`human_gate` is a snapshot derived from the component's
    kind at proposal time; the kind itself stays on the component.

    Attributes:
        pattern_id: The pattern that motivated the change.
        component_id: The component the patch applies to.
        path: The component's path, copied verbatim.
        unified_diff: The patch, from :func:`difflib.unified_diff`, with
            the component's own path on both headers.
        rationale_refs: The receipt ids that evidenced this pair, in
            receipts order.
        human_gate: ``True`` when this kind of component may not change
            without a human saying so.
    """

    pattern_id: str
    component_id: str
    path: str
    unified_diff: str
    rationale_refs: tuple[str, ...]
    human_gate: bool


def _rationale_refs(
    receipts: ReceiptsView, pattern_id: str, component_id: str
) -> tuple[str, ...]:
    """Return the receipt ids evidencing one pair, in receipts order.

    Args:
        receipts: The receipts to search.
        pattern_id: The pattern a receipt must name.
        component_id: The component the same receipt must name.

    Returns:
        The matching receipt ids, empty when the pair has no evidence.
    """
    return tuple(
        receipt.receipt_id
        for receipt in receipts.receipts
        if receipt.pattern_id == pattern_id and receipt.component_id == component_id
    )


def _is_absent(text: str, insert: str) -> bool:
    """Return whether *insert* is missing as a whole line of *text*.

    Whole lines, not containment: a body that says ``See: <insert>
    first.`` still needs the insert on a line of its own.

    Args:
        text: The component body to look in.
        insert: The text the pattern wants appended.

    Returns:
        ``True`` when *insert* is non-empty and no line of *text* equals
        it, ``False`` otherwise.
    """
    if not insert:
        return False
    wanted = insert.rstrip("\n")
    return all(line.rstrip("\n") != wanted for line in text.splitlines())


def _patch(path: str, text: str, insert: str) -> str:
    """Return the unified diff appending *insert* to *text*.

    Args:
        path: Value for both diff headers — the component's own path.
        text: The component body before the change.
        insert: The text appended after the body's last line.

    Returns:
        The patch, or the empty string when the two bodies are equal.
    """
    before = text.splitlines()
    after = [*before, *insert.splitlines()]
    return "".join(
        difflib.unified_diff(
            [f"{line}\n" for line in before],
            [f"{line}\n" for line in after],
            fromfile=path,
            tofile=path,
            lineterm="\n",
        )
    )


def _adds_a_function_def(patch: str) -> bool:
    """Return whether any line *patch* adds is a Python function def.

    An added line starts with ``+`` and is not the ``+++`` header. The
    ``+`` is dropped and the remainder left-stripped before matching, so
    an indented definition counts and a mid-sentence mention does not.

    Args:
        patch: A unified diff.

    Returns:
        ``True`` when at least one added line matches ``def <ident>(``.
    """
    for line in patch.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if _FUNCTION_DEF_PATTERN.match(line[1:].lstrip()):
            return True
    return False


def propose(
    wiki: WikiView,
    receipts: ReceiptsView,
    bundle: BundleView,
    rejected_ids: Sequence[str] = (),
) -> Candidate | None:
    """Propose at most one evidenced change to one component.

    Patterns are tried in wiki order and, under each, components in
    bundle order; the first pair passing every filter is returned at
    once. A pair is eligible when its pattern is not rejected, its
    component's kind is one this module ranks, some receipt names both,
    the insert is not already a whole line of the body, the resulting
    patch is non-empty, and — for a ``skill`` — the patch adds no Python
    function definition.

    Pure: nothing is opened, written, cached, or held. Callers own the
    rejection set and pass it in; the wiki is never edited here.

    Args:
        wiki: The open patterns, in wiki order.
        receipts: The evidence one run produced.
        bundle: The components of the current harness bundle.
        rejected_ids: Pattern ids to skip, matched exactly. Pattern
            granularity only — a pattern rejected for one component is
            rejected for all of them.

    Returns:
        One :class:`Candidate`, or ``None`` when no pair is eligible.
        Never a sequence: this leaf proposes one change at a time.
    """
    for pattern in wiki.open_patterns:
        if pattern.pattern_id in rejected_ids:
            continue
        for component in bundle.components:
            human_gate = _HUMAN_GATE_BY_KIND.get(component.kind)
            if human_gate is None:
                continue
            rationale_refs = _rationale_refs(
                receipts, pattern.pattern_id, component.component_id
            )
            if not rationale_refs:
                continue
            if not _is_absent(component.text, pattern.insert):
                continue
            patch = _patch(component.path, component.text, pattern.insert)
            if not patch:
                continue
            if component.kind == _SKILL and _adds_a_function_def(patch):
                continue
            return Candidate(
                pattern_id=pattern.pattern_id,
                component_id=component.component_id,
                path=component.path,
                unified_diff=patch,
                rationale_refs=rationale_refs,
                human_gate=human_gate,
            )
    return None
