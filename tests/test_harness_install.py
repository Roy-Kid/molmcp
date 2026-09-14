"""`molmcp init` installs what the *activated* harness commit declares.

Mirrors ``src/molmcp/harness_install.py``, the missing link of the chain the
last three changes built. ``molmcp config harness set`` registers a source,
``molmcp harness sync`` publishes its ``HEAD`` and promotes that source's
activation pointer, and ``molmcp.host.place_components`` places
``ComponentFile`` rows by kind — but nothing turns a *pointer* into those
rows, so an operator who has synced a harness and run ``molmcp init`` gets
none of it.

The resolver is what runs in between, and its four obligations are what this
file pins:

* read each configured source's activation pointer for its ``current`` SHA,
  and **skip** a source that has none — a configured source is not a synced
  one, and the operator who has not synced yet is not misconfigured;
* load ``harness.toml`` from that commit's tree;
* keep the non-bundle rows, strip ``KIND_PATH_PREFIX`` off each ``path`` for
  ``relative``, and join ``component_root`` for the absolute ``source``;
* resolve every row **under its own source's root**, so a multi-source
  install never reads one source's components out of another's tree.

Its own module rather than more of ``tests/test_cli_harness.py``, following
the split already in this suite: that file mirrors ``harness_sync.py``, the
*write* half (fetch, publish, activate), and this one mirrors the *read* half
that ``molmcp init`` composes. The two halves meet on disk here and nowhere
else, which is why nothing is faked between them: the checkout is built by
``git init``, the commit is published by the real ``molmcp harness sync``,
and the pointer is the real file the resolver binds. A seam standing in for
either would keep passing while the two commands disagreed about where a
commit lives.

**No network.** Every repository here is built under ``tmp_path`` and every
source is a local one, so the local transport is the only one constructed and
it opens no socket.

``Path.home`` is pinned to the ``home`` fixture, so every destination is the
real host layout without touching the developer's own home. No environment
variable is read: ``tests/test_no_env_switches.py`` scans every module under
``src/molmcp`` for that already.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from molmcp import cli
from molmcp import settings as st
from molmcp.host import SKIP_MANAGED_USAGE_SKILL, SKIP_NO_HOST_DESTINATION

#: Production module this file mirrors, read as text by the isolation tests.
SRC = Path(__file__).resolve().parents[1] / "src" / "molmcp"
RESOLVER_SOURCE = SRC / "harness_install.py"

#: Dotted module paths the resolver must not reach, and why each one is here.
#: ``molmcp.harness`` carries a module-level
#: ``from .provider_worker.worker import WorkerProvider``, so importing it
#: drags the whole FastMCP-bearing worker stack into the importing process.
#: ``molmcp init`` mounts no plane and must not pay for one, so the resolver
#: reaches ``molmcp.components`` — the stdlib leaf that owns ``Activation``,
#: ``ImmutableGitStore`` and ``load_harness_catalog`` — directly instead of
#: inheriting the cost through the serve-side reader.
FORBIDDEN_IMPORTS: tuple[str, ...] = ("molmcp.harness", "molmcp.provider_worker")

#: The leaf the resolver is expected to reach instead.
REQUIRED_IMPORT = "molmcp.components"

#: The host every behavioural test wires. One host, not four: which directory
#: a *kind* lands in is ``molmcp.host.place``'s table and is proven against
#: every host there, so repeating the matrix here would test that module
#: twice and this one not at all.
HOST = "claude"

#: A harness catalog declaring one row of every kind that has a host
#: destination, plus one that has none. Bundles are not optional: a catalog is
#: refused outright unless it declares both ``daily`` and ``dev``.
_MANIFEST = """\
requires = ["harness-catalog"]

[[component]]
kind = "skill"
name = "daily"
path = "skills/daily/SKILL.md"

[[component]]
kind = "skill"
name = "review"
path = "skills/review/SKILL.md"

[[component]]
kind = "agent"
name = "planner"
path = "agents/planner.md"

[[component]]
kind = "rule"
name = "style"
path = "rules/style.md"

[[component]]
kind = "provider"
name = "demo"
path = "providers/demo/plane.py"
entrypoint = "plane:build"

[[component]]
kind = "bundle"
name = "daily"
members = ["skill.daily", "skill.review", "provider.demo"]

[[component]]
kind = "bundle"
name = "dev"
members = ["agent.planner", "rule.style"]
"""

#: A catalog that aims a skill row straight at the managed usage skill.
#: ``install_skill`` owns that directory, and the placement seam refuses it.
_CLOBBER_MANIFEST = """\
requires = ["harness-catalog"]

[[component]]
kind = "skill"
name = "molcrafts"
path = "skills/molcrafts/SKILL.md"

[[component]]
kind = "skill"
name = "review"
path = "skills/review/SKILL.md"

[[component]]
kind = "bundle"
name = "daily"
members = ["skill.molcrafts"]

[[component]]
kind = "bundle"
name = "dev"
members = ["skill.review"]
"""

#: A catalog whose components live under a subdirectory of the tree. Used for
#: the second source of the multi-source tests: resolved under the *other*
#: source's root, none of its files exists at all.
_ROOTED_MANIFEST = """\
requires = ["harness-catalog"]
component_root = "harness"

[[component]]
kind = "skill"
name = "private-note"
path = "skills/private-note/SKILL.md"

[[component]]
kind = "bundle"
name = "daily"
members = ["skill.private-note"]

[[component]]
kind = "bundle"
name = "dev"
members = ["skill.private-note"]
"""

_DAILY_SKILL = "# daily skill\n"
_REVIEW_SKILL = "# review skill\n"
_PLANNER_AGENT = "# planner agent\n"
_STYLE_RULE = "# style rule\n"
_PROVIDER_MODULE = "def build():\n    return None\n"
_PRIVATE_SKILL = "# private note\n"
_CLOBBER_TEXT = "# not the constitution\n"
_SCRATCH = "still being edited\n"

#: Identity for the commits made here, passed per invocation so no developer's
#: global git config is read and none is written to ``tmp_path``.
_IDENTITY = (
    "-c",
    "user.name=molmcp tests",
    "-c",
    "user.email=tests@molmcp.invalid",
)


# -- a real repository, built here ------------------------------------------
#
# ``tests/test_cli_harness.py`` builds one the same way, and its helpers are
# private names in a module this change does not touch, so they are mirrored
# rather than imported: a test of the read half that breaks when the write
# half's tests are refactored is coupling this suite does not need.


def _git(root: Path, *args: str) -> str:
    """Run one git command inside *root* and return its stripped stdout."""
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(path: Path, text: str) -> None:
    """Write *text* to *path*, creating the parent directories it needs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(root: Path, message: str) -> str:
    """Commit everything currently in *root* and return the new SHA."""
    _git(root, "add", "-A")
    _git(root, *_IDENTITY, "commit", "--no-gpg-sign", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _harness_checkout(root: Path) -> tuple[Path, str]:
    """A one-commit harness checkout of :data:`_MANIFEST`, and its ``HEAD``."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "--initial-branch=main")
    _write(root / "harness.toml", _MANIFEST)
    _write(root / "skills" / "daily" / "SKILL.md", _DAILY_SKILL)
    _write(root / "skills" / "review" / "SKILL.md", _REVIEW_SKILL)
    _write(root / "agents" / "planner.md", _PLANNER_AGENT)
    _write(root / "rules" / "style.md", _STYLE_RULE)
    _write(root / "providers" / "demo" / "plane.py", _PROVIDER_MODULE)
    return root, _commit(root, "first")


def _clobber_checkout(root: Path) -> tuple[Path, str]:
    """A checkout whose catalog claims the managed usage skill's own path."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "--initial-branch=main")
    _write(root / "harness.toml", _CLOBBER_MANIFEST)
    _write(root / "skills" / "molcrafts" / "SKILL.md", _CLOBBER_TEXT)
    _write(root / "skills" / "review" / "SKILL.md", _REVIEW_SKILL)
    return root, _commit(root, "first")


def _rooted_checkout(root: Path) -> tuple[Path, str]:
    """A checkout whose catalog resolves its components under ``harness/``."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "--initial-branch=main")
    _write(root / "harness.toml", _ROOTED_MANIFEST)
    _write(
        root / "harness" / "skills" / "private-note" / "SKILL.md",
        _PRIVATE_SKILL,
    )
    return root, _commit(root, "first")


def _bundle_checkout(root: Path) -> Path:
    """A ``--source`` checkout: the daily and dev bundles, no catalog at all.

    This is the route ``molmcp init --source`` has always taken, and it is
    deliberately *not* a harness checkout: it has no ``harness.toml``, no
    commit and no pointer, because the point of asserting it here is that the
    activated-commit route was added beside it rather than on top of it.
    """
    _write(root / "daily" / "skills" / "notes" / "NOTE.md", "# notes\n")
    _write(root / "dev" / "commands" / "spec.md", "# /mol:spec\n")
    return root


# -- this install ------------------------------------------------------------


def _install(cache: Path, *harness: dict[str, str]) -> None:
    """Write the user settings file this install reads its sources from."""
    st.write_settings_file(
        st.user_settings_path(),
        {"cacheDir": str(cache), "watch": False, "harness": list(harness)},
    )


def _sync(name: str) -> None:
    """Run the real sync verb for one source and require that it succeeded."""
    assert cli.main(["harness", "sync", name]) == 0


def _init(*extra: str) -> int:
    """Run ``molmcp init`` for :data:`HOST` with any extra flags appended."""
    return cli.main(["init", HOST, *extra])


def _host_file(home: Path, *parts: str) -> Path:
    """One path inside the wired host's configuration directory."""
    return home.joinpath(".claude", *parts)


def _snapshot(root: Path) -> dict[str, str]:
    """Every regular file under *root* by relative POSIX path, with its text.

    Bytes rather than paths, because idempotence is a claim about content:
    a second run that rewrote a destination with different text would leave
    the same file list behind.
    """
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _packaged_constitution() -> str:
    """The usage ``SKILL.md`` ``install_skill`` copies, read from the package."""
    from molmcp import skill as skill_package

    source = Path(skill_package.__file__).parent / "SKILL.md"
    return source.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _offline_planes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the plane list, so no test here depends on installed science deps.

    ``molmcp init`` renders the MCP JSON from whatever providers this machine
    can import, which is a fact about the developer's environment rather than
    about the resolver under test.
    """
    monkeypatch.setattr(
        "molmcp.client_config.default_plane_ids",
        lambda: ("molcrafts", "molvis"),
    )


@pytest.fixture
def cache(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A scratch cache root, with the working directory pointed away from it.

    The working directory matters twice over: it is where ``load_settings``
    looks for a project settings file, and it must not be the developer's
    checkout, or this suite would read that repository's own configuration.
    """
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return tmp_path / "cache"


@pytest.fixture
def synced(cache: Path, tmp_path: Path) -> str:
    """One synced local source named ``official``; returns its activated SHA."""
    root, head = _harness_checkout(tmp_path / "official")
    _install(cache, {"name": "official", "locator": str(root)})
    _sync("official")
    return head


class TestInitInstallsWhatTheActivatedCatalogDeclares:
    """The happy path: one synced source, one ``molmcp init``, files on disk.

    Nothing between the two commands is faked. The pointer the sync promoted
    is the pointer this reads, and the tree it published is the tree these
    files are copied out of.
    """

    def test_every_declared_skill_lands_in_the_hosts_skills_directory(
        self, home: Path, synced: str
    ) -> None:
        """Both catalog skills, with the bytes the commit holds.

        ``skills/daily/SKILL.md`` arrives as ``daily/SKILL.md`` under the
        host's ``skills/``: the kind prefix is catalog grammar and is stripped
        before the row crosses into ``molmcp.host``.
        """
        assert _init() == 0

        assert _host_file(home, "skills", "daily", "SKILL.md").read_text(
            encoding="utf-8"
        ) == (_DAILY_SKILL)
        assert _host_file(home, "skills", "review", "SKILL.md").read_text(
            encoding="utf-8"
        ) == (_REVIEW_SKILL)

    def test_an_agent_row_lands_in_the_hosts_agents_directory(
        self, home: Path, synced: str
    ) -> None:
        assert _init() == 0

        assert _host_file(home, "agents", "planner.md").read_text(encoding="utf-8") == (
            _PLANNER_AGENT
        )

    def test_a_rule_row_lands_in_the_hosts_rules_directory(
        self, home: Path, synced: str
    ) -> None:
        assert _init() == 0

        assert _host_file(home, "rules", "style.md").read_text(encoding="utf-8") == (
            _STYLE_RULE
        )

    def test_a_kind_with_no_host_destination_writes_nothing(
        self, home: Path, synced: str
    ) -> None:
        """A ``provider`` is a plane ``molmcp serve`` mounts, not a host file.

        The catalog declares one, so this proves the row was *seen* and
        refused rather than never resolved: the two skills beside it landed.
        """
        assert _init() == 0

        assert _host_file(home, "skills", "daily", "SKILL.md").is_file()
        assert not _host_file(home, "providers").exists()
        assert not _host_file(home, "demo").exists()

    def test_nothing_the_catalog_did_not_declare_reaches_the_host(
        self, home: Path, cache: Path, tmp_path: Path
    ) -> None:
        """The published tree is never globbed; the catalog is the inventory.

        The undeclared file is *committed*, so it is genuinely in the
        activated tree — the only thing keeping it out of the host is that no
        catalog row names it.
        """
        root, _ = _harness_checkout(tmp_path / "official")
        _write(root / "skills" / "rogue" / "SKILL.md", "# rogue\n")
        _commit(root, "second")
        _install(cache, {"name": "official", "locator": str(root)})
        _sync("official")

        assert _init() == 0

        assert _host_file(home, "skills", "daily", "SKILL.md").is_file()
        assert not _host_file(home, "skills", "rogue").exists()


class TestThePlacementReportSaysWhatHappened:
    """The resolver hands back the report, not a bare list of paths.

    Two of the decisions a run makes are invisible in a list of destinations —
    that a component was refused, and that a destination already existed — so
    they are asserted off the report the primitive returns.
    """

    def test_the_report_names_every_destination_that_was_written(
        self, home: Path, synced: str
    ) -> None:
        # Imported inside the test so the rest of this file still reports a
        # behavioural failure rather than one collection error while the
        # resolver does not exist yet.
        from molmcp.harness_install import install_harness_components

        report = install_harness_components(HOST)

        assert set(report.installed) == {
            _host_file(home, "skills", "daily", "SKILL.md"),
            _host_file(home, "skills", "review", "SKILL.md"),
            _host_file(home, "agents", "planner.md"),
            _host_file(home, "rules", "style.md"),
        }

    def test_the_report_names_the_refused_row_and_its_reason(self, synced: str) -> None:
        from molmcp.harness_install import install_harness_components

        report = install_harness_components(HOST)

        assert report.skipped == (("provider.demo", SKIP_NO_HOST_DESTINATION),)

    def test_a_first_run_replaces_nothing(self, synced: str) -> None:
        from molmcp.harness_install import install_harness_components

        report = install_harness_components(HOST)

        assert report.replaced == ()

    def test_an_install_with_no_synced_source_reports_an_empty_run(
        self, cache: Path, tmp_path: Path
    ) -> None:
        """Nothing configured is the same answer as nothing activated.

        An empty report rather than a raise: an install that has never been
        pointed at a harness is the ordinary one, not a broken one.
        """
        from molmcp.harness_install import install_harness_components

        _install(cache)

        report = install_harness_components(HOST)

        assert report.installed == ()
        assert report.replaced == ()
        assert report.skipped == ()


class TestASourceThatWasNeverSyncedIsSkipped:
    """A configured source is not a synced one, and the difference is silent.

    The operator may have added an entry and not yet run ``molmcp harness
    sync``; that is a state the install passes through, not an error it
    reports. The unsynced entry is deliberately **first** in the settings
    list, so a resolver that stopped at the first pointer it could not read
    would install nothing at all.
    """

    def test_an_unsynced_source_is_not_an_error(
        self, cache: Path, tmp_path: Path
    ) -> None:
        never, _ = _harness_checkout(tmp_path / "never")
        _install(cache, {"name": "never", "locator": str(never)})

        assert _init() == 0

    def test_an_unsynced_source_installs_none_of_its_components(
        self, home: Path, cache: Path, tmp_path: Path
    ) -> None:
        never, _ = _harness_checkout(tmp_path / "never")
        _install(cache, {"name": "never", "locator": str(never)})

        assert _init() == 0

        assert not _host_file(home, "skills", "daily").exists()
        assert not _host_file(home, "agents", "planner.md").exists()

    def test_a_synced_neighbour_still_installs(
        self, home: Path, cache: Path, tmp_path: Path
    ) -> None:
        never, _ = _rooted_checkout(tmp_path / "never")
        official, _ = _harness_checkout(tmp_path / "official")
        _install(
            cache,
            {"name": "never", "locator": str(never)},
            {"name": "official", "locator": str(official)},
        )
        _sync("official")

        assert _init() == 0

        assert _host_file(home, "skills", "daily", "SKILL.md").read_text(
            encoding="utf-8"
        ) == (_DAILY_SKILL)
        assert not _host_file(home, "skills", "private-note").exists()


class TestEachSourceResolvesUnderItsOwnRoot:
    """Two synced sources, two trees, two ``component_root`` answers.

    The second catalog declares ``component_root = "harness"``, so its one
    file sits at ``<tree>/harness/skills/private-note/SKILL.md``. Resolved
    under the first source's tree — or with the first source's root — that
    path does not exist, and ``place_components`` refuses the whole run in
    its pre-flight pass. So this is not a cosmetic ordering check: getting the
    base wrong installs nothing at all.
    """

    @pytest.fixture
    def two_sources(self, cache: Path, tmp_path: Path) -> None:
        official, _ = _harness_checkout(tmp_path / "official")
        private, _ = _rooted_checkout(tmp_path / "private")
        _install(
            cache,
            {"name": "official", "locator": str(official)},
            {"name": "private", "locator": str(private)},
        )
        _sync("official")
        _sync("private")

    def test_the_plain_source_installs_from_the_tree_root(
        self, home: Path, two_sources: None
    ) -> None:
        assert _init() == 0

        assert _host_file(home, "skills", "daily", "SKILL.md").read_text(
            encoding="utf-8"
        ) == (_DAILY_SKILL)

    def test_the_rooted_source_installs_from_its_own_component_root(
        self, home: Path, two_sources: None
    ) -> None:
        assert _init() == 0

        assert _host_file(home, "skills", "private-note", "SKILL.md").read_text(
            encoding="utf-8"
        ) == (_PRIVATE_SKILL)


class TestTheManagedUsageSkillSurvives:
    """``install_skill`` owns the constitution; a catalog cannot take it.

    This is the one destination the placement seam refuses, and it is why the
    new step runs *after* ``install_skill`` in ``cli._init``: the seam skips a
    destination inside the managed skill directory, which protects a file that
    has already been written and nothing else.
    """

    @pytest.fixture
    def clobbering(self, cache: Path, tmp_path: Path) -> None:
        root, _ = _clobber_checkout(tmp_path / "official")
        _install(cache, {"name": "official", "locator": str(root)})
        _sync("official")

    def test_the_constitution_is_the_packaged_file_after_init(
        self, home: Path, clobbering: None
    ) -> None:
        assert _init() == 0

        installed = _host_file(home, "skills", "molcrafts", "SKILL.md")
        text = installed.read_text(encoding="utf-8")
        assert "metadata:" not in text
        assert "SYMBOL_NOT_FOUND" in text
        assert text != _CLOBBER_TEXT

    def test_the_report_names_the_refusal_rather_than_hiding_it(
        self, clobbering: None
    ) -> None:
        from molmcp.harness_install import install_harness_components

        report = install_harness_components(HOST)

        assert report.skipped == (("skill.molcrafts", SKIP_MANAGED_USAGE_SKILL),)

    def test_the_other_rows_of_that_catalog_still_install(
        self, home: Path, clobbering: None
    ) -> None:
        """The refusal is one row, not the run."""
        assert _init() == 0

        assert _host_file(home, "skills", "review", "SKILL.md").read_text(
            encoding="utf-8"
        ) == (_REVIEW_SKILL)


class TestOnlyTheActivatedCommitReachesTheHost:
    """The working tree of the checkout is not what gets installed.

    Identity is the SHA the pointer names, so what lands is what was
    committed at the moment of the sync — an edit made afterwards belongs to
    no published commit and reaches nothing until the operator syncs again.
    """

    def test_an_edit_made_after_the_sync_does_not_reach_the_host(
        self, home: Path, cache: Path, tmp_path: Path
    ) -> None:
        root, _ = _harness_checkout(tmp_path / "official")
        _install(cache, {"name": "official", "locator": str(root)})
        _sync("official")
        _write(root / "skills" / "daily" / "SKILL.md", _SCRATCH)

        assert _init() == 0

        assert _host_file(home, "skills", "daily", "SKILL.md").read_text(
            encoding="utf-8"
        ) == (_DAILY_SKILL)

    def test_a_component_declared_but_never_committed_reaches_nothing(
        self, home: Path, cache: Path, tmp_path: Path
    ) -> None:
        """A new row *and* its file, both left uncommitted after the sync.

        The activated commit's catalog has no such row, so the file is not a
        component of anything this install serves.
        """
        root, _ = _harness_checkout(tmp_path / "official")
        _install(cache, {"name": "official", "locator": str(root)})
        _sync("official")
        _write(
            root / "harness.toml",
            _MANIFEST + '\n[[component]]\nkind = "rule"\nname = "draft"\n'
            'path = "rules/draft.md"\n',
        )
        _write(root / "rules" / "draft.md", _SCRATCH)

        assert _init() == 0

        assert _host_file(home, "rules", "style.md").is_file()
        assert not _host_file(home, "rules", "draft.md").exists()


class TestInstallingTwiceIsIdempotent:
    """A second ``molmcp init`` is a no-diff run over the same commit."""

    def test_the_second_run_leaves_the_same_files_with_the_same_bytes(
        self, home: Path, synced: str
    ) -> None:
        assert _init() == 0
        first = _snapshot(home / ".claude")

        assert _init() == 0

        assert _snapshot(home / ".claude") == first

    def test_the_second_run_reports_every_destination_as_replaced(
        self, synced: str
    ) -> None:
        """``replaced`` is the whole of ``installed`` on a repeat of one set."""
        from molmcp.harness_install import install_harness_components

        first = install_harness_components(HOST)
        second = install_harness_components(HOST)

        assert first.replaced == ()
        assert second.replaced == second.installed
        assert second.installed == first.installed


def _imported_targets(path: Path) -> tuple[str, ...]:
    """Absolute dotted targets *path* imports, relative imports resolved.

    The same walk ``tests/test_host/test_place.py`` uses, for the reason
    ``notes.md:isolation-check-imports`` gives: a substring grep over the
    source is both too wide — it hits docstrings, and the docstring of a
    module that exists *to stay off* a dependency will name it — and too
    narrow, since it cannot see a name assembled by concatenation. Dependency
    claims are answered from the import nodes themselves.

    ``from . import harness`` names its target in an alias rather than in
    ``node.module``, so aliases are resolved too. That also yields
    ``molmcp.components.Activation`` for a symbol import, which is not a
    module — harmless here, because every claim made against this walk is
    about a dotted *prefix* that no symbol of an allowed module can spell.

    Args:
        path: A module file under ``src/molmcp``.

    Returns:
        Every dotted target the module imports, in source order.
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
                module = ".".join([*base, *tail])
            else:
                module = node.module or ""
            found.append(module)
            found.extend(f"{module}.{alias.name}" for alias in node.names)
    return tuple(found)


def _reaches(targets: tuple[str, ...], dotted: str) -> bool:
    """Whether any target is *dotted* itself or a module beneath it.

    Compared segment-wise rather than by ``str.startswith`` alone, so
    ``molmcp.harness_install`` is not read as a module inside
    ``molmcp.harness``.
    """
    return any(
        target == dotted or target.startswith(f"{dotted}.") for target in targets
    )


class TestTheResolverStaysOffTheWorkerStack:
    """``molmcp init`` mounts no plane and must not import one.

    ``molmcp.harness`` carries a module-level
    ``from .provider_worker.worker import WorkerProvider``, so importing it
    pulls the whole FastMCP-bearing worker stack into the process. The
    resolver needs three names — ``Activation``, ``ImmutableGitStore`` and
    ``load_harness_catalog`` — and every one of them lives in the stdlib-only
    ``molmcp.components`` leaf, so it reaches that leaf directly instead of
    inheriting the serve-side reader's cost.
    """

    def test_the_resolver_module_exists_where_this_file_mirrors_it(self) -> None:
        assert RESOLVER_SOURCE.is_file()

    @pytest.mark.parametrize("dotted", FORBIDDEN_IMPORTS)
    def test_it_imports_nothing_from_the_heavy_side(self, dotted: str) -> None:
        assert not _reaches(_imported_targets(RESOLVER_SOURCE), dotted)

    def test_it_reaches_the_stdlib_component_leaf_directly(self) -> None:
        assert _reaches(_imported_targets(RESOLVER_SOURCE), REQUIRED_IMPORT)

    def test_it_performs_no_import_the_walk_above_cannot_see(self) -> None:
        """No ``importlib.import_module`` to route around the AST check.

        The note that makes this an AST test rather than a grep also names
        the one hole an AST walk has, so it is closed here rather than left
        to a substring scan of the whole file.
        """
        tree = ast.parse(RESOLVER_SOURCE.read_text(encoding="utf-8"))

        dynamic = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "import_module")
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"import_module", "__import__"}
                )
            )
        ]

        assert dynamic == []
