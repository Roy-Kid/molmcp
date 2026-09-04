"""HarnessCatalog construction, lookup, and harness.toml loading."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import sys
from pathlib import Path

import pytest

import molmcp
from molmcp.components.catalog import (
    HarnessCatalog,
    ResolvedBundle,
    load_harness_catalog,
)
from molmcp.components.models import (
    ALLOWED_REQUIRES,
    BundleSpec,
    CatalogError,
    ComponentKind,
    ComponentSpec,
)

SHA = "0123456789abcdef0123456789abcdef01234567"
CAPABILITIES = frozenset({"provider-sdk", "harness-catalog"})
CANONICAL_TOML = """\
requires = ["provider-sdk", "harness-catalog"]

[[component]]
kind = "skill"
name = "daily"
path = "skills/daily/SKILL.md"

[[component]]
kind = "rule"
name = "safety"
path = "rules/safety.md"

[[component]]
kind = "provider"
name = "molvis"
path = "providers/molvis/provider.py"
entrypoint = "molmcp.providers.molvis:MolvisProvider"

[[component]]
kind = "overlay"
name = "molpy"
path = "overlays/molpy/overlay.py"
entrypoint = "molpy.overlay:MolpyOverlay"

[[component]]
kind = "agent"
name = "reviewer"
path = "agents/reviewer/AGENT.md"

[[component]]
kind = "bundle"
name = "daily"
members = ["skill.daily", "rule.safety", "provider.molvis", "overlay.molpy"]

[[component]]
kind = "bundle"
name = "dev"
members = ["skill.daily", "agent.reviewer", "rule.safety", "provider.molvis"]
"""
_COMPONENTS_DIR = Path(__file__).resolve().parents[2] / "src" / "molmcp" / "components"
_DAILY_IDS = (
    "skill.daily",
    "rule.safety",
    "provider.molvis",
    "overlay.molpy",
)
_DEV_IDS = (
    "skill.daily",
    "agent.reviewer",
    "rule.safety",
    "provider.molvis",
)


def _write_harness_toml(tmp_path: Path, content: str = CANONICAL_TOML) -> Path:
    path = tmp_path / "harness.toml"
    path.write_text(content, encoding="utf-8")
    return path


def _leaf_components() -> tuple[ComponentSpec, ...]:
    return (
        ComponentSpec(
            kind=ComponentKind.SKILL,
            name="daily",
            id="skill.daily",
            path="skills/daily/SKILL.md",
        ),
        ComponentSpec(
            kind=ComponentKind.RULE,
            name="safety",
            id="rule.safety",
            path="rules/safety.md",
        ),
        ComponentSpec(
            kind=ComponentKind.PROVIDER,
            name="molvis",
            id="provider.molvis",
            path="providers/molvis/provider.py",
            entrypoint="molmcp.providers.molvis:MolvisProvider",
        ),
        ComponentSpec(
            kind=ComponentKind.OVERLAY,
            name="molpy",
            id="overlay.molpy",
            path="overlays/molpy/overlay.py",
            entrypoint="molpy.overlay:MolpyOverlay",
        ),
        ComponentSpec(
            kind=ComponentKind.AGENT,
            name="reviewer",
            id="agent.reviewer",
            path="agents/reviewer/AGENT.md",
        ),
    )


def _daily_bundle(
    *,
    requires: tuple[str, ...] = (),
    members: tuple[str, ...] = _DAILY_IDS,
) -> BundleSpec:
    return BundleSpec(name="daily", members=members, requires=requires)


def _dev_bundle(
    *,
    requires: tuple[str, ...] = (),
    members: tuple[str, ...] = _DEV_IDS,
) -> BundleSpec:
    return BundleSpec(name="dev", members=members, requires=requires)


def _catalog(
    *,
    sha: str = SHA,
    requires: tuple[str, ...] = ("provider-sdk", "harness-catalog"),
    components: tuple[ComponentSpec, ...] | None = None,
    bundles: tuple[BundleSpec, ...] | None = None,
) -> HarnessCatalog:
    return HarnessCatalog(
        sha=sha,
        requires=requires,
        components=_leaf_components() if components is None else components,
        bundles=((_daily_bundle(), _dev_bundle()) if bundles is None else bundles),
    )


def _member_ids(resolved: ResolvedBundle) -> tuple[str, ...]:
    return tuple(member.id for member in resolved.members)


def _toml_without_bundle(name: str) -> str:
    marker = f'name = "{name}"'
    chunks: list[str] = []
    skipping = False
    for raw in CANONICAL_TOML.split("[[component]]"):
        if not skipping and marker in raw and "members" in raw:
            skipping = True
            continue
        chunks.append(raw)
    return "[[component]]".join(chunks)


def _non_stdlib_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                names = [node.module]
        for name in names:
            top = name.split(".")[0]
            if top in sys.stdlib_module_names or top == "__future__":
                continue
            if name == "molmcp.components" or name.startswith("molmcp.components."):
                continue
            found.append(name)
    return found


class TestHarnessCatalog:
    def test_is_frozen(self):
        catalog = _catalog()
        with pytest.raises(dataclasses.FrozenInstanceError):
            catalog.sha = "0" * 40  # type: ignore[misc]

    def test_uses_slots(self):
        catalog = _catalog()
        assert not hasattr(catalog, "__dict__")
        assert hasattr(HarnessCatalog, "__slots__")

    def test_constructs_with_valid_sha(self):
        catalog = _catalog()
        assert catalog.sha == SHA
        assert catalog.requires == ("provider-sdk", "harness-catalog")

    @pytest.mark.parametrize(
        "sha",
        [
            "not-a-sha",
            "0123456789ABCDEF0123456789ABCDEF01234567",
            "0123456789abcdef0123456789abcdef0123456",
            "0123456789abcdef0123456789abcdef012345678",
        ],
    )
    def test_rejects_invalid_sha(self, sha):
        with pytest.raises(CatalogError):
            _catalog(sha=sha)

    def test_rejects_unknown_requires_token(self):
        assert "not-a-capability" not in ALLOWED_REQUIRES
        with pytest.raises(CatalogError):
            _catalog(requires=("not-a-capability",))

    def test_rejects_missing_daily_bundle(self):
        with pytest.raises(CatalogError):
            _catalog(bundles=(_dev_bundle(),))

    def test_rejects_missing_dev_bundle(self):
        with pytest.raises(CatalogError):
            _catalog(bundles=(_daily_bundle(),))

    def test_rejects_duplicate_component_ids(self):
        leaves = _leaf_components()
        with pytest.raises(CatalogError):
            _catalog(components=leaves + (leaves[0],))

    def test_rejects_duplicate_bundle_names(self):
        with pytest.raises(CatalogError):
            _catalog(bundles=(_daily_bundle(), _daily_bundle(), _dev_bundle()))

    def test_rejects_unknown_bundle_member_id(self):
        with pytest.raises(CatalogError):
            _catalog(
                bundles=(
                    _daily_bundle(members=("skill.daily", "skill.missing")),
                    _dev_bundle(),
                )
            )

    def test_has_no_supported_capabilities_field(self):
        catalog = _catalog()
        assert not hasattr(catalog, "supported_capabilities")

    def test_get_skill_daily_returns_component_spec(self):
        spec = _catalog().get("skill.daily")
        assert isinstance(spec, ComponentSpec)
        assert spec.kind is ComponentKind.SKILL
        assert spec.id == "skill.daily"

    def test_get_daily_raises_unknown_id(self):
        with pytest.raises(CatalogError) as ei:
            _catalog().get("daily")
        assert "unknown-id" in str(ei.value)

    def test_get_missing_component_id_raises_unknown_id(self):
        with pytest.raises(CatalogError) as ei:
            _catalog().get("skill.missing")
        assert "unknown-id" in str(ei.value)

    def test_get_bundle_daily_returns_bundle_spec(self):
        spec = _catalog().get_bundle("daily")
        assert isinstance(spec, BundleSpec)
        assert spec.name == "daily"
        assert spec.members == _DAILY_IDS

    def test_get_bundle_missing_raises_unknown_bundle(self):
        with pytest.raises(CatalogError) as ei:
            _catalog().get_bundle("missing")
        assert "unknown-bundle" in str(ei.value)

    def test_resolve_bundle_daily_member_ids_are_golden(self):
        resolved = _catalog().resolve_bundle("daily")
        assert isinstance(resolved, ResolvedBundle)
        assert _member_ids(resolved) == _DAILY_IDS

    def test_resolve_bundle_dev_member_ids_are_golden(self):
        resolved = _catalog().resolve_bundle("dev")
        assert isinstance(resolved, ResolvedBundle)
        assert _member_ids(resolved) == _DEV_IDS

    def test_resolve_bundle_requires_is_first_seen_union(self):
        catalog = _catalog(
            requires=("provider-sdk",),
            bundles=(
                _daily_bundle(requires=("harness-catalog", "provider-sdk")),
                _dev_bundle(),
            ),
        )
        resolved = catalog.resolve_bundle("daily")
        assert resolved.requires == ("provider-sdk", "harness-catalog")

    def test_resolved_bundle_union_is_not_a_catalog_field(self):
        catalog_fields = tuple(
            field.name for field in dataclasses.fields(HarnessCatalog)
        )
        assert catalog_fields == ("sha", "requires", "components", "bundles")
        catalog = _catalog()
        assert not hasattr(catalog, "resolved_requires")
        bundle_fields = tuple(field.name for field in dataclasses.fields(BundleSpec))
        assert bundle_fields == ("name", "members", "requires")
        resolved_fields = tuple(
            field.name for field in dataclasses.fields(ResolvedBundle)
        )
        assert resolved_fields == ("name", "members", "requires")


class TestLoadHarnessCatalog:
    def test_load_stores_sha_argument(self, tmp_path):
        _write_harness_toml(tmp_path)
        sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        catalog = load_harness_catalog(tmp_path, sha, CAPABILITIES)
        assert catalog.sha == sha

    def test_two_argument_call_raises_type_error(self, tmp_path):
        _write_harness_toml(tmp_path)
        with pytest.raises(TypeError):
            load_harness_catalog(tmp_path, SHA)  # type: ignore[call-arg]

    def test_supported_capabilities_has_no_default(self):
        param = inspect.signature(load_harness_catalog).parameters[
            "supported_capabilities"
        ]
        assert param.default is inspect.Parameter.empty

    def test_wire_bundle_rows_become_bundle_spec(self, tmp_path):
        _write_harness_toml(tmp_path)
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert all(isinstance(bundle, BundleSpec) for bundle in catalog.bundles)
        assert {bundle.name for bundle in catalog.bundles} == {"daily", "dev"}
        assert "bundle.daily" not in {spec.id for spec in catalog.components}
        assert all(isinstance(spec, ComponentSpec) for spec in catalog.components)

    def test_five_kind_rows_become_component_spec(self, tmp_path):
        _write_harness_toml(tmp_path)
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert all(isinstance(spec, ComponentSpec) for spec in catalog.components)
        assert {spec.kind for spec in catalog.components} == {
            ComponentKind.SKILL,
            ComponentKind.AGENT,
            ComponentKind.RULE,
            ComponentKind.PROVIDER,
            ComponentKind.OVERLAY,
        }

    def test_get_daily_is_unknown_id_after_load(self, tmp_path):
        _write_harness_toml(tmp_path)
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        with pytest.raises(CatalogError) as ei:
            catalog.get("daily")
        assert "unknown-id" in str(ei.value)

    def test_get_bundle_daily_works_after_load(self, tmp_path):
        _write_harness_toml(tmp_path)
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        spec = catalog.get_bundle("daily")
        assert isinstance(spec, BundleSpec)
        assert spec.members == _DAILY_IDS

    def test_component_kind_still_has_no_bundle(self):
        assert not hasattr(ComponentKind, "BUNDLE")
        assert "bundle" not in {member.value for member in ComponentKind}
        with pytest.raises(ValueError) as ei:
            ComponentKind("bundle")
        assert type(ei.value) is ValueError

    def test_empty_capabilities_is_ineligible_when_requires_present(self, tmp_path):
        _write_harness_toml(tmp_path)
        with pytest.raises(CatalogError) as ei:
            load_harness_catalog(tmp_path, SHA, frozenset())
        assert "ineligible" in str(ei.value)

    def test_missing_harness_catalog_capability_is_ineligible(self, tmp_path):
        _write_harness_toml(tmp_path)
        with pytest.raises(CatalogError) as ei:
            load_harness_catalog(tmp_path, SHA, frozenset({"provider-sdk"}))
        assert "ineligible" in str(ei.value)

    def test_bundle_requires_missing_from_capabilities_is_ineligible(self, tmp_path):
        content = CANONICAL_TOML.replace(
            'requires = ["provider-sdk", "harness-catalog"]',
            'requires = ["provider-sdk"]',
        ).replace(
            'name = "daily"\nmembers = ["skill.daily", "rule.safety", '
            '"provider.molvis", "overlay.molpy"]',
            'name = "daily"\nmembers = ["skill.daily", "rule.safety", '
            '"provider.molvis", "overlay.molpy"]\nrequires = ["harness-catalog"]',
        )
        _write_harness_toml(tmp_path, content)
        with pytest.raises(CatalogError) as ei:
            load_harness_catalog(tmp_path, SHA, frozenset({"provider-sdk"}))
        assert "ineligible" in str(ei.value)

    def test_unknown_requires_token_fails_language_gate_before_eligibility(
        self, tmp_path
    ):
        content = CANONICAL_TOML.replace(
            'requires = ["provider-sdk", "harness-catalog"]',
            'requires = ["not-a-capability"]',
        )
        _write_harness_toml(tmp_path, content)
        with pytest.raises(CatalogError) as ei:
            load_harness_catalog(tmp_path, SHA, frozenset({"not-a-capability"}))
        assert "ineligible" not in str(ei.value)

    def test_rejects_unknown_top_level_key(self, tmp_path):
        _write_harness_toml(tmp_path, CANONICAL_TOML + "\nunexpected = 1\n")
        with pytest.raises(CatalogError):
            load_harness_catalog(tmp_path, SHA, CAPABILITIES)

    @pytest.mark.parametrize("field", ["sha", "version", "tag", "release", "id"])
    def test_rejects_identity_top_level_field(self, tmp_path, field):
        _write_harness_toml(tmp_path, CANONICAL_TOML + f'\n{field} = "1"\n')
        with pytest.raises(CatalogError):
            load_harness_catalog(tmp_path, SHA, CAPABILITIES)

    def test_rejects_unknown_component_kind(self, tmp_path):
        extra = """
[[component]]
kind = "widget"
name = "extra"
path = "widgets/extra.md"
"""
        _write_harness_toml(tmp_path, CANONICAL_TOML + extra)
        with pytest.raises(CatalogError):
            load_harness_catalog(tmp_path, SHA, CAPABILITIES)

    def test_rejects_missing_daily_bundle(self, tmp_path):
        _write_harness_toml(tmp_path, _toml_without_bundle("daily"))
        with pytest.raises(CatalogError):
            load_harness_catalog(tmp_path, SHA, CAPABILITIES)

    def test_rejects_missing_dev_bundle(self, tmp_path):
        _write_harness_toml(tmp_path, _toml_without_bundle("dev"))
        with pytest.raises(CatalogError):
            load_harness_catalog(tmp_path, SHA, CAPABILITIES)

    def test_does_not_load_catalog_toml(self, tmp_path):
        (tmp_path / "catalog.toml").write_text(CANONICAL_TOML, encoding="utf-8")
        with pytest.raises(CatalogError):
            load_harness_catalog(tmp_path, SHA, CAPABILITIES)

    def test_does_not_import_entrypoint_module(self, tmp_path):
        sys.modules.pop("does.not.exist", None)
        content = CANONICAL_TOML.replace(
            "molmcp.providers.molvis:MolvisProvider",
            "does.not.exist:Nope",
        )
        _write_harness_toml(tmp_path, content)
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert catalog.get("provider.molvis").entrypoint == "does.not.exist:Nope"
        assert "does.not.exist" not in sys.modules

    def test_does_not_require_component_paths_on_disk(self, tmp_path):
        _write_harness_toml(tmp_path)
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert catalog.get("skill.daily").id == "skill.daily"
        for spec in catalog.components:
            assert not (tmp_path / spec.path).exists()

    def test_components_package_imports_only_stdlib(self):
        sources = [
            _COMPONENTS_DIR / "catalog.py",
            _COMPONENTS_DIR / "models.py",
            _COMPONENTS_DIR / "__init__.py",
        ]
        for path in sources:
            assert path.is_file(), f"missing {path.name}"
            imported = _non_stdlib_imports(path)
            assert imported == [], f"{path.name} imports {imported}"

    def test_symbols_are_not_in_molmcp_all(self):
        exported = set(molmcp.__all__)
        assert "load_harness_catalog" not in exported
        assert "HarnessCatalog" not in exported
        assert "ComponentSpec" not in exported
        assert "ComponentKind" not in exported
