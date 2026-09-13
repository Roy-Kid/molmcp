"""Grammar for one component or bundle row in a harness catalog.

This module owns the *language gate* for a single row: known kinds,
kebab-case names, POSIX paths, and ``requires`` tokens. It does not
parse TOML and does not decide *eligibility* (whether this process can
honor those tokens). ``ComponentSpec`` is one installable piece;
``BundleSpec`` is a named group of those pieces. ``ComponentKind`` has
no ``bundle`` member.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType


class CatalogError(ValueError):
    """Raised when a harness catalog cannot be accepted.

    Three raisers, and the third is worth naming because it is the first
    outside this package and outside a catalog object. Inside it: the
    language gate (unknown key, unknown kind, token not in
    ``ALLOWED_REQUIRES``, invalid SHA, and so on) and the eligibility
    check (a grammatically valid ``requires`` token the caller cannot
    honor). Eligibility failures are the ones whose message contains
    ``ineligible``.

    Outside it: :class:`molmcp.harness.ComponentFold`, which folds several
    catalogs into one served set. Its ``__post_init__`` raises this type
    when its checkouts and their ``component_root`` strings disagree, and
    its ``root_for`` raises it for a source the fold was not built from,
    with ``unknown-source`` in the message — the same register
    :meth:`HarnessCatalog.get`, :meth:`HarnessCatalog.get_bundle`, and
    :meth:`HarnessCatalog.enabled_components` use.
    So the type does not mean "one catalog file was rejected"; it means a
    harness catalog, or something assembled directly out of several of
    them, cannot be accepted. A second error family for that one message
    was considered and refused: the register genuinely matches.
    """


class ComponentKind(StrEnum):
    """Kind of one installable *component* (not a bundle).

    A component is a single piece declared in ``harness.toml``. A bundle
    is a named group of those pieces and is a separate type
    (:class:`BundleSpec`). ``ComponentKind("bundle")`` raises
    ``ValueError``.

    Attributes:
        SKILL: Instruction file an agent reads (path under ``skills/``).
        AGENT: Agent definition file (path under ``agents/``).
        RULE: Constraint file (path under ``rules/``).
        PROVIDER: MCP provider module; requires an entrypoint.
        OVERLAY: Discovery overlay module; requires an entrypoint.
    """

    SKILL = "skill"
    AGENT = "agent"
    RULE = "rule"
    PROVIDER = "provider"
    OVERLAY = "overlay"


#: Git commit SHA: 40 lowercase hexadecimal characters, nothing else.
#: SHA (Secure Hash Algorithm) here is the full commit fingerprint the
#: caller supplies as catalog identity. Uppercase hex, short SHAs, and
#: refs (``main``, tags) do not match.
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
#: Component and bundle names: kebab-case starting with a lowercase
#: letter (``daily``, ``molvis``). Owned here; not imported from the
#: provider SDK.
COMPONENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
#: ``module:object`` string; this module never imports it.
_ENTRYPOINT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
_MEMBER_PATTERN = re.compile(r"^(skill|agent|rule|provider|overlay)\.[a-z][a-z0-9-]*$")
#: Tokens a ``requires`` list may mention (language gate only).
#: A token outside this set is invalid TOML, even if the caller put it
#: in ``supported_capabilities``. This set is not the default for that
#: argument and is not stored as an eligibility universe on the catalog.
ALLOWED_REQUIRES = frozenset({"provider-sdk", "harness-catalog"})
#: POSIX directory prefix each ``ComponentKind`` path must start with.
#: The remainder after the prefix must be non-empty (``skills/`` alone
#: is rejected).
KIND_PATH_PREFIX = MappingProxyType(
    {
        ComponentKind.SKILL: "skills/",
        ComponentKind.AGENT: "agents/",
        ComponentKind.RULE: "rules/",
        ComponentKind.PROVIDER: "providers/",
        ComponentKind.OVERLAY: "overlays/",
    }
)

_ENTRYPOINT_KINDS = frozenset({ComponentKind.PROVIDER, ComponentKind.OVERLAY})


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    """One installable component declared in a harness catalog.

    A component is a single piece (skill, agent, rule, provider, or
    overlay). It is not a bundle. Construction rejects bad values; it
    does not rewrite them. Frozen means the fields cannot change after
    construction.

    An *entrypoint* is a ``module:object`` string (``pkg.mod:Class``)
    naming a Python object to import later. It is required for
    ``provider`` and ``overlay``, and must be ``None`` for every other
    kind. This class never imports that string.

    Attributes:
        kind: One :class:`ComponentKind` value (never bundle).
        name: Kebab-case name matching ``COMPONENT_NAME_PATTERN``.
        id: Must equal ``f"{kind}.{name}"`` (TOML has no ``id`` key).
        path: Relative POSIX path under that kind's ``KIND_PATH_PREFIX``,
            with no backslash, no ``..`` segment, and at least one
            character after the prefix.
        entrypoint: ``module:object`` string, or ``None``.

    Raises:
        CatalogError: If any field fails the grammar above.
    """

    kind: ComponentKind
    name: str
    id: str
    path: str
    entrypoint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ComponentKind):
            raise CatalogError("kind must be a ComponentKind")
        if COMPONENT_NAME_PATTERN.fullmatch(self.name) is None:
            raise CatalogError(f"invalid component name: {self.name!r}")
        expected_id = f"{self.kind}.{self.name}"
        if self.id != expected_id:
            raise CatalogError(f"id must be {expected_id!r}, got {self.id!r}")
        _validate_component_path(self.kind, self.path)
        if self.kind in _ENTRYPOINT_KINDS:
            if (
                not isinstance(self.entrypoint, str)
                or _ENTRYPOINT_PATTERN.fullmatch(self.entrypoint) is None
            ):
                raise CatalogError(
                    f"{self.kind} entrypoint must be a module:object string"
                )
        elif self.entrypoint is not None:
            raise CatalogError(f"{self.kind} entrypoint must be None")


@dataclass(frozen=True, slots=True)
class BundleSpec:
    """Named grouping of component ids, with optional ``requires`` tokens.

    A bundle is a preset such as ``daily`` or ``dev``. It is not a
    :class:`ComponentKind` and cannot appear as a member of another
    bundle. ``requires`` lists capability tokens the file is allowed to
    name (language gate). Whether this process can honor them is
    eligibility, checked later by ``load_harness_catalog``.

    Attributes:
        name: Kebab-case bundle name (same pattern as component names).
        members: Non-empty tuple of ``kind.name`` ids
            (``skill.daily``, never ``bundle.daily``).
        requires: Tokens, each of which must be in ``ALLOWED_REQUIRES``.

    Raises:
        CatalogError: Empty members, malformed member id, unknown
            requires token, or invalid name.
    """

    name: str
    members: tuple[str, ...]
    requires: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if COMPONENT_NAME_PATTERN.fullmatch(self.name) is None:
            raise CatalogError(f"invalid bundle name: {self.name!r}")
        if not self.members:
            raise CatalogError("bundle members must not be empty")
        for member in self.members:
            if _MEMBER_PATTERN.fullmatch(member) is None:
                raise CatalogError(f"invalid bundle member: {member!r}")
        for token in self.requires:
            if token not in ALLOWED_REQUIRES:
                raise CatalogError(f"unknown requires token: {token!r}")


def _validate_component_path(kind: ComponentKind, path: str) -> None:
    if not path:
        raise CatalogError("path must not be empty")
    if "\\" in path:
        raise CatalogError("path must be POSIX (no backslash)")
    if Path(path).is_absolute() or path.startswith("/"):
        raise CatalogError("path must be relative")
    if ".." in path.split("/"):
        raise CatalogError("path must not contain '..' segments")
    prefix = KIND_PATH_PREFIX[kind]
    if not path.startswith(prefix) or len(path) <= len(prefix):
        raise CatalogError(f"path must start with {prefix!r} and continue")
