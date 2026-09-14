"""Placing catalog-declared component files into a host's directories.

``molmcp harness sync`` publishes a commit tree and moves a source's
activation pointer onto it, and ``molmcp init`` then has to install whatever
that tree's catalog declares. This module is the last link of that chain and
owns exactly one fact: which host directory a component *kind* belongs in.

Nothing here learns what a catalog is. The caller owns catalog grammar — it
strips each kind's path prefix, joins the component root of the source a row
came from, and hands over one :class:`ComponentFile` per file to place: four
plain values, no catalog type among them. ``host/`` answers with the one
thing the caller cannot know, the kind table below, which is why
:class:`ComponentFile` validates no kind at construction and
:func:`place_components` refuses an unknown one.

Two rules are load-bearing:

* **The tree is never globbed.** :func:`place_components` copies the files it
  is handed and reads no other path, so a file sitting beside a declared
  component that no catalog row mentions is not a component and cannot reach
  a host.
* **The managed usage skill is never clobbered.**
  :func:`~molmcp.host.install.install_skill` owns the constitution under
  :data:`~molmcp.host.layout.SKILL_NAME`; a row aimed there is skipped, which
  is the protection :func:`~molmcp.host.install.materialize_daily` already
  applies on the checkout route.

This module is Layer 2 and imports the standard library only. Nothing under
``molmcp.host`` may import ``molmcp.components``, ``molmcp.harness``,
``molmcp.client_config``, ``molmcp.cli``, ``molmcp.server``,
``molmcp.providers``, or ``molmcp.discovery``: a component arrives here as
four stdlib values precisely so none of them is needed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from .layout import Host, HostLayout, layout_for, remap_frontmatter

SKIP_NO_HOST_DESTINATION = "kind has no host destination"
"""Why a ``provider`` or ``overlay`` row is reported but not installed.

A provider is a plane ``molmcp serve`` mounts and an overlay is knowledge the
discovery index reads; neither is a file any host keeps, so both are declared
by a catalog and refused by this seam.
"""

SKIP_MANAGED_USAGE_SKILL = "managed usage skill is owned by molmcp init"
"""Why a row landing inside the managed usage skill is refused.

:func:`~molmcp.host.install.install_skill` writes that constitution, so a
catalog cannot take the name from it however it spells the path.
"""


@dataclass(frozen=True, slots=True)
class ComponentFile:
    """One file a catalog declared, already resolved by the caller.

    Frozen, so a description cannot be edited between the pre-flight pass and
    the copy that trusts it.

    Attributes:
        id: The catalog id, unchanged — ``"skill.daily"``. Used for reports
            and error messages only; nothing is placed by it.
        kind: The catalog kind as a plain string. It is deliberately not
            validated here: which kinds have a host destination is
            :func:`place_components`' table, and that table has one owner.
        relative: POSIX path of the file inside its kind's host directory,
            with the catalog's kind prefix already stripped —
            ``"daily/SKILL.md"``, not ``"skills/daily/SKILL.md"``.
        source: Absolute path of the file to copy, inside the activated
            commit tree. The caller has already joined the component root of
            the source this row came from, so a fold spanning several sources
            resolves every row under its own base.
    """

    id: str
    kind: str
    relative: str
    source: Path


@dataclass(frozen=True, slots=True)
class PlacementReport:
    """What one :func:`place_components` run placed, replaced, and refused.

    A bare tuple of paths would hide the two decisions a run makes, so both
    are recorded: that a destination already existed, and that a component
    was skipped rather than installed.

    Attributes:
        installed: Destinations written, in the order the components were
            given.
        replaced: The subset of *installed* that already existed before the
            run, in the same order. Empty on a first run; equal to
            *installed* on a repeat of the same set.
        skipped: One ``(component id, reason)`` pair per refusal, in input
            order. The reason is :data:`SKIP_NO_HOST_DESTINATION` or
            :data:`SKIP_MANAGED_USAGE_SKILL`.
    """

    installed: tuple[Path, ...]
    replaced: tuple[Path, ...]
    skipped: tuple[tuple[str, str], ...]


_KIND_ROOTS: Mapping[str, Callable[[HostLayout], tuple[str, ...]]] = MappingProxyType(
    {
        # ``skill_dir`` names the managed usage skill itself, so its parent is
        # the host's ``skills/`` — read off the one layout table rather than
        # spelled again here.
        "skill": lambda layout: layout.skill_dir[:-1],
        "agent": lambda layout: layout.agents,
        "rule": lambda layout: layout.rules,
    }
)
"""Which host directory each installable kind belongs in.

The single owner of that question. A caller resolving component paths knows
the catalog's own layout and nothing about a host's, which is why this table
lives on this side of the seam.
"""

_KINDS_WITHOUT_HOST_DESTINATION: frozenset[str] = frozenset({"provider", "overlay"})
"""Declared kinds that no host directory holds."""


def _host_root(component: ComponentFile, layout: HostLayout) -> tuple[str, ...] | None:
    """Layout path parts of the directory that holds *component*'s kind.

    Args:
        component: The description whose ``kind`` is being placed.
        layout: The target host's layout record.

    Returns:
        The path tuple of that kind's host directory, relative to home, or
        ``None`` when the kind has no host destination at all.

    Raises:
        ValueError: If the kind is not one of the five catalog kinds. A sixth
            means the caller is broken, not the file.
    """
    resolve = _KIND_ROOTS.get(component.kind)
    if resolve is not None:
        return resolve(layout)
    if component.kind in _KINDS_WITHOUT_HOST_DESTINATION:
        return None
    known = ", ".join(sorted({*_KIND_ROOTS, *_KINDS_WITHOUT_HOST_DESTINATION}))
    raise ValueError(
        f"component {component.id!r} has unknown kind {component.kind!r}; "
        f"known: {known}"
    )


def _destination(root: Path, component: ComponentFile) -> Path:
    """Resolve *component*'s ``relative`` under *root*, refusing any escape.

    The path is read as POSIX because that is the grammar a catalog is
    written in, and the result is required to sit strictly inside *root*, so
    neither ``..`` nor an absolute path can steer a write out of the host.

    Args:
        root: Absolute host directory for the component's kind.
        component: The description being placed.

    Returns:
        The absolute destination path, uncreated.

    Raises:
        ValueError: If ``relative`` is empty, absolute, or leaves *root*.
    """
    relative = PurePosixPath(component.relative)
    destination = root.joinpath(*relative.parts)
    escapes = (
        relative.is_absolute()
        or ".." in relative.parts
        or root not in destination.parents
    )
    if escapes:
        raise ValueError(
            f"component {component.id!r} places {component.relative!r} outside {root}"
        )
    return destination


def _require_file(component: ComponentFile) -> None:
    """Fail unless *component*'s source is an existing regular file.

    Args:
        component: The description being placed.

    Raises:
        FileNotFoundError: If the source is missing or is not a file, naming
            both the component id and the path so the broken catalog row is
            identifiable from the message alone.
    """
    if not component.source.is_file():
        raise FileNotFoundError(
            f"component {component.id!r} is not a file: {component.source}"
        )


def place_components(
    host: Host, components: Sequence[ComponentFile]
) -> PlacementReport:
    """Install every component that has a host destination into *host*.

    Each component is copied to its kind's host directory, in the order
    given. Only the files handed over are read: no directory is walked, so
    what reaches the host is exactly what a catalog declared. Every source is
    checked in a pre-flight pass before the first byte is written, so one
    unresolvable row leaves no partial set behind. Re-running with the same
    components rewrites the same destinations and reports them as
    ``replaced``.

    Args:
        host: One of the known hosts. Validated first, as in every other
            primitive of this family, so a broken host name raises even when
            there is nothing to place.
        components: Descriptions of the files to place, already resolved
            against the activated commit tree by the caller.

    Returns:
        A :class:`PlacementReport` naming what was written, what it replaced,
        and which components were refused with which reason.

    Raises:
        ValueError: If *host* is unknown, if a component's kind is unknown,
            or if a component's ``relative`` path escapes its host directory.
        FileNotFoundError: If a component's source is missing or is not a
            file; raised before anything is written.
        OSError: If a destination cannot be written.
    """
    layout = layout_for(host)
    home = Path.home()
    managed = home.joinpath(*layout.skill_dir)

    planned: list[tuple[ComponentFile, Path]] = []
    skipped: list[tuple[str, str]] = []
    for component in components:
        parts = _host_root(component, layout)
        if parts is None:
            skipped.append((component.id, SKIP_NO_HOST_DESTINATION))
            continue
        destination = _destination(home.joinpath(*parts), component)
        if destination == managed or managed in destination.parents:
            skipped.append((component.id, SKIP_MANAGED_USAGE_SKILL))
            continue
        planned.append((component, destination))

    for component, _ in planned:
        _require_file(component)
    rewritten: list[tuple[ComponentFile, Path, str]] = []
    for component, destination in planned:
        rewritten.append(
            (component, destination, component.source.read_text(encoding="utf-8"))
        )

    installed: list[Path] = []
    replaced: list[Path] = []
    for component, destination, text in rewritten:
        if destination.exists():
            replaced.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(remap_frontmatter(text, host), encoding="utf-8")
        installed.append(destination)

    return PlacementReport(
        installed=tuple(installed),
        replaced=tuple(replaced),
        skipped=tuple(skipped),
    )


__all__ = [
    "SKIP_MANAGED_USAGE_SKILL",
    "SKIP_NO_HOST_DESTINATION",
    "ComponentFile",
    "PlacementReport",
    "place_components",
]
