"""Public Provider SDK — the surface a plane author imports."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import importlib.util
import sys
from importlib.machinery import ModuleSpec

import pytest
from fastmcp import FastMCP

import molmcp.provider_sdk as sdk
from molmcp.provider import Provider as protocol
from molmcp.provider_sdk import (
    APPEND_WRITE,
    IDEMPOTENT_WRITE,
    LOCAL_MUTATION,
    MUTATION,
    READ_ONLY,
    READ_REMOTE,
    Provider,
    ProviderBase,
    ToolSpec,
    tool,
)
from molmcp.providers import annotations as legacy_annotations
from molmcp.providers.base import ProviderBase as LegacyProviderBase
from molmcp.providers.base import ToolSpec as LegacyToolSpec
from molmcp.providers.base import tool as legacy_tool

_SDK_EXPORTS = [
    "APPEND_WRITE",
    "IDEMPOTENT_WRITE",
    "LOCAL_MUTATION",
    "MUTATION",
    "Provider",
    "ProviderBase",
    "READ_ONLY",
    "READ_REMOTE",
    "ToolSpec",
    "tool",
]


class TestToolSpec:
    def test_is_frozen(self):
        spec = ToolSpec(name="peek", annotations=READ_ONLY, attribute="peek")
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.name = "open"  # type: ignore[misc]

    def test_uses_slots(self):
        spec = ToolSpec(name="peek", annotations=READ_ONLY, attribute="peek")
        assert not hasattr(spec, "__dict__")
        assert hasattr(ToolSpec, "__slots__")


class TestToolDecorator:
    def test_default_wire_name_is_the_method_name(self):
        class Demo(ProviderBase):
            name = "demo"

            @tool(READ_ONLY)
            def peek(self) -> dict[str, bool]:
                """Look."""
                return {"ok": True}

        specs = list(Demo().tool_specs())
        assert [spec.name for spec in specs] == ["peek"]
        assert specs[0].attribute == "peek"

    def test_explicit_wire_name_is_bare(self):
        class Demo(ProviderBase):
            name = "demo"

            @tool(READ_ONLY, name="open")
            def open_thing(self) -> dict[str, bool]:
                """Open."""
                return {"ok": True}

        specs = list(Demo().tool_specs())
        assert [spec.name for spec in specs] == ["open"]
        assert specs[0].attribute == "open_thing"


class TestProviderBase:
    def test_tool_specs_are_base_first_in_declaration_order(self):
        class Base(ProviderBase):
            name = "demo"

            @tool(READ_ONLY)
            def alpha(self) -> dict[str, str]:
                """A."""
                return {"id": "alpha"}

            @tool(READ_ONLY)
            def beta(self) -> dict[str, str]:
                """B."""
                return {"id": "beta"}

        class Child(Base):
            @tool(READ_ONLY)
            def gamma(self) -> dict[str, str]:
                """C."""
                return {"id": "gamma"}

        specs = list(Child().tool_specs())
        assert [spec.attribute for spec in specs] == ["alpha", "beta", "gamma"]
        assert [spec.name for spec in specs] == ["alpha", "beta", "gamma"]

    def test_overriding_an_attribute_replaces_the_spec(self):
        class Base(ProviderBase):
            name = "demo"

            @tool(READ_ONLY)
            def peek(self) -> dict[str, str]:
                """Original."""
                return {"id": "base"}

        class Child(Base):
            @tool(MUTATION)
            def peek(self) -> dict[str, str]:
                """Replaced."""
                return {"id": "child"}

        specs = list(Child().tool_specs())
        assert len(specs) == 1
        assert specs[0].attribute == "peek"
        assert specs[0].annotations is MUTATION

    def test_register_attaches_declared_tools(self):
        class Demo(ProviderBase):
            name = "demo"

            @tool(READ_ONLY)
            def ping(self) -> dict[str, bool]:
                """Ping the plane."""
                return {"ok": True}

            @tool(READ_ONLY, name="open")
            def open_thing(self) -> dict[str, bool]:
                """Open."""
                return {"ok": True}

        mcp = FastMCP("demo")
        Demo().register(mcp)
        names = {item.name for item in asyncio.run(mcp.list_tools())}
        assert names == {"ping", "open"}

    def test_duplicate_wire_names_are_rejected(self):
        class Clashing(ProviderBase):
            name = "clash"

            @tool(READ_ONLY, name="thing")
            def first(self) -> dict[str, int]:
                """One."""
                return {"n": 1}

            @tool(READ_ONLY, name="thing")
            def second(self) -> dict[str, int]:
                """Two."""
                return {"n": 2}

        with pytest.raises(ValueError) as excinfo:
            Clashing().register(FastMCP("clash"))

        message = str(excinfo.value)
        assert "thing" in message
        assert "first" in message
        assert "second" in message

    def test_probe_is_true_without_upstream(self):
        class Demo(ProviderBase):
            name = "demo"

        assert Demo().probe() is True

    def test_probe_is_false_when_upstream_is_missing(self):
        class Absent(ProviderBase):
            name = "absent"
            upstream = "molcrafts-nope"
            import_name = "molmcp_sdk_nope_xyz"

        assert Absent().probe() is False

    def test_probe_is_true_when_upstream_is_installed(self):
        class Present(ProviderBase):
            name = "present"
            upstream = "pytest"
            import_name = "pytest"

        assert Present().probe() is True

    def test_missing_upstream_names_the_install_command(self):
        class Absent(ProviderBase):
            name = "absent"
            upstream = "molcrafts-nope"
            import_name = "molmcp_sdk_nope_xyz"

        with pytest.raises(RuntimeError) as excinfo:
            Absent().require_upstream()

        message = str(excinfo.value)
        assert "molcrafts-nope" in message
        assert "pip install molcrafts-nope" in message


class TestAnnotationVocabulary:
    @pytest.mark.parametrize(
        ("constant", "read_only", "destructive", "idempotent", "open_world"),
        [
            (READ_ONLY, True, False, True, False),
            (READ_REMOTE, True, False, False, True),
            (MUTATION, False, True, False, True),
            (LOCAL_MUTATION, False, True, True, False),
            (APPEND_WRITE, False, False, False, False),
            (IDEMPOTENT_WRITE, False, False, True, False),
        ],
        ids=[
            "READ_ONLY",
            "READ_REMOTE",
            "MUTATION",
            "LOCAL_MUTATION",
            "APPEND_WRITE",
            "IDEMPOTENT_WRITE",
        ],
    )
    def test_constant_states_all_four_hints(
        self,
        constant,
        read_only: bool,
        destructive: bool,
        idempotent: bool,
        open_world: bool,
    ):
        assert constant.read_only_hint is read_only
        assert constant.destructive_hint is destructive
        assert constant.idempotent_hint is idempotent
        assert constant.open_world_hint is open_world


class TestLegacyProviderImports:
    def test_tool_spec_is_the_same_object(self):
        assert ToolSpec is LegacyToolSpec

    def test_provider_base_is_the_same_object(self):
        assert ProviderBase is LegacyProviderBase

    def test_tool_is_the_same_object(self):
        assert tool is legacy_tool

    @pytest.mark.parametrize(
        "name",
        [
            "APPEND_WRITE",
            "IDEMPOTENT_WRITE",
            "LOCAL_MUTATION",
            "MUTATION",
            "READ_ONLY",
            "READ_REMOTE",
        ],
    )
    def test_annotation_constant_is_the_same_object(self, name: str):
        assert getattr(sdk, name) is getattr(legacy_annotations, name)


class TestSdkExports:
    def test_all_is_exactly_the_sorted_public_names(self):
        assert _SDK_EXPORTS == sorted(_SDK_EXPORTS)
        assert sdk.__all__ == _SDK_EXPORTS

    def test_provider_is_the_runtime_protocol(self):
        assert Provider is protocol


class TestImportSafety:
    def test_importing_the_sdk_does_not_load_science_packages(self):
        science = ("molvis", "molq", "molexp")
        held_science = {name: sys.modules.pop(name, None) for name in science}
        held_sdk = sys.modules.pop("molmcp.provider_sdk", None)
        try:
            importlib.import_module("molmcp.provider_sdk")
            for name in science:
                assert name not in sys.modules
        finally:
            if held_sdk is not None:
                sys.modules["molmcp.provider_sdk"] = held_sdk
            for name, previous in held_science.items():
                if previous is not None:
                    sys.modules[name] = previous
                else:
                    sys.modules.pop(name, None)

    def test_probe_uses_find_spec_and_does_not_import_the_module(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        seen: list[str] = []

        def fake_find_spec(name: str, _package: str | None = None) -> ModuleSpec | None:
            seen.append(name)
            return None

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

        class Demo(ProviderBase):
            name = "demo"
            upstream = "molcrafts-nope"
            import_name = "molmcp_sdk_probe_absent"

        sys.modules.pop("molmcp_sdk_probe_absent", None)
        assert Demo().probe() is False
        assert seen == ["molmcp_sdk_probe_absent"]
        assert "molmcp_sdk_probe_absent" not in sys.modules
