"""FastMCP composition: core + namespaced provider mounts."""

from __future__ import annotations

import ast
import inspect
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from molmcp import CollectionIndex, cli, create_plane, create_stack, runtime, server
from molmcp import harness as harness_module
from molmcp.components import (
    ALLOWED_REQUIRES,
    BundleSpec,
    ComponentKind,
    ComponentSpec,
    HarnessCatalog,
)
from molmcp.config import AppConfig, ConfigurationError
from molmcp.provider_worker.worker import WorkerProvider
from molmcp.settings import HarnessSource, Settings, SettingsError


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
_SOURCE = HarnessSource(name="official", owner="molcrafts", repo="harness", ref="main")
_OTHER = HarnessSource(name="private", owner="acme", repo="tooling", ref="trunk")
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


def _catalog(*components: ComponentSpec, sha: str = _SHA) -> HarnessCatalog:
    """A real catalog: the two required bundles plus *components*.

    ``sha`` is the commit the catalog claims to describe. It matters only
    when two sources are activated at two different commits, because the SHA
    is the one argument a faked ``load_harness_catalog`` can tell two
    checkouts apart by — every checkout in this suite shares one tree.
    """
    return HarnessCatalog(
        sha=sha,
        requires=(),
        components=(_SKILL, *components),
        bundles=(
            BundleSpec(name="daily", members=("skill.daily",)),
            BundleSpec(name="dev", members=("skill.daily",)),
        ),
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


def _checkout(tmp_path: Path) -> Path:
    """A tree holding ``providers/demo/`` as a directory and a module in it."""
    tree = tmp_path / "tree"
    package = tree / "providers" / "demo"
    package.mkdir(parents=True)
    (package / "plane.py").write_text("", encoding="utf-8")
    return tree


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
        root: str | Path,
        sha: str,
        supported_capabilities: object,
    ) -> HarnessCatalog:
        wiring.catalogs.append(
            {"root": Path(root), "sha": sha, "capabilities": supported_capabilities}
        )
        if catalogs is None:
            return resolved_catalog
        if sha not in catalogs:
            raise AssertionError(
                f"catalogs= names one catalog per activated SHA and was "
                f"never told about {sha!r}; it names {sorted(catalogs)}"
            )
        return catalogs[sha]

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


@pytest.mark.parametrize(
    ("source", "missing"),
    [
        (HarnessSource(name="mine", owner="molcrafts"), ("repo", "ref")),
        (HarnessSource(name="mine", owner="molcrafts", repo="harness"), ("ref",)),
    ],
)
def test_a_partial_entry_names_the_entry_and_every_missing_field(
    tmp_path,
    monkeypatch,
    source: HarnessSource,
    missing: tuple[str, ...],
):
    """A half-authored entry is a ConfigurationError, not a guess.

    The entry's ``name`` is its completion address now that the settings
    are a list, so the message has to carry it: "repo is not set" points at
    no file position an operator can go and edit.
    """
    _wire(monkeypatch, harness=(source,))
    with pytest.raises(ConfigurationError) as excinfo:
        create_stack(config=_config(tmp_path))
    message = str(excinfo.value)
    assert source.name in message
    for field_name in missing:
        assert field_name in message
    assert not isinstance(excinfo.value, SettingsError)


def test_a_named_entry_with_no_coordinates_is_an_error_not_an_unset_harness(
    tmp_path, monkeypatch
):
    """Naming a source is a claim; the empty *list* is the way to unset."""
    _wire(monkeypatch, harness=(HarnessSource(name="mine"),))
    with pytest.raises(ConfigurationError) as excinfo:
        create_stack(config=_config(tmp_path))
    message = str(excinfo.value)
    assert "mine" in message
    for field_name in ("owner", "repo", "ref"):
        assert field_name in message


def test_an_incomplete_second_entry_raises_after_a_complete_first(
    tmp_path, monkeypatch
):
    """No entry is ever skipped: serving past one would serve the wrong repo.

    Skipping the unfinished entry and carrying on from its neighbour is
    refused for the reason a built-in default is refused — it would serve
    code from a repository the operator did not select.
    """
    _wire(monkeypatch, harness=(_SOURCE, HarnessSource(name="private", owner="acme")))
    with pytest.raises(ConfigurationError) as excinfo:
        create_stack(config=_config(tmp_path))
    message = str(excinfo.value)
    assert "private" in message
    assert "repo" in message
    assert "ref" in message


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


# -- the reader itself, over a real settings file ---------------------------
#
# Every test above fakes ``load_settings`` through the ``_wire`` seam, so a
# reader that cannot read the type it is handed passes all of them. These two
# call the real ``_harness_locator`` against a settings file on disk, under a
# temporary home so no developer's own ``~/.molmcp`` can reach the assertion.


def _home_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: dict[str, object]
) -> None:
    """Point ``~`` and the working directory at hermetic temporary trees."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".molmcp").mkdir(parents=True)
    project.mkdir()
    (home / ".molmcp" / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(project)


def test_the_real_locator_reads_the_named_sources_off_disk(tmp_path, monkeypatch):
    """The unfaked reader over a real file, in file order."""
    _home_settings(
        tmp_path,
        monkeypatch,
        {
            "harness": [
                {
                    "name": "official",
                    "owner": "molcrafts",
                    "repo": "harness",
                    "ref": "main",
                },
                {"name": "private", "owner": "acme", "repo": "tooling", "ref": "trunk"},
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


def test_a_name_only_entry_from_the_verb_makes_the_real_locator_raise(
    tmp_path, monkeypatch
):
    """The serve-time price of a half-authored entry, paid end to end.

    ``molmcp config harness set --name mine`` exits 0 — the settings layer
    accepts a named entry with no coordinates on purpose, because
    :data:`server._HARNESS_KEYS` is the *only* completeness rule and the CLI
    deliberately does not carry a second copy of it. The cost is that every
    subsequent ``molmcp serve`` refuses until the coordinates arrive, and
    that cost belongs in a test rather than in an operator's afternoon: the
    incomplete-entry raise has no other coverage in this suite.

    Both halves are real. The write goes through ``cli.main`` to the actual
    ``settings.set_harness_source`` and lands on disk, and the read is the
    unfaked ``_harness_locator``. The ``_wire`` seam every composition test
    above uses is deliberately absent here — a faked seam is what let a
    broken reader of this exact key look green once already.

    The expected field names are derived from ``server._HARNESS_KEYS``, never
    written out as ``owner, repo, ref``. A literal triple would pass just as
    well against ``settings._HARNESS_ENTRY_KEYS``, which also carries
    ``name``, erasing the distinction ``server.py`` documents between what an
    entry may write and what it must have filled in.
    """
    _home_settings(tmp_path, monkeypatch, {})

    assert cli.main(["config", "harness", "set", "--name", "mine"]) == 0

    with pytest.raises(ConfigurationError) as excinfo:
        server._harness_locator()

    message = str(excinfo.value)
    assert "mine" in message
    assert [key for key in server._HARNESS_KEYS if key not in message] == []


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
        assert call["root"] == tree
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
