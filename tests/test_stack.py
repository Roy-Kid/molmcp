"""FastMCP composition: core + namespaced provider mounts."""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from molmcp import CollectionIndex, create_plane, create_stack, runtime, server
from molmcp import harness as harness_module
from molmcp.components import (
    ALLOWED_REQUIRES,
    BundleSpec,
    ComponentKind,
    ComponentSpec,
    HarnessCatalog,
)
from molmcp.components.locator import LocatorError
from molmcp.config import AppConfig, ConfigurationError
from molmcp.provider_worker.worker import WorkerProvider
from molmcp.settings import HarnessSource, Settings


class _Vis:
    name = "molvis"

    def register(self, mcp: FastMCP) -> None:
        @mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
        def open() -> str:
            """Open a viewer session."""
            return "session"


async def test_stack_namespaces_provider_tools():
    stack = create_stack(
        collection=CollectionIndex([]),
        providers=[_Vis()],
        discover_entry_points=False,
    )
    assert stack.name == "molcrafts"
    names = {tool.name for tool in await stack.list_tools()}
    assert "packages" in names
    assert "open" in names
    assert "molvis_open" in names


async def test_stack_disable_skips_mount():
    stack = create_stack(
        collection=CollectionIndex([]),
        providers=[_Vis()],
        disable=["molvis"],
        discover_entry_points=False,
    )
    names = {tool.name for tool in await stack.list_tools()}
    assert "molvis_open" not in names
    assert "packages" in names


async def test_single_provider_plane_stays_bare():
    server = create_plane(
        "molvis",
        provider=_Vis(),
        discover_entry_points=False,
    )
    assert server.name == "molvis"
    names = {tool.name for tool in await server.list_tools()}
    assert names == {"open"}


# --- autonomous harness wiring (spec 08) ----------------------------------
#
# Every outbound seam create_stack could reach for is faked here: no git, no
# network, no environment. Each seam is patched on the module that *names*
# it, which is now two modules: ``molmcp.harness`` holds the checkout arms
# and every collaborator they construct (Activation, ImmutableGitStore,
# GitHubTransport, load_harness_catalog, WorkerProvider), while
# ``molmcp.server`` keeps what create_stack itself calls (load_settings,
# build_collection, discover_providers). Each arm resolves its collaborators
# from its own module globals, so a name patched on the module that merely
# imports that arm would never be read.

_SHA = "0123456789abcdef0123456789abcdef01234567"
#: A second, distinct commit. One SHA per source is the whole point of a
#: per-source pointer file: two named sources may be activated at two
#: different commits, and a seam holding one ``current`` for all of them
#: could not express that at all.
_OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
_SOURCE = HarnessSource(name="official", locator="molcrafts/harness@main")
_OTHER = HarnessSource(name="private", locator="acme/tooling@trunk")
_CAPABILITIES = frozenset({"provider-sdk", "harness-catalog"})
_SKILL = ComponentSpec(
    kind=ComponentKind.SKILL,
    name="daily",
    id="skill.daily",
    path="skills/daily.md",
)

#: One recorded call: positional arguments, then keyword arguments.
_Call = tuple[tuple[object, ...], dict[str, object]]


def _config(tmp_path: Path) -> AppConfig:
    """Config whose ``cache_dir`` is the already-resolved root of the store."""
    return AppConfig.from_dict(
        {"schema_version": "2", "cache_dir": str(tmp_path / "cache")},
        workspace_root=tmp_path,
    )


def _catalog(
    *components: ComponentSpec, sha: str = _SHA, component_root: str = ""
) -> HarnessCatalog:
    """A real catalog: the two required bundles plus *components*.

    ``sha`` is the commit the catalog claims to describe. It matters only
    when two sources are activated at two different commits, because the SHA
    is the one argument a faked ``load_harness_catalog`` can tell two
    checkouts apart by — every checkout in this suite shares one tree.

    ``component_root`` defaults to the absent key, so every call site written
    before it describes a rootless catalog and reads exactly as it did.
    """
    leaves = (_SKILL, *components)
    ids = tuple(spec.id for spec in leaves)
    return HarnessCatalog(
        sha=sha,
        requires=(),
        components=leaves,
        bundles=(
            BundleSpec(name="daily", members=ids),
            BundleSpec(name="dev", members=ids),
        ),
        component_root=component_root,
    )


def _provider_component(
    path: str = "providers/demo/plane.py",
    *,
    name: str = "demo",
    entrypoint: str = "demo.plane:DemoProvider",
) -> ComponentSpec:
    """A checkout provider row whose ``id`` differs from its ``name``.

    ``entrypoint`` is the only field two sources' ``provider.demo`` rows can
    differ in *and* have the difference reach an assertion: ``name`` and
    ``id`` are the contested key itself, and ``path`` resolves through the
    one shared fake tree, so two spellings of it land on one import root.
    """
    return ComponentSpec(
        kind=ComponentKind.PROVIDER,
        name=name,
        id=f"provider.{name}",
        path=path,
        entrypoint=entrypoint,
    )


def _overlay_component(
    path: str = "overlays/demo.py",
    *,
    name: str = "demo",
    entrypoint: str = "demo:make_overlay",
) -> ComponentSpec:
    """A checkout overlay row — one seed for the overlay arm to hand on.

    Nothing here is ever imported: ``_wire`` fakes the loader itself, so the
    row only has to be a real ``ComponentSpec`` of the kind the overlay fold
    keeps. What a seed is imported *from* is this file's subject, and that
    base is recorded rather than resolved; the real loader is driven against
    a real tree in ``tests/test_runtime.py``.
    """
    return ComponentSpec(
        kind=ComponentKind.OVERLAY,
        name=name,
        id=f"overlay.{name}",
        path=path,
        entrypoint=entrypoint,
    )


def _checkout(tmp_path: Path, *, component_root: str = "") -> Path:
    """A tree holding ``providers/demo/`` as a directory and a module in it.

    ``component_root`` plants that directory under the catalog-declared root
    instead of at the top of the tree, so ``_import_root``'s
    a-directory-is-used-as-it-stands branch answers about the base the fold
    resolved rather than about the tree. The return value stays the *tree* —
    what a store hands back — because that is what ``_wire`` is given.
    """
    tree = tmp_path / "tree"
    base = tree.joinpath(*component_root.split("/")) if component_root else tree
    package = base / "providers" / "demo"
    package.mkdir(parents=True)
    (package / "plane.py").write_text("", encoding="utf-8")
    return tree


#: ``user.name`` / ``user.email`` for the one commit ``_git_checkout`` makes.
#: Passed per invocation rather than configured, so no developer's global git
#: identity is read and none is written into ``tmp_path``.
_GIT_IDENTITY = (
    "-c",
    "user.name=molmcp tests",
    "-c",
    "user.email=tests@molmcp.invalid",
)


def _git_checkout(root: Path) -> Path:
    """Create *root* as a real one-commit git repository and return it.

    A ``path`` source is complete only when it names a checkout, so the
    tests for the completed case need an actual repository rather than a
    directory: ``git init`` is the whole difference between this helper and
    :func:`_checkout` above, and it is the difference the locator now reads.

    Mirrored from ``tests/test_components/test_git.py``'s ``_init`` /
    ``_commit`` rather than imported from it. Those are private names in a
    module this change does not touch, and importing them would make a
    refactor of the transport's own tests break the composition tests; the
    three lines are cheaper than the coupling.
    """
    root.mkdir(parents=True, exist_ok=True)
    _run_git(root, "init", "-q", "--initial-branch=main")
    (root / "harness.toml").write_text("", encoding="utf-8")
    _run_git(root, "add", "-A")
    _run_git(root, *_GIT_IDENTITY, "commit", "--no-gpg-sign", "-q", "-m", "first")
    return root


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


class _Marker:
    """An in-tree provider that registers one identifiable tool."""

    def __init__(self, name: str, tool: str = "intree") -> None:
        self.name = name
        self._tool = tool

    def register(self, mcp: FastMCP) -> None:
        @mcp.tool(name=self._tool, annotations=ToolAnnotations(read_only_hint=True))
        def marker() -> str:
            """Prove which provider object was mounted under this namespace."""
            return self._tool


class _FakeWorker:
    """Stand-in for ``WorkerProvider`` recording the constructor mapping."""

    def __init__(self, *, name: str, entrypoint: str, path: str | Path) -> None:
        self.name = name
        self.entrypoint = entrypoint
        self.path = path

    def probe(self) -> bool:
        return True

    def register(self, mcp: FastMCP) -> None:
        @mcp.tool(name="worker", annotations=ToolAnnotations(read_only_hint=True))
        def worker() -> str:
            """Prove the checkout worker, not the in-tree plane, was mounted."""
            return self.name

    def __getattr__(self, item: str) -> object:
        if item == "close":
            raise AssertionError("spec 08 must not reach for WorkerProvider.close")
        raise AttributeError(item)


class _FakeTransport:
    """GitHub transport stand-in that refuses to touch the network."""

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        raise AssertionError("serve must not resolve a ref to a commit")

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        raise AssertionError("serve must not fetch an archive")


class _FakeStore:
    """Immutable store stand-in over a checkout that is already on disk."""

    def __init__(
        self,
        root: str | Path,
        transport: object,
        *,
        tree: Path | None,
        published: bool,
    ) -> None:
        self.root = Path(root)
        self.transport = transport
        self._tree = tree
        self._published = published

    def has(self, sha: str) -> bool:
        return self._published

    def tree_path(self, sha: str) -> Path:
        if not self._published or self._tree is None:
            raise AssertionError(f"tree_path asked for unpublished {sha!r}")
        return self._tree

    def publish(self, sha: str, *, owner: str, repo: str) -> Path:
        raise AssertionError("serve must not clone a missing sha")


class _FakeActivation:
    """Pointer view with a fixed ``current`` whose writes are all refused."""

    def __init__(self, current: str | None) -> None:
        self.current = current
        self.staged: str | None = None
        self.previous: str | None = None

    def stage(self, sha: str) -> None:
        raise AssertionError(f"serve must not stage {sha!r}")

    def promote(self) -> None:
        raise AssertionError("serve must not promote")

    def rollback(self) -> None:
        raise AssertionError("serve must not roll back")


def _pointer_source(pointer: Path) -> str | None:
    """Recover the source name a per-source pointer file belongs to.

    ``<cache>/harness.official.pointer`` is the source named ``official``.
    The one shared ``<cache>/harness.pointer`` names no source and yields
    ``None`` — unambiguously, because the store root beside it is a
    *directory* named ``harness``, so no source name can produce that file.
    """
    name = pointer.name
    if not (name.startswith("harness.") and name.endswith(".pointer")):
        return None
    return name[len("harness.") : -len(".pointer")] or None


class _ActivationSeam:
    """Stand-in for the ``Activation`` class; only ``bind`` is ever used.

    A scalar ``current`` answers the same SHA for every source, which is what
    every single-source caller means and why they need no argument of their
    own. A ``currents`` mapping answers per source, and the source is
    recovered from the *pointer path* handed to :meth:`bind` rather than from
    call order — so an arm that binds the right number of files in the wrong
    order, or one that keeps binding a single shared pointer, cannot satisfy
    it by accident.
    """

    def __init__(
        self,
        wiring: _Wiring,
        current: str | None,
        currents: Mapping[str, str | None] | None,
    ) -> None:
        self._wiring = wiring
        self._current = current
        self._currents = currents

    def bind(
        self,
        path: str | Path,
        *,
        store: object,
        supported_capabilities: object,
    ) -> _FakeActivation:
        pointer = Path(path)
        self._wiring.binds.append(
            {
                "path": pointer,
                "store": store,
                "supported_capabilities": supported_capabilities,
            }
        )
        return _FakeActivation(self._current_for(pointer))

    def _current_for(self, pointer: Path) -> str | None:
        """The SHA the source owning this pointer file is activated at."""
        if self._currents is None:
            return self._current
        source = _pointer_source(pointer)
        if source is None:
            raise AssertionError(
                f"currents= names one SHA per source, but {pointer.name!r} "
                "carries no source name: the arm is still binding one shared "
                "pointer for every source"
            )
        if source not in self._currents:
            raise AssertionError(
                f"currents= was never told about the source {source!r}; "
                f"it names {sorted(self._currents)}"
            )
        return self._currents[source]


class _RecordingCollection(CollectionIndex):
    """Collection that counts its own lifecycle calls."""

    def __init__(self) -> None:
        super().__init__([])
        self.starts = 0
        self.closes = 0

    def start(self) -> None:
        self.starts += 1
        super().start()

    def close(self) -> None:
        self.closes += 1
        super().close()


@dataclass
class _Wiring:
    """What the faked seams saw during one ``create_stack`` call."""

    settings: list[_Call] = field(default_factory=list)
    transports: list[_Call] = field(default_factory=list)
    stores: list[_FakeStore] = field(default_factory=list)
    binds: list[dict[str, object]] = field(default_factory=list)
    catalogs: list[dict[str, object]] = field(default_factory=list)
    overlays: list[dict[str, object]] = field(default_factory=list)
    workers: list[_FakeWorker] = field(default_factory=list)
    built: list[dict[str, object]] = field(default_factory=list)
    collections: list[_RecordingCollection] = field(default_factory=list)
    discoveries: list[dict[str, object]] = field(default_factory=list)


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    harness: tuple[HarnessSource, ...] | None = None,
    tree: Path | None = None,
    current: str | None = None,
    currents: Mapping[str, str | None] | None = None,
    published: bool = True,
    catalog: HarnessCatalog | None = None,
    catalogs: Mapping[str, HarnessCatalog] | None = None,
    entry_points: tuple[object, ...] = (),
) -> _Wiring:
    """Fake every seam ``create_stack`` reaches out through and record it.

    ``current`` is one activated SHA for every named source; ``currents``
    names one per source, with ``None`` for a source that has nothing
    activated. They are mutually exclusive: honouring both would mean the seam
    picking one of two answers with nothing in the call saying which, so
    passing both is refused here rather than resolved silently.

    ``catalog`` is likewise one catalog for every checkout, and ``catalogs``
    names one **per activated SHA** — keyed by commit rather than by source
    name because the SHA is the only argument that reaches a catalog load
    (``load_harness_catalog(tree, sha, capabilities)``) and every checkout in
    this suite shares one faked tree. Two sources therefore need two distinct
    ``currents`` before they can have two distinct catalogs, which is the
    real relationship: what a source contributes follows from the commit it
    is activated at.

    ``_session_capability_overlays`` is faked alongside the git seams rather
    than left real, because it is one too: it puts a checkout directory on
    ``sys.path`` and imports out of it, in this process, for the rest of the
    run. Faking it records the *base* create_stack chose, which is this
    file's share of the overlay arm — what a loader then does with a base
    belongs to ``tests/test_runtime.py``, where the real function runs
    against a real tree.
    """
    if current is not None and currents is not None:
        raise TypeError(
            "_wire takes current= (one SHA for every source) or currents= "
            "(one SHA per source name), never both"
        )
    if catalog is not None and catalogs is not None:
        raise TypeError(
            "_wire takes catalog= (one catalog for every checkout) or "
            "catalogs= (one catalog per activated SHA), never both"
        )
    wiring = _Wiring()
    resolved_catalog = catalog if catalog is not None else _catalog()

    def load_settings(*args: object, **kwargs: object) -> Settings:
        wiring.settings.append((args, kwargs))
        return Settings(harness=tuple(harness or ()))

    def github_transport(*args: object, **kwargs: object) -> _FakeTransport:
        wiring.transports.append((args, kwargs))
        return _FakeTransport()

    def immutable_git_store(root: str | Path, transport: object) -> _FakeStore:
        made = _FakeStore(root, transport, tree=tree, published=published)
        wiring.stores.append(made)
        return made

    def load_harness_catalog(
        tree: str | Path,
        sha: str,
        supported_capabilities: object,
    ) -> HarnessCatalog:
        wiring.catalogs.append(
            {"tree": Path(tree), "sha": sha, "capabilities": supported_capabilities}
        )
        if catalogs is None:
            return resolved_catalog
        if sha not in catalogs:
            raise AssertionError(
                f"catalogs= names one catalog per activated SHA and was "
                f"never told about {sha!r}; it names {sorted(catalogs)}"
            )
        return catalogs[sha]

    def session_capability_overlays(
        seeds: Sequence[ComponentSpec], base: Path
    ) -> tuple[object, ...]:
        wiring.overlays.append({"seeds": tuple(seeds), "base": base})
        return ()

    def worker_provider(*, name: str, entrypoint: str, path: str | Path) -> _FakeWorker:
        made = _FakeWorker(name=name, entrypoint=entrypoint, path=path)
        wiring.workers.append(made)
        return made

    def build_collection(
        config: object,
        registry: object = None,
        *,
        extras: Sequence[object] = (),
    ) -> _RecordingCollection:
        wiring.built.append(
            {"config": config, "registry": registry, "extras": tuple(extras)}
        )
        collection = _RecordingCollection()
        wiring.collections.append(collection)
        return collection

    def discover_providers(
        *,
        failures: list[dict[str, str]] | None = None,
        only_available: bool = False,
    ) -> list[object]:
        wiring.discoveries.append({"only_available": only_available})
        return list(entry_points)

    monkeypatch.setattr(server, "load_settings", load_settings)
    monkeypatch.setattr(harness_module, "GitHubTransport", github_transport)
    monkeypatch.setattr(harness_module, "ImmutableGitStore", immutable_git_store)
    monkeypatch.setattr(
        harness_module, "Activation", _ActivationSeam(wiring, current, currents)
    )
    monkeypatch.setattr(harness_module, "load_harness_catalog", load_harness_catalog)
    monkeypatch.setattr(harness_module, "WorkerProvider", worker_provider)
    monkeypatch.setattr(
        server, "_session_capability_overlays", session_capability_overlays
    )
    monkeypatch.setattr(server, "build_collection", build_collection)
    monkeypatch.setattr(server, "discover_providers", discover_providers)
    return wiring


async def _tool_names(stack: FastMCP) -> set[str]:
    return {tool.name for tool in await stack.list_tools()}


async def _tool_name_list(stack: FastMCP) -> list[str]:
    """Every composed tool name *with* its multiplicity.

    :func:`_tool_names` collapses a name mounted twice into one entry, so it
    cannot tell "one plane named ``demo``" from "two planes mounted under one
    ``demo`` namespace". Counting needs the list.
    """
    return [tool.name for tool in await stack.list_tools()]


# -- arm gating -------------------------------------------------------------


def test_dual_injection_never_consults_the_harness_locator(tmp_path, monkeypatch):
    """Both arms injected: the locator is not read, bound, or catalogued."""
    wiring = _wire(monkeypatch, harness=(_SOURCE,))
    create_stack(
        collection=CollectionIndex([]),
        providers=[_Vis()],
        config=_config(tmp_path),
    )
    assert wiring.settings == []
    assert wiring.binds == []
    assert wiring.catalogs == []


async def test_an_empty_source_list_serves_exactly_like_today(tmp_path, monkeypatch):
    """No source named: no bind, no extras, entry points then ``disable=``.

    The empty *list* is the un-harnessed install — the one configuration
    that must keep serving exactly as it did before a harness existed.
    """
    wiring = _wire(
        monkeypatch,
        harness=(),
        entry_points=(_Marker("demo"), _Marker("other")),
    )
    stack = create_stack(config=_config(tmp_path), disable=["other"])
    assert wiring.binds == []
    assert wiring.catalogs == []
    assert wiring.built[0]["extras"] == ()
    assert wiring.discoveries == [{"only_available": True}]
    names = await _tool_names(stack)
    assert "demo_intree" in names
    assert "other_intree" not in names


def test_an_empty_locator_cannot_construct():
    """Half-authored coordinate entries are no longer representable."""
    with pytest.raises((LocatorError, ValueError)):
        HarnessSource(name="mine", locator="")


def test_a_relative_locator_cannot_construct():
    with pytest.raises((LocatorError, ValueError)):
        HarnessSource(name="mine", locator="./checkout")


def test_a_name_only_entry_cannot_construct():
    with pytest.raises(TypeError):
        HarnessSource(name="mine")


def test_a_github_locator_without_a_ref_is_servable(monkeypatch):
    """``owner/repo`` with no ``@ref`` is a complete GitHub origin."""
    source = HarnessSource(name="official", locator="molcrafts/harness")
    assert source.ref == ""
    _wire(monkeypatch, harness=(source,))
    assert server._harness_locator() == (source,)


def test_two_complete_sources_are_returned_in_file_order(monkeypatch):
    """Order is the file's order — the contract later resolution inherits."""
    _wire(monkeypatch, harness=(_SOURCE, _OTHER))
    assert server._harness_locator() == (_SOURCE, _OTHER)


def test_two_sources_still_bind_exactly_one_store_root(tmp_path, monkeypatch):
    """Several named sources, one store: no per-source root is introduced.

    ``ImmutableGitStore`` already records provenance per SHA and refuses a
    SHA claimed by a second repository, so a second root would buy nothing
    and would strand every already-published tree.
    """
    config = _config(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE, _OTHER),
        current=_SHA,
        tree=_checkout(tmp_path),
    )
    create_stack(config=config)
    assert len(wiring.stores) == 1
    assert wiring.stores[0].root == config.cache_dir / "harness"


def test_two_sources_bind_one_activation_pointer_each(tmp_path, monkeypatch):
    """One store above, one *pointer file per source* here — the other half.

    This is the half of the old single-store test that per-source activation
    breaks, split out rather than deleted so the store's reason keeps its own
    test. A pointer is not shareable the way a store is: the record
    ``Activation`` binds holds one ``active`` SHA, so a second source folded
    into ``<cache>/harness.pointer`` would either overwrite the first's
    commit or be overwritten by it. The file name carries the source instead,
    and the store keeps its one root because it is keyed by SHA and needs no
    such name.

    The paths are asserted as an ordered list, so a bind that lands the right
    number of files under the wrong names fails here rather than passing on a
    count.
    """
    config = _config(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE, _OTHER),
        current=_SHA,
        tree=_checkout(tmp_path),
    )
    create_stack(config=config)
    assert [bind["path"] for bind in wiring.binds] == [
        config.cache_dir / "harness.official.pointer",
        config.cache_dir / "harness.private.pointer",
    ]


# -- completeness is the locator, not a coordinate triple -------------------
#
# A GitHub locator is complete without a path. A local locator is an
# absolute or ``~/`` path that names a checkout. Relative spellings cannot
# construct.


def test_a_local_source_with_a_locator_path_is_complete(tmp_path, monkeypatch):
    """A checkout on disk is an origin; GitHub coordinates stay derived empty."""
    source = HarnessSource(
        name="mine", locator=str(_git_checkout(tmp_path / "checkout"))
    )
    _wire(monkeypatch, harness=(source,))

    assert server._harness_locator() == (source,)


async def test_a_local_source_reaches_the_activation_arm_and_serves(
    tmp_path, monkeypatch
):
    """The whole composition, not just the locator: a local entry serves.

    ``_harness_locator`` returning the source is necessary and not
    sufficient — the entry has to travel the same arm a remote one does. The
    pointer bind is the evidence it did, and the core tool is the evidence
    the stack came up rather than raising on the way.
    """
    config = _config(tmp_path)
    source = HarnessSource(
        name="mine", locator=str(_git_checkout(tmp_path / "checkout"))
    )
    wiring = _wire(monkeypatch, harness=(source,))

    stack = create_stack(config=config)

    assert [bind["path"] for bind in wiring.binds] == [
        config.cache_dir / "harness.mine.pointer"
    ]
    assert "packages" in await _tool_names(stack)


@pytest.mark.parametrize("directory", ["gone", "plain"])
def test_a_local_path_that_is_not_a_checkout_is_refused_by_the_locator(
    tmp_path, monkeypatch, directory: str
):
    """A path naming no checkout is the local half-authored coordinate.

    Two ways to get one, and they are one failure: the directory is not
    there at all (``gone``), or it is there and is not a repository
    (``plain``) — an operator who typed the parent, or the checkout before
    cloning into it. Both are refused *here*, beside the remote entry's
    missing ``ref``, rather than later as a ``GitError`` out of a transport:
    the settings file is what is wrong, and the message has to say which
    entry and which path so there is somewhere to go and fix it.
    """
    root = tmp_path / directory
    if directory == "plain":
        root.mkdir()
    source = HarnessSource(name="mine", locator=str(root))
    _wire(monkeypatch, harness=(source,))

    with pytest.raises(ConfigurationError) as excinfo:
        server._harness_locator()

    message = str(excinfo.value)
    assert "mine" in message
    assert str(root) in message


def test_an_unusable_local_path_refuses_before_anything_is_bound(tmp_path, monkeypatch):
    """Refused whole: no store, no pointer, no transport for the bad entry.

    The complement of the parametrized test above. It proves the raise comes
    out of the locator; this one proves nothing downstream of the locator ran
    first, which is what "fails at the same place, not later inside the
    transport" costs if it is not true — a half-bound cache directory for a
    settings file that was never servable.
    """
    source = HarnessSource(name="mine", locator=str(tmp_path / "gone"))
    wiring = _wire(monkeypatch, harness=(source,))

    with pytest.raises(ConfigurationError):
        create_stack(config=_config(tmp_path))

    assert wiring.stores == []
    assert wiring.binds == []
    assert wiring.catalogs == []


def test_a_local_and_a_remote_source_are_complete_side_by_side(tmp_path, monkeypatch):
    """One list, two origins, file order kept — the mixed install.

    Origin is read per entry. A rule that picked one shape for the whole
    list would either reject the local entry or stop checking the remote
    one's coordinates.
    """
    local = HarnessSource(
        name="mine", locator=str(_git_checkout(tmp_path / "checkout"))
    )
    _wire(monkeypatch, harness=(_SOURCE, local))

    assert server._harness_locator() == (_SOURCE, local)


# -- the reader itself, over a real settings file ---------------------------
#
# Every test above fakes ``load_settings`` through the ``_wire`` seam, so a
# reader that cannot read the type it is handed passes all of them. These two
# call the real ``_harness_locator`` against a settings file on disk, under a
# temporary home so no developer's own ``~/.molmcp`` can reach the assertion.


def _fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``~`` at a temporary tree and return it.

    Both spellings of "the user's home" are aimed at the same directory:
    :meth:`Path.home`, which is how this package finds it, and the ``HOME`` /
    ``USERPROFILE`` environment :func:`os.path.expanduser` consults —
    ``Path.expanduser`` delegates to that function and does **not** go through
    ``Path.home``. Pinning both keeps the ``~`` tests below on the behaviour
    (a home-relative path names one directory in every session) rather than on
    which of the two APIs an expansion is written with.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand the process in a temporary project directory and return it.

    This is the directory an MCP client's ``molmcp serve`` inherits — one of
    many, differing per session, and the thing a ``path`` entry in the shared
    settings file may not be read against.
    """
    project = tmp_path / "project"
    project.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(project)
    return project


def _home_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: dict[str, object]
) -> Path:
    """Point ``~`` and the working directory at hermetic temporary trees.

    Returns the settings file it wrote, so a caller can read the bytes back
    and check that serving left them alone.
    """
    home = _fake_home(tmp_path, monkeypatch)
    (home / ".molmcp").mkdir(parents=True)
    settings_file = home / ".molmcp" / "settings.json"
    settings_file.write_text(json.dumps(data), encoding="utf-8")
    _working_directory(tmp_path, monkeypatch)
    return settings_file


def test_the_real_locator_reads_the_named_sources_off_disk(tmp_path, monkeypatch):
    """The unfaked reader over a real file, in file order."""
    _home_settings(
        tmp_path,
        monkeypatch,
        {
            "harness": [
                {"name": "official", "locator": "molcrafts/harness@main"},
                {"name": "private", "locator": "acme/tooling@trunk"},
            ]
        },
    )
    assert server._harness_locator() == (_SOURCE, _OTHER)


def test_the_real_locator_reads_an_empty_settings_file_as_no_harness(
    tmp_path, monkeypatch
):
    """The unfaked reader on a stock install: ``()``, not an error."""
    _home_settings(tmp_path, monkeypatch, {})
    assert server._harness_locator() == ()


def test_the_real_locator_accepts_a_path_entry_off_disk(tmp_path, monkeypatch):
    """The local origin, end to end: settings file to servable source.

    Every faked-seam test above hands ``_harness_locator`` a
    ``HarnessSource`` the test itself constructed, so a reader that cannot
    round-trip the ``path`` key through JSON passes all of them — the exact
    failure this section exists for. Here the entry is a dict in a file and
    the checkout is a real repository.
    """
    checkout = _git_checkout(tmp_path / "checkout")
    _home_settings(
        tmp_path,
        monkeypatch,
        {"harness": [{"name": "mine", "locator": str(checkout)}]},
    )

    assert server._harness_locator() == (
        HarnessSource(name="mine", locator=str(checkout)),
    )


def test_harness_coordinates_are_gone_from_server():
    assert not hasattr(server, "_HARNESS_KEYS")
    assert not hasattr(harness_module, "HARNESS_COORDINATES")


# -- local locators: absolute, ``~/``, never relative -----------------------
#
# Relative paths cannot construct a ``HarnessSource``. ``~/harness`` and an
# absolute checkout remain servable; the stored locator string is not
# rewritten on the way through the reader.


async def test_a_home_relative_source_reaches_the_activation_arm_and_serves(
    tmp_path, monkeypatch
):
    """``~/harness`` travels the same arm an absolute path does.

    Being accepted by the locator is necessary and not sufficient — the
    expansion has to hold all the way through activation. The pointer bind is
    the evidence the entry got there, and the core tool is the evidence the
    stack came up rather than raising on the way.
    """
    config = _config(tmp_path)
    home = _fake_home(tmp_path, monkeypatch)
    _working_directory(tmp_path, monkeypatch)
    _git_checkout(home / "harness")
    wiring = _wire(
        monkeypatch, harness=(HarnessSource(name="mine", locator="~/harness"),)
    )

    stack = create_stack(config=config)

    assert [bind["path"] for bind in wiring.binds] == [
        config.cache_dir / "harness.mine.pointer"
    ]
    assert "packages" in await _tool_names(stack)


def test_a_home_relative_path_resolves_under_home_not_the_working_directory(
    tmp_path, monkeypatch
):
    """The expansion is home's, and it is not a search path.

    The only checkout on disk sits at ``harness`` under the *working
    directory* and home is empty, so the entry names nothing and is refused —
    proof that the accepted case above was the home expansion rather than a
    relative read that happened to find a repository.
    """
    _fake_home(tmp_path, monkeypatch)
    project = _working_directory(tmp_path, monkeypatch)
    _git_checkout(project / "harness")
    _wire(monkeypatch, harness=(HarnessSource(name="mine", locator="~/harness"),))

    with pytest.raises(ConfigurationError) as excinfo:
        server._harness_locator()

    assert "mine" in str(excinfo.value)


def test_the_real_locator_serves_a_home_relative_path_without_rewriting_it(
    tmp_path, monkeypatch
):
    """The unfaked reader over a real file: accepted, and the file untouched.

    ``_harness_locator`` is the one step of a serve that opens
    ``~/.molmcp/settings.json``, so it is the one step that could normalise
    the entry on the way past. It must not: the stored string is the
    operator's, an expansion belongs to the session doing the resolving, and
    a machine's absolute path written back into a file that syncs between
    machines is a different bug in the same family.
    """
    settings_file = _home_settings(
        tmp_path, monkeypatch, {"harness": [{"name": "mine", "locator": "~/harness"}]}
    )
    before = settings_file.read_bytes()
    # ``<home>/.molmcp/settings.json`` — read back off the helper's own answer
    # rather than respelled here, so the checkout lands under whatever ``~``
    # was pointed at.
    _git_checkout(settings_file.parent.parent / "harness")

    assert server._harness_locator() == (
        HarnessSource(name="mine", locator="~/harness"),
    )

    assert settings_file.read_bytes() == before
    assert "~/harness" in settings_file.read_text(encoding="utf-8")


def test_the_real_locator_serves_an_absolute_path_without_rewriting_it(
    tmp_path, monkeypatch
):
    """The spelling that was always accepted, still accepted and still verbatim.

    The control for the test above: whatever the new rule does to a moving
    path, an absolute entry keeps serving and its string keeps its bytes.
    """
    checkout = _git_checkout(tmp_path / "checkout")
    settings_file = _home_settings(
        tmp_path, monkeypatch, {"harness": [{"name": "mine", "locator": str(checkout)}]}
    )
    before = settings_file.read_bytes()

    assert server._harness_locator() == (
        HarnessSource(name="mine", locator=str(checkout)),
    )

    assert settings_file.read_bytes() == before
    assert str(checkout) in settings_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("sources", "currents", "pointers", "shas"),
    [
        pytest.param(
            (_SOURCE,),
            {_SOURCE.name: None},
            ("harness.official.pointer",),
            (),
            id="the-one-source-has-nothing-activated",
        ),
        pytest.param(
            (_SOURCE, _OTHER),
            {_SOURCE.name: None, _OTHER.name: _OTHER_SHA},
            ("harness.official.pointer", "harness.private.pointer"),
            (_OTHER_SHA, _OTHER_SHA),
            id="the-first-of-two-sources-has-nothing-activated",
        ),
    ],
)
async def test_absent_current_falls_back_without_resolving_or_promoting(
    tmp_path,
    monkeypatch,
    sources: tuple[HarnessSource, ...],
    currents: Mapping[str, str | None],
    pointers: tuple[str, ...],
    shas: tuple[str, ...],
):
    """A source with no current SHA serves the unset fallback — even mixed.

    Binding is a *read*: a source with nothing activated is an empty record,
    not a skipped file, so every named source is bound whatever its neighbour
    is at. Only the ones that came back with a SHA go on to a catalog, and
    the second case is the mixed one — the unactivated source comes *first*,
    so an arm that stopped at the first empty record would serve the second
    source's commit as nothing at all.

    The fallback assertions hold in both cases because the activated source's
    catalog declares no overlay and no provider: extras stay empty, entry
    points are still enumerated, and the in-tree ``demo`` plane still mounts.

    This does not claim which source got which SHA. With one SHA in play the
    mapping is unfalsifiable from here — the two names could be swapped and
    the same one catalog read would follow. It is pinned where the pointer
    files are real, in ``tests/test_harness.py::TestActivatedCheckouts``, and
    the two-commit case below is the composition-side half of it.
    """
    config = _config(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=sources,
        currents=currents,
        tree=_checkout(tmp_path),
        entry_points=(_Marker("demo"),),
    )
    stack = create_stack(config=config)
    assert [bind["path"] for bind in wiring.binds] == [
        config.cache_dir / name for name in pointers
    ]
    assert [entry["sha"] for entry in wiring.catalogs] == list(shas)
    assert wiring.built[0]["extras"] == ()
    assert wiring.discoveries == [{"only_available": True}]
    assert "demo_intree" in await _tool_names(stack)


def test_two_activated_sources_are_each_read_at_their_own_commit(tmp_path, monkeypatch):
    """Two sources, two commits: each checkout is read at its own pointer's SHA.

    Both sources are activated, at two *distinct* commits, so the pairing is
    falsifiable here in a way it is not with one SHA in play: the seam answers
    by pointer file name, and the catalog reads are asserted as an ordered
    list, one per arm per checkout. An arm that read ``harness.private``'s
    answer for ``official`` flips both halves of that list, and an arm that
    still binds one shared pointer never gets an answer at all — neither
    failure is visible to a count or to a set.

    What is claimed is exactly ``pointer file -> SHA -> the catalog read for
    that checkout``. The ``source`` *label* the checkout carries is not
    claimed: every checkout in this suite shares one faked tree, so a
    correctly ordered pair of checkouts wearing each other's names would read
    the same catalogs in the same order. That label is pinned in
    ``tests/test_harness.py::TestActivatedCheckouts``, where the pointer files
    are real.

    Two reads per commit is not redundancy to be optimised away: the overlay
    arm and the provider arm each read the catalog for themselves, so an arm
    that does not run never pays for a catalog it would not use.
    """
    config = _config(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE, _OTHER),
        currents={_SOURCE.name: _SHA, _OTHER.name: _OTHER_SHA},
        tree=_checkout(tmp_path),
    )
    create_stack(config=config)
    assert [bind["path"] for bind in wiring.binds] == [
        config.cache_dir / "harness.official.pointer",
        config.cache_dir / "harness.private.pointer",
    ]
    assert [entry["sha"] for entry in wiring.catalogs] == [
        _SHA,
        _OTHER_SHA,
        _SHA,
        _OTHER_SHA,
    ]


def test_one_activated_source_and_one_without_are_answered_apart(tmp_path, monkeypatch):
    """Two named sources, one activated: two binds, one source's catalogs.

    This is the first test that needs the activation pointer to be per
    source, and the first that a seam holding a single ``current`` could not
    express: ``official`` is activated at :data:`_SHA` while ``private`` has
    nothing activated at all. Both pointers are still bound — binding is a
    read, and an unactivated source is an empty record rather than a skipped
    one — but only ``official`` has a tree to read a catalog from, so the two
    reads the two arms make are both for its SHA.

    Three ways of getting this wrong die here: an arm still binding one
    shared ``harness.pointer`` (the seam cannot recover a source name from
    that file and says so), a seam ignoring ``currents`` for the scalar
    ``current`` (no catalog read at all), and a seam answering one SHA for
    every source (two checkouts, so four reads rather than two).

    One axis is deliberately *not* claimed: with a single source activated,
    swapping which name holds ``_SHA`` still yields one checkout at ``_SHA``,
    so nothing here pins name to SHA — the mapping is written in the opposite
    order to the source list to discourage a positional reading, not to prove
    one impossible. That pairing is pinned where the pointer files are real,
    in ``tests/test_harness.py::TestActivatedCheckouts``, which is also the
    only place the real ``Activation.bind`` and ``load_harness_catalog`` are
    exercised at all: ``_wire`` fakes both here
    (``notes.md:faked-seam-hides-broken-reader``), so this test is evidence
    about what ``create_stack`` asks for and none about what answers it.
    """
    config = _config(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE, _OTHER),
        currents={_OTHER.name: None, _SOURCE.name: _SHA},
        tree=_checkout(tmp_path),
    )
    create_stack(config=config)
    assert [bind["path"] for bind in wiring.binds] == [
        config.cache_dir / "harness.official.pointer",
        config.cache_dir / "harness.private.pointer",
    ]
    assert [entry["sha"] for entry in wiring.catalogs] == [_SHA, _SHA]


def test_current_missing_from_the_store_names_that_sha(tmp_path, monkeypatch):
    """An activated SHA with no tree is an error, never a silent re-clone."""
    _wire(
        monkeypatch,
        harness=(_SOURCE,),
        current=_SHA,
        tree=_checkout(tmp_path),
        published=False,
    )
    with pytest.raises(ConfigurationError) as excinfo:
        create_stack(config=_config(tmp_path))
    assert _SHA in str(excinfo.value)


async def test_injected_collection_still_runs_the_provider_git_arm(
    tmp_path, monkeypatch
):
    """Skip is per owner: an injected collection only skips the overlay arm.

    One arm runs, so the reads are one per activated source rather than two
    — the count that would drop back to one is an arm that folded a single
    checkout out of two named sources.
    """
    tree = _checkout(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE, _OTHER),
        current=_SHA,
        tree=tree,
        catalog=_catalog(_provider_component()),
    )
    stack = create_stack(collection=CollectionIndex([]), config=_config(tmp_path))
    assert "demo_worker" in await _tool_names(stack)
    assert wiring.built == []
    assert len(wiring.catalogs) == 2


async def test_injected_providers_still_run_the_overlay_git_arm(tmp_path, monkeypatch):
    """Injected providers skip only the provider arm and still pass disable=.

    The surviving overlay arm reads one catalog per activated source, the
    mirror of the provider-arm case above.
    """
    tree = _checkout(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE, _OTHER),
        current=_SHA,
        tree=tree,
        catalog=_catalog(_provider_component()),
    )
    stack = create_stack(
        providers=[_Marker("demo")],
        config=_config(tmp_path),
        disable=["demo"],
    )
    assert "demo_intree" not in await _tool_names(stack)
    assert wiring.workers == []
    assert wiring.discoveries == []
    assert len(wiring.catalogs) == 2
    assert len(wiring.built) == 1


async def test_entry_point_discovery_off_is_not_a_provider_git_arm(
    tmp_path, monkeypatch
):
    """``discover_entry_points=False`` with no providers mounts nothing."""
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE,),
        current=_SHA,
        tree=_checkout(tmp_path),
        catalog=_catalog(_provider_component()),
    )
    stack = create_stack(
        collection=CollectionIndex([]),
        config=_config(tmp_path),
        discover_entry_points=False,
    )
    assert [name for name in await _tool_names(stack) if name.startswith("demo_")] == []
    assert wiring.settings == []
    assert wiring.binds == []
    assert wiring.workers == []


# -- named bind -------------------------------------------------------------


def test_named_store_and_pointer_hang_off_the_resolved_cache_root(
    tmp_path, monkeypatch
):
    """One store at ``<cache>/harness``, a pointer per source beside it.

    The transport is constructed once for any number of sources —
    ``GitHubTransport`` takes ``(owner, repo)`` per call, so a second source
    in a second repository needs no second instance — and its constructor is
    still called with nothing, the token staying where it already lives.

    Every bind is handed that same one store *object*, not merely an equal
    root: the identity is what says the N pointers share one SHA-keyed store
    rather than N stores that happen to agree on a path.
    """
    config = _config(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE, _OTHER),
        current=_SHA,
        tree=_checkout(tmp_path),
    )
    create_stack(config=config)
    assert wiring.transports == [((), {})]
    assert len(wiring.stores) == 1
    store = wiring.stores[0]
    assert store.root == config.cache_dir / "harness"
    assert isinstance(store.transport, _FakeTransport)
    assert [bind["path"] for bind in wiring.binds] == [
        config.cache_dir / "harness.official.pointer",
        config.cache_dir / "harness.private.pointer",
    ]
    assert [bind["store"] is store for bind in wiring.binds] == [True, True]


def test_unset_cache_dir_still_binds_under_the_resolved_default_root(
    tmp_path, monkeypatch
):
    """No ``cacheDir`` set is the common case, not a broken harness.

    ``AppConfig.cache_dir`` is ``None`` until somebody configures ``cacheDir``,
    so reading it raw turns "I set the three harness keys" into an error for
    the majority of users. The fallback to the default cache root already has
    one home in :mod:`molmcp.runtime`; every per-source bind hangs off that
    resolved root, and the one store beside them does too.
    """
    config = AppConfig.from_dict({"schema_version": "2"}, workspace_root=tmp_path)
    assert config.cache_dir is None
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE, _OTHER),
        current=_SHA,
        tree=_checkout(tmp_path),
    )
    create_stack(config=config)
    resolved = runtime.resolved_cache_dir(config)
    assert len(wiring.stores) == 1
    store = wiring.stores[0]
    assert store.root == resolved / "harness"
    assert [bind["path"] for bind in wiring.binds] == [
        resolved / "harness.official.pointer",
        resolved / "harness.private.pointer",
    ]
    assert [bind["store"] is store for bind in wiring.binds] == [True, True]


def test_server_module_imports_nothing_from_discovery():
    """The cache root is AppConfig's; server.py stays out of discovery."""
    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    modules: list[str] = []
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
            names |= {alias.name for alias in node.names}
    assert [name for name in modules if "discovery" in name] == []
    assert "DiscoveryConfig" not in names
    assert "default_cache_dir" not in names


def test_harness_module_imports_nothing_from_discovery():
    """The resolver inherits the shield, and the guard follows the code.

    ``server.py``'s own scan cannot see this: the harness arms moved to
    ``molmcp.harness`` in ``3c407a8``, so a ``discovery`` import added there
    would leave ``server.py`` clean and still breach the boundary. Both modules
    reach the cache root through ``runtime.resolved_cache_dir``, which is the
    one owner of that fallback precisely so neither has to import discovery.
    """
    tree = ast.parse(Path(harness_module.__file__).read_text(encoding="utf-8"))
    modules: list[str] = []
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
            names |= {alias.name for alias in node.names}
    assert [name for name in modules if "discovery" in name] == []
    assert "DiscoveryConfig" not in names
    assert "default_cache_dir" not in names


def test_the_locator_is_read_once_with_the_project_root(tmp_path, monkeypatch):
    """``load_settings(Path.cwd())``: bare hides a project's harness keys."""
    config = _config(tmp_path)
    tree = _checkout(tmp_path)
    monkeypatch.chdir(tmp_path)
    wiring = _wire(monkeypatch, harness=(_SOURCE,), current=_SHA, tree=tree)
    create_stack(config=config)
    assert len(wiring.settings) == 1
    args, kwargs = wiring.settings[0]
    assert len(args) + len(kwargs) == 1
    assert (args[0] if args else kwargs["project_root"]) == Path.cwd()


# -- capabilities -----------------------------------------------------------


def test_one_capability_object_reaches_bind_and_both_catalog_calls(
    tmp_path, monkeypatch
):
    """One frozenset object: every bind plus one catalog call per arm per source.

    Two arms over two sources is 2 x N calls, and the object handed to each
    one is asserted by identity rather than equality. An equal-but-distinct
    frozenset per source would pass an ``==`` check and would mean the
    capability set had been rebuilt somewhere down the loop, which is the
    thing this test exists to refuse.
    """
    tree = _checkout(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE, _OTHER),
        current=_SHA,
        tree=tree,
        catalog=_catalog(_provider_component()),
    )
    create_stack(config=_config(tmp_path))
    assert harness_module.SUPPORTED_CAPABILITIES == _CAPABILITIES
    capabilities = harness_module.SUPPORTED_CAPABILITIES
    assert len(wiring.binds) == 2
    for bind in wiring.binds:
        assert bind["supported_capabilities"] is capabilities
    assert len(wiring.catalogs) == 4
    for call in wiring.catalogs:
        assert call["capabilities"] is harness_module.SUPPORTED_CAPABILITIES
        assert call["tree"] == tree
        assert call["sha"] == _SHA


def test_supported_capabilities_is_a_subset_of_allowed_requires_not_an_alias():
    """Catalog grammar and runtime ability are two sets that happen to match."""
    assert harness_module.SUPPORTED_CAPABILITIES <= ALLOWED_REQUIRES
    assert harness_module.SUPPORTED_CAPABILITIES is not ALLOWED_REQUIRES


# -- worker provider mapping and XOR ---------------------------------------


def test_worker_provider_is_named_by_component_name_not_id(tmp_path, monkeypatch):
    """``name=`` is the EP name ``demo``; ``provider.demo`` is not a plane id."""
    spec = _provider_component()
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE,),
        current=_SHA,
        tree=_checkout(tmp_path),
        catalog=_catalog(spec),
    )
    create_stack(collection=CollectionIndex([]), config=_config(tmp_path))
    assert spec.id == "provider.demo"
    assert [worker.name for worker in wiring.workers] == ["demo"]
    assert wiring.workers[0].name != spec.id


def test_worker_provider_entrypoint_stays_an_unimported_string(tmp_path, monkeypatch):
    """The entrypoint is carried as a string; the checkout is not imported."""
    spec = _provider_component()
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE,),
        current=_SHA,
        tree=_checkout(tmp_path),
        catalog=_catalog(spec),
    )
    create_stack(collection=CollectionIndex([]), config=_config(tmp_path))
    assert wiring.workers[0].entrypoint == spec.entrypoint
    assert "demo.plane" not in sys.modules
    assert "demo" not in sys.modules


@pytest.mark.parametrize("path", ["providers/demo/plane.py", "providers/demo"])
def test_worker_provider_path_is_the_import_root_directory(
    tmp_path, monkeypatch, path: str
):
    """A file row gives its parent; a directory row gives that directory."""
    tree = _checkout(tmp_path)
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE,),
        current=_SHA,
        tree=tree,
        catalog=_catalog(_provider_component(path=path)),
    )
    create_stack(collection=CollectionIndex([]), config=_config(tmp_path))
    assert Path(wiring.workers[0].path) == tree / "providers" / "demo"


@pytest.mark.parametrize("path", ["providers/demo/plane.py", "providers/demo"])
def test_rooted_worker_provider_path_is_under_the_component_root(
    tmp_path, monkeypatch, path: str
):
    """The sibling above, with ``component_root = "plugins/mol"`` declared.

    Both path shapes land on one directory again, and it is the one under
    the root. The tree really holds ``plugins/mol/providers/demo``, so the
    directory row takes ``_import_root``'s is-a-directory branch off the
    folded base rather than falling back to a parent that happens to look
    plausible.
    """
    tree = _checkout(tmp_path, component_root="plugins/mol")
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE,),
        current=_SHA,
        tree=tree,
        catalog=_catalog(_provider_component(path=path), component_root="plugins/mol"),
    )
    create_stack(collection=CollectionIndex([]), config=_config(tmp_path))
    assert (
        Path(wiring.workers[0].path) == tree / "plugins" / "mol" / "providers" / "demo"
    )


async def test_checkout_wins_the_name_and_entry_point_only_planes_pass_through(
    tmp_path, monkeypatch
):
    """XOR against ``discover_providers(only_available=True)``, by EP name."""
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE,),
        current=_SHA,
        tree=_checkout(tmp_path),
        catalog=_catalog(_provider_component()),
        entry_points=(_Marker("demo"), _Marker("other")),
    )
    stack = create_stack(config=_config(tmp_path))
    names = await _tool_names(stack)
    assert "demo_worker" in names
    assert "demo_intree" not in names
    assert "other_intree" in names
    assert wiring.discoveries == [{"only_available": True}]


async def test_two_sources_declaring_one_plane_mount_it_once(tmp_path, monkeypatch):
    """``provider.demo`` in two catalogs is one plane, and it is the first.

    This is the collision the fold exists for. ``ComponentSpec`` pins
    ``id == f"provider.{name}"``, so two sources declaring ``provider.demo``
    are two planes named ``demo`` — and a plane name is the namespace its
    tools mount under, so building both would mount twice under one
    namespace and leave which one answers ``demo_worker`` to mount order.

    Both catalogs are still *read*: the fold collapses the id, it does not
    skip a source. The two sources are activated at two different commits so
    that they can declare two different rows at all, and the winner is
    identified by ``entrypoint`` — the one field of a contested
    ``provider.demo`` that can differ, since the name and the id are the
    contested key itself. "First" is read off the chain
    ``harness.official.pointer -> _SHA -> that catalog``, not off the
    ``source`` label the checkout carries, which nothing in this file can see.

    The mount count is asserted on the tool-name *list*, because mounting
    twice under one namespace may well leave a single name visible; the
    number of workers constructed is the assertion that cannot be satisfied
    by a shadowed second mount.
    """
    first = _provider_component(entrypoint="demo.plane:OfficialProvider")
    second = _provider_component(entrypoint="demo.plane:PrivateProvider")
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE, _OTHER),
        currents={_SOURCE.name: _SHA, _OTHER.name: _OTHER_SHA},
        tree=_checkout(tmp_path),
        catalogs={
            _SHA: _catalog(first, sha=_SHA),
            _OTHER_SHA: _catalog(second, sha=_OTHER_SHA),
        },
    )
    stack = create_stack(config=_config(tmp_path))
    assert len(wiring.catalogs) == 4
    assert [worker.name for worker in wiring.workers] == ["demo"]
    assert wiring.workers[0].entrypoint == first.entrypoint
    assert wiring.workers[0].entrypoint != second.entrypoint
    assert (await _tool_name_list(stack)).count("demo_worker") == 1


async def test_the_folded_name_set_excludes_a_plane_the_second_source_named(
    tmp_path, monkeypatch
):
    """The XOR is against every source's planes, not against the first's.

    ``test_checkout_wins_the_name_and_entry_point_only_planes_pass_through``
    covers this with one source. Here the checkout's ``demo`` comes from the
    **second** source and the first declares something else entirely, so an
    arm that built the entry-point exclusion set from the first catalog — or
    from one checkout out of two — would let the in-tree ``demo`` plane
    through and mount a second plane under that namespace.

    The exclusion set is the fold's, so both sources' kept planes are in it:
    ``alpha`` and ``demo`` both mount, and only the unclaimed ``other``
    survives from the entry points.
    """
    alpha = _provider_component(path="providers/alpha/plane.py", name="alpha")
    demo = _provider_component()
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE, _OTHER),
        currents={_SOURCE.name: _SHA, _OTHER.name: _OTHER_SHA},
        tree=_checkout(tmp_path),
        catalogs={
            _SHA: _catalog(alpha, sha=_SHA),
            _OTHER_SHA: _catalog(demo, sha=_OTHER_SHA),
        },
        entry_points=(_Marker("demo"), _Marker("other")),
    )
    stack = create_stack(config=_config(tmp_path))
    assert [worker.name for worker in wiring.workers] == ["alpha", "demo"]
    names = await _tool_names(stack)
    assert "alpha_worker" in names
    assert "demo_worker" in names
    assert "demo_intree" not in names
    assert "other_intree" in names


# -- overlay seed base ------------------------------------------------------


@pytest.mark.parametrize(
    ("component_root", "segments"),
    [("", ()), ("plugins/mol", ("plugins", "mol"))],
)
def test_overlay_seeds_are_handed_the_folded_base_not_the_checkout_tree(
    tmp_path, monkeypatch, component_root: str, segments: tuple[str, ...]
):
    """``create_stack`` hands the overlay loader ``root_for``'s answer.

    This is the overlay half of the failure ``component_root`` exists to
    make unreachable: the key applied in the provider arm and forgotten in
    this one is half a harness, and half a harness is harder to diagnose
    than one that resolves nothing, because the install looks like it works.
    The provider half is asserted two sections above, and again over a real
    tree in ``tests/test_harness.py``.

    The *recorded argument* is the assertion because the subject is
    ``create_stack``'s composition — which directory it chose. What the
    loader does with a base is the loader's contract, pinned in
    ``tests/test_runtime.py`` against a real tree through the real function.

    The rootless case is an equality against the tree object itself, not a
    prefix check, so a base carrying a ``.`` component or a trailing
    separator fails it: today's rootless installs must resolve byte-identical
    paths.
    """
    tree = _checkout(tmp_path, component_root=component_root)
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE,),
        current=_SHA,
        tree=tree,
        catalog=_catalog(_overlay_component(), component_root=component_root),
    )
    create_stack(config=_config(tmp_path))
    assert [call["base"] for call in wiring.overlays] == [tree.joinpath(*segments)]
    # The seed really reached the arm, so the base above was chosen with an
    # overlay row in hand rather than for an empty spec list.
    assert [spec.id for spec in wiring.overlays[0]["seeds"]] == ["overlay.demo"]


# -- lifecycle --------------------------------------------------------------


async def test_core_lifespan_closes_the_collection_and_never_closes_a_worker(
    tmp_path, monkeypatch
):
    """``coll.close()`` stays in the core finally; no worker teardown here."""
    wiring = _wire(
        monkeypatch,
        harness=(_SOURCE,),
        current=_SHA,
        tree=_checkout(tmp_path),
        catalog=_catalog(_provider_component()),
    )
    stack = create_stack(config=_config(tmp_path))
    collection = wiring.collections[0]
    async with stack._lifespan_manager():
        assert collection.starts == 1
        assert collection.closes == 0
    assert collection.closes == 1
    assert wiring.workers != []
    assert not hasattr(WorkerProvider, "close")


# -- frozen keyword surface (spec 14) ---------------------------------------


class TestCreateStackSignature:
    """``create_stack``'s keyword surface is a published contract.

    Every host adapter, the CLI, and every embedder calls this by keyword.
    A parameter renamed, reordered into a positional slot, or quietly added
    breaks callers this repository cannot see, so the tuple is pinned rather
    than described. ``create_plane`` grew ``extras``; ``create_stack`` did
    not, and this is where that stays true.
    """

    #: Exactly today's parameters, in today's order.
    PARAMETERS = (
        "collection",
        "config",
        "providers",
        "disable",
        "discover_entry_points",
        "enable_path_safety",
        "enable_response_limit",
        "response_limit_bytes",
        "validate_annotations",
        "instructions",
    )

    def test_the_parameter_names_are_exactly_the_frozen_tuple(self):
        assert tuple(inspect.signature(create_stack).parameters) == self.PARAMETERS

    def test_every_parameter_is_keyword_only(self):
        parameters = inspect.signature(create_stack).parameters
        kinds = {name: p.kind for name, p in parameters.items()}
        assert kinds == dict.fromkeys(self.PARAMETERS, inspect.Parameter.KEYWORD_ONLY)
