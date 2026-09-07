"""Init: one composed serve entry; skill only via init; --disable providers."""

from __future__ import annotations

import ast
import json
import pathlib
import sys
from pathlib import Path

import pytest

from molmcp import client_config
from molmcp import host as host_package
from molmcp.client_config import (
    render_init,
    render_mcp_json,
    resolve_plane_toggles,
)
from molmcp.planes import CORE_PLANE_ID as CORE


def test_default_core_plus_providers():
    t = resolve_plane_toggles(available=("molcrafts", "molvis", "molq"))
    assert t.enabled == ("molcrafts", "molvis", "molq")
    assert t.disabled == ()


def test_disable_then_enable():
    t = resolve_plane_toggles(
        available=("molcrafts", "molvis", "molq"),
        disable=["molq", "molvis"],
        enable=["molvis"],
    )
    assert t.enabled == ("molcrafts", "molvis")
    assert t.disabled == ("molq",)


def test_disable_core_raises():
    with pytest.raises(ValueError, match="cannot be disabled"):
        resolve_plane_toggles(available=("molcrafts", "molvis"), disable=["molcrafts"])


def test_disable_catalog_raises():
    with pytest.raises(ValueError, match="catalog is not a plane"):
        resolve_plane_toggles(available=("molcrafts", "molvis"), disable=["catalog"])


def test_composed_server_map_is_a_single_serve():
    t = resolve_plane_toggles(available=("molcrafts", "molvis", "molq"))
    servers = render_mcp_json(t)["mcpServers"]
    assert set(servers) == {CORE}
    args = servers[CORE]["args"]
    assert args[-1] == "serve" or "serve" in args
    assert "--disable" not in args


def test_disabled_provider_becomes_a_serve_flag():
    t = resolve_plane_toggles(
        available=("molcrafts", "molvis", "molq"),
        disable=["molq"],
    )
    args = render_mcp_json(t)["mcpServers"][CORE]["args"]
    assert args[args.index("--disable") + 1] == "molq"
    assert "molq" not in t.enabled


def test_render_init_includes_core():
    _toggle, text = render_init("grok", available=("molcrafts", "molvis"))
    payload = json.loads(text)
    assert set(payload["mcpServers"]) == {CORE}


def test_cli_init_writes_json_and_skill(tmp_path, monkeypatch, capsys):
    from molmcp import cli

    monkeypatch.setattr(client_config.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        "molmcp.client_config.default_plane_ids",
        lambda: ("molcrafts", "molvis", "molq"),
    )
    code = cli.main(["init", "grok", "--disable", "molq"])
    assert code == 0
    err = capsys.readouterr().err
    assert "wrote" in err
    servers = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))[
        "mcpServers"
    ]
    assert set(servers) == {CORE}
    skill = tmp_path / ".grok" / "skills" / "molcrafts" / "SKILL.md"
    assert skill.is_file()
    assert "SYMBOL_NOT_FOUND" in skill.read_text(encoding="utf-8")


def test_cli_init_cannot_disable_core(capsys, monkeypatch, tmp_path):
    from molmcp import cli

    monkeypatch.setattr(client_config.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        "molmcp.client_config.default_plane_ids",
        lambda: ("molcrafts", "molvis"),
    )
    code = cli.main(["init", "grok", "--disable", "molcrafts"])
    assert code == 2
    assert "cannot be disabled" in capsys.readouterr().err


class TestLaunchableFromAGuiClient:
    def test_command_is_the_resolved_absolute_path(self, monkeypatch, tmp_path):
        installed = tmp_path / "venv" / "bin" / "molmcp"
        installed.parent.mkdir(parents=True)
        installed.touch()
        monkeypatch.setattr(client_config.shutil, "which", lambda name: str(installed))

        config = client_config.render_mcp_json(
            client_config.PlaneToggle(("molcrafts",), (), ("molcrafts",))
        )

        assert config["mcpServers"]["molcrafts"]["command"] == str(installed)

    def test_fallback_uses_this_interpreter_not_a_bare_python(self, monkeypatch):
        monkeypatch.setattr(client_config.shutil, "which", lambda name: None)

        command = client_config._molmcp_command()

        assert command[0] == sys.executable
        assert command[1:3] == ["-m", "molmcp"]


class TestOneJsonForEveryHost:
    def test_the_body_is_identical_for_every_host(self):
        toggle = client_config.PlaneToggle(("molcrafts",), (), ("molcrafts",))

        bodies = {
            host: client_config.render_init(host, available=toggle.all_planes)[1]
            for host in ("grok", "claude", "cursor", "codex")
        }

        assert len(set(bodies.values())) == 1

    @pytest.mark.parametrize("host", ["grok", "claude", "cursor", "codex"])
    def test_every_host_gets_parseable_json(self, host):
        _, text = client_config.render_init(host, available=("molcrafts",))

        assert "mcpServers" in json.loads(text)

    def test_each_host_has_a_skill_directory(self):
        for host in ("grok", "claude", "cursor", "codex"):
            assert client_config.default_skill_dir(host).name == "molcrafts"


def test_skill_template_is_shipped():
    text = client_config.skill_template()
    assert "packages" in text
    assert "SYMBOL_NOT_FOUND" in text
    assert "disable-model-invocation: false" in text
    assert "user-invocable: false" in text
    assert "when-to-use:" in text


#: Production modules this file reads as text, so a deleted table stays deleted.
_SRC = Path(__file__).resolve().parents[1] / "src" / "molmcp"
CLIENT_CONFIG_SOURCE = _SRC / "client_config.py"
CLI_SOURCE = _SRC / "cli.py"

#: Names ``client_config`` must hand back from ``molmcp.host`` unchanged.
RE_EXPORTED_NAMES: tuple[str, ...] = (
    "Host",
    "SKILL_NAME",
    "HOSTS",
    "layout_for",
    "default_write_path",
    "default_skill_dir",
    "install_skill",
    "skill_template",
)

#: Every host ``molmcp init`` wires, in the order ``--help`` prints them.
INIT_HOSTS: tuple[str, ...] = ("grok", "claude", "cursor", "codex")

#: The write primitives ``cli._init`` composes, in the order it must call them.
INIT_PRIMITIVES: tuple[str, ...] = (
    "install_skill",
    "materialize_daily",
    "write_adapter",
    "materialize_dev_index",
    "activate_dev",
)

#: Primitives that take a checkout; each must get the resolved value.
SOURCE_CONSUMERS: tuple[str, ...] = (
    "materialize_daily",
    "materialize_dev_index",
    "activate_dev",
)


def _init_function() -> ast.FunctionDef:
    """The ``cli._init`` definition, parsed from source rather than imported."""
    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_init"
    )


def _calls_to(node: ast.AST, name: str) -> list[ast.Call]:
    """Every bare-name call to *name* anywhere under *node*."""
    return [
        found
        for found in ast.walk(node)
        if isinstance(found, ast.Call)
        and isinstance(found.func, ast.Name)
        and found.func.id == name
    ]


def _statement_indices(body: list[ast.stmt], name: str) -> list[int]:
    """Positions of the top-level statements of *body* that call *name*."""
    return [index for index, stmt in enumerate(body) if _calls_to(stmt, name)]


def _args_source_reads(node: ast.AST) -> list[ast.Attribute]:
    """Every ``args.source`` read under *node*."""
    return [
        found
        for found in ast.walk(node)
        if isinstance(found, ast.Attribute)
        and found.attr == "source"
        and isinstance(found.value, ast.Name)
        and found.value.id == "args"
    ]


def _resolved_binding(function: ast.FunctionDef) -> str:
    """Name bound to the single ``resolve_bundle_source(...)`` result."""
    for stmt in function.body:
        if isinstance(stmt, ast.Assign | ast.AnnAssign):
            value = stmt.value
            if (
                value is not None
                and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "resolve_bundle_source"
            ):
                target = (
                    stmt.targets[0] if isinstance(stmt, ast.Assign) else stmt.target
                )
                assert isinstance(target, ast.Name)
                return target.id
    raise AssertionError("_init binds no name to resolve_bundle_source(...)")


def _argument_names(call: ast.Call) -> set[str]:
    """Bare names passed to *call*, positionally or by keyword."""
    passed = [*call.args, *(keyword.value for keyword in call.keywords)]
    return {node.id for node in passed if isinstance(node, ast.Name)}


class TestHostTableLivesOnlyInTheHostPackage:
    """One host path table: ``molmcp.host``. ``client_config`` only reads it."""

    def test_the_private_host_dicts_are_gone_from_the_source(self) -> None:
        text = CLIENT_CONFIG_SOURCE.read_text(encoding="utf-8")

        assert "_HOST_PATHS" not in text
        assert "_HOST_SKILL_DIRS" not in text

    @pytest.mark.parametrize("name", RE_EXPORTED_NAMES)
    def test_the_re_export_is_the_same_object_not_a_wrapper(self, name: str) -> None:
        assert getattr(client_config, name) is getattr(host_package, name)

    def test_an_unknown_host_names_the_known_hosts_in_sorted_order(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            render_init("nope")

        assert str(excinfo.value) == (
            "unknown host 'nope'; known: claude, codex, cursor, grok"
        )


class TestInitParserKeepsOneHostList:
    """``--help`` still offers the same four hosts, derived from ``HOSTS``."""

    def test_help_offers_the_four_hosts_in_declaration_order(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from molmcp import cli

        with pytest.raises(SystemExit):
            cli.main(["init", "--help"])

        assert "{" + ",".join(INIT_HOSTS) + "}" in capsys.readouterr().out

    def test_the_cli_repeats_no_second_host_list(self) -> None:
        tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))

        host_literals = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.List | ast.Tuple | ast.Set)
            and set(INIT_HOSTS)
            <= {
                element.value
                for element in node.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
        ]

        assert host_literals == []


class TestInitComposesTheHostPrimitives:
    """``cli._init`` resolves the checkout once, then writes in a fixed order."""

    def test_the_bundle_source_is_resolved_exactly_once(self) -> None:
        function = _init_function()

        assert len(_calls_to(function, "resolve_bundle_source")) == 1

    def test_each_primitive_is_its_own_statement_in_order(self) -> None:
        body = _init_function().body

        positions = {name: _statement_indices(body, name) for name in INIT_PRIMITIVES}

        assert {name: len(found) for name, found in positions.items()} == dict.fromkeys(
            INIT_PRIMITIVES, 1
        )
        ordered = [positions[name][0] for name in INIT_PRIMITIVES]
        assert ordered == sorted(set(ordered))

    def test_the_resolver_runs_before_the_primitives_it_feeds(self) -> None:
        body = _init_function().body

        resolved_at = _statement_indices(body, "resolve_bundle_source")
        primitives_at = [
            index
            for name in INIT_PRIMITIVES
            for index in _statement_indices(body, name)
        ]

        assert len(resolved_at) == 1
        assert primitives_at != []
        assert resolved_at[0] < min(primitives_at)

    def test_args_source_is_read_only_by_the_resolver(self) -> None:
        function = _init_function()

        resolvers = _calls_to(function, "resolve_bundle_source")

        assert len(resolvers) == 1
        assert len(_args_source_reads(function)) == 1
        assert len(_args_source_reads(resolvers[0])) == 1

    @pytest.mark.parametrize("name", SOURCE_CONSUMERS)
    def test_a_source_consumer_gets_the_resolved_value(self, name: str) -> None:
        function = _init_function()
        resolved = _resolved_binding(function)

        calls = _calls_to(function, name)

        assert len(calls) == 1
        assert resolved in _argument_names(calls[0])

    def test_a_source_that_is_not_a_directory_fails_loudly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from molmcp import cli

        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            "molmcp.client_config.default_plane_ids",
            lambda: ("molcrafts", "molvis"),
        )
        not_a_checkout = tmp_path / "checkout.md"
        not_a_checkout.write_text("# not a checkout\n", encoding="utf-8")

        code = cli.main(["init", "grok", "--source", str(not_a_checkout)])

        assert code != 0
        assert str(not_a_checkout) in capsys.readouterr().err
