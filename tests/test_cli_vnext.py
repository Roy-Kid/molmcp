from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from molmcp import __version__, cli, gate
from molmcp.environment import EnvironmentReport


def _empty_report(locator=None) -> EnvironmentReport:
    return EnvironmentReport(
        locator=locator,
        is_self=locator is None,
        site_paths=(),
        sources=(),
        skipped=(),
        excluded=(),
    )


class _FakeCollection:
    def info(self):
        return {}


def _config(tmp_path):
    path = tmp_path / "molcrafts.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "sources": {"project": "."},
                "watch": False,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])
    assert exited.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_version_short_flag(capsys):
    with pytest.raises(SystemExit) as exited:
        cli.main(["-V"])
    assert exited.value.code == 0
    assert capsys.readouterr().out.strip() == f"molmcp {__version__}"


def test_no_arguments_defaults_to_planes(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "molcrafts" in out.lower()
    assert "catalog is not a plane" not in out.lower()


def test_serve_no_plane_uses_stack(monkeypatch, tmp_path):
    captured = {}

    class FakeServer:
        def run(self, **kwargs):
            captured.update(kwargs)

    def fake_stack(**kwargs):
        captured["disable"] = list(kwargs.get("disable") or [])
        return FakeServer()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "create_stack", fake_stack)
    assert cli.main(["serve", "--disable", "molq"]) == 0
    assert captured["disable"] == ["molq"]
    assert captured["transport"] == "stdio"


def test_serve_core(monkeypatch, tmp_path, capsys):
    captured = {}

    class FakeServer:
        def run(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "create_plane", lambda *a, **kwargs: FakeServer())
    monkeypatch.setattr(cli, "create_stack", lambda **kwargs: FakeServer())
    assert cli.main(["serve", "molcrafts"]) == 0
    assert captured == {
        "transport": "stdio",
        "show_banner": False,
        "log_level": "ERROR",
    }


def test_serve_catalog_is_user_error(monkeypatch, tmp_path, capsys):
    class FakeServer:
        def run(self, **kwargs):
            raise AssertionError("must fail before run")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "create_plane", lambda *a, **kwargs: FakeServer())
    code = cli.main(["serve", "catalog"])
    assert code == 2
    assert "catalog is not a plane" in capsys.readouterr().err


def test_search_emits_json(monkeypatch, tmp_path, capsys):
    class Hit:
        def to_dict(self):
            return {"ref": "@molpack/pack", "executable": True}

    class Collection:
        def search(self, *args, **kwargs):
            return [Hit()]

    monkeypatch.setattr(cli, "build_collection", lambda config: Collection())
    assert cli.main(["search", "pack", "--config", str(_config(tmp_path))]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["ref"] == "@molpack/pack"


def test_unknown_index_source_is_user_error(monkeypatch, tmp_path, capsys):
    class Collection:
        sources = ()

    monkeypatch.setattr(cli, "build_collection", lambda config: Collection())
    code = cli.main(["index", "missing", "--config", str(_config(tmp_path))])
    assert code == 2
    assert "unknown configured sources" in capsys.readouterr().err


def test_non_loopback_override_requires_auth(monkeypatch, tmp_path, capsys):
    class FakeServer:
        def run(self, **kwargs):
            raise AssertionError("must fail before run")

    monkeypatch.setattr(cli, "create_plane", lambda *a, **kwargs: FakeServer())
    monkeypatch.setattr(cli, "create_stack", lambda **kwargs: FakeServer())
    code = cli.main(
        [
            "serve",
            "molcrafts",
            "--config",
            str(_config(tmp_path)),
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
        ]
    )
    assert code == 2
    assert "requires server.auth_token_env" in capsys.readouterr().err


def _patch_collection(monkeypatch):
    monkeypatch.setattr(cli, "build_collection", lambda config: _FakeCollection())


def test_route_cli(capsys):
    assert cli.main(["route", "draw a molecule"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(m["plane"] == "molvis" for m in payload["planes"])


# -- `molmcp gate`: dispatch, and nothing else --------------------------
#
# The verdict has one owner, `molmcp.gate.run_gate`. What is tested here is
# the seam between the two: which root the CLI hands over, which exit code it
# turns the report into, and that it decides nothing on its own. `run_gate`
# is monkeypatched by the name `cli` resolves, the same handling
# `create_stack` gets above — a CLI test that read the real repository would
# be testing gate.py a second time, from further away.

_CLI_SOURCE = Path(cli.__file__)

#: Strings belonging to the verdict. Any of them spelled inside `_gate` means
#: the CLI has started re-deriving what gate.py already decided, and the two
#: copies can then disagree about the one required check.
_VERDICT_TOKENS = (
    "official/gate",
    "official-gate",
    "uv run molmcp gate",
    ".pre-commit-config.yaml",
    ".github/workflows",
    "${{",
    "stages",
)

#: Names that would hand the CLI a second copy of a pinned literal.
_VERDICT_IMPORTS = ("CHECK_NAME", "GATE_RUN", "PR_JOB_ID", "SCHEDULE_JOB_ID")

#: Flags the gate subparser must not grow. There is one profile: an
#: evaluation needs two subagents and a GitHub runner has none, so a `--full`
#: could never run where it would be wired, and `--skip` is a required check
#: with an off switch.
_REJECTED_FLAGS = ("--full", "--skip")


def _patch_gate(monkeypatch, *, ok, failed=()):
    """Replace the `run_gate` the CLI resolves; return what it was called with."""
    recorded: dict[str, object] = {}
    report = gate.GateReport(ok=ok, failed=failed)

    def run_gate(**kwargs):
        recorded.update(kwargs)
        return report

    monkeypatch.setattr(cli, "run_gate", run_gate)
    return recorded


def _gate_handler():
    """The `_gate` handler read as source, or a readable failure."""
    tree = ast.parse(_CLI_SOURCE.read_text(encoding="utf-8"))
    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_gate"
    ]
    assert handlers, (
        "src/molmcp/cli.py defines no `_gate` handler. `molmcp gate` is a "
        "dispatch: the handler calls run_gate and prints what it returns; the "
        "verdict stays in molmcp.gate."
    )
    return handlers[0]


def _gate_strings():
    """Every string literal in `_gate`, its own docstring excepted."""
    body = _gate_handler().body
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        body = body[1:]
    return [
        node.value
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _gate_imports():
    """Every name `cli.py` imports from the gate module."""
    tree = ast.parse(_CLI_SOURCE.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("gate")
        for alias in node.names
    }


def test_gate_calls_run_gate_with_the_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    recorded = _patch_gate(monkeypatch, ok=True)

    cli.main(["gate"])

    assert recorded == {"root": Path.cwd()}


def test_gate_returns_zero_when_the_report_is_ok(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _patch_gate(monkeypatch, ok=True)

    assert cli.main(["gate"]) == 0


def test_gate_returns_one_when_the_report_is_not_ok(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _patch_gate(monkeypatch, ok=False, failed=("a job runs something else",))

    assert cli.main(["gate"]) == 1


def test_gate_prints_every_reported_failure(monkeypatch, tmp_path, capsys):
    message = "a hook entry: was wrapped and no longer equals the job's run:"
    monkeypatch.chdir(tmp_path)
    _patch_gate(monkeypatch, ok=False, failed=(message,))

    cli.main(["gate"])

    captured = capsys.readouterr()
    assert message in captured.out + captured.err


@pytest.mark.parametrize("flag", _REJECTED_FLAGS)
def test_gate_subparser_takes_no_flags(monkeypatch, tmp_path, capsys, flag):
    monkeypatch.chdir(tmp_path)
    _patch_gate(monkeypatch, ok=True)

    with pytest.raises(SystemExit) as exited:
        cli.main(["gate", flag])

    assert exited.value.code != 0
    assert "unrecognized arguments" in capsys.readouterr().err


def test_cli_resolves_run_gate_as_its_own_attribute():
    assert cli.run_gate is gate.run_gate, (
        "cli.py must import run_gate into its own namespace "
        "(`from .gate import run_gate`), the way it imports create_stack: that "
        "is the name the dispatch resolves and the name a test replaces."
    )


def test_cli_imports_no_pinned_literal_from_the_gate_module():
    held = sorted(_gate_imports().intersection(_VERDICT_IMPORTS))

    assert held == [], (
        f"cli.py imports {held} from the gate module. gate.py is the authority "
        f"for those literals and the YAML files are its copies; a third copy "
        f"in the CLI is one more thing to keep in step."
    )


def test_gate_handler_calls_run_gate():
    called = {
        node.func.id
        for node in ast.walk(_gate_handler())
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "run_gate" in called, (
        f"`_gate` calls {sorted(called)} and never run_gate. The subcommand "
        f"exists to ask gate.py for a verdict."
    )


def test_gate_handler_derives_no_verdict_of_its_own():
    derived = [
        node
        for node in ast.walk(_gate_handler())
        if isinstance(node, (ast.Compare, ast.BoolOp))
    ]

    assert derived == [], (
        f"`_gate` holds {len(derived)} comparison(s) of its own, first on line "
        f"{derived[0].lineno if derived else 0}. `ok` is decided once, by "
        f"run_gate; a CLI that re-derives it from `failed` is a second verdict "
        f"that can disagree with the first. Read `report.ok`."
    )


def test_gate_handler_spells_no_verdict_string():
    spelled = sorted(
        {
            token
            for value in _gate_strings()
            for token in _VERDICT_TOKENS
            if token in value
        }
    )

    assert spelled == [], (
        f"`_gate` spells {spelled}. Those are the tokens the report is written "
        f"in, and gate.py already names the offending file and token in every "
        f"message; the CLI prints what it is handed."
    )
