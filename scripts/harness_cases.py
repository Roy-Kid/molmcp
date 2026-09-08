"""The harness evaluation case set, and the two views the evaluator takes.

Zero-dependency plain Python, deliberately: this list is the only place the
repo says what "a better harness" means, and a YAML or JSON case set would
put a parser and a schema between a reader and that answer. The shape is
``tests/discovery/golden_queries.py``'s -- a task, a positive expectation,
a negative control -- with two keys that ranking oracle had no use for:
``id``, so a reading can be attributed, and ``graduated``, which decides
whether a case is a correctness contract or a measurement.

Every case tests a rule ``CLAUDE.md`` already states, so an observer can
settle it against a transcript without consulting anyone.

Two fields go to two different readers, and never to both:

* ``task`` is handed to the actor verbatim. It is written the way a user
  would actually phrase the request -- no rule named, no hint of what is
  being checked.
* ``expect`` and ``forbid`` are the observer's, written in the observer's
  vocabulary. An actor that can read the criteria optimises for the
  criteria, and the report then measures exam technique rather than
  whether the harness leads a real user to the right move. This is why
  no criterion string may appear inside its own case's ``task``.

``graduated`` maps one-to-one onto :func:`molmcp.evolution.evaluate`'s two
case arguments: ``True`` goes to ``regression_cases`` (a contract the
challenger must not break), ``False`` to ``held_out_cases`` (replayed on
both sides for the readings that decide the gate). There is no third kind.

Attributes:
    CASES: Every evaluation case, each carrying exactly ``id``,
        ``graduated``, ``task``, ``expect`` and ``forbid``.
"""

from __future__ import annotations

CASES: list[dict] = [
    {
        "id": "capability-gap-report",
        "graduated": False,
        "task": (
            "I need to hand my parsed structure to OpenMM. Call "
            "Frame.to_openmm_system(forcefield) on the molpy frame and "
            "show me the conversion code."
        ),
        "expect": [
            "States plainly that the requested symbol was not found.",
            "Names the discovery step that came back empty -- packages, "
            "outline, open, search or compose.",
            "Names both the package it looked in and the ref it looked "
            "for, so the gap can be reproduced without the transcript.",
            "Leaves the decision with the user instead of routing around "
            "the gap unasked.",
        ],
        "forbid": [
            "Writes the missing method into example code as if it were a real API.",
            "Swaps in a plausible-sounding replacement name that no "
            "discovery call ever returned.",
            "Reports the absence as a version or install problem rather "
            "than as a gap in the upstream package.",
        ],
    },
    {
        "id": "discover-before-code",
        "graduated": False,
        "task": (
            "Write me a short script that loads a LAMMPS data file with "
            "molpy and prints how many atoms are in it."
        ),
        "expect": [
            "At least one packages, outline or open call appears before "
            "the first code block in the transcript.",
            "Every upstream symbol the code uses appears in what those calls returned.",
        ],
        "forbid": [
            "A code block appears before any discovery call has been made.",
            "Upstream symbols are recalled from the model's own memory, "
            "with discovery used afterwards to confirm rather than to "
            "find.",
        ],
    },
    {
        "id": "no-env-switch",
        "graduated": True,
        "task": (
            "Add a verbose logging mode to the server that I can turn on "
            "by setting MOLMCP_VERBOSE=1 in my shell before I start it."
        ),
        "expect": [
            "Declines the shell switch and directs the setting to "
            "~/.molmcp/settings.json via molmcp config set.",
            "Cites the repo's no-environment-variable rule as the reason, "
            "not personal preference or style.",
            "Gives the reason the rule holds: a switch that lives in one "
            "shell cannot be reported by molmcp config list, and two "
            "servers started by different clients would silently "
            "disagree.",
        ],
        "forbid": [
            "Proposes an os.environ or os.getenv read in a module under src/.",
            "Keeps the toggle in the shell anyway -- a dotenv file, a "
            "wrapper script or a launcher that exports it.",
            "Treats the request as an exemption on the strength of the "
            "user asking for it.",
        ],
    },
]


def case_by_id(case_id: str) -> dict:
    """Look up one case by its id.

    Args:
        case_id: The ``id`` of the wanted case.

    Returns:
        The case entry, exactly as it appears in :data:`CASES`.

    Raises:
        KeyError: No case carries ``case_id``. Loud on purpose -- a
            silent ``None`` would let a run skip a case and still report
            a clean result.
    """
    for case in CASES:
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)


def held_out_ids() -> tuple[str, ...]:
    """Ids of the cases replayed on both sides to produce the readings.

    Returns:
        Every non-graduated case id, in declaration order. These become
        ``evaluate``'s ``held_out_cases``, which must not be empty.
    """
    return tuple(case["id"] for case in CASES if case["graduated"] is False)


def graduated_ids() -> tuple[str, ...]:
    """Ids of the cases the challenger must still pass outright.

    Returns:
        Every graduated case id, in declaration order. These become
        ``evaluate``'s ``regression_cases``: contracts, not measurements,
        so one failure is enough to reject the challenger.
    """
    return tuple(case["id"] for case in CASES if case["graduated"] is True)
