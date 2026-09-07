"""Evolution episode receipts: redaction, a TTL'd log, default-off consent.

An *evolution episode* is one round of this harness working on itself:
something is attempted, and it ends ``ok``, ``failed``, or ``skipped``.
Its *receipt* is the small record that outlives it — six fields naming
what was attempted and how it ended, and deliberately not the reasoning
that got there. Receipts go to a local directory that drops them once
they pass a TTL (*time to live*) of
:data:`~molmcp.evolution.receipts.RECEIPT_TTL_DAYS` days.

Leaf package, a sibling of :mod:`molmcp.helpers`: standard library only.
It does not import FastMCP, does not read settings, does not borrow the
adoption ledger, and is not re-exported from :mod:`molmcp` — a receipt is
a local record, not an MCP tool.

``fence_untrusted`` is absent from ``__all__`` on purpose. The fence is
for the payload :func:`~molmcp.evolution.receipts.upload_payload` hands
an LLM, which imports it inside its own body; bytes written by
:class:`~molmcp.evolution.receipts.ReceiptLog` are redacted plaintext.
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

__all__ = [
    "RECEIPT_FIELDS",
    "RECEIPT_TTL_DAYS",
    "RECEIPT_VERSION",
    "SHARE_RECEIPTS_KEY",
    "Consent",
    "EpisodeReceipt",
    "ReceiptError",
    "ReceiptLog",
    "redact_text",
    "upload_payload",
]
