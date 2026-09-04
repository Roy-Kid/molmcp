"""Leaf grammar for ComponentKind, ComponentSpec, and BundleSpec."""

from __future__ import annotations

import dataclasses
import re
from enum import StrEnum

import pytest
from molmcp.components.models import (
    ALLOWED_REQUIRES,
    COMPONENT_NAME_PATTERN,
    KIND_PATH_PREFIX,
    SHA_PATTERN,
    BundleSpec,
    CatalogError,
    ComponentKind,
    ComponentSpec,
)


def _pattern_source(value: str | re.Pattern[str]) -> str:
    return value if isinstance(value, str) else value.pattern


def _skill_spec(
    *,
    kind: ComponentKind = ComponentKind.SKILL,
    name: str = "daily",
    id: str = "skill.daily",
    path: str = "skills/daily/SKILL.md",
    entrypoint: str | None = None,
) -> ComponentSpec:
    return ComponentSpec(kind=kind, name=name, id=id, path=path, entrypoint=entrypoint)


def _agent_spec(
    *,
    entrypoint: str | None = None,
) -> ComponentSpec:
    return ComponentSpec(
        kind=ComponentKind.AGENT,
        name="reviewer",
        id="agent.reviewer",
        path="agents/reviewer/AGENT.md",
        entrypoint=entrypoint,
    )


def _rule_spec(
    *,
    entrypoint: str | None = None,
) -> ComponentSpec:
    return ComponentSpec(
        kind=ComponentKind.RULE,
        name="safety",
        id="rule.safety",
        path="rules/safety.md",
        entrypoint=entrypoint,
    )


def _provider_spec(
    *,
    kind: ComponentKind = ComponentKind.PROVIDER,
    name: str = "molvis",
    id: str = "provider.molvis",
    path: str = "providers/molvis/provider.py",
    entrypoint: str | None = "molmcp.providers.molvis:MolvisProvider",
) -> ComponentSpec:
    return ComponentSpec(kind=kind, name=name, id=id, path=path, entrypoint=entrypoint)


def _overlay_spec(
    *,
    entrypoint: str | None = "molpy.overlay:MolpyOverlay",
) -> ComponentSpec:
    return ComponentSpec(
        kind=ComponentKind.OVERLAY,
        name="molpy",
        id="overlay.molpy",
        path="overlays/molpy/overlay.py",
        entrypoint=entrypoint,
    )


def _bundle_spec(
    *,
    name: str = "daily",
    members: tuple[str, ...] = ("skill.daily", "rule.safety"),
    requires: tuple[str, ...] = (),
) -> BundleSpec:
    return BundleSpec(name=name, members=members, requires=requires)


class TestComponentKind:
    def test_is_str_enum(self):
        assert issubclass(ComponentKind, StrEnum)

    def test_members_are_exactly_the_five_leaf_kinds(self):
        assert list(ComponentKind) == [
            ComponentKind.SKILL,
            ComponentKind.AGENT,
            ComponentKind.RULE,
            ComponentKind.PROVIDER,
            ComponentKind.OVERLAY,
        ]

    def test_values_are_lowercase_strings(self):
        assert ComponentKind.SKILL == "skill"
        assert ComponentKind.AGENT == "agent"
        assert ComponentKind.RULE == "rule"
        assert ComponentKind.PROVIDER == "provider"
        assert ComponentKind.OVERLAY == "overlay"

    def test_has_no_bundle_member(self):
        assert not hasattr(ComponentKind, "BUNDLE")
        assert "bundle" not in {member.value for member in ComponentKind}

    def test_constructing_bundle_raises_value_error(self):
        with pytest.raises(ValueError) as ei:
            ComponentKind("bundle")
        assert type(ei.value) is ValueError

    def test_catalog_error_subclasses_value_error(self):
        assert issubclass(CatalogError, ValueError)

    def test_sha_pattern_is_forty_lowercase_hex(self):
        assert _pattern_source(SHA_PATTERN) == r"^[0-9a-f]{40}$"


class TestComponentSpec:
    def test_component_name_pattern_is_the_kebab_regex(self):
        assert _pattern_source(COMPONENT_NAME_PATTERN) == r"^[a-z][a-z0-9-]*$"

    def test_kind_path_prefix_maps_each_leaf_kind(self):
        assert KIND_PATH_PREFIX[ComponentKind.SKILL] == "skills/"
        assert KIND_PATH_PREFIX[ComponentKind.AGENT] == "agents/"
        assert KIND_PATH_PREFIX[ComponentKind.RULE] == "rules/"
        assert KIND_PATH_PREFIX[ComponentKind.PROVIDER] == "providers/"
        assert KIND_PATH_PREFIX[ComponentKind.OVERLAY] == "overlays/"

    def test_constructs_skill_daily(self):
        spec = ComponentSpec(
            kind=ComponentKind.SKILL,
            name="daily",
            id="skill.daily",
            path="skills/daily/SKILL.md",
        )
        assert spec.kind is ComponentKind.SKILL
        assert spec.name == "daily"
        assert spec.id == "skill.daily"
        assert spec.path == "skills/daily/SKILL.md"
        assert spec.entrypoint is None

    def test_constructs_agent_reviewer(self):
        spec = ComponentSpec(
            kind=ComponentKind.AGENT,
            name="reviewer",
            id="agent.reviewer",
            path="agents/reviewer/AGENT.md",
        )
        assert spec.kind is ComponentKind.AGENT
        assert spec.path == "agents/reviewer/AGENT.md"
        assert spec.entrypoint is None

    def test_constructs_rule_safety(self):
        spec = ComponentSpec(
            kind=ComponentKind.RULE,
            name="safety",
            id="rule.safety",
            path="rules/safety.md",
        )
        assert spec.kind is ComponentKind.RULE
        assert spec.path == "rules/safety.md"
        assert spec.entrypoint is None

    def test_constructs_provider_molvis(self):
        spec = ComponentSpec(
            kind=ComponentKind.PROVIDER,
            name="molvis",
            id="provider.molvis",
            path="providers/molvis/provider.py",
            entrypoint="molmcp.providers.molvis:MolvisProvider",
        )
        assert spec.kind is ComponentKind.PROVIDER
        assert spec.path == "providers/molvis/provider.py"
        assert spec.entrypoint == "molmcp.providers.molvis:MolvisProvider"

    def test_constructs_overlay_molpy(self):
        spec = ComponentSpec(
            kind=ComponentKind.OVERLAY,
            name="molpy",
            id="overlay.molpy",
            path="overlays/molpy/overlay.py",
            entrypoint="molpy.overlay:MolpyOverlay",
        )
        assert spec.kind is ComponentKind.OVERLAY
        assert spec.path == "overlays/molpy/overlay.py"
        assert spec.entrypoint == "molpy.overlay:MolpyOverlay"

    def test_rejects_kind_that_is_not_component_kind(self):
        with pytest.raises(CatalogError):
            ComponentSpec(
                kind="skill",  # type: ignore[arg-type]
                name="daily",
                id="skill.daily",
                path="skills/daily/SKILL.md",
            )

    def test_rejects_name_with_underscore(self):
        with pytest.raises(CatalogError):
            _skill_spec(name="daily_skill", id="skill.daily_skill")

    def test_rejects_uppercase_name(self):
        with pytest.raises(CatalogError):
            _skill_spec(name="Daily", id="skill.Daily")

    def test_rejects_empty_name(self):
        with pytest.raises(CatalogError):
            _skill_spec(name="", id="skill.")

    def test_rejects_id_mismatch(self):
        with pytest.raises(CatalogError):
            _skill_spec(name="daily", id="skill.other")

    def test_rejects_absolute_path(self):
        with pytest.raises(CatalogError):
            _skill_spec(path="/skills/daily/SKILL.md")

    def test_rejects_backslash_in_path(self):
        with pytest.raises(CatalogError):
            _skill_spec(path="skills\\daily\\SKILL.md")

    def test_rejects_dotdot_segment(self):
        with pytest.raises(CatalogError):
            _skill_spec(path="skills/../secret")

    def test_rejects_empty_path(self):
        with pytest.raises(CatalogError):
            _skill_spec(path="")

    def test_rejects_wrong_kind_prefix(self):
        with pytest.raises(CatalogError):
            _skill_spec(path="docs/daily.md")

    def test_rejects_prefix_with_nothing_after(self):
        with pytest.raises(CatalogError):
            _skill_spec(path="skills/")

    def test_rejects_provider_missing_entrypoint(self):
        with pytest.raises(CatalogError):
            _provider_spec(entrypoint=None)

    def test_rejects_provider_empty_entrypoint(self):
        with pytest.raises(CatalogError):
            _provider_spec(entrypoint="")

    def test_rejects_provider_entrypoint_without_colon(self):
        with pytest.raises(CatalogError):
            _provider_spec(entrypoint="molmcp.providers.molvis")

    def test_rejects_overlay_missing_entrypoint(self):
        with pytest.raises(CatalogError):
            _overlay_spec(entrypoint=None)

    def test_rejects_skill_with_entrypoint(self):
        with pytest.raises(CatalogError):
            _skill_spec(entrypoint="molmcp.skills.daily:Daily")

    def test_rejects_agent_with_entrypoint(self):
        with pytest.raises(CatalogError):
            _agent_spec(entrypoint="molmcp.agents.reviewer:Reviewer")

    def test_rejects_rule_with_entrypoint(self):
        with pytest.raises(CatalogError):
            _rule_spec(entrypoint="molmcp.rules.safety:Safety")

    def test_is_frozen(self):
        spec = _skill_spec()
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.name = "other"  # type: ignore[misc]

    def test_uses_slots(self):
        spec = _skill_spec()
        assert not hasattr(spec, "__dict__")
        assert hasattr(ComponentSpec, "__slots__")


class TestBundleSpec:
    def test_allowed_requires_is_the_two_capability_tokens(self):
        assert ALLOWED_REQUIRES == frozenset({"provider-sdk", "harness-catalog"})

    def test_constructs_daily_with_empty_requires(self):
        spec = BundleSpec(
            name="daily",
            members=("skill.daily", "rule.safety"),
        )
        assert spec.name == "daily"
        assert spec.members == ("skill.daily", "rule.safety")
        assert spec.requires == ()

    def test_constructs_with_provider_sdk_requires(self):
        spec = BundleSpec(
            name="daily",
            members=("skill.daily", "rule.safety"),
            requires=("provider-sdk",),
        )
        assert spec.requires == ("provider-sdk",)

    def test_rejects_empty_members(self):
        with pytest.raises(CatalogError):
            _bundle_spec(members=())

    def test_rejects_member_without_kind_prefix(self):
        with pytest.raises(CatalogError):
            _bundle_spec(members=("daily",))

    def test_rejects_bundle_member(self):
        with pytest.raises(CatalogError):
            _bundle_spec(members=("bundle.daily",))

    def test_rejects_unknown_requires_token(self):
        with pytest.raises(CatalogError):
            _bundle_spec(requires=("not-a-capability",))

    def test_rejects_name_with_underscore(self):
        with pytest.raises(CatalogError):
            _bundle_spec(name="daily_bundle")

    def test_is_frozen(self):
        spec = _bundle_spec()
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.name = "other"  # type: ignore[misc]

    def test_uses_slots(self):
        spec = _bundle_spec()
        assert not hasattr(spec, "__dict__")
        assert hasattr(BundleSpec, "__slots__")
