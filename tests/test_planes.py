"""The plane catalog: membership from the group, product copy from here.

``list_plane_infos`` / ``known_plane_ids`` answer *which MCP servers this
install can offer*. That question has exactly one authority — the
``molmcp.providers`` entry-point group, read through
``discover_providers``. A name that only a table inside ``planes.py`` knows
about is a second authority: it makes the catalog advertise a plane that
``molmcp serve`` cannot start.

Two jobs stay in this module and are pinned here as well, because they are
what makes deleting the membership table safe rather than lossy:

* the **copy table** — product ``purpose`` / ``when_to_connect`` sentences,
  keyed by name but never a source of membership;
* ``tools_hint`` — read off the discovered instance's own ``tool_specs()``,
  duck-typed exactly like ``probe`` is, so no parallel tool list exists.

Discovery is faked with ``monkeypatch``: no entry point is added to
``pyproject.toml`` for a fixture, and nothing here imports
``molmcp.providers.base`` — a plain object with a ``tool_specs`` method is
the whole contract the catalog may rely on.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from molmcp import planes

#: Today's product sentences, keyed by name. Holding a key here is *not*
#: membership: only a name the group reported may be looked up in it.
_COPY: dict[str, tuple[str, str]] = {
    "molvis": (
        "Live molvis viewer: persistent Python namespace + browser canvas.",
        "User wants to draw, load, select, or interact with a molecule in 3D.",
    ),
    "molq": (
        "molq job lifecycle: list/get/logs destinations; opt-in submit/cancel.",
        "User wants cluster jobs, queue status, or submission.",
    ),
    "molexp": (
        "molexp workspace navigation, idempotent scaffold, and adoption of a "
        "legacy data directory (not a run driver).",
        "User works with experiment workspaces, projects, FAIR layout, or has "
        "a folder of results to lift into one.",
    ),
}

#: What a discovered name the copy table never heard of has to read like.
_GENERIC_PURPOSE = "Provider plane 'demo' (entry point molmcp.providers)."
_GENERIC_WHEN = "When work needs the 'demo' product surface."


@dataclass(frozen=True, slots=True)
class _Spec:
    """The single field ``tools_hint`` reads off a published tool spec."""

    name: str


class _Plane:
    """A discovered plane object that publishes its own tool specs."""

    def __init__(self, name: str, *tools: str, available: bool = True) -> None:
        self.name = name
        self._tools = tools
        self._available = available

    def probe(self) -> bool:
        return self._available

    def register(self, mcp: object) -> None:
        raise AssertionError(f"listing planes must not register {self.name!r}")

    def tool_specs(self) -> Iterator[_Spec]:
        return iter([_Spec(name=tool) for tool in self._tools])


class _BarePlane:
    """The Protocol minimum: a name and ``register``, no ``tool_specs``."""

    def __init__(self, name: str, *, available: bool = True) -> None:
        self.name = name
        self._available = available

    def probe(self) -> bool:
        return self._available

    def register(self, mcp: object) -> None:
        raise AssertionError(f"listing planes must not register {self.name!r}")


def _discover(
    monkeypatch: pytest.MonkeyPatch,
    *members: _Plane | _BarePlane,
) -> list[bool]:
    """Make the group report *members*; record every ``only_available`` asked.

    The fake filters on ``probe()`` itself, the way the real
    ``discover_providers`` does, so an unavailable member is what the
    catalog never sees rather than something the catalog has to skip.
    """
    asked: list[bool] = []

    def discover_providers(
        *,
        failures: list[dict[str, str]] | None = None,
        only_available: bool = False,
    ) -> list[_Plane | _BarePlane]:
        asked.append(only_available)
        return [m for m in members if not only_available or m.probe()]

    monkeypatch.setattr(planes, "discover_providers", discover_providers)
    return asked


def _copy_cases() -> list[tuple[str, str, str]]:
    """One ``(name, purpose, when)`` case per row of the copy table."""
    return [(name, purpose, when) for name, (purpose, when) in _COPY.items()]


def _ids(infos: list[planes.PlaneInfo]) -> list[str]:
    return [info.id for info in infos]


def _one(infos: list[planes.PlaneInfo], plane_id: str) -> planes.PlaneInfo:
    """The single listed plane called *plane_id* — listed exactly once."""
    matches = [info for info in infos if info.id == plane_id]
    assert len(matches) == 1, f"{plane_id!r} listed {len(matches)} times"
    return matches[0]


class TestListPlaneInfos:
    """One row per discovered plane, plus the always-on core."""

    @pytest.mark.parametrize(("name", "purpose", "when"), _copy_cases())
    def test_an_official_name_gets_the_copy_table_sentences(
        self, monkeypatch, name: str, purpose: str, when: str
    ):
        """A discovered official plane still reads as the product, not a stub.

        These three sentences have no other home: dropping them would leave
        molvis / molq / molexp describing themselves as "the 'molvis' product
        surface", which is what the generic fallback is *for*.
        """
        _discover(monkeypatch, _Plane(name, "open"))

        info = _one(planes.list_plane_infos(), name)

        assert info.purpose == purpose
        assert info.when_to_connect == when

    def test_an_official_name_hints_the_tools_its_instance_publishes(self, monkeypatch):
        """``tools_hint`` is this instance's ``tool_specs()``, not a copy of it."""
        _discover(monkeypatch, _Plane("molvis", "open"))

        info = _one(planes.list_plane_infos(), "molvis")

        assert info.tools_hint == ("open",)

    def test_an_unknown_member_gets_the_generic_sentences(self, monkeypatch):
        """A name outside the copy table is a member, described generically."""
        _discover(monkeypatch, _Plane("demo", "peek"))

        info = _one(planes.list_plane_infos(), "demo")

        assert info.purpose == _GENERIC_PURPOSE
        assert info.when_to_connect == _GENERIC_WHEN

    def test_an_unknown_member_hints_the_tools_its_instance_publishes(
        self, monkeypatch
    ):
        """No copy-table row, yet the tools are known — they come off the object."""
        _discover(monkeypatch, _Plane("demo", "peek"))

        info = _one(planes.list_plane_infos(), "demo")

        assert info.tools_hint == ("peek",)

    def test_a_member_without_tool_specs_hints_no_tools(self, monkeypatch):
        """``tool_specs`` is optional, like ``probe``: absent means no hint."""
        bare = _BarePlane("molq")
        assert not hasattr(bare, "tool_specs")
        _discover(monkeypatch, bare)

        info = _one(planes.list_plane_infos(), "molq")

        assert info.tools_hint == ()

    def test_the_copy_table_still_answers_a_member_without_tool_specs(
        self, monkeypatch
    ):
        """Copy is keyed by name; it does not depend on publishing tools."""
        _discover(monkeypatch, _BarePlane("molq"))

        info = _one(planes.list_plane_infos(), "molq")

        assert (info.purpose, info.when_to_connect) == _COPY["molq"]

    @pytest.mark.parametrize("include_unavailable", [False, True])
    def test_an_empty_group_leaves_only_the_core(
        self, monkeypatch, include_unavailable: bool
    ):
        """Nothing installed lists nothing, though the copy table is full."""
        _discover(monkeypatch)

        infos = planes.list_plane_infos(
            include_unavailable_providers=include_unavailable
        )

        assert _ids(infos) == [planes.CORE_PLANE_ID]

    def test_unavailable_members_are_listed_when_diagnostics_ask(self, monkeypatch):
        """A discovered plane whose science package is missing is still real."""
        _discover(monkeypatch, _Plane("demo", "peek", available=False))

        infos = planes.list_plane_infos(include_unavailable_providers=True)

        assert _ids(infos) == [planes.CORE_PLANE_ID, "demo"]

    def test_an_official_name_that_was_not_discovered_is_not_invented(
        self, monkeypatch
    ):
        """Diagnostics widen ``probe()``, never the membership question."""
        _discover(monkeypatch, _Plane("demo", "peek", available=False))

        listed = _ids(planes.list_plane_infos(include_unavailable_providers=True))

        for name in _COPY:
            assert name not in listed

    def test_diagnostics_ask_discovery_for_the_unavailable_members(self, monkeypatch):
        """The wider list comes from ``only_available=False``, not from a table."""
        asked = _discover(monkeypatch, _Plane("demo", "peek", available=False))

        planes.list_plane_infos(include_unavailable_providers=True)

        assert False in asked

    def test_the_module_has_no_provider_meta_membership_table(self):
        """The three-jobs table is gone: membership, copy, and tools split up."""
        assert not hasattr(planes, "_PROVIDER_META")

    def test_the_module_never_imports_the_provider_base_module(self):
        """``tools_hint`` is duck-typed; layer 2 does not depend on the SDK base."""
        source = Path(planes.__file__).read_text(encoding="utf-8")
        assert "providers.base" not in source
        imported: list[str] = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
        assert [name for name in imported if "providers" in name] == []


class TestKnownPlaneIds:
    """What ``molmcp serve <id>`` may accept — the same one authority."""

    def test_available_ids_are_the_core_plus_what_discovery_reported(self, monkeypatch):
        _discover(monkeypatch, _Plane("demo", "peek"))

        assert planes.known_plane_ids(only_available=True) == frozenset(
            {planes.CORE_PLANE_ID, "demo"}
        )

    def test_unavailable_ids_come_from_discovery_not_from_the_copy_table(
        self, monkeypatch
    ):
        """Explicit serve is allowed to fail loudly — but only for real planes."""
        _discover(monkeypatch, _Plane("demo", "peek", available=False))

        assert planes.known_plane_ids() == frozenset({planes.CORE_PLANE_ID, "demo"})

    @pytest.mark.parametrize("only_available", [False, True])
    def test_an_empty_group_knows_only_the_core(
        self, monkeypatch, only_available: bool
    ):
        _discover(monkeypatch)

        assert planes.known_plane_ids(only_available=only_available) == frozenset(
            {planes.CORE_PLANE_ID}
        )

    @pytest.mark.parametrize("only_available", [False, True])
    def test_the_only_available_flag_reaches_discovery_unchanged(
        self, monkeypatch, only_available: bool
    ):
        asked = _discover(monkeypatch, _Plane("demo", "peek"))

        planes.known_plane_ids(only_available=only_available)

        assert asked == [only_available]


class TestRouteTask:
    """Keyword routing is a core table, not a membership list."""

    def test_drawing_still_routes_to_molvis(self):
        answer = planes.route_task("draw dopamine")

        assert [match["plane"] for match in answer["planes"]] == ["molvis"]

    def test_an_unknown_member_is_listed_but_never_keyword_routed(self, monkeypatch):
        """Joining the group publishes a plane; it does not claim vocabulary."""
        _discover(monkeypatch, _Plane("demo", "peek"))

        assert "demo" in _ids(planes.list_plane_infos())
        assert planes.route_task("demo peek please")["planes"] == []
