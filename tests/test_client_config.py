"""Init: one composed serve entry; skill only via init; --disable providers."""

from __future__ import annotations

import json
import sys

import pytest

from molmcp import client_config
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
    plan = tmp_path / ".grok" / "skills" / "molexp-plan" / "SKILL.md"
    assert plan.is_file()
    plan_text = plan.read_text(encoding="utf-8")
    assert "One step per turn" in plan_text
    assert "No writes before confirm" in plan_text
    assert "SYMBOL_NOT_FOUND" in plan_text
    assert "molexp-plan" in err


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
            assert (
                client_config.default_skill_dir(host, "molexp-plan").name
                == "molexp-plan"
            )


def test_skill_template_is_shipped():
    text = client_config.skill_template()
    assert "packages" in text
    assert "SYMBOL_NOT_FOUND" in text
    assert "disable-model-invocation: false" in text
    assert "user-invocable: false" in text
    assert "when-to-use:" in text
    assert "molexp-plan" in text


def test_molexp_plan_template_is_shipped():
    text = client_config.skill_template("molexp-plan")
    assert "name: molexp-plan" in text
    assert "user-invocable: true" in text
    assert "One step per turn" in text
    assert "No writes before confirm" in text
    assert "SYMBOL_NOT_FOUND" in text
    assert "/molexp-plan" in text


def test_unknown_skill_raises():
    with pytest.raises(ValueError, match="unknown skill"):
        client_config.skill_template("not-a-skill")
