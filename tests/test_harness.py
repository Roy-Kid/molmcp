"""Mirrors ``src/molmcp/harness.py`` — the fold that serves N harness sources.

The first line names the mirrored module on purpose. Four modules in this
directory already begin ``test_harness_`` and none of them mirrors anything
under ``src/``: ``test_harness_agents.py``, ``test_harness_cases.py`` and
``test_harness_eval.py`` hold ``scripts/`` and its agent files to their
disciplines, and ``test_harness_catalog_fixture.py`` parses ``docs/``. This
one is the ``src/molmcp/harness.py`` mirror the layout rule asks for, and
covers only the symbols that module owns.

Four units are exercised here, each in isolation:

*``pointer_path`` is a security fix, not a formatting helper.*
``HarnessSource.name`` is governed only as "non-empty, whitespace-free":
``settings.py:152-159`` puts the ``/`` and ``@`` rejection in an ``elif``
that explicitly excludes ``name``, and the class docstring says why — an
operator who may name an index source ``MolCrafts`` may name a harness
source ``MolCrafts``. So ``HarnessSource(name="../../evil")`` constructs
today, and the moment a name is interpolated into ``harness.{name}.pointer``
it becomes path *structure* rather than a label. The guard is at the point of
use because that is the only place that knows the name is about to be a path
segment.

*``SourcedComponent`` pairs an origin with an untouched spec.*
``components/models.py:120-127`` pins ``id == f"{kind}.{name}"`` and
``_MEMBER_PATTERN`` admits nothing else, so ``official.provider.demo`` is not
a constructible id. The cross-source key is the ``(source, spec)`` pair, in
this layer — the arrangement ``tests/test_no_builtin_harness_source.py``
already spells out in its failure message.

*``fold_components`` is first-wins, and reports the loser.* The catalogs are
read from real ``harness.toml`` files written under ``tmp_path``: nothing here
patches ``load_harness_catalog``, because a suite that fakes the reader it
depends on proves only the call order (``notes.md:faked-seam-hides-broken-reader``).
Nothing fetches, no store is constructed, and no pointer is bound — how a
catalog reaches a checkout is ``activated_checkouts``' problem, and is covered
by the class below.

*``activated_checkouts`` is driven with no seam at all.* The five names
``tests/test_stack.py``'s ``_wire`` fakes — ``Activation``,
``ImmutableGitStore``, ``GitHubTransport``, ``load_harness_catalog`` and
``WorkerProvider`` — are the five this file never patches. That is the same
rule again, and it is the reason this class exists rather than one more
``_wire`` test: a seam proves the composition *order* and nothing whatsoever
about the functions it replaces. The store is planted by hand under
``tmp_path`` and the pointers are literal version-1 JSON, so a real
:class:`~molmcp.components.ImmutableGitStore` and a real
:meth:`~molmcp.components.Activation.bind` do the work.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from molmcp import harness
from molmcp.components import CatalogError, ComponentKind, ComponentSpec
from molmcp.components.activate import _POINTER_KEYS, ACTIVATION_VERSION
from molmcp.config import AppConfig, ConfigurationError
from molmcp.settings import HarnessSource

#: Frozen *and* slotted dataclasses answer a rebound field with either error,
#: depending on which guard fires first; the idiom is
#: ``tests/test_components/test_activate.py:74``.
_ASSIGN_ERRORS = (AttributeError, dataclasses.FrozenInstanceError)

#: Two distinct 40-character lowercase hex SHAs — the only shape
#: ``HarnessCatalog`` accepts as identity (``models.py:57``).
_OFFICIAL_SHA = "0123456789abcdef0123456789abcdef01234567"
_PRIVATE_SHA = "89abcdef0123456789abcdef0123456789abcdef"

#: The module logger the fold reports a displaced component through. Named
#: here rather than derived, so a rename has to pass through this file.
_LOGGER = "molmcp.harness"


def _provider(name: str, entrypoint: str) -> ComponentSpec:
    """One provider row. Its ``id`` is ``provider.<name>`` and cannot be else."""
    return ComponentSpec(
        kind=ComponentKind.PROVIDER,
        name=name,
        id=f"provider.{name}",
        path=f"providers/{name}/plane.py",
        entrypoint=entrypoint,
    )


def _skill(name: str) -> ComponentSpec:
    """One skill row, used only to prove the fold keeps to the asked-for kind."""
    return ComponentSpec(
        kind=ComponentKind.SKILL,
        name=name,
        id=f"skill.{name}",
        path=f"skills/{name}.md",
    )


def _catalog_toml(specs: Sequence[ComponentSpec]) -> str:
    """Render specs as the ``harness.toml`` a real checkout would carry.

    Every catalog must declare a ``daily`` and a ``dev`` bundle
    (``catalog.py:87-88``) and bundle members must be non-empty and resolve
    (``models.py:167``), so both bundles list every component in the file.
    No catalog-level ``requires`` is emitted: eligibility is
    ``load_harness_catalog``'s subject, not the fold's.
    """
    members = ", ".join(f'"{spec.id}"' for spec in specs)
    rows: list[str] = []
    for spec in specs:
        row = [
            "[[component]]",
            f'kind = "{spec.kind.value}"',
            f'name = "{spec.name}"',
            f'path = "{spec.path}"',
        ]
        if spec.entrypoint is not None:
            row.append(f'entrypoint = "{spec.entrypoint}"')
        rows.append("\n".join(row))
    for bundle in ("daily", "dev"):
        rows.append(
            f'[[component]]\nkind = "bundle"\nname = "{bundle}"\nmembers = [{members}]'
        )
    return "\n\n".join(rows) + "\n"


def _checkout(
    root: Path,
    source: str,
    sha: str,
    specs: Sequence[ComponentSpec],
) -> harness.Checkout:
    """A checkout whose tree really holds the catalog these specs describe."""
    tree = root / source / "tree"
    tree.mkdir(parents=True)
    (tree / "harness.toml").write_text(_catalog_toml(specs), encoding="utf-8")
    return harness.Checkout(sha=sha, tree=tree, source=source)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Only this module's warnings; a neighbour's INFO is not the report."""
    return [
        record
        for record in caplog.records
        if record.name == _LOGGER and record.levelno == logging.WARNING
    ]


def _entries(root: Path) -> list[Path]:
    """Everything that exists under *root*, for a before/after comparison."""
    return sorted(root.rglob("*"))


def _source(
    name: str,
    *,
    owner: str = "molcrafts",
    repo: str = "harness",
) -> HarnessSource:
    """One complete ``harness`` entry, the shape ``_harness_locator`` hands over.

    The coordinates are filled in because a real one always is by the time
    this function sees it, and are otherwise irrelevant: ``activated_checkouts``
    reads the pointer, never the repository.
    """
    return HarnessSource(name=name, owner=owner, repo=repo, ref="main")


def _config_and_root(tmp_path: Path) -> tuple[AppConfig, Path]:
    """A resolved config, and the cache root its store and pointers hang off.

    The root is read back off the config rather than recomputed from
    *tmp_path*: ``AppConfig.from_dict`` resolves the path, and on darwin
    ``/var`` is a symlink to ``/private/var``, so the two spellings are not
    the same string.
    """
    config = AppConfig.from_dict(
        {"schema_version": "2", "cache_dir": str(tmp_path / "cache")},
        workspace_root=tmp_path,
    )
    assert config.cache_dir is not None
    return config, config.cache_dir


def _publish_by_hand(
    store_root: Path,
    sha: str,
    *,
    owner: str = "molcrafts",
    repo: str = "harness",
) -> Path:
    """Plant one complete SHA directory the way the store reads it back.

    ``<store_root>/commits/<sha>/`` holding ``metadata.json`` and ``tree/`` is
    the layout ``components/store.py:43-52`` documents and the exact pair
    ``ImmutableGitStore.has`` checks at ``store.py:90-91``. It is written here
    rather than fetched: ``publish`` is the only path that reaches the network,
    and no test in this file calls it.

    Returns:
        The flattened catalog root — what ``tree_path(sha)`` will answer.
    """
    sha_dir = store_root / "commits" / sha
    tree = sha_dir / "tree"
    tree.mkdir(parents=True)
    (sha_dir / "metadata.json").write_text(
        json.dumps({"sha": sha, "owner": owner, "repo": repo}), encoding="utf-8"
    )
    return tree


def _pointer_payload(active: str | None) -> dict[str, object]:
    """A version-1 activation record, keyed exactly as ``_POINTER_KEYS``."""
    return {
        "version": ACTIVATION_VERSION,
        "active": active,
        "staging": None,
        "previous": None,
    }


def _write_pointer(path: Path, active: str | None) -> None:
    """Write one activation pointer file, JSON literal, no ``Activation``.

    Writing the file by hand is the point: ``stage`` and ``promote`` are the
    only writers in the product and neither has a production caller, so a test
    that reached for them would be exercising a path no install runs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_pointer_payload(active)), encoding="utf-8")


class TestPointerPath:
    """One pointer file per source, and a name that can only be a label.

    The guard's reserved set is exactly ``{".", ".."}`` — kept for symmetry
    with :data:`molmcp.components.store._RESERVED_SHA_KEYS`, **not** because
    either one traverses. Embedded as ``harness.{name}.pointer`` neither is a
    path segment at all: ``harness....pointer`` is one ordinary filename. The
    separator, absolute-path and empty checks are the ones doing the real
    work, and :meth:`test_the_dot_names_are_symmetry_and_the_separators_are_the_hole`
    pins that difference so nobody later "simplifies" the guard by dropping
    the half that matters.
    """

    def test_names_the_pointer_file_beside_the_store(self, tmp_path: Path) -> None:
        """``<root>/harness.<name>.pointer`` — a sibling of the store root."""
        root = tmp_path / "cache"
        assert harness.pointer_path(root, "official") == (
            root / "harness.official.pointer"
        )

    def test_the_pointer_stays_a_direct_child_of_the_root(self, tmp_path: Path) -> None:
        """One path segment, under the root it was handed, always."""
        root = tmp_path / "cache"
        result = harness.pointer_path(root, "official")
        assert result.parent == root
        assert result.name == "harness.official.pointer"

    def test_two_names_never_map_to_one_file(self, tmp_path: Path) -> None:
        """Distinct sources own distinct pointers, or activation is shared."""
        root = tmp_path / "cache"
        official = harness.pointer_path(root, "official")
        private = harness.pointer_path(root, "private")
        assert official != private
        assert {official.parent, private.parent} == {root}

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("..", id="reserved-dotdot"),
            pytest.param(".", id="reserved-dot"),
            pytest.param("../../evil", id="dotdot-escape"),
            pytest.param("../../../evil", id="dotdot-escape-deeper"),
            pytest.param("a/b", id="posix-separator"),
            pytest.param("a\\b", id="windows-separator"),
            pytest.param("/etc/passwd", id="absolute"),
            pytest.param("", id="empty"),
        ],
    )
    def test_refuses_a_name_that_cannot_be_one_path_segment(
        self, tmp_path: Path, name: str
    ) -> None:
        """Every hostile name is refused, and nothing lands on disk.

        The assertion that actually proves the guard is not the exception
        type — it is that the filesystem is untouched, at the root and at the
        place the naive ``<root>/harness.{name}.pointer`` would have written.
        A guard that raised *after* creating the parent directory would pass
        an exception-only test.
        """
        root = tmp_path / "cache" / "molmcp"
        root.mkdir(parents=True)
        before = _entries(tmp_path)

        with pytest.raises(ConfigurationError) as excinfo:
            harness.pointer_path(root, name)

        message = str(excinfo.value)
        # ``{name!r}`` is this repo's register for naming a rejected value
        # (``store.py:180``, ``models.py:124``, ``settings.py:152``), and the
        # only one that can name the empty string at all.
        assert repr(name) in message
        assert _entries(tmp_path) == before
        naive = Path(os.path.normpath(root / f"harness.{name}.pointer"))
        assert not naive.exists()

    def test_the_dot_names_are_symmetry_and_the_separators_are_the_hole(
        self, tmp_path: Path
    ) -> None:
        """Which refusals are load-bearing, stated as an assertion.

        ``.`` and ``..`` interpolate into an ordinary filename that stays
        inside the root; a separator or a deep ``..`` is what turns the name
        into structure. Both are refused, but only the second group closes a
        hole — this is the fact a later "simplification" would delete.
        """
        root = tmp_path / "cache" / "molmcp"

        for harmless in (".", ".."):
            naive = Path(os.path.normpath(root / f"harness.{harmless}.pointer"))
            assert naive.parent == root

        assert Path(os.path.normpath(root / "harness.a/b.pointer")).parent != root
        escaped = Path(os.path.normpath(root / "harness.../../../evil.pointer"))
        assert not escaped.is_relative_to(root)


class TestSourcedComponent:
    """The ``(source_name, component_id)`` pair, built where it belongs.

    ``tests/test_no_builtin_harness_source.py:69-75`` forbids the harness
    source from ``components/`` and says why in its own failure message:
    "Cross-source namespacing belongs to the resolution layer, keyed by a
    (source_name, component_id) pair, and never enters ``ComponentSpec.id``."
    This class is that sentence, executable.
    """

    def test_is_a_frozen_slots_dataclass_of_source_and_spec(self) -> None:
        """Two fields, in that order, and no instance ``__dict__``."""
        assert dataclasses.is_dataclass(harness.SourcedComponent)
        params = harness.SourcedComponent.__dataclass_params__
        assert params.frozen is True
        assert "__slots__" in vars(harness.SourcedComponent)
        names = tuple(f.name for f in dataclasses.fields(harness.SourcedComponent))
        assert names == ("source", "spec")

    def test_carries_the_component_id_unchanged(self) -> None:
        """A component out of ``official`` still has id ``provider.demo``."""
        spec = _provider("demo", "demo.plane:DemoPlane")
        sourced = harness.SourcedComponent(source="official", spec=spec)
        assert sourced.source == "official"
        assert sourced.spec is spec
        assert sourced.spec.id == "provider.demo"
        assert sourced.spec.name == "demo"

    def test_the_namespaced_id_is_not_even_constructible(self) -> None:
        """Why the pair exists: ``ComponentSpec`` refuses the other design.

        Recorded here rather than assumed — the day ``models.py`` relaxes
        this, the fold's whole shape is back on the table.
        """
        with pytest.raises(CatalogError):
            ComponentSpec(
                kind=ComponentKind.PROVIDER,
                name="demo",
                id="official.provider.demo",
                path="providers/demo/plane.py",
                entrypoint="demo.plane:DemoPlane",
            )

    def test_assignment_to_either_field_raises(self) -> None:
        """Frozen means the origin cannot drift away from its spec."""
        sourced = harness.SourcedComponent(
            source="official",
            spec=_provider("demo", "demo.plane:DemoPlane"),
        )
        with pytest.raises(_ASSIGN_ERRORS):
            sourced.source = "private"
        with pytest.raises(_ASSIGN_ERRORS):
            sourced.spec = _provider("other", "other.plane:OtherPlane")

    def test_no_attribute_holds_a_namespaced_id(self) -> None:
        """The pair carries the origin beside the id, never folded into it."""
        sourced = harness.SourcedComponent(
            source="official",
            spec=_provider("demo", "demo.plane:DemoPlane"),
        )
        assert not hasattr(sourced, "id")


class TestFoldComponents:
    """First-wins on ``spec.id`` in source order; the loser is reported.

    For ``ComponentKind.PROVIDER`` an id collision *is* a plane-name
    collision (``id == f"provider.{name}"``), so keying on the id is what
    stops two ``WorkerProvider(name="demo")`` mounting under one namespace.
    ``kept`` is the answer to that; there is deliberately no ``displaced``
    field — see :meth:`test_the_loser_is_reported_and_not_stored`.
    """

    def _two_sources(
        self,
        tmp_path: Path,
        first: Sequence[ComponentSpec],
        second: Sequence[ComponentSpec],
    ) -> tuple[harness.Checkout, ...]:
        return (
            _checkout(tmp_path, "official", _OFFICIAL_SHA, first),
            _checkout(tmp_path, "private", _PRIVATE_SHA, second),
        )

    def test_the_first_source_wins_a_contested_id(self, tmp_path: Path) -> None:
        """Two ``provider.demo`` rows, one kept, and it is the first file's."""
        winner = _provider("demo", "official.plane:DemoPlane")
        loser = _provider("demo", "private.plane:DemoPlane")
        checkouts = self._two_sources(tmp_path, [winner], [loser])

        fold = harness.fold_components(checkouts, ComponentKind.PROVIDER)

        assert len(fold.kept) == 1
        kept = fold.kept[0]
        assert kept.source == "official"
        assert kept.spec.id == "provider.demo"
        assert kept.spec.entrypoint == "official.plane:DemoPlane"

    def test_distinct_ids_are_kept_in_source_then_catalog_order(
        self, tmp_path: Path
    ) -> None:
        """Source order outside, catalog order within — both, and only both."""
        alpha = _provider("alpha", "official.plane:Alpha")
        beta = _provider("beta", "official.plane:Beta")
        gamma = _provider("gamma", "private.plane:Gamma")
        checkouts = self._two_sources(tmp_path, [alpha, beta], [gamma])

        fold = harness.fold_components(checkouts, ComponentKind.PROVIDER)

        assert [(sc.source, sc.spec.id) for sc in fold.kept] == [
            ("official", "provider.alpha"),
            ("official", "provider.beta"),
            ("private", "provider.gamma"),
        ]

    def test_only_the_requested_kind_is_folded(self, tmp_path: Path) -> None:
        """A catalog is an inventory of every kind; one fold reads one kind."""
        checkouts = self._two_sources(
            tmp_path,
            [_skill("daily"), _provider("alpha", "official.plane:Alpha")],
            [_skill("nightly")],
        )

        fold = harness.fold_components(checkouts, ComponentKind.PROVIDER)

        assert [sc.spec.id for sc in fold.kept] == ["provider.alpha"]

    def test_names_is_the_kept_component_name_set(self, tmp_path: Path) -> None:
        """``fold.names`` is the set ``create_stack`` XORs entry points against."""
        checkouts = self._two_sources(
            tmp_path,
            [_provider("alpha", "official.plane:Alpha")],
            [_provider("gamma", "private.plane:Gamma")],
        )

        fold = harness.fold_components(checkouts, ComponentKind.PROVIDER)

        assert fold.names == frozenset({"alpha", "gamma"})

    def test_a_contested_name_appears_once_in_names(self, tmp_path: Path) -> None:
        """One mount per plane id, which is what a set of kept names buys."""
        checkouts = self._two_sources(
            tmp_path,
            [_provider("demo", "official.plane:DemoPlane")],
            [_provider("demo", "private.plane:DemoPlane")],
        )

        fold = harness.fold_components(checkouts, ComponentKind.PROVIDER)

        assert fold.names == frozenset({"demo"})
        assert len(fold.kept) == 1

    def test_specs_from_returns_one_sources_specs_in_catalog_order(
        self, tmp_path: Path
    ) -> None:
        """The overlay arm needs per-checkout grouping; this is that grouping."""
        alpha = _provider("alpha", "official.plane:Alpha")
        beta = _provider("beta", "official.plane:Beta")
        gamma = _provider("gamma", "private.plane:Gamma")
        checkouts = self._two_sources(tmp_path, [alpha, beta], [gamma])

        fold = harness.fold_components(checkouts, ComponentKind.PROVIDER)

        assert fold.specs_from("official") == (alpha, beta)
        assert fold.specs_from("private") == (gamma,)

    def test_specs_from_omits_a_displaced_spec(self, tmp_path: Path) -> None:
        """The loser is not kept, so its own source does not report it either."""
        checkouts = self._two_sources(
            tmp_path,
            [_provider("demo", "official.plane:DemoPlane")],
            [_provider("demo", "private.plane:DemoPlane")],
        )

        fold = harness.fold_components(checkouts, ComponentKind.PROVIDER)

        assert fold.specs_from("private") == ()

    def test_specs_from_an_unknown_source_is_empty(self, tmp_path: Path) -> None:
        """A source nobody folded has no specs — an empty tuple, not a raise."""
        checkouts = self._two_sources(
            tmp_path,
            [_provider("alpha", "official.plane:Alpha")],
            [_provider("gamma", "private.plane:Gamma")],
        )

        fold = harness.fold_components(checkouts, ComponentKind.PROVIDER)

        assert fold.specs_from("nobody") == ()

    def test_the_fold_carries_the_checkouts_it_was_folded_from(
        self, tmp_path: Path
    ) -> None:
        """One owner of ``source -> tree``: the ``Checkout`` objects themselves.

        ``checkout_planes(fold)`` takes one argument because of this. A
        parallel map would be a second owner of a fact ``Checkout`` already
        holds, and a fold built from a different list would answer ``()`` from
        ``specs_from`` with no error at all.
        """
        checkouts = self._two_sources(
            tmp_path,
            [_provider("alpha", "official.plane:Alpha")],
            [_provider("gamma", "private.plane:Gamma")],
        )

        fold = harness.fold_components(checkouts, ComponentKind.PROVIDER)

        assert fold.checkouts == tuple(checkouts)
        assert [checkout.source for checkout in fold.checkouts] == [
            "official",
            "private",
        ]

    def test_no_checkouts_folds_to_an_empty_result(self) -> None:
        """No harness source is not an error; it is the empty fold."""
        fold = harness.fold_components((), ComponentKind.PROVIDER)

        assert fold.checkouts == ()
        assert fold.kept == ()
        assert fold.names == frozenset()
        assert fold.specs_from("official") == ()

    def test_the_loser_is_reported_and_not_stored(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Exactly one warning, naming winner, loser and the contested id.

        There is no ``displaced`` field: nothing in production would read it,
        and this repo's own first-wins precedents
        (``discovery/overlay/catalog.py:83``, ``conventions.py:95``) drop
        losers without recording them. The warning is what earns its keep; a
        field whose only reader is a test does not.
        """
        checkouts = self._two_sources(
            tmp_path,
            [_provider("demo", "official.plane:DemoPlane")],
            [_provider("demo", "private.plane:DemoPlane")],
        )

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            fold = harness.fold_components(checkouts, ComponentKind.PROVIDER)

        records = _warnings(caplog)
        assert len(records) == 1
        message = records[0].getMessage()
        assert "official" in message
        assert "private" in message
        assert "provider.demo" in message
        assert not hasattr(fold, "displaced")
        assert tuple(f.name for f in dataclasses.fields(fold)) == (
            "checkouts",
            "kept",
        )

    def test_an_uncontested_fold_reports_nothing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A warning per ordinary serve would train the operator to ignore it."""
        checkouts = self._two_sources(
            tmp_path,
            [_provider("alpha", "official.plane:Alpha")],
            [_provider("gamma", "private.plane:Gamma")],
        )

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            harness.fold_components(checkouts, ComponentKind.PROVIDER)

        assert _warnings(caplog) == []

    def test_the_fold_is_frozen_and_slotted(self, tmp_path: Path) -> None:
        """``names`` is derived, and neither stored field can be rebound."""
        checkouts = self._two_sources(
            tmp_path,
            [_provider("alpha", "official.plane:Alpha")],
            [_provider("gamma", "private.plane:Gamma")],
        )

        fold = harness.fold_components(checkouts, ComponentKind.PROVIDER)

        assert fold.__dataclass_params__.frozen is True
        assert "__slots__" in vars(harness.ComponentFold)
        assert isinstance(harness.ComponentFold.names, property)
        with pytest.raises(_ASSIGN_ERRORS):
            fold.kept = ()
        with pytest.raises(_ASSIGN_ERRORS):
            fold.checkouts = ()


class TestActivatedCheckouts:
    """The real function, over a real store and real pointer files.

    Nothing in this class monkeypatches ``Activation``, ``ImmutableGitStore``,
    ``GitHubTransport``, ``load_harness_catalog`` or ``WorkerProvider`` — the
    five names ``tests/test_stack.py``'s ``_wire`` seam replaces. A suite built
    only on that seam proves the composition *order* and nothing whatsoever
    about those five, which is not hypothetical: link 01 left ``molmcp serve``
    broken for every install while 1852 tests passed, because the only
    occurrence of ``_harness_locator`` under ``tests/`` was a test *name*
    (``notes.md:faked-seam-hides-broken-reader``).

    So the store is planted by hand — ``<root>/harness/commits/<sha>/`` with a
    ``metadata.json`` and a ``tree/`` — and the pointers are literal version-1
    JSON. Nothing fetches: ``GitHubTransport.__init__`` (``git.py:82-89``)
    stores a token and does no I/O, and ``publish`` is never called.

    The planted trees are left **empty**, deliberately. ``activated_checkouts``
    binds a pointer and hands back a tree; reading ``harness.toml`` is
    ``fold_components``' job. A tree with no catalog in it is how an
    implementation that read one here would be caught.
    """

    def test_the_hand_written_pointer_is_the_records_own_shape(self) -> None:
        """The plant is checked against the contract, not against a memory.

        Every pointer in this class is written as a JSON literal, so the two
        facts the per-source-pointer route was chosen to preserve — version 1,
        and exactly these four keys — have to be asserted somewhere or the
        whole class could drift away from ``activate.py`` while staying green.
        """
        payload = _pointer_payload(_OFFICIAL_SHA)
        assert set(payload) == _POINTER_KEYS
        assert payload["version"] == 1

    def test_two_sources_yield_two_checkouts_in_file_order(
        self, tmp_path: Path
    ) -> None:
        """Each named source contributes its own commit, its own tree, its own name.

        This is the line link 01 lost: ``server.py:336`` consumed the ordered
        tuple of sources as a *boolean* and then bound one pointer, so a second
        entry changed nothing about what was served.
        """
        config, root = _config_and_root(tmp_path)
        official_tree = _publish_by_hand(root / "harness", _OFFICIAL_SHA)
        private_tree = _publish_by_hand(
            root / "harness", _PRIVATE_SHA, owner="acme", repo="tooling"
        )
        _write_pointer(harness.pointer_path(root, "official"), _OFFICIAL_SHA)
        _write_pointer(harness.pointer_path(root, "private"), _PRIVATE_SHA)

        checkouts = harness.activated_checkouts(
            config, (_source("official"), _source("private"))
        )

        assert isinstance(checkouts, tuple)
        assert [checkout.source for checkout in checkouts] == ["official", "private"]
        assert [checkout.sha for checkout in checkouts] == [
            _OFFICIAL_SHA,
            _PRIVATE_SHA,
        ]
        assert [checkout.tree for checkout in checkouts] == [
            official_tree,
            private_tree,
        ]
        assert all(checkout.tree.is_dir() for checkout in checkouts)

    def test_the_order_is_the_settings_list_order(self, tmp_path: Path) -> None:
        """File order, not directory order: the operator's priority control.

        ``fold_components`` resolves a contested id first-wins over this
        sequence, so the order this function returns is the only thing
        deciding which source's ``provider.demo`` gets served.
        """
        config, root = _config_and_root(tmp_path)
        _publish_by_hand(root / "harness", _OFFICIAL_SHA)
        _publish_by_hand(root / "harness", _PRIVATE_SHA, owner="acme", repo="tooling")
        _write_pointer(harness.pointer_path(root, "official"), _OFFICIAL_SHA)
        _write_pointer(harness.pointer_path(root, "private"), _PRIVATE_SHA)

        checkouts = harness.activated_checkouts(
            config,
            (_source("private", owner="acme", repo="tooling"), _source("official")),
        )

        assert [checkout.source for checkout in checkouts] == ["private", "official"]
        assert [checkout.sha for checkout in checkouts] == [
            _PRIVATE_SHA,
            _OFFICIAL_SHA,
        ]

    def test_exactly_one_commits_directory_holds_every_activated_sha(
        self, tmp_path: Path
    ) -> None:
        """One store, several pointers — the whole shape of this link.

        ``ImmutableGitStore`` records provenance per SHA and refuses a SHA
        claimed by a second repository, so a per-source root would buy nothing
        and would strand every already-published tree. The pointers are what
        multiply, and they are plain siblings of the one store root.
        """
        config, root = _config_and_root(tmp_path)
        _publish_by_hand(root / "harness", _OFFICIAL_SHA)
        _publish_by_hand(root / "harness", _PRIVATE_SHA, owner="acme", repo="tooling")
        _write_pointer(harness.pointer_path(root, "official"), _OFFICIAL_SHA)
        _write_pointer(harness.pointer_path(root, "private"), _PRIVATE_SHA)

        harness.activated_checkouts(config, (_source("official"), _source("private")))

        commits = sorted(path for path in root.rglob("commits") if path.is_dir())
        assert commits == [root / "harness" / "commits"]
        assert sorted(path.name for path in commits[0].iterdir()) == sorted(
            [_OFFICIAL_SHA, _PRIVATE_SHA]
        )
        assert sorted(path.name for path in root.iterdir()) == [
            "harness",
            "harness.official.pointer",
            "harness.private.pointer",
        ]

    def test_two_sources_activating_one_sha_share_the_one_tree(
        self, tmp_path: Path
    ) -> None:
        """Two pointers may name the same commit; the store still holds it once."""
        config, root = _config_and_root(tmp_path)
        tree = _publish_by_hand(root / "harness", _OFFICIAL_SHA)
        _write_pointer(harness.pointer_path(root, "official"), _OFFICIAL_SHA)
        _write_pointer(harness.pointer_path(root, "private"), _OFFICIAL_SHA)

        checkouts = harness.activated_checkouts(
            config, (_source("official"), _source("private"))
        )

        assert [checkout.source for checkout in checkouts] == ["official", "private"]
        assert {checkout.tree for checkout in checkouts} == {tree}
        assert [path.name for path in (root / "harness" / "commits").iterdir()] == [
            _OFFICIAL_SHA
        ]

    @pytest.mark.parametrize(
        ("activated", "absent"),
        [
            pytest.param("official", "private", id="second-source-unactivated"),
            pytest.param("private", "official", id="first-source-unactivated"),
        ],
    )
    def test_a_source_with_no_pointer_file_is_skipped(
        self, tmp_path: Path, activated: str, absent: str
    ) -> None:
        """A named-but-unactivated source is not an error; it contributes nothing.

        Nothing activated serves exactly like an unset locator, and it does so
        *per source*: the neighbour still yields its checkout. The missing
        pointer is also not created on the way past — ``Activation.bind`` turns
        a missing file into an in-memory empty record and writes nothing, and
        serving must never be the thing that writes an activation.
        """
        config, root = _config_and_root(tmp_path)
        shas = {"official": _OFFICIAL_SHA, "private": _PRIVATE_SHA}
        _publish_by_hand(root / "harness", shas[activated])
        _write_pointer(harness.pointer_path(root, activated), shas[activated])

        checkouts = harness.activated_checkouts(
            config, (_source("official"), _source("private"))
        )

        assert [checkout.source for checkout in checkouts] == [activated]
        assert checkouts[0].sha == shas[activated]
        assert not harness.pointer_path(root, absent).exists()

    def test_a_pointer_with_no_active_sha_contributes_nothing(
        self, tmp_path: Path
    ) -> None:
        """A pointer file that exists but activates nothing is the same skip."""
        config, root = _config_and_root(tmp_path)
        _publish_by_hand(root / "harness", _OFFICIAL_SHA)
        _write_pointer(harness.pointer_path(root, "official"), _OFFICIAL_SHA)
        _write_pointer(harness.pointer_path(root, "private"), None)

        checkouts = harness.activated_checkouts(
            config, (_source("official"), _source("private"))
        )

        assert [checkout.source for checkout in checkouts] == ["official"]

    def test_no_sources_is_the_empty_result_and_touches_no_disk(
        self, tmp_path: Path
    ) -> None:
        """The un-harnessed install: no checkout, and no cache root created."""
        config, root = _config_and_root(tmp_path)

        assert harness.activated_checkouts(config, ()) == ()
        assert not root.exists()

    def test_an_unpublished_sha_names_both_the_sha_and_its_source(
        self, tmp_path: Path
    ) -> None:
        """The error identifies *which* source is broken, not just the SHA.

        Naming the SHA and the store root identifies nothing under N sources:
        the operator has to know which entry of the ``harness`` list to go and
        fix. A missing tree is named rather than silently re-fetched, because
        serving a different commit than the one that was activated is the one
        outcome nobody asked for.

        The second source is named ``acme`` rather than ``private`` on purpose:
        on darwin ``tmp_path`` lives under ``/private/var``, so ``"private" in
        message`` would pass on the store root alone and prove nothing.
        """
        config, root = _config_and_root(tmp_path)
        _publish_by_hand(root / "harness", _OFFICIAL_SHA)
        _write_pointer(harness.pointer_path(root, "official"), _OFFICIAL_SHA)
        _write_pointer(harness.pointer_path(root, "acme"), _PRIVATE_SHA)

        with pytest.raises(ConfigurationError) as excinfo:
            harness.activated_checkouts(
                config,
                (_source("official"), _source("acme", owner="acme", repo="tooling")),
            )

        message = str(excinfo.value)
        assert _PRIVATE_SHA in message
        assert "acme" in message
        # The healthy neighbour is not implicated in its neighbour's failure.
        assert _OFFICIAL_SHA not in message

    def test_two_entries_sharing_a_name_are_refused(self, tmp_path: Path) -> None:
        """One name, one pointer file: two entries under it is not resolvable.

        Two sources named alike would share ``harness.<name>.pointer`` — so the
        second silently serves whatever the first activated — and would make
        ``ComponentFold.specs_from(source)`` ambiguous. ``collection/index.py:75``
        is the precedent: a duplicate *origin* name is the one hard error.
        """
        config, root = _config_and_root(tmp_path)
        before = _entries(tmp_path)

        with pytest.raises(ConfigurationError) as excinfo:
            harness.activated_checkouts(
                config,
                (_source("official"), _source("official", owner="acme", repo="tool")),
            )

        assert "official" in str(excinfo.value)
        assert _entries(tmp_path) == before
        assert not root.exists()

    def test_two_names_differing_only_in_case_are_refused(self, tmp_path: Path) -> None:
        """``official`` and ``Official`` are one pointer file on this platform.

        The comparison is ``casefold()``, not equality: ``HarnessSource``
        deliberately permits ``MolCrafts`` casing, so an exact check passes
        this pair — and on darwin (this repo's dev platform) and on Windows
        both names map to one file, which is exactly the hazard the check
        exists for.

        Both spellings must appear in the message. Naming only the casefolded
        key points at neither line of the settings file the operator has to
        edit, and the whole value of this error is sending them there.
        """
        config, _ = _config_and_root(tmp_path)

        with pytest.raises(ConfigurationError) as excinfo:
            harness.activated_checkouts(
                config,
                (_source("official"), _source("Official", owner="acme", repo="tool")),
            )

        message = str(excinfo.value)
        assert "official" in message
        assert "Official" in message

    def test_an_unusable_source_name_is_refused_before_anything_is_read(
        self, tmp_path: Path
    ) -> None:
        """The traversal guard is reached through this function, not only directly.

        ``HarnessSource(name="a/b")`` constructs today — ``settings.py:152-159``
        excludes ``name`` from the ``/`` rejection — so ``pointer_path``'s guard
        is the only thing between a settings file and a write outside the cache
        root. A ``TestPointerPath`` that passed while this function built its
        paths by hand would prove nothing.
        """
        config, root = _config_and_root(tmp_path)
        root.mkdir(parents=True)
        before = _entries(tmp_path)

        with pytest.raises(ConfigurationError) as excinfo:
            harness.activated_checkouts(config, (_source("a/b"),))

        assert repr("a/b") in str(excinfo.value)
        assert _entries(tmp_path) == before

    def test_a_stale_legacy_pointer_is_named_once_and_never_read(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``<root>/harness.pointer`` is reported, and contributes no checkout.

        The legacy file here names a SHA that *is* published, so an
        implementation that fell back to reading it would hand back a checkout
        and fail this test on the empty result rather than on the warning. That
        is the assertion that matters: authority stays unambiguous because the
        legacy file is never read, the shape ``CLAUDE.md``'s stranded-orphan
        rule asks for.

        The probe is one ``legacy.exists()`` plus one ``pointer_path(...)``
        existence check per source, evaluated once *before* the per-source
        loop — filesystem contact this function otherwise never makes.
        """
        config, root = _config_and_root(tmp_path)
        _publish_by_hand(root / "harness", _OFFICIAL_SHA)
        _write_pointer(root / "harness.pointer", _OFFICIAL_SHA)

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            checkouts = harness.activated_checkouts(
                config, (_source("official"), _source("private"))
            )

        assert checkouts == ()
        records = _warnings(caplog)
        assert len(records) == 1
        message = records[0].getMessage()
        assert "harness.pointer" in message

    def test_a_half_migrated_install_is_not_warned(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Stale file plus one activated source: no warning, deliberately.

        Nothing in the product ever writes ``harness.pointer`` — there is no
        caller of ``Activation.stage`` / ``promote`` / ``rollback`` anywhere in
        ``src/`` — so one notice at the point it can still matter is the whole
        budget. A test that did not pin this would let someone "helpfully" make
        the probe unconditional, and a warning on every ordinary serve trains
        the operator to ignore the one that mattered.
        """
        config, root = _config_and_root(tmp_path)
        _publish_by_hand(root / "harness", _OFFICIAL_SHA)
        _write_pointer(root / "harness.pointer", _PRIVATE_SHA)
        _write_pointer(harness.pointer_path(root, "official"), _OFFICIAL_SHA)

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            checkouts = harness.activated_checkouts(
                config, (_source("official"), _source("private"))
            )

        assert [checkout.source for checkout in checkouts] == ["official"]
        assert [checkout.sha for checkout in checkouts] == [_OFFICIAL_SHA]
        assert _warnings(caplog) == []

    def test_an_install_with_no_legacy_pointer_reports_nothing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The ordinary serve is silent — including the unactivated source."""
        config, root = _config_and_root(tmp_path)
        _publish_by_hand(root / "harness", _OFFICIAL_SHA)
        _write_pointer(harness.pointer_path(root, "official"), _OFFICIAL_SHA)

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            harness.activated_checkouts(
                config, (_source("official"), _source("private"))
            )

        assert _warnings(caplog) == []
