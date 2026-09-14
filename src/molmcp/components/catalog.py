"""Parse ``harness.toml``, then check eligibility.

Load the TOML catalog at a checkout root (language gate), construct
:class:`HarnessCatalog`, then check eligibility against the caller's
``supported_capabilities`` and discard that set. Do not load
entrypoints or inspect git.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .models import (
    ALLOWED_REQUIRES,
    SHA_PATTERN,
    BundleSpec,
    CatalogError,
    ComponentKind,
    ComponentSpec,
)

_TOP_LEVEL_KEYS = frozenset({"requires", "component", "component_root"})
_COMPONENT_KEYS = frozenset({"kind", "name", "path", "entrypoint"})
_BUNDLE_KEYS = frozenset({"kind", "name", "members", "requires"})


@dataclass(frozen=True, slots=True)
class ResolvedBundle:
    """Bundle name plus member specs and the ordered ``requires`` union.

    Built by :meth:`HarnessCatalog.resolve_bundle`. Not stored on
    :class:`HarnessCatalog`. ``requires`` is catalog-level tokens first,
    then any bundle token not already seen (duplicates dropped, order
    kept).

    Attributes:
        name: Bundle name (``daily``, ``dev``, ...).
        members: Member :class:`ComponentSpec` values, in catalog order.
        requires: First-seen union of catalog then bundle tokens.
    """

    name: str
    members: tuple[ComponentSpec, ...]
    requires: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HarnessCatalog:
    """Immutable catalog of components and bundles for one commit SHA.

    Identity is only ``sha`` (40-character lowercase git commit
    fingerprint). Direct construction runs the language gate (valid SHA,
    known ``requires`` tokens, unique ids). Bundles are optional: zero
    bundles means the catalog is one implicit package of every
    component. Eligibility against a runtime capability set is *not* a
    field and is *not* checked here; only :func:`load_harness_catalog`
    does that.

    Attributes:
        sha: Caller-supplied 40-character lowercase hex git SHA.
        requires: Catalog-level capability tokens (language-gate set).
        components: Leaf :class:`ComponentSpec` rows (no bundles).
        bundles: :class:`BundleSpec` rows (author-chosen names; may be
            empty).
        component_root: Tree-relative POSIX directory every component
            ``path`` in this catalog resolves under, or ``""`` for the
            tree itself. Declared last because the four fields above
            carry no defaults. Component paths are carried beside it and
            are never rewritten to include it.

    Raises:
        CatalogError: Invalid SHA, unknown requires token, duplicate
            id or bundle name, a bundle member id that is not in
            ``components``, or a ``component_root`` that could escape
            the tree.
    """

    sha: str
    requires: tuple[str, ...]
    components: tuple[ComponentSpec, ...]
    bundles: tuple[BundleSpec, ...]
    component_root: str = ""

    def __post_init__(self) -> None:
        _validate_component_root(self.component_root)
        if SHA_PATTERN.fullmatch(self.sha) is None:
            raise CatalogError(f"invalid sha: {self.sha!r}")
        for token in self.requires:
            if token not in ALLOWED_REQUIRES:
                raise CatalogError(f"unknown requires token: {token!r}")
        names = tuple(bundle.name for bundle in self.bundles)
        name_set = set(names)
        ids = tuple(spec.id for spec in self.components)
        if len(ids) != len(set(ids)):
            raise CatalogError("duplicate component id")
        if len(names) != len(name_set):
            raise CatalogError("duplicate bundle name")
        id_set = set(ids)
        for bundle in self.bundles:
            for member in bundle.members:
                if member not in id_set:
                    raise CatalogError(f"unknown bundle member: {member!r}")

    def get(self, component_id: str) -> ComponentSpec:
        """Return the component whose ``id`` is ``component_id``.

        Looks only at ``components``. Bundle names are not ids:
        ``get("daily")`` fails even when a bundle named ``daily`` exists
        (use :meth:`get_bundle`).

        Args:
            component_id: Component id (``skill.daily``, not ``daily``).

        Returns:
            The matching :class:`ComponentSpec`.

        Raises:
            CatalogError: No component has that id. The message contains
                ``unknown-id``.
        """

        for spec in self.components:
            if spec.id == component_id:
                return spec
        raise CatalogError(f"unknown-id: {component_id!r}")

    def get_bundle(self, name: str) -> BundleSpec:
        """Return the bundle named ``name``.

        Args:
            name: Bundle name (``daily``, ``dev``, ...).

        Returns:
            The matching :class:`BundleSpec`.

        Raises:
            CatalogError: If no bundle has that name. The message
                contains ``unknown-bundle``.
        """

        for bundle in self.bundles:
            if bundle.name == name:
                return bundle
        raise CatalogError(f"unknown-bundle: {name!r}")

    def resolve_bundle(self, name: str) -> ResolvedBundle:
        """Turn a bundle's member ids into specs and union ``requires``.

        ``requires`` is ``self.requires`` followed by that bundle's
        tokens that have not already appeared, preserving first-seen
        order. Eligibility is not checked again; a catalog that loaded
        successfully already passed that gate.

        Args:
            name: Bundle name to resolve.

        Returns:
            A :class:`ResolvedBundle` (one-off view, not stored on this
            catalog).

        Raises:
            CatalogError: No bundle has that name (``unknown-bundle``).
        """

        bundle = self.get_bundle(name)
        by_id = {spec.id: spec for spec in self.components}
        members = tuple(by_id[member_id] for member_id in bundle.members)
        requires = tuple(dict.fromkeys((*self.requires, *bundle.requires)))
        return ResolvedBundle(name=bundle.name, members=members, requires=requires)

    def enabled_components(
        self, names: tuple[str, ...] | None
    ) -> tuple[ComponentSpec, ...]:
        """Return the first-seen union of components selected by *names*.

        ``()`` is an explicit empty view. ``None`` means every bundle,
        or every component when the catalog has no bundles. Unknown
        names raise :class:`CatalogError` containing ``unknown-bundle``.
        Components listed in more than one selected bundle appear once,
        in enable-list then member order.

        Args:
            names: Bundle names to include, ``None`` for all, or ``()``
                for none.

        Returns:
            Selected :class:`ComponentSpec` rows.

        Raises:
            CatalogError: A name is not a bundle in this catalog. The
                message contains ``unknown-bundle``.
        """
        if names == ():
            return ()
        if not self.bundles:
            if names is None:
                return self.components
            raise CatalogError("unknown-bundle: known: []")
        selected = (
            names
            if names is not None
            else tuple(bundle.name for bundle in self.bundles)
        )
        known = {bundle.name for bundle in self.bundles}
        unknown = tuple(name for name in selected if name not in known)
        if unknown:
            listed = ", ".join(repr(name) for name in sorted(known))
            raise CatalogError(f"unknown-bundle: {unknown[0]!r}; known: [{listed}]")
        kept: dict[str, ComponentSpec] = {}
        for name in selected:
            for spec in self.resolve_bundle(name).members:
                kept.setdefault(spec.id, spec)
        return tuple(kept.values())


def load_harness_catalog(
    tree: str | Path,
    sha: str,
    supported_capabilities: frozenset[str],
) -> HarnessCatalog:
    """Load ``{tree}/harness.toml`` through the language gate, then eligibility.

    ``harness.toml`` is the TOML catalog at the checkout root. ``sha`` is
    the caller's 40-character lowercase git commit SHA (Secure Hash
    Algorithm fingerprint); it is stored as catalog identity and is not
    read from the file.

    Two gates, in order:

    1. Language — parse the file and construct :class:`HarnessCatalog`.
       Unknown keys, unknown kinds, or a ``requires`` token outside
       ``ALLOWED_REQUIRES`` fail here. ``ALLOWED_REQUIRES`` is not the
       default for ``supported_capabilities`` and is not the eligibility
       universe.
    2. Eligibility — every token in ``catalog.requires`` and in every
       ``bundle.requires`` must be a subset of
       ``supported_capabilities``. Then that set is discarded; it is
       not stored on the catalog. An empty ``frozenset()`` is legal and
       makes any non-empty ``requires`` ineligible. A token that is not
       in ``ALLOWED_REQUIRES`` still fails the language gate even if it
       appears in ``supported_capabilities`` (the message will not
       contain ``ineligible``).

    This function does not import entrypoints, does not check that
    component paths exist on disk, and does not talk to git.

    The optional ``component_root`` key is parsed here and carried onto
    the catalog unchanged; it never moves ``harness.toml`` itself, which
    always sits directly in ``tree``. This is also the only gate that can
    see the key's *presence*, so it additionally refuses
    ``component_root = ""``: :meth:`HarnessCatalog.__post_init__`
    receives ``""`` from a defaulted field and from a written one alike
    and cannot tell them apart.

    Args:
        tree: Directory that contains ``harness.toml``. Named for
            ``Checkout.tree``, which is what every caller passes; the
            catalog's own ``component_root`` is a different directory and
            is never spelled ``root`` here.
        sha: 40-character lowercase hex git commit SHA.
        supported_capabilities: Capability tokens this process can honor.
            Required (no default).

    Returns:
        A frozen :class:`HarnessCatalog` whose ``sha`` equals the
        ``sha`` argument.

    Raises:
        CatalogError: Missing file, invalid TOML, unknown field or kind,
            language-gate failure, an empty ``component_root`` written
            out, or an ineligible ``requires`` token (message contains
            ``ineligible``).
        TypeError: If ``supported_capabilities`` is omitted.
    """

    path = Path(tree) / "harness.toml"
    if not path.is_file():
        raise CatalogError(f"missing harness.toml at {path}")
    try:
        parsed: object = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CatalogError(f"invalid harness.toml: {exc}") from exc
    table = _as_table(parsed, "harness.toml")
    _reject_unknown(table, _TOP_LEVEL_KEYS, "harness.toml")
    requires = _require_str_tuple(table.get("requires", []), "requires")
    component_root = ""
    if "component_root" in table:
        component_root = _require_string(table["component_root"], "component_root")
        if not component_root:
            raise CatalogError("component_root must not be empty when present")
    components, bundles = _parse_component_rows(table.get("component", []))
    catalog = HarnessCatalog(
        sha=sha,
        requires=requires,
        components=components,
        bundles=bundles,
        component_root=component_root,
    )
    _assert_eligible(catalog, supported_capabilities)
    return catalog


def _parse_component_rows(
    raw_rows: object,
) -> tuple[tuple[ComponentSpec, ...], tuple[BundleSpec, ...]]:
    if not isinstance(raw_rows, list):
        raise CatalogError("component must be a list of tables")
    components: list[ComponentSpec] = []
    bundles: list[BundleSpec] = []
    for raw_row in raw_rows:
        parsed = _parse_row(_as_table(raw_row, "component"))
        if isinstance(parsed, BundleSpec):
            bundles.append(parsed)
        else:
            components.append(parsed)
    return tuple(components), tuple(bundles)


def _parse_row(row: dict[str, object]) -> ComponentSpec | BundleSpec:
    kind_value = row.get("kind")
    if not isinstance(kind_value, str):
        raise CatalogError("component row is missing kind")
    # Wire kind "bundle" is not a ComponentKind; split before the enum.
    if kind_value == "bundle":
        return _parse_bundle_row(row)
    return _parse_component_row(row, kind_value)


def _parse_bundle_row(row: dict[str, object]) -> BundleSpec:
    _reject_unknown(row, _BUNDLE_KEYS, "bundle")
    name = _require_string(row.get("name"), "bundle name")
    members = _require_str_tuple(row.get("members"), "bundle members")
    requires = _require_str_tuple(row.get("requires", []), "bundle requires")
    return BundleSpec(name=name, members=members, requires=requires)


def _parse_component_row(row: dict[str, object], kind_value: str) -> ComponentSpec:
    _reject_unknown(row, _COMPONENT_KEYS, "component")
    try:
        kind = ComponentKind(kind_value)
    except ValueError as exc:
        raise CatalogError(f"unknown component kind: {kind_value!r}") from exc
    name = _require_string(row.get("name"), "component name")
    path = _require_string(row.get("path"), "component path")
    raw_entrypoint = row.get("entrypoint")
    entrypoint = (
        None
        if raw_entrypoint is None
        else _require_string(raw_entrypoint, "entrypoint")
    )
    return ComponentSpec(
        kind=kind,
        name=name,
        id=f"{kind}.{name}",
        path=path,
        entrypoint=entrypoint,
    )


def _validate_component_root(value: str) -> None:
    r"""Refuse a ``component_root`` that could escape the tree it joins onto.

    ``component_root`` is always the *first* component joined onto a
    checkout tree, which is what makes each clause below load-bearing:

    * A backslash is not POSIX.
    * Any ``:`` at all. ``"D:evil"`` carries no ``..``, holds no
      backslash, and ``Path("D:evil").is_absolute()`` is ``False`` on
      POSIX -- yet ``PureWindowsPath("C:/store/tree") / "D:evil"`` is
      ``WindowsPath("D:evil")``: a drive on the first joined component
      resets the anchor and discards the base. CI runs ``windows-latest``.
    * Absolute, spelled as **two** clauses.
      ``PureWindowsPath("/plugins").is_absolute()`` is ``False``, so
      ``is_absolute()`` alone misses ``/plugins`` -- while
      ``PureWindowsPath("C:/store/tree") / "/plugins"`` is
      ``WindowsPath("C:/plugins")``. This is the same pair
      ``_validate_component_path`` already carries, for the same reason.
    * A ``".."`` **or ``"."``** segment, found by splitting on ``"/"``
      rather than reading ``PurePath.parts``, which silently drops ``.``
      and collapses ``//`` and would therefore miss both. Empty segments
      are deliberately allowed: ``"plugins/mol/"`` and ``"plugins//mol"``
      both collapse to the same directory and escape nothing.

    **Path separators are deliberately NOT refused.** ``"plugins/mol"``
    is two segments and must stay legal -- that is the entire point of
    the key, and the layout it exists to support.
    ``ImmutableGitStore._sha_dir`` and ``harness.pointer_path`` refuse
    separators because a SHA and a source name are single segments; this
    is the opposite case, so restoring that check here would break the
    only layout ``component_root`` was added for.

    ``""`` is legal: it means "the tree itself", and at construction time
    a defaulted ``""`` and a written ``""`` are the same string. Refusing
    the key *written* empty belongs to :func:`load_harness_catalog`, the
    only gate that can still see presence.

    Args:
        value: The catalog's ``component_root``, as authored.

    Raises:
        CatalogError: The value could escape the tree. The message
            carries ``value`` in ``repr`` form.
    """

    if "\\" in value:
        raise CatalogError(f"component_root must be POSIX (no backslash): {value!r}")
    if ":" in value:
        raise CatalogError(f"component_root must not contain ':': {value!r}")
    if Path(value).is_absolute() or value.startswith("/"):
        raise CatalogError(f"component_root must be relative: {value!r}")
    segments = value.split("/")
    if ".." in segments or "." in segments:
        raise CatalogError(
            f"component_root must not contain '.' or '..' segments: {value!r}"
        )


def _assert_eligible(
    catalog: HarnessCatalog,
    supported_capabilities: frozenset[str],
) -> None:
    needed = set(catalog.requires)
    for bundle in catalog.bundles:
        needed.update(bundle.requires)
    unsupported = needed - supported_capabilities
    if unsupported:
        tokens = ", ".join(sorted(unsupported))
        raise CatalogError(f"ineligible requires: {tokens}")


def _reject_unknown(
    data: dict[str, object], allowed: frozenset[str], where: str
) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise CatalogError(f"unknown field(s) in {where}: {', '.join(unknown)}")


def _as_table(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CatalogError(f"{where} must be a table")
    table: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise CatalogError(f"{where} keys must be strings")
        table[key] = item
    return table


def _require_string(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise CatalogError(f"{where} must be a string")
    return value


def _require_str_tuple(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CatalogError(f"{where} must be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise CatalogError(f"{where} must be a list of strings")
        items.append(item)
    return tuple(items)
