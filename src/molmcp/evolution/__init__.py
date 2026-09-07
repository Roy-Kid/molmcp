"""The pattern wiki and the evolution decisions taken on top of it.

A *pattern* is a shape of work the harness meets more than once, and
:mod:`~molmcp.evolution.wiki` gives each one a single page: the verdicts
it has collected, oldest first, and a current hypothesis derived from
them rather than stored beside them. Pages live in a local directory the
caller names, and :class:`~molmcp.evolution.wiki.Maintainer` is the only
way a verdict reaches one. It duck-types what it ingests, reading four
attribute names off whatever it is handed, so the shape that carries a
verdict can change without moving the wiki.

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

:mod:`~molmcp.evolution.promote` is who holds it. A
:class:`~molmcp.evolution.promote.PromotionRequest` pairs one full
commit identity with the report that judged it;
:class:`~molmcp.evolution.promote.GatePolicy` rules on it from an
owner/bot/other table and nothing else — no network, no credential, no
forge. :class:`~molmcp.evolution.promote.Promoter` then does the one
thing the ruling earns: a low-risk change is staged and promoted on an
injected, duck-typed pointer machine; a high-risk one is only parked in
a private ``canary.json`` with the pointer left alone; a refused one
moves nothing. Its ledger's rule for undoing an activation is that a
``rolled_back`` row consumes the previous slot, so the current
activation is the last ``activated`` row with no ``rolled_back`` row
after it — never a pairing by report id, which would re-activate a
generation that had already been withdrawn.

Note the two ``C`` names this façade carries.
:class:`~molmcp.evolution.propose.Candidate` is a proposed patch;
:class:`~molmcp.evolution.evaluate.Challenger` is the checkout under
evaluation. Different concepts, so different names — though the report
field is still ``candidate_sha``.

Leaf package, a sibling of :mod:`molmcp.helpers`: the standard library
plus that helper. It does not import FastMCP, does not read settings,
does not borrow the adoption ledger, and is not re-exported from
:mod:`molmcp` — a wiki page is not a plane, and a promotion verdict is
not an MCP tool.

``fence_untrusted`` is absent from ``__all__`` on purpose. The fence
belongs to the one read path that hands text to an LLM — the markdown
:func:`~molmcp.evolution.wiki.render_page` returns — and that function
imports it itself; bytes written by
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
from .promote import (
    PROMOTER_STATE_VERSION,
    ApplyOutcome,
    ApplyResult,
    AuthorKind,
    GateDecision,
    GatePolicy,
    HistoryEntry,
    Promoter,
    PromoterError,
    PromotionRequest,
    Risk,
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
    "PROMOTER_STATE_VERSION",
    "REGRESSION_FAILED",
    "WORSE_CALL_COUNT",
    "WORSE_LATENCY",
    "WORSE_TOKENS",
    "WORSE_TOOL_ERRORS",
    "ApplyOutcome",
    "ApplyResult",
    "AuthorKind",
    "BundleView",
    "Candidate",
    "Challenger",
    "Component",
    "ContractOutcome",
    "ContractRunner",
    "EvalCase",
    "EvaluationError",
    "EvaluationReport",
    "GateDecision",
    "GatePolicy",
    "HistoryEntry",
    "Maintainer",
    "Metrics",
    "Pattern",
    "Promoter",
    "PromoterError",
    "PromotionRequest",
    "Receipt",
    "ReceiptsView",
    "ReplayFn",
    "Risk",
    "WikiError",
    "WikiPage",
    "WikiReceipt",
    "WikiStore",
    "WikiView",
    "evaluate",
    "propose",
    "render_page",
]
