"""Evolution episode receipts and the pattern wiki they are folded into.

An *evolution episode* is one round of this harness working on itself:
something is attempted, and it ends ``ok``, ``failed``, or ``skipped``.
Its *receipt* is the small record that outlives it — six fields naming
what was attempted and how it ended, and deliberately not the reasoning
that got there. Receipts go to a local directory that drops them once
they pass a TTL (*time to live*) of
:data:`~molmcp.evolution.receipts.RECEIPT_TTL_DAYS` days.

A *pattern* is a shape of work the harness meets more than once, and
:mod:`~molmcp.evolution.wiki` gives each one a single page: the verdicts
it has collected, oldest first, and a current hypothesis derived from
them rather than stored beside them. Pages live in a local directory the
caller names, and :class:`~molmcp.evolution.wiki.Maintainer` is the only
way a verdict reaches one. The two modules meet by duck typing — the
wiki reads four attribute names off whatever it is handed — so neither
type has to move when the other changes.

Leaf package, a sibling of :mod:`molmcp.helpers`: the standard library
plus that helper. It does not import FastMCP, does not read settings,
does not borrow the adoption ledger, and is not re-exported from
:mod:`molmcp` — a receipt is a local record, not an MCP tool, and a wiki
page is not a plane.

``fence_untrusted`` is absent from ``__all__`` on purpose. The fence
belongs to the two read paths that hand text to an LLM — the payload
:func:`~molmcp.evolution.receipts.upload_payload` builds and the markdown
:func:`~molmcp.evolution.wiki.render_page` returns — and both import it
themselves; bytes written by
:class:`~molmcp.evolution.receipts.ReceiptLog` and
:class:`~molmcp.evolution.wiki.WikiStore` are unfenced data.
Re-exporting it here would advertise a fence for uses that must not have
one.
"""

from .receipts import (
    RECEIPT_FIELDS,
    RECEIPT_TTL_DAYS,
    RECEIPT_VERSION,
    SHARE_RECEIPTS_KEY,
    Consent,
    EpisodeReceipt,
    ReceiptError,
    ReceiptLog,
    redact_text,
    upload_payload,
)
from .wiki import (
    Maintainer,
    WikiError,
    WikiPage,
    WikiReceipt,
    WikiStore,
    render_page,
)

__all__ = [
    "RECEIPT_FIELDS",
    "RECEIPT_TTL_DAYS",
    "RECEIPT_VERSION",
    "SHARE_RECEIPTS_KEY",
    "Consent",
    "EpisodeReceipt",
    "Maintainer",
    "ReceiptError",
    "ReceiptLog",
    "WikiError",
    "WikiPage",
    "WikiReceipt",
    "WikiStore",
    "redact_text",
    "render_page",
    "upload_payload",
]
