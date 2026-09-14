"""HostLayout: the single host path table and its thin path readers.

Mirrors ``src/molmcp/host/layout.py`` for spec
``autonomous-harness-evolution-07-host-adapter`` (ac-001, ac-002, ac-003).
Every tuple below is hard-coded from the spec's HostLayout field table, which
is authoritative; ``mcp_json`` and ``skill_dir`` additionally repeat today's
``client_config._HOST_PATHS`` / ``_HOST_SKILL_DIRS`` destinations, because
moving the table must not move where ``molmcp init`` writes.

Home is redirected by patching ``pathlib.Path.home``; no environment variable
selects a destination here or in production.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from molmcp.host.layout import (
    HOSTS,
    SKILL_NAME,
    Host,
    HostLayout,
    default_skill_dir,
    default_write_path,
    layout_for,
)

#: Insertion order of ``HOSTS``; ``cli.py`` derives its ``--help`` choices
#: from it, so the order is part of the contract.
HOST_ORDER: tuple[Host, ...] = ("grok", "claude", "cursor", "codex")

#: The spec's HostLayout field table, verbatim. Every value is a path tuple
#: relative to ``Path.home()``.
LAYOUTS: dict[Host, dict[str, tuple[str, ...]]] = {
    "grok": {
        "mcp_json": (".mcp.json",),
        "skill_dir": (".grok", "skills", "molcrafts"),
        "adapter": (".grok", "molmcp-adapter.md"),
        "agents": (".grok", "agents"),
        "rules": (".grok", "rules"),
    },
    "claude": {
        "mcp_json": (".claude.json",),
        "skill_dir": (".claude", "skills", "molcrafts"),
        "adapter": (".claude", "molmcp-adapter.md"),
        "agents": (".claude", "agents"),
        "rules": (".claude", "rules"),
    },
    "cursor": {
        "mcp_json": (".cursor", "mcp.json"),
        "skill_dir": (".cursor", "skills", "molcrafts"),
        "adapter": (".cursor", "molmcp-adapter.md"),
        "agents": (".cursor", "agents"),
        "rules": (".cursor", "rules"),
    },
    "codex": {
        "mcp_json": (".codex", "mcp.json"),
        "skill_dir": (".codex", "skills", "molcrafts"),
        "adapter": (".codex", "molmcp-adapter.md"),
        "agents": (".codex", "agents"),
        "rules": (".codex", "rules"),
    },
}

#: Fields carried by every record, per the spec table.
FIELD_NAMES = frozenset(
    {
        "mcp_json",
        "skill_dir",
        "adapter",
        "agents",
        "rules",
    }
)

#: Catalog-placed destinations (usage skill and MCP JSON stay elsewhere).
BUNDLE_FIELDS = ("adapter", "agents", "rules")

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "molmcp"
HOST_PKG = SRC / "host"

#: ac-002: ``host/`` is Layer 2 and never imports an outer layer.
FORBIDDEN_ROOTS: tuple[str, ...] = (
    "molmcp.client_config",
    "molmcp.cli",
    "molmcp.server",
    "molmcp.providers",
    "molmcp.discovery",
    "molmcp.components",
    "molmcp.harness",
)


def _imported_names(path: pathlib.Path) -> tuple[str, ...]:
    """Absolute dotted targets imported by *path*, relative imports resolved."""
    package = ".".join(("molmcp", *path.relative_to(SRC).parent.parts))
    parts = package.split(".")
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = parts[: len(parts) - (node.level - 1)]
                tail = node.module.split(".") if node.module else []
                prefix = ".".join([*base, *tail])
            else:
                prefix = node.module or ""
            found.append(prefix)
            found.extend(f"{prefix}.{alias.name}" for alias in node.names)
    return tuple(found)


def _offends(dotted: str) -> bool:
    return any(
        dotted == root or dotted.startswith(f"{root}.") for root in FORBIDDEN_ROOTS
    )


class TestHostLayout:
    """``layout_for`` / ``default_write_path`` / ``default_skill_dir``."""

    # --- Basics -------------------------------------------------------

    @pytest.mark.parametrize("host", HOST_ORDER)
    def test_layout_for_returns_todays_mcp_json(self, host: Host) -> None:
        assert layout_for(host).mcp_json == LAYOUTS[host]["mcp_json"]

    @pytest.mark.parametrize("host", HOST_ORDER)
    def test_layout_for_returns_todays_skill_dir(self, host: Host) -> None:
        assert layout_for(host).skill_dir == LAYOUTS[host]["skill_dir"]

    @pytest.mark.parametrize("host", HOST_ORDER)
    def test_layout_for_returns_the_bundle_destinations(self, host: Host) -> None:
        layout = layout_for(host)

        actual = {name: getattr(layout, name) for name in BUNDLE_FIELDS}

        assert actual == {name: LAYOUTS[host][name] for name in BUNDLE_FIELDS}

    def test_the_record_declares_exactly_the_spec_table_fields(self) -> None:
        names = {field.name for field in dataclasses.fields(HostLayout)}

        assert names == FIELD_NAMES

    @pytest.mark.parametrize("host", HOST_ORDER)
    def test_skill_dir_ends_with_the_managed_skill_name(self, host: Host) -> None:
        assert SKILL_NAME == "molcrafts"
        assert layout_for(host).skill_dir[-1] == SKILL_NAME

    def test_hosts_holds_exactly_the_four_known_hosts(self) -> None:
        assert set(HOSTS) == {"grok", "claude", "cursor", "codex"}

    def test_hosts_iterates_in_cli_choice_order(self) -> None:
        assert tuple(HOSTS) == HOST_ORDER

    @pytest.mark.parametrize("host", HOST_ORDER)
    def test_default_write_path_joins_home_with_mcp_json(
        self, host: Host, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)

        assert default_write_path(host) == tmp_path.joinpath(*LAYOUTS[host]["mcp_json"])

    @pytest.mark.parametrize("host", HOST_ORDER)
    def test_default_skill_dir_joins_home_with_skill_dir(
        self, host: Host, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)

        assert default_skill_dir(host) == tmp_path.joinpath(*LAYOUTS[host]["skill_dir"])

    # --- Immutability -------------------------------------------------

    def test_the_record_is_frozen(self) -> None:
        layout = layout_for("grok")

        with pytest.raises(dataclasses.FrozenInstanceError):
            layout.mcp_json = (".other.json",)

    def test_the_record_uses_slots(self) -> None:
        layout = layout_for("grok")

        assert "__slots__" in vars(HostLayout)
        assert not hasattr(layout, "__dict__")

    @pytest.mark.parametrize("host", HOST_ORDER)
    def test_every_field_value_is_a_tuple_of_str(self, host: Host) -> None:
        layout = layout_for(host)

        for field in dataclasses.fields(HostLayout):
            value = getattr(layout, field.name)
            assert isinstance(value, tuple), field.name
            assert all(isinstance(part, str) for part in value), field.name

    # --- Edge ---------------------------------------------------------

    def test_layout_for_rejects_an_unknown_host(self) -> None:
        with pytest.raises(ValueError, match="emacs"):
            layout_for("emacs")

    def test_default_write_path_rejects_an_unknown_host(self) -> None:
        with pytest.raises(ValueError, match="emacs"):
            default_write_path("emacs")

    def test_default_skill_dir_rejects_an_unknown_host(self) -> None:
        with pytest.raises(ValueError, match="emacs"):
            default_skill_dir("emacs")

    # --- Layering (ac-002) --------------------------------------------

    def test_no_host_module_imports_an_outer_layer(self) -> None:
        assert HOST_PKG.is_dir(), f"{HOST_PKG} does not exist"
        modules = sorted(HOST_PKG.rglob("*.py"))
        assert modules, f"no modules under {HOST_PKG}"

        offenders = {
            module.relative_to(SRC).as_posix(): [
                dotted for dotted in _imported_names(module) if _offends(dotted)
            ]
            for module in modules
        }

        assert {name: hits for name, hits in offenders.items() if hits} == {}


_FENCE = """\
---
name: daily
description: >
  A daily skill
when-to-use: every morning
user-invocable: false
disable-model-invocation: true
argument-hint: "<topic>"
tools: Read, Grep
model: sonnet
metadata:
  author: molmcp
---
# body
"""


class TestRemapFrontmatter:
    def test_grok_keeps_when_to_use_and_drops_tools(self) -> None:
        from molmcp.host.layout import remap_frontmatter

        out = remap_frontmatter(_FENCE, "grok")
        assert "when-to-use: every morning" in out
        assert 'argument-hint: "<topic>"' in out
        assert "tools:" not in out
        assert "metadata:" not in out
        assert "# body" in out

    def test_claude_drops_when_to_use(self) -> None:
        from molmcp.host.layout import remap_frontmatter

        out = remap_frontmatter(_FENCE, "claude")
        assert "when-to-use:" not in out
        assert "user-invocable: false" in out
        assert "argument-hint:" in out

    def test_cursor_keeps_only_three_keys(self) -> None:
        from molmcp.host.layout import remap_frontmatter

        out = remap_frontmatter(_FENCE, "cursor")
        assert "name: daily" in out
        assert "disable-model-invocation: true" in out
        assert "user-invocable:" not in out
        assert "argument-hint:" not in out

    def test_codex_keeps_name_and_description(self) -> None:
        from molmcp.host.layout import remap_frontmatter

        out = remap_frontmatter(_FENCE, "codex")
        assert "name: daily" in out
        assert "description: >" in out
        assert "disable-model-invocation:" not in out

    def test_no_fence_is_unchanged(self) -> None:
        from molmcp.host.layout import remap_frontmatter

        raw = "# just a rule\n"
        assert remap_frontmatter(raw, "grok") == raw

    def test_folded_description_continuations_stay(self) -> None:
        from molmcp.host.layout import remap_frontmatter

        out = remap_frontmatter(_FENCE, "grok")
        assert "  A daily skill" in out
