"""``run_gate`` — the wiring contract under one repository root.

``molmcp gate`` is this repository's single required GitHub check, and what
it decides is narrow: whether three copies of one sentence still agree — the
literal ``run:`` of the pull-request job, the literal ``run:`` of the
schedule job, and the pre-commit hook's ``entry:``. It decides nothing else.
Lint and tests belong to ``ci.yml``'s OS/Python matrix; a gate that shelled
out to them would be a second, slower copy of that matrix, and a gate that
read the environment would decide differently on a laptop than on a runner.

Every verdict test therefore hands ``run_gate`` a *root* and reads the
report. The two trees under ``tests/fixtures/gate/`` carry the same relative
paths production reads — ``.github/workflows/official-gate.yml`` and
``.pre-commit-config.yaml``. ``wired/`` is a legal wiring; ``contract-fail/``
is that same wiring with one line changed, the hook's ``entry:`` wrapped in
``bash -c 'uv sync --extra dev && …'`` so that it no longer equals the PR
job's ``run:``. One planted breakage is what makes the reported failure
attributable to a line rather than to the tree.

The static half states what ``gate.py`` must never grow. An earlier draft of
this spec had a ``--full`` profile that called spec 11's ``evaluate``; it was
deleted because an evaluation needs two subagents and a GitHub runner has
none, and because the module it named never existed. The constants, the
signature, and the report's two fields are pinned here so that the deleted
profile cannot walk back in through the stale acceptance file that still
mentions ``FULL_RUN``.

``TestOfficialGateParity`` reads no fixture. It opens the files this repository
actually ships and asserts each copied token against ``gate.GATE_RUN`` — the
same equality ``run_gate`` checks, asserted from the other side. It is not a
diff between the workflow and the pre-commit config: two copies that drifted
together would still agree with each other and still be wrong, so each is
compared against the constant that is the authority. Its scanner is local for
the same reason. Borrowing ``gate.py``'s own reader would leave these
assertions blind to the one bug that would matter most — a reader that
mis-parses the repository's shape, and so compares nothing at all.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest
from _ast_checks import reads_environment

from molmcp import gate

#: Imported as a module, not by name: several tests below ask which names the
#: module *has* (``hasattr(gate, "FULL_RUN")``), which needs the module object
#: rather than a list of names that already resolved.
run_gate = gate.run_gate
GateReport = gate.GateReport

_REPO = Path(__file__).resolve().parents[1]

#: The module under test, read as data by the static half.
_GATE_SOURCE = _REPO / "src" / "molmcp" / "gate.py"

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gate"

#: A legal wiring: the two files agree on the one literal.
WIRED = _FIXTURES / "wired"

#: The same tree with the hook's ``entry:`` wrapped, and nothing else moved.
CONTRACT_FAIL = _FIXTURES / "contract-fail"

#: The two paths ``run_gate`` reads, relative to the root it is given.
_WORKFLOW = ".github/workflows/official-gate.yml"
_PRE_COMMIT = ".pre-commit-config.yaml"
_CONTRACT_FILES = (_WORKFLOW, _PRE_COMMIT)

#: ``gate.py`` is the authority; the YAML files are serialized copies.
_CONSTANTS = (
    ("CHECK_NAME", "official/gate"),
    ("PR_JOB_ID", "official-gate"),
    ("SCHEDULE_JOB_ID", "official-gate-schedule"),
    ("GATE_RUN", "uv run molmcp gate"),
)

#: Names of the deleted profile. ``release.yml`` already owns job id ``gate``.
_DELETED_CONSTANTS = ("FULL_RUN", "CHEAP_RUN", "GATE_PROFILE")

#: Parameters a profile would need. ``root`` is the whole signature.
_DELETED_PARAMETERS = ("full", "evaluate", "skip", "profile")

#: The report's fields, in order.
_REPORT_FIELDS = ("ok", "failed")

#: Fragments of the one planted breakage. A verdict that does not name the
#: offending token leaves the reader with the same search the gate just did.
_OFFENCE_FRAGMENTS = ("entry", "bash -c")

#: Runners ``run_gate`` must not become. Lint and tests stay in ``ci.yml``.
_FORBIDDEN_IMPORTS = ("subprocess", "pytest", "ruff")

#: Call names that would mean the verdict spawned a process.
_SPAWN_CALLS = frozenset({"Popen", "check_output", "check_call", "system", "run_safe"})


def _gate_tree() -> ast.Module:
    """``gate.py`` parsed, or a readable failure instead of an ``OSError``."""
    assert _GATE_SOURCE.is_file(), (
        f"{_GATE_SOURCE.relative_to(_REPO)} does not exist. The verdict has "
        f"one owner: cli.py only dispatches to run_gate."
    )
    return ast.parse(_GATE_SOURCE.read_text(encoding="utf-8"))


def _imported_modules(tree: ast.AST) -> set[str]:
    """Every module name an ``import`` or ``from … import`` names."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            modules.add(module)
            modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


def _called_names(tree: ast.AST) -> set[str]:
    """Every simple name or attribute that appears in call position."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _wired_missing(tmp_path: Path, relative: str) -> Path:
    """A copy of the wired tree with one of the two contract files removed."""
    root = tmp_path / "root"
    shutil.copytree(WIRED, root)
    (root / relative).unlink()
    return root


def _messages(report: GateReport) -> str:
    return "\n".join(report.failed)


class TestRunGate:
    # -- the legal wiring ----------------------------------------------

    def test_wired_fixture_is_ok(self) -> None:
        assert run_gate(root=WIRED).ok is True

    def test_wired_fixture_reports_no_failure(self) -> None:
        report = run_gate(root=WIRED)

        assert report.failed == ()

    def test_returns_a_gate_report(self) -> None:
        assert isinstance(run_gate(root=WIRED), GateReport)

    # -- the planted breakage ------------------------------------------

    def test_contract_fail_fixture_is_not_ok(self) -> None:
        assert run_gate(root=CONTRACT_FAIL).ok is False

    def test_contract_fail_fixture_reports_a_failure(self) -> None:
        report = run_gate(root=CONTRACT_FAIL)

        assert report.failed != ()

    @pytest.mark.parametrize("fragment", _OFFENCE_FRAGMENTS)
    def test_contract_fail_verdict_names_the_disagreeing_token(
        self, fragment: str
    ) -> None:
        """The one changed line is the hook's wrapped ``entry:``."""
        report = run_gate(root=CONTRACT_FAIL)

        assert fragment in _messages(report), (
            f"the verdict does not name {fragment!r}: {report.failed}"
        )

    def test_failed_is_a_tuple_of_strings(self) -> None:
        report = run_gate(root=CONTRACT_FAIL)

        assert isinstance(report.failed, tuple)
        assert all(isinstance(message, str) for message in report.failed)

    # -- a root that is missing half the contract -----------------------

    @pytest.mark.parametrize("relative", _CONTRACT_FILES)
    def test_missing_contract_file_is_a_verdict_not_an_exception(
        self, tmp_path: Path, relative: str
    ) -> None:
        """A half-wired tree is red, not a traceback out of the gate."""
        root = _wired_missing(tmp_path, relative)

        assert run_gate(root=root).ok is False

    @pytest.mark.parametrize("relative", _CONTRACT_FILES)
    def test_missing_contract_file_reports_a_failure(
        self, tmp_path: Path, relative: str
    ) -> None:
        root = _wired_missing(tmp_path, relative)

        assert run_gate(root=root).failed != ()

    def test_empty_root_is_not_ok(self, tmp_path: Path) -> None:
        assert run_gate(root=tmp_path).ok is False

    # -- signature ------------------------------------------------------

    def test_signature_is_root_and_nothing_else(self) -> None:
        assert list(inspect.signature(run_gate).parameters) == ["root"]

    def test_root_is_keyword_only(self) -> None:
        parameter = inspect.signature(run_gate).parameters["root"]

        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    def test_root_has_no_default(self) -> None:
        """The CLI passes ``Path.cwd()``; the gate never guesses a root."""
        parameter = inspect.signature(run_gate).parameters["root"]

        assert parameter.default is inspect.Parameter.empty

    @pytest.mark.parametrize("name", _DELETED_PARAMETERS)
    def test_carries_no_profile_parameter(self, name: str) -> None:
        """One profile. There is no agent in a runner to evaluate with."""
        assert name not in inspect.signature(run_gate).parameters

    # -- the report -----------------------------------------------------

    def test_report_field_names_are_ok_and_failed(self) -> None:
        names = tuple(field.name for field in dataclasses.fields(GateReport))

        assert names == _REPORT_FIELDS

    def test_report_has_exactly_two_fields(self) -> None:
        assert len(dataclasses.fields(GateReport)) == 2

    @pytest.mark.parametrize("field_name", _REPORT_FIELDS)
    def test_report_is_frozen(self, field_name: str) -> None:
        report = run_gate(root=WIRED)

        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(report, field_name, "mutated")

    def test_report_uses_slots(self) -> None:
        report = run_gate(root=WIRED)

        assert hasattr(GateReport, "__slots__")
        assert not hasattr(report, "__dict__")

    def test_ok_is_a_bool(self) -> None:
        assert isinstance(run_gate(root=WIRED).ok, bool)

    # -- constants ------------------------------------------------------

    @pytest.mark.parametrize(("name", "literal"), _CONSTANTS)
    def test_constant_equals_its_literal(self, name: str, literal: str) -> None:
        assert getattr(gate, name) == literal

    def test_pr_job_id_is_not_the_release_gate(self) -> None:
        """``.github/workflows/release.yml`` already owns job id ``gate``."""
        assert gate.PR_JOB_ID != "gate"

    def test_the_two_jobs_have_different_ids(self) -> None:
        assert gate.SCHEDULE_JOB_ID != gate.PR_JOB_ID

    def test_check_name_is_a_job_name_not_a_job_id(self) -> None:
        """GitHub matches a required check on the job's ``name:``."""
        assert gate.CHECK_NAME != gate.PR_JOB_ID

    @pytest.mark.parametrize("name", _DELETED_CONSTANTS)
    def test_module_carries_no_second_profile_literal(self, name: str) -> None:
        """A second literal is a second thing for parity to disagree with."""
        assert not hasattr(gate, name)

    # -- what the source must never grow, read off the source -----------

    def test_source_never_reads_the_environment(self) -> None:
        """A gate configured by the environment decides two things at once."""
        assert not reads_environment(_gate_tree())

    @pytest.mark.parametrize("module", _FORBIDDEN_IMPORTS)
    def test_source_imports_no_runner(self, module: str) -> None:
        """Lint and tests are ``ci.yml``'s matrix; the gate checks wiring."""
        imported = _imported_modules(_gate_tree())
        offenders = {
            name for name in imported if name == module or name.startswith(f"{module}.")
        }

        assert offenders == set()

    def test_source_imports_nothing_named_evaluate(self) -> None:
        """Evaluation needs two subagents; a GitHub runner has none."""
        imported = _imported_modules(_gate_tree())

        assert [name for name in imported if "evaluate" in name] == []

    def test_source_spawns_no_process(self) -> None:
        called = _called_names(_gate_tree())

        assert called & _SPAWN_CALLS == set()


# -- the repository's own copies ----------------------------------------

#: The two files this repository ships, at the same relative paths the
#: fixtures use. They do not exist until the workflow and the hook are
#: written, which is what every message below has to survive readably.
_REPO_WORKFLOW = _REPO / _WORKFLOW
_REPO_PRE_COMMIT = _REPO / _PRE_COMMIT

#: The product matrix. This spec does not fold the gate into it.
_CI_WORKFLOW = _REPO / ".github" / "workflows" / "ci.yml"

#: The release gate, which already owns the job id ``gate``.
_RELEASE_WORKFLOW = _REPO / ".github" / "workflows" / "release.yml"

#: Both project files carry the same frontmatter, and ``ci.config`` in it
#: still points at the product matrix.
_PROJECT_DOCS = (_REPO / "CLAUDE.md", _REPO / "AGENTS.md")
_CI_CONFIG = ".github/workflows/ci.yml"

_WHY_WORKFLOW = (
    f"Nothing runs {gate.GATE_RUN!r} on a pull request, so the "
    f"{gate.CHECK_NAME!r} required check reports nothing and a branch "
    f"protected by it is protected by an absence."
)

_WHY_PRE_COMMIT = (
    f"Nothing runs {gate.GATE_RUN!r} before a push, so a broken wiring is "
    f"first heard about from GitHub."
)

_WHY_REPO_FILE = (
    "This spec does not create or move it; it is read here only to show that "
    "it stayed where it was."
)

#: The hook carrying the literal. The same word as ``PR_JOB_ID`` on purpose:
#: one check, one name in every file that mentions it.
_HOOK_ID = "official-gate"

#: The hook the commit stage keeps, and the stage names pre-commit uses.
_COMMIT_HOOK_ID = "ci-lint"
_COMMIT_STAGE = "pre-commit"
_PUSH_STAGE = "pre-push"

#: The two keys pair 2 pins: the pull-request job's gate ``run:`` and the
#: hook's ``entry:``. Both are read against ``GATE_RUN``, never against each
#: other.
_PAIR_TWO = ("run", "entry")

#: Wrappers that would make a token a different string from the one the other
#: file runs. ``uv sync --extra dev`` is a prior Install step, not the token.
_WRAPPERS = ("uv sync", "bash -c")

#: How the gate step is picked out before its literal is read. Not a second
#: call literal: it selects which ``run:`` to compare, and the comparison is
#: always against ``gate.GATE_RUN``.
_GATE_CALL = "molmcp gate"

#: The two jobs, read off the authority.
_JOB_IDS = (gate.PR_JOB_ID, gate.SCHEDULE_JOB_ID)

#: A GitHub expression is legal in ``if:`` and ``concurrency:`` and forbidden
#: in a ``run:``: what it expands to on a runner is not what was compared.
_EXPRESSION = "${{"

#: Enough of ``ci.yml``'s matrix to show it is still the product matrix.
_MATRIX_TOKENS = ("matrix:", "os:", "python-version:")

#: YAML's block scalar indicators.
_BLOCK_SCALARS = frozenset({"|", "|-", "|+", ">", ">-", ">+"})


class _Entry(NamedTuple):
    """One significant line of a scanned file.

    Attributes:
        number: 1-based line number, so a failure can name a location.
        indent: Leading spaces, which is what nesting means in these files.
        text: The line with surrounding whitespace removed.
    """

    number: int
    indent: int
    text: str


def _rel(path: Path) -> str:
    return path.relative_to(_REPO).as_posix()


def _read(path: Path, why: str) -> str:
    """The file's text, or a readable failure instead of an ``OSError``."""
    assert path.is_file(), f"{_rel(path)} does not exist. {why}"
    return path.read_text(encoding="utf-8")


def _scan(text: str) -> tuple[_Entry, ...]:
    """*text* as significant lines: blanks and whole-line comments dropped."""
    entries: list[_Entry] = []
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.append(_Entry(number, len(raw) - len(raw.lstrip(" ")), stripped))
    return tuple(entries)


def _under(entries: tuple[_Entry, ...], index: int) -> tuple[_Entry, ...]:
    """Every line nested under ``entries[index]``."""
    parent = entries[index].indent
    end = index + 1
    while end < len(entries) and entries[end].indent > parent:
        end += 1
    return entries[index + 1 : end]


def _unquoted(value: str) -> str:
    """*value* without one matching pair of surrounding quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _pair(entry: _Entry) -> tuple[str, str] | None:
    """*entry* as a ``key: value`` mapping entry, or ``None``.

    A leading ``- `` is dropped, so the first key of a list item reads like
    any other key. A key holding a space is not a key: that is a line of
    shell inside a block scalar.
    """
    text = entry.text[2:].lstrip() if entry.text.startswith("- ") else entry.text
    key, separator, value = text.partition(":")
    if not separator or not key or " " in key:
        return None
    return key, _unquoted(value.strip())


def _scalar(block: tuple[_Entry, ...], key: str) -> str | None:
    """*key* read off the direct children of *block*, or ``None``."""
    if not block:
        return None
    depth = min(entry.indent for entry in block)
    for entry in block:
        if entry.indent != depth:
            continue
        found = _pair(entry)
        if found is not None and found[0] == key:
            return found[1]
    return None


def _items(block: tuple[_Entry, ...], key: str) -> tuple[str, ...]:
    """*key* read off *block* as a list, written flow (``[a, b]``) or nested."""
    if not block:
        return ()
    depth = min(entry.indent for entry in block)
    for index, entry in enumerate(block):
        if entry.indent != depth:
            continue
        found = _pair(entry)
        if found is None or found[0] != key:
            continue
        value = found[1]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return ()
            return tuple(_unquoted(part.strip()) for part in inner.split(","))
        if value:
            return (value,)
        return tuple(
            _unquoted(child.text[2:].strip())
            for child in _under(block, index)
            if child.text.startswith("- ")
        )
    return ()


def _runs(block: tuple[_Entry, ...]) -> tuple[str, ...]:
    """Every ``run:`` anywhere in a job, a block scalar joined into one line."""
    texts: list[str] = []
    for index, entry in enumerate(block):
        found = _pair(entry)
        if found is None or found[0] != "run":
            continue
        value = found[1]
        if value and value not in _BLOCK_SCALARS:
            texts.append(value)
        else:
            texts.append(" ".join(child.text for child in _under(block, index)))
    return tuple(texts)


def _jobs(path: Path, why: str) -> dict[str, tuple[_Entry, ...]]:
    """Every job of the workflow at *path*, by id."""
    entries = _scan(_read(path, why))
    top: tuple[_Entry, ...] = ()
    for index, entry in enumerate(entries):
        if entry.indent == 0 and _pair(entry) == ("jobs", ""):
            top = _under(entries, index)
            break
    assert top, f"{_rel(path)} has no top-level `jobs:` mapping."
    depth = min(entry.indent for entry in top)
    jobs: dict[str, tuple[_Entry, ...]] = {}
    for index, entry in enumerate(top):
        if entry.indent != depth:
            continue
        found = _pair(entry)
        if found is not None:
            jobs[found[0]] = _under(top, index)
    return jobs


def _gate_jobs() -> dict[str, tuple[_Entry, ...]]:
    return _jobs(_REPO_WORKFLOW, _WHY_WORKFLOW)


def _job(job_id: str) -> tuple[_Entry, ...]:
    """The job *job_id*, or a failure naming the ids that are there."""
    jobs = _gate_jobs()
    assert job_id in jobs, (
        f"{_WORKFLOW} has no job with id {job_id!r}; ids found: "
        f"{sorted(jobs) or 'none'}. The pull-request job reports the check "
        f"and the scheduled job re-checks the wiring on a timer."
    )
    return jobs[job_id]


def _gate_run(job_id: str) -> str:
    """The one ``run:`` of *job_id* that calls the gate."""
    calls = [text for text in _runs(_job(job_id)) if _GATE_CALL in text]
    assert len(calls) == 1, (
        f"{_WORKFLOW}: job {job_id!r} has {len(calls)} step(s) whose run: "
        f"mentions {_GATE_CALL!r}, and exactly one of them is the gate call. "
        f"`uv sync --extra dev` is the prior Install step, not the compared "
        f"token. Found: {calls}."
    )
    return calls[0]


def _hooks() -> dict[str, tuple[_Entry, ...]]:
    """Every pre-commit hook, by id."""
    entries = _scan(_read(_REPO_PRE_COMMIT, _WHY_PRE_COMMIT))
    hooks: dict[str, tuple[_Entry, ...]] = {}
    for index, entry in enumerate(entries):
        if not entry.text.startswith("- "):
            continue
        found = _pair(entry)
        if found is not None and found[0] == "id":
            hooks[found[1]] = _under(entries, index)
    return hooks


def _hook(hook_id: str) -> tuple[_Entry, ...]:
    """The hook *hook_id*, or a failure naming the ids that are there."""
    hooks = _hooks()
    assert hook_id in hooks, (
        f"{_PRE_COMMIT} has no hook with id {hook_id!r}; ids found: {sorted(hooks)}."
    )
    return hooks[hook_id]


def _hook_entry() -> str:
    """The gate hook's ``entry:``, which is one half of pair 2."""
    entry = _scalar(_hook(_HOOK_ID), "entry")
    assert entry is not None, (
        f"{_PRE_COMMIT}: hook {_HOOK_ID!r} has no entry:; expected "
        f"entry: {gate.GATE_RUN}."
    )
    return entry


def _token(key: str) -> str:
    """One of pair 2's two tokens, named by the key that carries it."""
    return _gate_run(gate.PR_JOB_ID) if key == "run" else _hook_entry()


def _frontmatter(path: Path) -> tuple[_Entry, ...]:
    """The lines between the opening and closing ``---`` fences."""
    lines = _read(path, _WHY_REPO_FILE).splitlines()
    assert lines[:1] == ["---"], (
        f"{_rel(path)} must open with a --- frontmatter fence; its first line "
        f"is {lines[:1]!r}."
    )
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
        None,
    )
    assert closing is not None, (
        f"{_rel(path)} opens a --- frontmatter fence that is never closed."
    )
    return _scan("\n".join(lines[1:closing]))


def _ci_config(path: Path) -> str | None:
    """``mol_project.ci.config`` of *path*'s frontmatter."""
    entries = _frontmatter(path)
    for index, entry in enumerate(entries):
        if _pair(entry) == ("ci", ""):
            return _scalar(_under(entries, index), "config")
    return None


class TestOfficialGateParity:
    # -- pair 2: the PR job's run: and the hook's entry: ----------------

    @pytest.mark.parametrize("key", _PAIR_TWO)
    def test_pair_two_token_is_the_one_literal(self, key: str) -> None:
        """Each copy against the constant, never against the other copy."""
        token = _token(key)

        assert token == gate.GATE_RUN, (
            f"the {key}: token is {token!r}, not {gate.GATE_RUN!r}. Local, "
            f"pull request and timer must run the same sentence, character "
            f"for character; gate.GATE_RUN is the authority and both files "
            f"are copies of it."
        )

    @pytest.mark.parametrize("wrapper", _WRAPPERS)
    @pytest.mark.parametrize("key", _PAIR_TWO)
    def test_pair_two_token_is_not_wrapped(self, key: str, wrapper: str) -> None:
        token = _token(key)

        assert wrapper not in token, (
            f"the {key}: token {token!r} wraps the call in {wrapper!r}, which "
            f"makes it a different string from the one the other file runs. "
            f"Installing is a prior step, not part of the compared token."
        )

    # -- the check name, and the job ids ---------------------------------

    def test_workflow_defines_exactly_the_two_jobs(self) -> None:
        assert sorted(_gate_jobs()) == sorted(_JOB_IDS)

    def test_no_job_takes_the_release_gate_id(self) -> None:
        """``release.yml`` owns ``gate``; two jobs under one id is a rename."""
        assert "gate" not in _gate_jobs()

    def test_pull_request_job_is_named_the_required_check(self) -> None:
        name = _scalar(_job(gate.PR_JOB_ID), "name")

        assert name == gate.CHECK_NAME == "official/gate", (
            f"{_WORKFLOW}: job {gate.PR_JOB_ID!r} has name: {name!r}. GitHub "
            f"matches a required check on the name it displays, not on the "
            f"job id, so this one line is what makes the check exist."
        )

    def test_schedule_job_is_not_named_the_required_check(self) -> None:
        name = _scalar(_job(gate.SCHEDULE_JOB_ID), "name")

        assert name != gate.CHECK_NAME, (
            f"{_WORKFLOW}: job {gate.SCHEDULE_JOB_ID!r} is also named "
            f"{gate.CHECK_NAME!r}, which would let a timer report the check a "
            f"pull request is supposed to report."
        )

    def test_schedule_job_runs_the_same_literal(self) -> None:
        assert _gate_run(gate.SCHEDULE_JOB_ID) == gate.GATE_RUN

    # -- literal run:, and nothing from the environment ------------------

    @pytest.mark.parametrize("job_id", _JOB_IDS)
    def test_no_run_expands_an_expression(self, job_id: str) -> None:
        expanded = [text for text in _runs(_job(job_id)) if _EXPRESSION in text]

        assert expanded == [], (
            f"{_WORKFLOW}: job {job_id!r} has {len(expanded)} run: holding "
            f"{_EXPRESSION!r}, first {expanded[:1]!r}. `if:` may hold an "
            f"expression; a run: may not, because what it expands to on a "
            f"runner is not what parity compared."
        )

    @pytest.mark.parametrize("job_id", _JOB_IDS)
    def test_job_selects_nothing_from_the_environment(self, job_id: str) -> None:
        lines = [
            entry.number
            for entry in _job(job_id)
            if (found := _pair(entry)) is not None and found[0] == "env"
        ]

        assert lines == [], (
            f"{_WORKFLOW}: job {job_id!r} has env: on line(s) {lines}. There "
            f"is one profile, so an env: here can only be selecting a second "
            f"one, and the gate would then decide two different things."
        )

    # -- which stage the hook runs in ------------------------------------

    def test_gate_hook_runs_only_before_a_push(self) -> None:
        stages = _items(_hook(_HOOK_ID), "stages")

        assert stages == (_PUSH_STAGE,), (
            f"{_PRE_COMMIT}: hook {_HOOK_ID!r} has stages: {list(stages)}, not "
            f"[{_PUSH_STAGE}]. The gate runs before a push; the commit stage "
            f"stays fast."
        )

    def test_commit_stage_still_holds_ci_lint(self) -> None:
        stages = _items(_hook(_COMMIT_HOOK_ID), "stages")

        assert _COMMIT_STAGE in stages, (
            f"{_PRE_COMMIT}: hook {_COMMIT_HOOK_ID!r} no longer lists the "
            f"{_COMMIT_STAGE!r} stage; it is what that stage holds."
        )

    def test_commit_stage_does_not_hold_the_gate(self) -> None:
        assert _COMMIT_STAGE not in _items(_hook(_HOOK_ID), "stages")

    # -- what this spec leaves where it found it -------------------------

    @pytest.mark.parametrize("token", _MATRIX_TOKENS)
    def test_ci_workflow_still_carries_the_product_matrix(self, token: str) -> None:
        text = _read(_CI_WORKFLOW, _WHY_REPO_FILE)

        assert token in text, (
            f"{_rel(_CI_WORKFLOW)} no longer mentions {token!r}. Lint and "
            f"tests stay on the OS/Python matrix; the gate checks wiring and "
            f"replaces none of it."
        )

    def test_ci_workflow_does_not_run_the_gate(self) -> None:
        text = _read(_CI_WORKFLOW, _WHY_REPO_FILE)

        assert _GATE_CALL not in text, (
            f"{_rel(_CI_WORKFLOW)} runs {_GATE_CALL!r}. The required check is "
            f"one job in one file; running it across a matrix reports the same "
            f"verdict six times under six names."
        )

    def test_release_workflow_keeps_its_gate_job(self) -> None:
        jobs = _jobs(_RELEASE_WORKFLOW, _WHY_REPO_FILE)

        assert "gate" in jobs, (
            f"{_rel(_RELEASE_WORKFLOW)} no longer has job id 'gate'; that job "
            f"is why the new one is called {gate.PR_JOB_ID!r}."
        )

    @pytest.mark.parametrize("path", _PROJECT_DOCS, ids=lambda p: p.name)
    def test_project_doc_still_points_ci_config_at_the_matrix(self, path: Path) -> None:
        configured = _ci_config(path)

        assert configured == _CI_CONFIG, (
            f"{_rel(path)} frontmatter has mol_project.ci.config "
            f"{configured!r}. It names the product matrix, and this spec adds "
            f"a required check beside it rather than moving it."
        )
