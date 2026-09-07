"""The gate that says whether a harness change is worth taking.

A *harness* is the set of skills, rules and agent definitions that shape
how an agent works. Changing one is cheap; knowing whether the change
helped is not. :mod:`~molmcp.evolution.evaluate` is the part that can be
made reproducible: given a challenger checkout, the champion's sha, and
readings taken on both, it returns one frozen
:class:`~molmcp.evolution.evaluate.EvaluationReport` — accepted or not,
and the single reason why.

Four readings, compared one at a time and never summed into a score. A
composite would let a cheap win pay for a broken run, so the report names
which reading decided rather than hiding it in an average. The comparison
runs on the un-rounded means and only the report rounds: a champion
averaging 10.0 against a challenger averaging 10.4 both round to 10, and
rounding first would let that regression through as a tie.

The two seams it needs — :class:`~molmcp.evolution.evaluate.ContractRunner`
and :class:`~molmcp.evolution.evaluate.ReplayFn` — are keyword-only with
no default at all, because any default would have to be a real host and a
host is exactly what this module is kept away from. Who fills them, and
how the readings are taken, is the caller's problem; this module only
decides.

It moves no pointer. The report is a verdict, and acting on one belongs
to whoever holds the pointer.

Leaf package, a sibling of :mod:`molmcp.helpers`: the standard library
and its own types. It does not import FastMCP, does not read settings,
and is not re-exported from :mod:`molmcp` — a verdict is not an MCP tool.
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

__all__ = [
    "ACCEPTED",
    "DEFAULT_SEEDS",
    "DROP_CALL_COUNT",
    "DROP_LATENCY_S",
    "DROP_TOKENS",
    "DROP_TOOL_ERRORS",
    "NO_PRACTICAL_GAIN",
    "REGRESSION_FAILED",
    "WORSE_CALL_COUNT",
    "WORSE_LATENCY",
    "WORSE_TOKENS",
    "WORSE_TOOL_ERRORS",
    "Challenger",
    "ContractOutcome",
    "ContractRunner",
    "EvalCase",
    "EvaluationError",
    "EvaluationReport",
    "Metrics",
    "ReplayFn",
    "evaluate",
]
