"""HarnessCatalog construction, lookup, enable filtering, and harness.toml loading."""

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
HARNESS_REPO_TOML = """\
requires = ["provider-sdk", "harness-catalog"]
component_root = "plugins/mol"

[[component]]
kind = "skill"
name = "spec"
path = "skills/spec/SKILL.md"

[[component]]
kind = "agent"
name = "scientist"
path = "agents/scientist.md"

[[component]]
kind = "rule"
name = "large-spec-split"
path = "rules/large-spec-split.md"

[[component]]
kind = "provider"
name = "demo"
path = "providers/demo/provider.py"
entrypoint = "molmcp.providers.demo:DemoProvider"

[[component]]
kind = "overlay"
name = "demo"
path = "overlays/demo/overlay.py"
entrypoint = "demo.overlay:DemoOverlay"

[[component]]
kind = "bundle"
name = "daily"
members = ["skill.spec", "rule.large-spec-split"]

[[component]]
kind = "bundle"
name = "dev"
members = ["skill.spec", "agent.scientist", "rule.large-spec-split"]
"""
"""A catalog spelled the way ``MolCrafts/harness`` authors its own rows.

This is deliberately **not** "the same shape" as the real file. The real
repository is 55 rows and ships **zero** providers and **zero** overlays;
this fixture carries three rows in its real kind census -- a skill at
``skills/<name>/SKILL.md``, an agent at ``agents/<name>.md``, a rule at
``rules/<name>.md`` -- plus one provider and one overlay row that exist
only to cover the loader's kind table. Nothing here builds a fold or
asserts anything about either arm.
"""

#: Every value ``_validate_component_root`` must refuse. Each is refused
#: twice over: through ``load_harness_catalog`` and through direct
#: ``HarnessCatalog(component_root=...)`` construction, because the value
#: gate lives in ``__post_init__`` and only the loader can see presence.
_REJECTED_COMPONENT_ROOTS = (
    "..",
    "../evil",
    "a/../b",
    ".",
    "/plugins",
    "plugins\\mol",
    "D:evil",
)
#: Values that must load. ``plugins/mol`` is two segments, so the path
#: separator check ``ImmutableGitStore._sha_dir`` and ``harness.pointer_path``
#: both carry is deliberately absent from this guard.
_ACCEPTED_COMPONENT_ROOTS = ("plugins/mol", "plugins/mol/nested")
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


def _toml_with_component_root(value: str, base: str = CANONICAL_TOML) -> str:
    r"""Return ``base`` with ``component_root = value`` above its first table.

    The key is written as a TOML *literal* string (single quotes) so that
    ``plugins\mol`` reaches the guard as data. In a basic string a lone
    backslash is an invalid escape, and the loader would answer with a
    ``TOMLDecodeError`` wrapped as ``invalid harness.toml`` before
    ``_validate_component_root`` ever saw the value.

    A bare key after a ``[[component]]`` header is itself a
    ``TOMLDecodeError``, so the key goes beside ``requires`` at the top.
    """

    return f"component_root = '{value}'\n{base}"


def _catalog_with_root(component_root: str) -> HarnessCatalog:
    """Construct a catalog directly, passing ``component_root`` by keyword.

    Bypasses the loader on purpose: the *value* gate lives in
    ``HarnessCatalog.__post_init__``, so a bad value must be
    unconstructible even when no ``harness.toml`` exists.
    """

    return HarnessCatalog(
        sha=SHA,
        requires=("provider-sdk", "harness-catalog"),
        components=_leaf_components(),
        bundles=(_daily_bundle(), _dev_bundle()),
        component_root=component_root,
    )


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


def _enable_leaves() -> tuple[ComponentSpec, ...]:
    return (
        ComponentSpec(
            kind=ComponentKind.SKILL,
            name="notes",
            id="skill.notes",
            path="skills/notes/SKILL.md",
        ),
        ComponentSpec(
            kind=ComponentKind.RULE,
            name="style",
            id="rule.style",
            path="rules/style.md",
        ),
        ComponentSpec(
            kind=ComponentKind.AGENT,
            name="reviewer",
            id="agent.reviewer",
            path="agents/reviewer/AGENT.md",
        ),
    )


def _sci_bundle() -> BundleSpec:
    return BundleSpec(name="sci", members=("skill.notes", "rule.style"))


def _lab_bundle() -> BundleSpec:
    return BundleSpec(name="lab", members=("skill.notes", "agent.reviewer"))


def _enable_catalog(
    *,
    bundles: tuple[BundleSpec, ...] | None = None,
) -> HarnessCatalog:
    return _catalog(
        components=_enable_leaves(),
        bundles=(_sci_bundle(), _lab_bundle()) if bundles is None else bundles,
    )


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

    def test_constructs_with_only_sci_bundle(self):
        catalog = _enable_catalog(bundles=(_sci_bundle(),))
        assert tuple(bundle.name for bundle in catalog.bundles) == ("sci",)

    def test_constructs_with_empty_bundles(self):
        catalog = _enable_catalog(bundles=())
        assert catalog.bundles == ()
        assert tuple(spec.id for spec in catalog.components) == (
            "skill.notes",
            "rule.style",
            "agent.reviewer",
        )

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

    def test_constructs_by_keyword_without_component_root(self):
        catalog = _catalog()
        assert catalog.component_root == ""

    def test_component_root_is_declared_last(self):
        names = tuple(field.name for field in dataclasses.fields(HarnessCatalog))
        assert names[-1] == "component_root"

    @pytest.mark.parametrize("component_root", _ACCEPTED_COMPONENT_ROOTS)
    def test_accepts_multi_segment_component_root(self, component_root):
        """``plugins/mol`` is two segments and must stay legal.

        ``ImmutableGitStore._sha_dir`` and ``harness.pointer_path`` both
        refuse path separators, because a SHA and a source name are
        interpolated as single segments. ``component_root`` is the opposite
        case, so that half of the borrowed guard is deliberately absent.
        Assert it, or a later "simplification" restores the separator check
        and breaks the only layout this key exists to support.
        """

        assert _catalog_with_root(component_root).component_root == component_root

    def test_empty_component_root_is_constructible(self):
        """``""`` means "the tree itself" and must construct.

        At construction time a defaulted ``""`` and a written ``""`` are the
        same string, so ``__post_init__`` cannot tell them apart and must not
        try. The empty-when-present refusal belongs to the loader, which is
        the only gate that can still see presence.
        """

        assert _catalog_with_root("").component_root == ""

    @pytest.mark.parametrize("component_root", _REJECTED_COMPONENT_ROOTS)
    def test_rejects_escaping_component_root(self, component_root):
        with pytest.raises(CatalogError) as ei:
            _catalog_with_root(component_root)
        assert repr(component_root) in str(ei.value)

    def test_rejects_slash_prefixed_component_root_that_is_not_absolute(self):
        """``/plugins`` needs the second clause of the absolute-path test.

        ``PureWindowsPath("/plugins").is_absolute()`` is ``False``, so
        ``Path(value).is_absolute()`` alone misses it off Windows -- while
        ``PureWindowsPath("C:/store/tree") / "/plugins"`` is
        ``WindowsPath("C:/plugins")``, the base gone. The guard needs
        ``or value.startswith("/")``, exactly the pair
        ``_validate_component_path`` already carries.
        """

        assert "/plugins" in _REJECTED_COMPONENT_ROOTS
        with pytest.raises(CatalogError) as ei:
            _catalog_with_root("/plugins")
        assert repr("/plugins") in str(ei.value)

    def test_rejects_drive_relative_component_root(self):
        """``D:evil`` passes every other clause and still discards the base.

        It carries no ``..``, holds no backslash, and
        ``Path("D:evil").is_absolute()`` is ``False`` on POSIX -- yet
        ``PureWindowsPath("C:/store/tree") / "D:evil"`` is
        ``WindowsPath("D:evil")``: a drive on the *first* joined component
        resets the anchor and drops the base entirely. ``component_root`` is
        always that first component, and CI runs ``windows-latest``.
        """

        assert "D:evil" in _REJECTED_COMPONENT_ROOTS
        with pytest.raises(CatalogError) as ei:
            _catalog_with_root("D:evil")
        assert repr("D:evil") in str(ei.value)

    def test_rejects_dot_component_root_by_the_segment_predicate(self):
        """``.`` is refused for its segment, not for being empty-when-present.

        It passes every other clause and ``Path("/store/tree") / "."`` is
        ``/store/tree``, a second spelling of ``""``. The predicate splits on
        ``"/"`` rather than reading ``PurePath.parts``, which silently drops
        ``.`` and collapses ``//`` and would therefore miss it. This test
        reaches the value gate directly, with no ``harness.toml`` anywhere,
        so the loader's presence check cannot be what answers -- while
        ``component_root=""`` on the same path constructs.
        """

        with pytest.raises(CatalogError) as ei:
            _catalog_with_root(".")
        assert repr(".") in str(ei.value)

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
        assert catalog_fields == (
            "sha",
            "requires",
            "components",
            "bundles",
            "component_root",
        )
        catalog = _catalog()
        assert not hasattr(catalog, "resolved_requires")
        bundle_fields = tuple(field.name for field in dataclasses.fields(BundleSpec))
        assert bundle_fields == ("name", "members", "requires")
        resolved_fields = tuple(
            field.name for field in dataclasses.fields(ResolvedBundle)
        )
        assert resolved_fields == ("name", "members", "requires")

    def test_enabled_components_unions_sci_then_lab_in_first_seen_order(self):
        catalog = _enable_catalog()
        ids = tuple(spec.id for spec in catalog.enabled_components(("sci", "lab")))
        assert ids == ("skill.notes", "rule.style", "agent.reviewer")

    def test_enabled_components_unions_lab_then_sci_in_first_seen_order(self):
        catalog = _enable_catalog()
        ids = tuple(spec.id for spec in catalog.enabled_components(("lab", "sci")))
        assert ids == ("skill.notes", "agent.reviewer", "rule.style")

    def test_enabled_components_empty_tuple_returns_empty(self):
        catalog = _enable_catalog()
        assert catalog.enabled_components(()) == ()

    def test_enabled_components_unknown_name_raises_unknown_bundle(self):
        catalog = _enable_catalog()
        with pytest.raises(CatalogError) as ei:
            catalog.enabled_components(("nope",))
        message = str(ei.value)
        assert "unknown-bundle" in message
        assert "sci" in message
        assert "lab" in message

    def test_enabled_components_none_with_empty_bundles_returns_all_components(self):
        catalog = _enable_catalog(bundles=())
        assert catalog.components != ()
        assert catalog.enabled_components(None) == catalog.components

    def test_enabled_components_named_bundle_with_empty_bundles_raises(self):
        catalog = _enable_catalog(bundles=())
        with pytest.raises(CatalogError) as ei:
            catalog.enabled_components(("sci",))
        assert "unknown-bundle" in str(ei.value)


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

    def test_loads_component_root_from_the_file(self, tmp_path):
        _write_harness_toml(tmp_path, _toml_with_component_root("plugins/mol"))
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert catalog.component_root == "plugins/mol"

    def test_absent_component_root_is_the_empty_string(self, tmp_path):
        """The canonical file names no ``component_root`` and still loads.

        Paired with ``test_rejects_empty_component_root_when_present``: only
        the difference between the two proves a presence check exists at all.
        """

        _write_harness_toml(tmp_path)
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert catalog.component_root == ""

    def test_rejects_empty_component_root_when_present(self, tmp_path):
        """``component_root = ""`` is refused, though an absent key is not.

        The loader is the only gate that can still see presence:
        ``__post_init__`` receives ``""`` from a defaulted field and from a
        written one alike.

        The refusal must not be ``_reject_unknown``'s: an unrecognised key
        already raises ``CatalogError`` naming ``component_root``, so
        without the second assertion this test passes before the key exists.
        """

        _write_harness_toml(tmp_path, _toml_with_component_root(""))
        with pytest.raises(CatalogError) as ei:
            load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert "component_root" in str(ei.value)
        assert "unknown field" not in str(ei.value)

    def test_component_root_is_not_an_unknown_field(self, tmp_path):
        """Asserted behaviourally, never against ``catalog._TOP_LEVEL_KEYS``.

        Reading that constant back would be true the instant an implementer
        edits the line, which is not evidence that the key parses.
        """

        _write_harness_toml(tmp_path, _toml_with_component_root("plugins/mol"))
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert isinstance(catalog, HarnessCatalog)

    def test_component_paths_are_carried_not_rewritten(self, tmp_path):
        """With a ``component_root`` set, ``path`` comes out exactly as authored.

        The expected literal is written out here rather than derived from the
        TOML input, so the assertion can fail. ``component_root`` is carried
        beside the paths and never folded into them.
        """

        _write_harness_toml(tmp_path, _toml_with_component_root("plugins/mol"))
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert catalog.get("skill.daily").path == "skills/daily/SKILL.md"
        assert catalog.get("rule.safety").path == "rules/safety.md"
        assert catalog.get("provider.molvis").path == "providers/molvis/provider.py"
        assert catalog.get("overlay.molpy").path == "overlays/molpy/overlay.py"
        assert catalog.get("agent.reviewer").path == "agents/reviewer/AGENT.md"

    def test_component_root_does_not_weaken_the_kind_prefix(self, tmp_path):
        """A rooted catalog still refuses a path missing its kind prefix.

        ``KIND_PATH_PREFIX`` validates paths unchanged; ``component_root`` is
        not a licence to drop ``skills/`` from a skill row.
        """

        content = _toml_with_component_root(
            "plugins/mol",
            CANONICAL_TOML.replace(
                'path = "skills/daily/SKILL.md"', 'path = "daily/SKILL.md"'
            ),
        )
        _write_harness_toml(tmp_path, content)
        with pytest.raises(CatalogError) as ei:
            load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert "skills/" in str(ei.value)

    @pytest.mark.parametrize("component_root", _ACCEPTED_COMPONENT_ROOTS)
    def test_loads_multi_segment_component_root(self, tmp_path, component_root):
        _write_harness_toml(tmp_path, _toml_with_component_root(component_root))
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert catalog.component_root == component_root

    @pytest.mark.parametrize("component_root", _REJECTED_COMPONENT_ROOTS)
    def test_rejects_escaping_component_root(self, tmp_path, component_root):
        _write_harness_toml(tmp_path, _toml_with_component_root(component_root))
        with pytest.raises(CatalogError) as ei:
            load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert repr(component_root) in str(ei.value)

    def test_harness_repo_shaped_catalog_loads(self, tmp_path):
        """The real repository's authored spellings, through the real loader.

        No ``_wire`` seam and no monkeypatch: this is the only evidence in
        this repository that the ``MolCrafts/harness`` layout parses at all.
        """

        _write_harness_toml(tmp_path, HARNESS_REPO_TOML)
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert catalog.component_root == "plugins/mol"
        assert catalog.get("skill.spec").path == "skills/spec/SKILL.md"
        assert catalog.get("agent.scientist").path == "agents/scientist.md"
        assert catalog.get("rule.large-spec-split").path == "rules/large-spec-split.md"

    def test_harness_repo_shaped_catalog_covers_the_kind_table(self, tmp_path):
        _write_harness_toml(tmp_path, HARNESS_REPO_TOML)
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert {spec.kind for spec in catalog.components} == set(ComponentKind)
        assert {bundle.name for bundle in catalog.bundles} == {"daily", "dev"}

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

    def test_loads_without_daily_bundle(self, tmp_path):
        _write_harness_toml(tmp_path, _toml_without_bundle("daily"))
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert tuple(bundle.name for bundle in catalog.bundles) == ("dev",)

    def test_loads_without_dev_bundle(self, tmp_path):
        _write_harness_toml(tmp_path, _toml_without_bundle("dev"))
        catalog = load_harness_catalog(tmp_path, SHA, CAPABILITIES)
        assert tuple(bundle.name for bundle in catalog.bundles) == ("daily",)

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
