"""Init: one composed serve entry; skill only via init; --disable providers."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

from molmcp import client_config
from molmcp import host as host_package
from molmcp import skill as skill_package
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
)

#: Names ``client_config`` re-exported while the host package was being split
#: out, and no longer does. They have one importable home, ``molmcp.host``:
#: reaching them through this module must fail rather than quietly work.
WITHDRAWN_NAMES: tuple[str, ...] = (
    "install_skill",
    "skill_template",
    "default_skill_dir",
)

#: Every host ``molmcp init`` wires, in the order ``--help`` prints them.
INIT_HOSTS: tuple[str, ...] = ("grok", "claude", "cursor", "codex")

#: The write primitives ``cli._init`` composes, in the order it must call them.
#:
#: ``install_harness_components`` is the activated-commit route — the pointer
#: one ``molmcp harness sync`` promoted, read down to the files its catalog
#: declares — and it is last for a reason that is not cosmetic. The placement
#: seam protects the managed usage skill by *skipping* a destination inside
#: that directory, which protects a file only once it is there, so the step
#: has to run after ``install_skill`` has written the constitution. Everything
#: between is the ``--source`` checkout route, which this one joins rather
#: than replaces.
INIT_PRIMITIVES: tuple[str, ...] = (
    "install_skill",
    "write_adapter",
    "install_harness_components",
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

    @pytest.mark.parametrize("name", WITHDRAWN_NAMES)
    def test_a_withdrawn_name_is_neither_attribute_nor_export(self, name: str) -> None:
        assert not hasattr(client_config, name)
        assert name not in client_config.__all__

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

    def test_each_primitive_is_its_own_statement_in_order(self) -> None:
        body = _init_function().body

        positions = {name: _statement_indices(body, name) for name in INIT_PRIMITIVES}

        assert {name: len(found) for name, found in positions.items()} == dict.fromkeys(
            INIT_PRIMITIVES, 1
        )
        ordered = [positions[name][0] for name in INIT_PRIMITIVES]
        assert ordered == sorted(set(ordered))

    def test_catalog_components_are_placed_after_the_constitution_exists(
        self,
    ) -> None:
        """The activated-commit route runs once ``install_skill`` has written.

        Stated on its own as well as through the tuple above, because it is
        the one ordering constraint with a reason rather than a convention:
        ``place_components`` keeps a catalog off the managed usage skill by
        skipping any destination inside that directory, and skipping protects
        a file that is already there. Placed before ``install_skill``, the
        refusal would still fire and the constitution would then be written
        over whatever the catalog had put in its place.
        """
        body = _init_function().body

        assert (
            _statement_indices(body, "install_skill")[0]
            < _statement_indices(body, "install_harness_components")[0]
        )

    def test_init_does_not_take_a_source_flag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from molmcp import cli

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with pytest.raises(SystemExit) as ei:
            cli.main(["init", "grok", "--source", str(tmp_path)])
        assert ei.value.code == 2
        assert "unrecognized arguments" in capsys.readouterr().err


#: The shipped usage constitution, read straight from the package it lives in.
#: The host package exposes no accessor for it — ``skill_template`` is in
#: :data:`WITHDRAWN_NAMES` above — and ``install_skill`` copies this very file.
SKILL_FILE = Path(skill_package.__file__).parent / "SKILL.md"

#: The only install line that buys back a missing core. Frozen: nothing else
#: restores ``packages`` / ``open`` / ``route``.
CORE_INSTALL = "pip install molcrafts-molmcp"

#: Plane -> the distribution its namespace needs, frozen by the provider
#: cutover. ``molexp`` publishes under its own name; the other two are
#: prefixed. A reader who follows one of these must land on a real project.
SCIENCE_PACKAGES: tuple[tuple[str, str], ...] = (
    ("molvis", "molcrafts-molvis"),
    ("molq", "molcrafts-molq"),
    ("molexp", "molexp"),
)

#: A call the skill must never teach a model to make. ``require_upstream`` is
#: provider-internal, is reachable through no MCP tool, and recovers nothing.
FORBIDDEN_SKILL_CALL = "require_upstream"

#: ``<something>-mcp`` distribution names. None are published, so naming one
#: turns the recovery into a ``pip install`` that can only fail.
MCP_SUFFIXED_PACKAGE = re.compile(r"[\w-]+-mcp\b")

#: Any pip line at all, used to prove where install advice is allowed to live.
PIP_INSTALL = re.compile(r"pip install ")


def _skill_text() -> str:
    """The packaged ``SKILL.md``, as the agent that loads the skill reads it."""
    return SKILL_FILE.read_text(encoding="utf-8")


def _sections(text: str, marker: str) -> dict[str, str]:
    """Body of every *marker*-level markdown heading, keyed by its title.

    A deeper heading stays inside its parent's body, so splitting on ``##``
    hands back whole sections and splitting one of those on ``###`` hands
    back that section's numbered paths.
    """
    prefix = f"{marker} "
    found: dict[str, str] = {}
    title = ""
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith(prefix):
            if title:
                found[title] = "\n".join(body)
            title, body = line[len(prefix) :].strip(), []
        elif title:
            body.append(line)
    if title:
        found[title] = "\n".join(body)
    return found


def _recovery_paths() -> tuple[str, ...]:
    """The numbered paths of the one section that recovers a missing tool."""
    text = _skill_text()
    owning = [body for body in _sections(text, "##").values() if CORE_INSTALL in body]
    assert len(owning) == 1, "the core install line must have exactly one home"
    return tuple(_sections(owning[0], "###").values())


class TestSkillOffersTwoRecoveriesAndNoThird:
    """A missing tool has two causes, and the skill separates their fixes.

    The core being absent and a namespaced plane being absent look the same
    to a model and need opposite answers, so the constitution splits them.
    Both fixes end in a ``pip install``; each name below is pinned because a
    wrong one sends the user to a project that does not exist.
    """

    def test_the_recovery_section_splits_into_exactly_two_paths(self) -> None:
        assert len(_recovery_paths()) == 2

    def test_the_first_path_installs_the_core(self) -> None:
        first, _second = _recovery_paths()

        assert CORE_INSTALL in first

    def test_the_second_path_never_installs_the_core_again(self) -> None:
        _first, second = _recovery_paths()

        assert CORE_INSTALL not in second

    def test_the_second_path_reopens_a_disabled_plane_before_installing(
        self,
    ) -> None:
        _first, second = _recovery_paths()

        installs = [match.start() for match in PIP_INSTALL.finditer(second)]

        assert "--disable" in second
        assert installs != []
        assert second.index("--disable") < min(installs)

    @pytest.mark.parametrize(("plane", "package"), SCIENCE_PACKAGES)
    def test_a_plane_names_its_frozen_science_package(
        self, plane: str, package: str
    ) -> None:
        _first, second = _recovery_paths()
        exact = re.compile(rf"install\s+{re.escape(package)}(?![\w-])")

        rows = [
            line for line in second.splitlines() if plane in line and exact.search(line)
        ]

        assert len(rows) == 1

    def test_the_skill_never_tells_a_model_to_call_require_upstream(self) -> None:
        assert FORBIDDEN_SKILL_CALL not in _skill_text()

    def test_no_recovery_names_an_unpublished_mcp_suffixed_package(self) -> None:
        assert MCP_SUFFIXED_PACKAGE.findall(_skill_text()) == []

    def test_every_install_line_lives_inside_a_recovery_path(self) -> None:
        whole = len(PIP_INSTALL.findall(_skill_text()))
        inside = sum(len(PIP_INSTALL.findall(path)) for path in _recovery_paths())

        assert whole > 0
        assert inside == whole


class TestTheInstalledSkillIsThePinnedFile:
    """``molmcp init`` hands the agent the file the pins above are read from."""

    def test_init_copies_the_constitution_byte_for_byte(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        written = host_package.install_skill("grok")

        text = written.read_text(encoding="utf-8")
        assert "when-to-use:" in text
        assert "metadata:" not in text
        assert "SYMBOL_NOT_FOUND" in text
