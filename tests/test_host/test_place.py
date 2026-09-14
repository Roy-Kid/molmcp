"""Placing catalog-declared components from an activated commit into a host.

Mirrors ``src/molmcp/host/place.py``. This is the last link of the pinned
chain: ``molmcp harness sync`` publishes a commit tree under
``cacheDir/harness/commits/<sha>/tree`` and moves that source's activation
pointer onto it, and ``molmcp init`` has to be able to install what that
tree's ``harness.toml`` declares. It could not: ``install.materialize_daily``
reads ``<source>/daily/skills/<name>/``, a layout a harness checkout does not
have, so against a real one it installs nothing.

The seam this module tests is the fix, and its shape is the design decision
under test. ``src/molmcp/host/`` is stdlib-only and imports no other
``molmcp`` module; that convention is kept, so ``host/`` is never told what a
``HarnessCatalog`` is. Instead the caller — which already holds the fold, the
checkout trees and each catalog's ``component_root`` — resolves every
component down to a plain description of one file to place, and hands those
descriptions over:

    ComponentFile(id=..., kind=..., relative=..., source=...)

Four stdlib-expressible fields, no catalog type among them. ``host/`` then
owns exactly one thing the caller does not: which host directory a *kind*
belongs in. ``place_components`` answers with a ``PlacementReport`` rather
than a bare tuple, because two of the rules below are about what a run
*decided* — that a component was skipped, and that a destination already
existed — and neither is visible in a list of paths.

Two rules are load-bearing enough to say out loud here:

* **The tree is never globbed.** ``place_components`` copies the files it is
  handed and reads nothing else, which is how "what is installed came from
  the activated commit" survives at this seam. The commit-pinning half of
  that chain — that the published tree holds committed content only — is
  proven where the publishing happens, not here.
* **The managed usage skill is never clobbered.** ``install_skill`` owns
  ``skills/molcrafts/``; ``materialize_daily`` already refuses to write into
  a directory named ``SKILL_NAME``, and that protection has to survive a
  catalog that declares a component there.

``Path.home`` is patched to ``tmp_path`` so every destination is the real
layout without touching the developer's home. No environment variable is
read: ``tests/test_no_env_switches.py`` already scans every module under
``src/molmcp`` for that, so it is not restated here.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import sys
from pathlib import Path
from typing import Literal

import pytest

from molmcp.host import (
    SKILL_NAME,
    SKIP_MANAGED_USAGE_SKILL,
    SKIP_NO_HOST_DESTINATION,
    ComponentFile,
    PlacementReport,
    place_components,
)

HostName = Literal["grok", "claude", "cursor", "codex"]

SRC = Path(__file__).resolve().parents[2] / "src" / "molmcp"

#: The module under test, read as data by the isolation check below.
PLACE_SOURCE = SRC / "host" / "place.py"

#: Fixture markers. Each one names where its file came from, so a body that
#: turns up in the wrong destination says so.
SKILL_BODY = "CATALOG-SKILL-BODY"
AGENT_BODY = "CATALOG-AGENT-BODY"
RULE_BODY = "CATALOG-RULE-BODY"
PROVIDER_BODY = "CATALOG-PROVIDER-BODY"
OVERLAY_BODY = "CATALOG-OVERLAY-BODY"

#: A file that exists in the developer's working checkout but was never in
#: the commit the pointer names. Nothing carrying this may reach a host.
UNCOMMITTED_BODY = "UNCOMMITTED-WORKING-TREE-BODY"

#: A file sitting beside a declared component inside the published tree that
#: no catalog row mentions. The tree is an inventory, not a directory to walk.
UNDECLARED_BODY = "UNDECLARED-SIBLING-BODY"

#: The managed usage constitution ``install_skill`` writes, so an overwrite
#: by this module would show as a changed body.
MANAGED_BODY = "MANAGED-BY-INSTALL-SKILL"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``Path.home()`` at ``tmp_path`` — never at a real home."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """One activated commit tree, laid out as ``harness sync`` publishes it.

    ``commits/<sha>/tree/`` with a ``component_root`` of ``harness``, holding
    one file per component kind plus one undeclared sibling of the skill.
    """
    sha = "9f1c3b2a7d4e0165c8a9b3d27e5f10486c73ab92"
    root = tmp_path / "cache" / "harness" / "commits" / sha / "tree" / "harness"

    files = {
        "skills/daily/SKILL.md": SKILL_BODY,
        "skills/daily/NOTES.md": UNDECLARED_BODY,
        "agents/librarian/AGENT.md": AGENT_BODY,
        "rules/no-invented-api.md": RULE_BODY,
        "providers/bench/provider.py": PROVIDER_BODY,
        "overlays/molpy/overlay.py": OVERLAY_BODY,
    }
    for relative, body in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n\n{body}\n", encoding="utf-8")
    return root


def _skill(tree: Path) -> ComponentFile:
    """The ``skill.daily`` row of the example catalog, already resolved."""
    return ComponentFile(
        id="skill.daily",
        kind="skill",
        relative="daily/SKILL.md",
        source=tree / "skills" / "daily" / "SKILL.md",
    )


def _agent(tree: Path) -> ComponentFile:
    """The ``agent.librarian`` row, already resolved."""
    return ComponentFile(
        id="agent.librarian",
        kind="agent",
        relative="librarian/AGENT.md",
        source=tree / "agents" / "librarian" / "AGENT.md",
    )


def _rule(tree: Path) -> ComponentFile:
    """The ``rule.no-invented-api`` row, already resolved."""
    return ComponentFile(
        id="rule.no-invented-api",
        kind="rule",
        relative="no-invented-api.md",
        source=tree / "rules" / "no-invented-api.md",
    )


def _provider(tree: Path) -> ComponentFile:
    """The ``provider.bench`` row — a plane, not a file a host installs."""
    return ComponentFile(
        id="provider.bench",
        kind="provider",
        relative="bench/provider.py",
        source=tree / "providers" / "bench" / "provider.py",
    )


def _overlay(tree: Path) -> ComponentFile:
    """The ``overlay.molpy`` row — knowledge for discovery, not for a host."""
    return ComponentFile(
        id="overlay.molpy",
        kind="overlay",
        relative="molpy/overlay.py",
        source=tree / "overlays" / "molpy" / "overlay.py",
    )


def _bodies(root: Path) -> list[str]:
    """Text of every regular file under *root*; empty when *root* is absent."""
    if not root.is_dir():
        return []
    return [
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _file_set(root: Path) -> set[Path]:
    """Every regular file under *root*, relative to it."""
    if not root.is_dir():
        return set()
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def _imported_names(path: Path) -> tuple[str, ...]:
    """Absolute dotted targets imported by *path*, relative imports resolved.

    The same walk ``test_layout.py`` uses: a substring grep over the source
    is both too wide (it hits docstrings) and too narrow (it misses a name
    built by concatenation), so dependency claims are answered from the
    import nodes themselves.
    """
    package = ".".join(("molmcp", *path.relative_to(SRC).parent.parts))
    parts = package.split(".")
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = parts[: len(parts) - (node.level - 1)]
                tail = node.module.split(".") if node.module else []
                found.append(".".join([*base, *tail]))
            else:
                found.append(node.module or "")
    return tuple(found)


class TestComponentFile:
    """The seam's input: one file to place, described in stdlib types only."""

    # --- Basics -------------------------------------------------------

    def test_it_carries_the_four_fields_the_seam_needs(self) -> None:
        names = {field.name for field in dataclasses.fields(ComponentFile)}

        assert names == {"id", "kind", "relative", "source"}

    def test_the_id_is_the_catalog_id_unchanged(self, tree: Path) -> None:
        assert _skill(tree).id == "skill.daily"

    def test_the_kind_is_the_catalog_kind_as_a_plain_string(self, tree: Path) -> None:
        """A ``str``, not a ``ComponentKind``, and not validated here.

        The carrier stays dumb: which kinds have a host destination is
        ``place_components``' table and has exactly one owner. That is why
        an unknown kind is refused there rather than at construction.
        """
        kind = _skill(tree).kind

        assert kind == "skill"
        assert type(kind) is str

    def test_the_relative_path_is_stripped_of_the_catalog_prefix(
        self, tree: Path
    ) -> None:
        """``skills/daily/SKILL.md`` arrives as ``daily/SKILL.md``.

        The caller strips ``KIND_PATH_PREFIX``; that prefix is catalog
        grammar and ``host/`` never learns it.
        """
        assert _skill(tree).relative == "daily/SKILL.md"

    def test_the_source_is_an_absolute_path_in_the_activated_tree(
        self, tree: Path
    ) -> None:
        """The caller has already joined ``component_root`` onto the tree.

        One base per row, because a fold can hold several sources and each
        one resolves under its own ``ComponentFold.root_for`` answer.
        """
        source = _skill(tree).source

        assert source.is_absolute()
        assert source.read_text(encoding="utf-8").count(SKILL_BODY) == 1

    # --- Immutability -------------------------------------------------

    def test_it_is_frozen(self, tree: Path) -> None:
        component = _skill(tree)

        with pytest.raises(dataclasses.FrozenInstanceError):
            component.kind = "agent"  # type: ignore[misc]


class TestPlacementReport:
    """The seam's output: what the run placed, replaced, and refused."""

    # --- Basics -------------------------------------------------------

    def test_it_carries_the_three_fields_a_run_decides(self) -> None:
        names = {field.name for field in dataclasses.fields(PlacementReport)}

        assert names == {"installed", "replaced", "skipped"}

    def test_the_two_skip_reasons_are_distinct_strings(self) -> None:
        """A reader must be able to tell the two refusals apart."""
        assert SKIP_NO_HOST_DESTINATION != SKIP_MANAGED_USAGE_SKILL
        assert SKIP_NO_HOST_DESTINATION and SKIP_MANAGED_USAGE_SKILL

    # --- Immutability -------------------------------------------------

    def test_it_is_frozen(self, home: Path) -> None:
        report = place_components("grok", ())

        with pytest.raises(dataclasses.FrozenInstanceError):
            report.installed = ()  # type: ignore[misc]


class TestPlaceComponents:
    """The kind decides the destination; the caller decides the files."""

    # --- Basics -------------------------------------------------------

    def test_its_signature_takes_a_host_and_the_components(self) -> None:
        parameters = list(inspect.signature(place_components).parameters)

        assert parameters == ["host", "components"]

    def test_a_skill_component_lands_in_the_host_skills_tree(
        self, home: Path, tree: Path
    ) -> None:
        place_components("grok", (_skill(tree),))

        skill = home / ".grok" / "skills" / "daily" / "SKILL.md"
        assert SKILL_BODY in skill.read_text(encoding="utf-8")

    def test_every_declared_skill_lands(self, home: Path, tree: Path) -> None:
        """A catalog declaring several skills installs all of them."""
        second = tree / "skills" / "review" / "SKILL.md"
        second.parent.mkdir(parents=True)
        second.write_text(f"# review\n\n{SKILL_BODY}\n", encoding="utf-8")
        review = ComponentFile(
            id="skill.review",
            kind="skill",
            relative="review/SKILL.md",
            source=second,
        )

        place_components("grok", (_skill(tree), review))

        skills = home / ".grok" / "skills"
        assert _file_set(skills) == {
            Path("daily") / "SKILL.md",
            Path("review") / "SKILL.md",
        }

    def test_an_agent_component_lands_in_the_host_agents_tree(
        self, home: Path, tree: Path
    ) -> None:
        place_components("grok", (_agent(tree),))

        agent = home / ".grok" / "agents" / "librarian" / "AGENT.md"
        assert AGENT_BODY in agent.read_text(encoding="utf-8")

    def test_a_rule_component_lands_in_the_host_rules_tree(
        self, home: Path, tree: Path
    ) -> None:
        place_components("grok", (_rule(tree),))

        rule = home / ".grok" / "rules" / "no-invented-api.md"
        assert RULE_BODY in rule.read_text(encoding="utf-8")

    def test_an_agent_is_not_installed_as_a_skill(self, home: Path, tree: Path) -> None:
        place_components("grok", (_agent(tree),))

        assert AGENT_BODY not in "".join(_bodies(home / ".grok" / "skills"))

    def test_a_provider_component_is_not_installed_as_a_skill(
        self, home: Path, tree: Path
    ) -> None:
        """A provider is a plane this process mounts, not a host file."""
        place_components("grok", (_provider(tree),))

        assert PROVIDER_BODY not in "".join(_bodies(home / ".grok"))

    def test_an_overlay_component_is_not_installed_anywhere(
        self, home: Path, tree: Path
    ) -> None:
        place_components("grok", (_overlay(tree),))

        assert OVERLAY_BODY not in "".join(_bodies(home / ".grok"))

    def test_a_provider_is_reported_skipped_with_a_reason(
        self, home: Path, tree: Path
    ) -> None:
        report = place_components("grok", (_provider(tree),))

        assert report.installed == ()
        assert report.skipped == (("provider.bench", SKIP_NO_HOST_DESTINATION),)

    def test_a_mixed_bundle_installs_three_kinds_and_skips_two(
        self, home: Path, tree: Path
    ) -> None:
        report = place_components(
            "grok",
            (
                _skill(tree),
                _agent(tree),
                _rule(tree),
                _provider(tree),
                _overlay(tree),
            ),
        )

        assert report.installed == (
            home / ".grok" / "skills" / "daily" / "SKILL.md",
            home / ".grok" / "agents" / "librarian" / "AGENT.md",
            home / ".grok" / "rules" / "no-invented-api.md",
        )
        assert report.skipped == (
            ("provider.bench", SKIP_NO_HOST_DESTINATION),
            ("overlay.molpy", SKIP_NO_HOST_DESTINATION),
        )

    def test_nothing_to_place_writes_nothing(self, home: Path) -> None:
        report = place_components("grok", ())

        assert report == PlacementReport(installed=(), replaced=(), skipped=())
        assert not (home / ".grok").exists()

    @pytest.mark.parametrize("host", ["grok", "claude", "cursor", "codex"])
    def test_every_host_gets_its_own_skills_tree(
        self, home: Path, tree: Path, host: HostName
    ) -> None:
        place_components(host, (_skill(tree),))

        assert any(
            SKILL_BODY in body
            for body in _bodies(home / f".{host}" / "skills" / "daily")
        )

    # --- What is installed came from the activated commit --------------

    def test_an_undeclared_sibling_in_the_tree_is_never_installed(
        self, home: Path, tree: Path
    ) -> None:
        """The tree is an inventory, not a directory to walk.

        ``skills/daily/NOTES.md`` sits beside the declared ``SKILL.md`` and
        no catalog row mentions it, so nothing may copy it — this is the
        seam's half of "a file nobody declared is not a component".
        """
        place_components("grok", (_skill(tree),))

        assert UNDECLARED_BODY not in "".join(_bodies(home / ".grok"))

    def test_work_left_uncommitted_in_the_checkout_cannot_reach_a_host(
        self, home: Path, tmp_path: Path, tree: Path
    ) -> None:
        """Install after a sync sees the published tree and nothing else.

        The developer's own checkout carries an edit that was never
        committed, so it is not in the tree the pointer names. The seam is
        handed rows resolved under that tree, and it reads no other
        directory — so the edit cannot be installed. That the published
        tree holds committed content only is proven where publishing
        happens; this is the half that says nothing bypasses it.
        """
        working = tmp_path / "checkout" / "harness"
        uncommitted = working / "skills" / "daily" / "SKILL.md"
        uncommitted.parent.mkdir(parents=True)
        uncommitted.write_text(f"# daily\n\n{UNCOMMITTED_BODY}\n", encoding="utf-8")

        place_components("grok", (_skill(tree),))

        bodies = "".join(_bodies(home / ".grok"))
        assert UNCOMMITTED_BODY not in bodies
        assert SKILL_BODY in bodies

    # --- Edge ---------------------------------------------------------

    def test_the_managed_usage_skill_is_never_clobbered(
        self, home: Path, tree: Path
    ) -> None:
        """A catalog declaring ``skills/molcrafts/`` does not win that name.

        ``install_skill`` owns the usage constitution;
        ``materialize_daily`` already skips a directory named
        ``SKILL_NAME`` and that protection has to survive this route.
        """
        managed = home / ".grok" / "skills" / SKILL_NAME / "SKILL.md"
        managed.parent.mkdir(parents=True)
        managed.write_text(MANAGED_BODY, encoding="utf-8")
        squatter = tree / "skills" / SKILL_NAME / "SKILL.md"
        squatter.parent.mkdir(parents=True)
        squatter.write_text(SKILL_BODY, encoding="utf-8")

        place_components(
            "grok",
            (
                ComponentFile(
                    id="skill.molcrafts",
                    kind="skill",
                    relative=f"{SKILL_NAME}/SKILL.md",
                    source=squatter,
                ),
            ),
        )

        assert managed.read_text(encoding="utf-8") == MANAGED_BODY

    def test_the_managed_usage_skill_refusal_is_reported(
        self, home: Path, tree: Path
    ) -> None:
        squatter = tree / "skills" / SKILL_NAME / "SKILL.md"
        squatter.parent.mkdir(parents=True)
        squatter.write_text(SKILL_BODY, encoding="utf-8")

        report = place_components(
            "grok",
            (
                ComponentFile(
                    id="skill.molcrafts",
                    kind="skill",
                    relative=f"{SKILL_NAME}/SKILL.md",
                    source=squatter,
                ),
            ),
        )

        assert report.installed == ()
        assert report.skipped == (("skill.molcrafts", SKIP_MANAGED_USAGE_SKILL),)

    def test_a_missing_component_file_names_the_id_and_the_path(
        self, home: Path, tree: Path
    ) -> None:
        missing = tree / "skills" / "ghost" / "SKILL.md"
        ghost = ComponentFile(
            id="skill.ghost",
            kind="skill",
            relative="ghost/SKILL.md",
            source=missing,
        )

        with pytest.raises(FileNotFoundError) as caught:
            place_components("grok", (ghost,))

        message = str(caught.value)
        assert "skill.ghost" in message
        assert str(missing) in message

    def test_a_missing_component_file_installs_no_partial_set(
        self, home: Path, tree: Path
    ) -> None:
        """One unresolvable row fails the whole run before anything is written."""
        ghost = ComponentFile(
            id="skill.ghost",
            kind="skill",
            relative="ghost/SKILL.md",
            source=tree / "skills" / "ghost" / "SKILL.md",
        )

        with pytest.raises(FileNotFoundError):
            place_components("grok", (_skill(tree), ghost))

        assert _file_set(home / ".grok") == set()

    def test_a_directory_is_not_a_component_file(self, home: Path, tree: Path) -> None:
        """A component names one file; a directory fails the same way."""
        directory = ComponentFile(
            id="skill.daily",
            kind="skill",
            relative="daily",
            source=tree / "skills" / "daily",
        )

        with pytest.raises(FileNotFoundError, match="skill.daily"):
            place_components("grok", (directory,))

    @pytest.mark.parametrize(
        "relative",
        ["../evil.md", "daily/../../evil.md", "/etc/evil.md"],
    )
    def test_a_relative_path_that_escapes_the_host_root_is_refused(
        self, home: Path, tree: Path, relative: str
    ) -> None:
        escaping = ComponentFile(
            id="skill.evil",
            kind="skill",
            relative=relative,
            source=tree / "skills" / "daily" / "SKILL.md",
        )

        with pytest.raises(ValueError, match="skill.evil"):
            place_components("grok", (escaping,))

    def test_an_unknown_kind_is_refused(self, home: Path, tree: Path) -> None:
        """Five kinds exist; a sixth means the caller is broken, not the file."""
        unknown = ComponentFile(
            id="widget.thing",
            kind="widget",
            relative="thing.md",
            source=tree / "rules" / "no-invented-api.md",
        )

        with pytest.raises(ValueError, match="widget"):
            place_components("grok", (unknown,))

    def test_an_unknown_host_is_refused_before_anything_is_placed(
        self, home: Path
    ) -> None:
        """Host validation first, as in every other primitive of this family."""
        with pytest.raises(ValueError, match="emacs"):
            place_components("emacs", ())

    # --- Lifecycle: a second run replaces, never duplicates -------------

    def test_the_first_run_reports_nothing_replaced(
        self, home: Path, tree: Path
    ) -> None:
        report = place_components("grok", (_skill(tree), _rule(tree)))

        assert report.replaced == ()
        assert len(report.installed) == 2

    def test_a_second_run_writes_the_same_file_set(
        self, home: Path, tree: Path
    ) -> None:
        components = (_skill(tree), _agent(tree), _rule(tree))
        place_components("grok", components)
        first = _file_set(home / ".grok")

        place_components("grok", components)

        assert _file_set(home / ".grok") == first

    def test_a_second_run_reports_every_destination_as_replaced(
        self, home: Path, tree: Path
    ) -> None:
        components = (_skill(tree), _rule(tree))
        place_components("grok", components)

        report = place_components("grok", components)

        assert report.replaced == report.installed
        assert report.installed != ()

    def test_a_changed_source_overwrites_the_destination(
        self, home: Path, tree: Path
    ) -> None:
        place_components("grok", (_skill(tree),))
        (tree / "skills" / "daily" / "SKILL.md").write_text(
            f"# daily\n\n{SKILL_BODY}-v2\n", encoding="utf-8"
        )

        place_components("grok", (_skill(tree),))

        skill = home / ".grok" / "skills" / "daily" / "SKILL.md"
        assert f"{SKILL_BODY}-v2" in skill.read_text(encoding="utf-8")


class TestPlaceStaysInsideHost:
    """``host/`` never learns what a ``HarnessCatalog`` is."""

    def test_the_module_exists(self) -> None:
        assert PLACE_SOURCE.is_file(), f"{PLACE_SOURCE} does not exist"

    def test_it_imports_only_stdlib_and_its_own_package(self) -> None:
        """The seam is why this holds, so this is where it is enforced.

        ``test_layout.py`` forbids the five outer layers for the whole
        package; this is the stricter rule the injected seam buys — a
        component reaches ``host/`` as four plain values, so nothing here
        needs ``molmcp.components``, ``molmcp.harness`` or anything else
        under ``molmcp`` outside ``molmcp.host``.
        """
        offenders = [
            dotted
            for dotted in _imported_names(PLACE_SOURCE)
            if dotted
            and not dotted.startswith("molmcp.host")
            and dotted.split(".")[0] not in sys.stdlib_module_names
        ]

        assert offenders == []
