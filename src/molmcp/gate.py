"""``molmcp gate`` — the wiring contract, and nothing else.

This repository has one required GitHub check, ``official/gate``, and the
same sentence runs it in three places: the pull-request job's ``run:``, the
schedule job's ``run:``, and the pre-push hook's ``entry:``. All three are
serialized copies of :data:`GATE_RUN`; the authority is the constant here.
:func:`run_gate` reads the two files those copies live in and reports every
place they have drifted apart.

What it deliberately does not do is run anything. Lint and tests belong to
``ci.yml``'s OS/Python matrix, and a gate that shelled out to them would be
a second, slower copy of that matrix which could disagree with the first.
So this module spawns no process, and it reads no environment either: a
gate configured from outside decides one thing on a laptop and another on a
runner, which is exactly the disagreement it exists to catch.

There is one profile. An earlier draft had a ``--full`` that called an
evaluation, but an evaluation needs two subagents and a GitHub runner has
none, so it could never have run where it was wired. Every parameter that
would have selected a profile is gone: :func:`run_gate` takes ``root`` and
that is the whole signature.

A root missing half the contract is a verdict, not a traceback — a report
saying which file is absent is actionable, and an ``OSError`` out of a
required check only says the check itself broke. Each message names the
offending token, so a reader can act on it without opening both files.

The YAML reading here is a scanner over a known shape, not a parser: these
two files are written by this repository, and every value it needs is a
scalar or a flow sequence on one line. Adding a YAML dependency to compare
four strings would be a runtime dependency for the gate that guards the
runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

__all__ = [
    "CHECK_NAME",
    "GATE_RUN",
    "PR_JOB_ID",
    "SCHEDULE_JOB_ID",
    "GateReport",
    "run_gate",
]

#: The GitHub required check name — the pull-request job's ``name:``, not
#: its id. GitHub matches a required check on the name it displays.
CHECK_NAME = "official/gate"

#: Id of the pull-request job. Never ``gate``:
#: ``.github/workflows/release.yml`` already owns that id for the release
#: gate, and two jobs answering to one id is a rename waiting to happen.
PR_JOB_ID = "official-gate"

#: Id of the scheduled job. It runs the same literal on a timer, so that a
#: wiring broken between pull requests is still found within the week.
SCHEDULE_JOB_ID = "official-gate-schedule"

#: The one call literal. The workflow's two ``run:`` steps and the
#: pre-commit hook's ``entry:`` are copies of this string, compared
#: character for character.
GATE_RUN = "uv run molmcp gate"

#: The workflow half of the contract, relative to the repository root.
WORKFLOW_PATH = ".github/workflows/official-gate.yml"

#: The pre-commit half of the contract, relative to the repository root.
PRE_COMMIT_PATH = ".pre-commit-config.yaml"

#: Id of the pre-commit hook carrying :data:`GATE_RUN`. The same word as
#: :data:`PR_JOB_ID` on purpose: one check, one name everywhere.
HOOK_ID = PR_JOB_ID

#: The hook the commit stage keeps. The gate is a pre-push hook; a commit
#: stage that grew a second slow hook is a wiring change, not a preference.
COMMIT_HOOK_ID = "ci-lint"

#: pre-commit's name for the push stage, and the gate hook's only stage.
PRE_PUSH_STAGE = "pre-push"

#: pre-commit's name for the commit stage.
COMMIT_STAGE = "pre-commit"

#: The pull-request job's guard: it is the check, so it runs for everything
#: except the timer.
PR_IF = "github.event_name != 'schedule'"

#: The scheduled job's guard, the complement of :data:`PR_IF`, so that one
#: event never runs both jobs.
SCHEDULE_IF = "github.event_name == 'schedule'"

#: A GitHub expression. Legal in ``if:`` and ``concurrency:``, forbidden in
#: a ``run:``: what an expression expands to is not what parity compared.
EXPRESSION = "${{"

#: How the gate step is recognised before its literal is compared — the two
#: words no other step in either job carries. Not a second call literal: it
#: selects which ``run:`` to read, and the reading is against
#: :data:`GATE_RUN`.
_GATE_CALL = "molmcp gate"

#: YAML's block scalar indicators. A gate call written as a block is not a
#: single-line literal, whatever its body says.
_BLOCK_INDICATORS = frozenset({"|", "|-", "|+", ">", ">-", ">+"})

#: Quote characters a scalar may be wrapped in.
_QUOTES = "\"'"


@dataclass(frozen=True, slots=True)
class GateReport:
    """The verdict of one :func:`run_gate` call.

    Attributes:
        ok: Whether every checked copy of the contract still agrees.
        failed: One message per disagreement, each naming the file and the
            offending token. Empty exactly when ``ok`` is ``True``.
    """

    ok: bool
    failed: tuple[str, ...]


class _Line(NamedTuple):
    """One significant line of a scanned file.

    Attributes:
        number: 1-based line number, used to name a failure's location.
        indent: Leading spaces, which is what nesting means in these files.
        text: The line with surrounding whitespace removed.
    """

    number: int
    indent: int
    text: str


class _Run(NamedTuple):
    """One ``run:`` step of a job.

    Attributes:
        number: Line number of the ``run:`` key.
        text: The single-line scalar, or the joined body of a block scalar.
        block: Whether the value was written as a block rather than inline.
    """

    number: int
    text: str
    block: bool


def _unquote(value: str) -> str:
    """Strip one matching pair of surrounding quotes from *value*.

    Args:
        value: A scalar as written, already stripped of whitespace.

    Returns:
        The scalar without its wrapping quotes. Quotes inside an unquoted
        value — ``github.event_name != 'schedule'`` — are left alone.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
        return value[1:-1]
    return value


def _significant_lines(path: Path) -> tuple[_Line, ...]:
    """Read *path*, dropping blank lines and whole-line comments.

    Args:
        path: File to read, known to exist.

    Returns:
        Every remaining line with its number and indent.
    """
    lines: list[_Line] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        lines.append(_Line(number, len(raw) - len(raw.lstrip(" ")), text))
    return tuple(lines)


def _key_value(line: _Line) -> tuple[str, str] | None:
    """Split *line* into a mapping key and its scalar.

    A leading ``- `` is dropped first, so the first key of a list item reads
    like any other key. A key containing a space is not a key: that is a
    line of shell inside a block scalar.

    Args:
        line: A significant line.

    Returns:
        The key and its unquoted scalar, or ``None`` when the line is not a
        mapping entry. The scalar is empty when the value is nested below.
    """
    text = line.text[2:].lstrip() if line.text.startswith("- ") else line.text
    key, separator, value = text.partition(":")
    if not separator or not key or " " in key:
        return None
    return key, _unquote(value.strip())


def _children(lines: tuple[_Line, ...], index: int) -> tuple[_Line, ...]:
    """The lines nested under ``lines[index]``.

    Args:
        lines: The block being scanned.
        index: Position of the parent line.

    Returns:
        Every following line indented deeper than the parent, up to the
        first that is not.
    """
    parent = lines[index].indent
    end = index + 1
    while end < len(lines) and lines[end].indent > parent:
        end += 1
    return lines[index + 1 : end]


def _field(block: tuple[_Line, ...], key: str) -> tuple[_Line, str] | None:
    """Look *key* up among the direct children of *block*.

    Args:
        block: The nested lines of one mapping.
        key: Mapping key to find.

    Returns:
        The line carrying *key* and its scalar, or ``None``. Only the
        shallowest lines of *block* are direct children; a deeper ``name:``
        belongs to a step, not to the job.
    """
    if not block:
        return None
    depth = min(line.indent for line in block)
    for line in block:
        if line.indent != depth:
            continue
        pair = _key_value(line)
        if pair is not None and pair[0] == key:
            return line, pair[1]
    return None


def _sequence(block: tuple[_Line, ...], key: str) -> tuple[str, ...] | None:
    """Read *key* of *block* as a list, flow or nested.

    Args:
        block: The nested lines of one mapping.
        key: Mapping key to find.

    Returns:
        The items in order, or ``None`` when *key* is absent.
    """
    found = _field(block, key)
    if found is None:
        return None
    line, value = found
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return (
            tuple(_unquote(item.strip()) for item in inner.split(",")) if inner else ()
        )
    if value:
        return (value,)
    return tuple(
        _unquote(child.text[2:].strip())
        for child in _children(block, block.index(line))
        if child.text.startswith("- ")
    )


def _jobs(lines: tuple[_Line, ...]) -> dict[str, tuple[_Line, ...]]:
    """Every job of a workflow, by id.

    Args:
        lines: The significant lines of a workflow file.

    Returns:
        Each job id mapped to the lines nested under it, in file order.
        Empty when the file has no top-level ``jobs:``.
    """
    block: tuple[_Line, ...] = ()
    for index, line in enumerate(lines):
        if line.indent == 0 and _key_value(line) == ("jobs", ""):
            block = _children(lines, index)
            break
    if not block:
        return {}
    depth = min(line.indent for line in block)
    jobs: dict[str, tuple[_Line, ...]] = {}
    for index, line in enumerate(block):
        pair = _key_value(line) if line.indent == depth else None
        if pair is not None:
            jobs[pair[0]] = _children(block, index)
    return jobs


def _runs(block: tuple[_Line, ...]) -> tuple[_Run, ...]:
    """Every ``run:`` anywhere inside a job.

    Args:
        block: The lines nested under one job.

    Returns:
        One :class:`_Run` per ``run:`` key, in file order.
    """
    runs: list[_Run] = []
    for index, line in enumerate(block):
        pair = _key_value(line)
        if pair is None or pair[0] != "run":
            continue
        value = pair[1]
        if value and value not in _BLOCK_INDICATORS:
            runs.append(_Run(line.number, value, False))
            continue
        body = " ".join(child.text for child in _children(block, index))
        runs.append(_Run(line.number, body, True))
    return tuple(runs)


def _hooks(lines: tuple[_Line, ...]) -> dict[str, tuple[_Line, ...]]:
    """Every pre-commit hook, by id.

    Args:
        lines: The significant lines of a pre-commit config.

    Returns:
        Each hook id mapped to the lines nested under its list item.
    """
    hooks: dict[str, tuple[_Line, ...]] = {}
    for index, line in enumerate(lines):
        if not line.text.startswith("- "):
            continue
        pair = _key_value(line)
        if pair is not None and pair[0] == "id":
            hooks[pair[1]] = _children(lines, index)
    return hooks


def _check_gate_call(job_id: str, block: tuple[_Line, ...]) -> tuple[str, ...]:
    """Check that the job calls the gate by the one literal.

    Args:
        job_id: Id of the job, so a failure names which one.
        block: The lines nested under that job.

    Returns:
        A message per offending step: none found, written as a block, or
        written as some other string.
    """
    calls = [run for run in _runs(block) if _GATE_CALL in run.text]
    if not calls:
        return (
            f"{WORKFLOW_PATH}: job {job_id!r} has no step whose "
            f"run: is {GATE_RUN!r} (the Install step is a prior step, "
            f"not the compared token).",
        )
    failed: list[str] = []
    for run in calls:
        if run.block:
            failed.append(
                f"{WORKFLOW_PATH}: job {job_id!r} line {run.number}: "
                f"run: is a block scalar; the gate call is the single-line "
                f"run: {GATE_RUN!r}."
            )
        elif run.text != GATE_RUN:
            failed.append(
                f"{WORKFLOW_PATH}: job {job_id!r} line {run.number}: "
                f"run: {run.text!r} is not {GATE_RUN!r}."
            )
    return tuple(failed)


def _check_literal_runs(job_id: str, block: tuple[_Line, ...]) -> tuple[str, ...]:
    """Check that no ``run:`` of the job hides behind an expression.

    Args:
        job_id: Id of the job, so a failure names which one.
        block: The lines nested under that job.

    Returns:
        A message per ``run:`` containing a GitHub expression. ``if:`` and
        ``concurrency:`` may hold one; a ``run:`` may not, because what an
        expression expands to on a runner is not what parity compared.
    """
    return tuple(
        f"{WORKFLOW_PATH}: job {job_id!r} line {run.number}: "
        f"run: {run.text!r} contains {EXPRESSION!r}; a run: that expands "
        f"is not a literal."
        for run in _runs(block)
        if EXPRESSION in run.text
    )


def _check_no_profile_env(job_id: str, block: tuple[_Line, ...]) -> tuple[str, ...]:
    """Check that the job selects nothing from the environment.

    Args:
        job_id: Id of the job, so a failure names which one.
        block: The lines nested under that job.

    Returns:
        A message per ``env:`` key found anywhere in the job. There is one
        profile, so an ``env:`` here can only be selecting a second.
    """
    return tuple(
        f"{WORKFLOW_PATH}: job {job_id!r} line {line.number}: env: — the "
        f"gate has one profile and selects nothing from the environment."
        for line in block
        if (pair := _key_value(line)) is not None and pair[0] == "env"
    )


def _check_condition(
    job_id: str, block: tuple[_Line, ...], expected: str
) -> tuple[str, ...]:
    """Check the job's ``if:`` guard.

    Args:
        job_id: Id of the job, so a failure names which one.
        block: The lines nested under that job.
        expected: The guard this job must carry.

    Returns:
        One message when the guard is absent or different, else nothing.
    """
    found = _field(block, "if")
    if found is None:
        return (f"{WORKFLOW_PATH}: job {job_id!r} has no if:; expected {expected!r}.",)
    if found[1] != expected:
        return (
            f"{WORKFLOW_PATH}: job {job_id!r} line {found[0].number}: "
            f"if: {found[1]!r} is not {expected!r}.",
        )
    return ()


def _check_pr_name(block: tuple[_Line, ...]) -> tuple[str, ...]:
    """Check that the pull-request job is named after the required check.

    Args:
        block: The lines nested under the pull-request job.

    Returns:
        One message when the ``name:`` GitHub matches on is absent or is
        not :data:`CHECK_NAME`, else nothing.
    """
    found = _field(block, "name")
    if found is None:
        return (
            f"{WORKFLOW_PATH}: job {PR_JOB_ID!r} has no name:; the required "
            f"check is matched on name: {CHECK_NAME!r}.",
        )
    if found[1] != CHECK_NAME:
        return (
            f"{WORKFLOW_PATH}: job {PR_JOB_ID!r} line {found[0].number}: "
            f"name: {found[1]!r} is not the required check name "
            f"{CHECK_NAME!r}.",
        )
    return ()


def _check_schedule_name(block: tuple[_Line, ...]) -> tuple[str, ...]:
    """Check that the scheduled job does not claim the required check name.

    Args:
        block: The lines nested under the scheduled job.

    Returns:
        One message when the job is named :data:`CHECK_NAME`, else nothing.
        Two jobs under one name would let a timer report the check that a
        pull request is supposed to report.
    """
    found = _field(block, "name")
    if found is not None and found[1] == CHECK_NAME:
        return (
            f"{WORKFLOW_PATH}: job {SCHEDULE_JOB_ID!r} line "
            f"{found[0].number}: name: {CHECK_NAME!r} is the required check "
            f"name; the scheduled job needs its own.",
        )
    return ()


def _check_workflow(root: Path) -> tuple[str, ...]:
    """Check the workflow half of the contract under *root*.

    Args:
        root: Repository root the relative paths are read from.

    Returns:
        Every disagreement found, empty when the workflow is wired.
    """
    path = root / WORKFLOW_PATH
    if not path.is_file():
        return (
            f"{WORKFLOW_PATH} is missing: nothing runs {GATE_RUN!r} on a "
            f"pull request, so the {CHECK_NAME!r} check reports nothing.",
        )
    jobs = _jobs(_significant_lines(path))
    expected = (PR_JOB_ID, SCHEDULE_JOB_ID)
    found = ", ".join(jobs) or "none"
    failed = [
        f"{WORKFLOW_PATH}: no job with id {job_id!r} (jobs found: {found})."
        for job_id in expected
        if job_id not in jobs
    ]
    failed.extend(
        f"{WORKFLOW_PATH}: unexpected job id {job_id!r}; this workflow holds "
        f"{PR_JOB_ID!r} and {SCHEDULE_JOB_ID!r} and nothing else."
        for job_id in jobs
        if job_id not in expected
    )
    for job_id, condition in ((PR_JOB_ID, PR_IF), (SCHEDULE_JOB_ID, SCHEDULE_IF)):
        block = jobs.get(job_id)
        if block is None:
            continue
        failed.extend(_check_condition(job_id, block, condition))
        failed.extend(_check_gate_call(job_id, block))
        failed.extend(_check_literal_runs(job_id, block))
        failed.extend(_check_no_profile_env(job_id, block))
    if PR_JOB_ID in jobs:
        failed.extend(_check_pr_name(jobs[PR_JOB_ID]))
    if SCHEDULE_JOB_ID in jobs:
        failed.extend(_check_schedule_name(jobs[SCHEDULE_JOB_ID]))
    return tuple(failed)


def _check_gate_hook(hook: tuple[_Line, ...]) -> tuple[str, ...]:
    """Check the gate hook's ``entry:`` and stage.

    Args:
        hook: The lines nested under the ``official-gate`` hook.

    Returns:
        A message per disagreement. The ``entry:`` is the bare literal —
        not ``entry: uv`` plus ``args:``, and not a
        ``bash -c 'uv sync && …'`` wrapper, either of which is a different
        string from the one the workflow runs.
    """
    failed: list[str] = []
    entry = _field(hook, "entry")
    if entry is None:
        failed.append(
            f"{PRE_COMMIT_PATH}: hook {HOOK_ID!r} has no entry:; expected "
            f"entry: {GATE_RUN!r}."
        )
    elif entry[1] != GATE_RUN:
        failed.append(
            f"{PRE_COMMIT_PATH}: hook {HOOK_ID!r} line {entry[0].number}: "
            f"entry: {entry[1]!r} is not {GATE_RUN!r}."
        )
    stages = _sequence(hook, "stages")
    if stages != (PRE_PUSH_STAGE,):
        failed.append(
            f"{PRE_COMMIT_PATH}: hook {HOOK_ID!r} stages: "
            f"{list(stages or ())} is not [{PRE_PUSH_STAGE}]; the gate runs "
            f"before a push and the commit stage stays fast."
        )
    return tuple(failed)


def _check_pre_commit(root: Path) -> tuple[str, ...]:
    """Check the pre-commit half of the contract under *root*.

    Args:
        root: Repository root the relative paths are read from.

    Returns:
        Every disagreement found, empty when the hook is wired.
    """
    path = root / PRE_COMMIT_PATH
    if not path.is_file():
        return (
            f"{PRE_COMMIT_PATH} is missing: nothing runs {GATE_RUN!r} before "
            f"a push, so the wiring is only checked once it is on GitHub.",
        )
    hooks = _hooks(_significant_lines(path))
    failed: list[str] = []
    gate_hook = hooks.get(HOOK_ID)
    if gate_hook is None:
        failed.append(
            f"{PRE_COMMIT_PATH}: no hook with id {HOOK_ID!r} carrying "
            f"entry: {GATE_RUN!r}."
        )
    else:
        failed.extend(_check_gate_hook(gate_hook))
    commit_hook = hooks.get(COMMIT_HOOK_ID)
    if commit_hook is None:
        failed.append(
            f"{PRE_COMMIT_PATH}: no hook with id {COMMIT_HOOK_ID!r}; the "
            f"{COMMIT_STAGE!r} stage holds it and nothing else."
        )
    else:
        stages = _sequence(commit_hook, "stages")
        if stages is None or COMMIT_STAGE not in stages:
            failed.append(
                f"{PRE_COMMIT_PATH}: hook {COMMIT_HOOK_ID!r} no longer lists "
                f"the {COMMIT_STAGE!r} stage; the {COMMIT_STAGE!r} stage "
                f"holds it and nothing else."
            )
    return tuple(failed)


def run_gate(*, root: Path) -> GateReport:
    """Decide whether the wiring contract under *root* still holds.

    Reads two files and compares four strings: the pull-request job's gate
    ``run:``, the scheduled job's gate ``run:``, the pre-push hook's
    ``entry:``, and the pull-request job's ``name:``. Nothing is executed,
    no process is spawned, and no environment is read — lint and tests are
    ``ci.yml``'s matrix, and a gate that read the environment would decide
    differently on a laptop than on a runner.

    Args:
        root: Repository root to read the contract from. Required and
            keyword-only: the CLI passes ``Path.cwd()`` and tests pass a
            fixture tree, so the gate never guesses which repository it is
            judging.

    Returns:
        A :class:`GateReport` whose ``failed`` names every disagreement,
        file and token. A missing file is one of those messages, not an
        exception: a required check that raises only says that it broke.
    """
    failed = (*_check_workflow(root), *_check_pre_commit(root))
    return GateReport(ok=not failed, failed=failed)
