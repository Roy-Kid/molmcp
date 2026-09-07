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

:mod:`~molmcp.evolution.propose` is what those two feed: given the
patterns a wiki still has open, the receipts one run left, and the
components of the current harness bundle, it returns at most one frozen
:class:`~molmcp.evolution.propose.Candidate` — a single pattern applied
to a single component, with the patch and the receipt ids that
evidenced it. It is a pure function over views the caller builds; it
opens no file, and it never applies what it proposes.

:mod:`~molmcp.evolution.evaluate` is the gate on the far side of that.
Given a challenger checkout and the champion's sha it replays the
held-out cases under three frozen seeds and returns one frozen
:class:`~molmcp.evolution.evaluate.EvaluationReport`: accepted or not,
and the single reason why. Four readings, compared one at a time and
never summed into a score. It moves no pointer — the report is a
verdict, and promoting on one belongs to whoever holds the pointer.

Note the two ``C`` names this façade carries.
:class:`~molmcp.evolution.propose.Candidate` is a proposed patch;
:class:`~molmcp.evolution.evaluate.Challenger` is the checkout under
evaluation. Different concepts, so different names — though the report
field is still ``candidate_sha``.

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

from .evaluate import (
    ACCEPTED,
    DEFAULT_SEEDS,
    DROP_CALL_COUNT,
    DROP_LATENCY_S,
    DROP_TOKENS,
    DROP_TOOL_ERRORS,
    NO_PRACTICAL_GAIN,
    REGRESSION_FAILED,
    WORSE_CALL_COUNT,
    WORSE_LATENCY,
    WORSE_TOKENS,
    WORSE_TOOL_ERRORS,
    Challenger,
    ContractOutcome,
    ContractRunner,
    EvalCase,
    EvaluationError,
    EvaluationReport,
    Metrics,
    ReplayFn,
    evaluate,
)
from .propose import (
    BundleView,
    Candidate,
    Component,
    Pattern,
    Receipt,
    ReceiptsView,
    WikiView,
    propose,
)
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
    "ACCEPTED",
    "DEFAULT_SEEDS",
    "DROP_CALL_COUNT",
    "DROP_LATENCY_S",
    "DROP_TOKENS",
    "DROP_TOOL_ERRORS",
    "NO_PRACTICAL_GAIN",
    "RECEIPT_FIELDS",
    "RECEIPT_TTL_DAYS",
    "RECEIPT_VERSION",
    "REGRESSION_FAILED",
    "SHARE_RECEIPTS_KEY",
    "WORSE_CALL_COUNT",
    "WORSE_LATENCY",
    "WORSE_TOKENS",
    "WORSE_TOOL_ERRORS",
    "BundleView",
    "Candidate",
    "Challenger",
    "Component",
    "Consent",
    "ContractOutcome",
    "ContractRunner",
    "EpisodeReceipt",
    "EvalCase",
    "EvaluationError",
    "EvaluationReport",
    "Maintainer",
    "Metrics",
    "Pattern",
    "Receipt",
    "ReceiptError",
    "ReceiptLog",
    "ReceiptsView",
    "ReplayFn",
    "WikiError",
    "WikiPage",
    "WikiReceipt",
    "WikiStore",
    "WikiView",
    "evaluate",
    "propose",
    "redact_text",
    "render_page",
    "upload_payload",
]
