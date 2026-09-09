"""`molmcp harness sync` — the verb between a configured source and a served one.

``molmcp config harness set`` writes a coordinate and ``molmcp serve`` reads
an activation pointer, and until this verb exists nothing fetches, publishes
or activates in between: ``store.publish``, ``Activation.stage`` and
``Activation.promote`` have no production caller at all, so a configured
source can never become a served one.

Its own module rather than more of ``tests/test_cli_config.py``, following the
split already in this suite — ``molmcp cache`` has ``test_cli_cache.py`` and
``molmcp config`` has ``test_cli_config.py``. ``harness`` is a second
top-level verb with its own settings surface, its own on-disk artifacts
(the shared store and one pointer file per source) and its own failure
modes, and folding it into the ``config`` module would put two commands'
fixtures in one file.

**No network.** Every repository here is built by ``git init`` under
``tmp_path``. The one test that has to prove the *remote* arm picks the
GitHub transport patches that class's two methods and serves the archive out
of a local repository, so even that path opens no socket.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from molmcp import cli
from molmcp import settings as st
from molmcp.components import (
    Activation,
    GitError,
    GitHubTransport,
    ImmutableGitStore,
    LocalGitTransport,
)
from molmcp.harness import SUPPORTED_CAPABILITIES, pointer_path

#: The smallest ``harness.toml`` ``Activation.stage`` will accept: a catalog
#: is refused outright unless it declares both the ``daily`` and the ``dev``
#: bundle, so "minimal" is three components, not zero.
_MANIFEST = """\
requires = ["harness-catalog"]

[[component]]
kind = "skill"
name = "daily"
path = "skills/daily/SKILL.md"

[[component]]
kind = "bundle"
name = "daily"
members = ["skill.daily"]

[[component]]
kind = "bundle"
name = "dev"
members = ["skill.daily"]
"""
_SKILL = "# daily\n"
_SCRATCH = "still being edited\n"

#: Identity for the commits made here, passed per invocation so no
#: developer's global git config is read and none is written to ``tmp_path``.
_IDENTITY = (
    "-c",
    "user.name=molmcp tests",
    "-c",
    "user.email=tests@molmcp.invalid",
)


# -- a real repository, built here ------------------------------------------
#
# ``tests/test_components/test_git.py`` builds one the same way. Its helpers
# are private names in a module this change does not touch, so they are
# mirrored rather than imported: a CLI test that breaks when the transport's
# own tests are refactored is coupling this suite does not need, and the
# three subprocess calls are cheaper than the dependency.


def _git(root: Path, *args: str) -> str:
    """Run one git command inside ``root`` and return its stripped stdout."""
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    """Run one git command inside ``root`` and return its raw stdout."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
    ).stdout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _empty_repo(root: Path) -> Path:
    """``git init`` and nothing else: a checkout whose ``HEAD`` resolves to nothing."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "--initial-branch=main")
    return root


def _commit(root: Path, message: str) -> str:
    """Commit everything currently in ``root`` and return the new SHA."""
    _git(root, "add", "-A")
    _git(root, *_IDENTITY, "commit", "--no-gpg-sign", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _checkout(root: Path) -> tuple[Path, str]:
    """A one-commit harness checkout; returns the root and its ``HEAD`` SHA."""
    _empty_repo(root)
    _write(root / "harness.toml", _MANIFEST)
    _write(root / "skills" / "daily" / "SKILL.md", _SKILL)
    return root, _commit(root, "first")


def _archive(root: Path, sha: str) -> bytes:
    """The tarball GitHub would serve for ``sha``: one top-level directory."""
    return _git_bytes(
        root, "archive", "--format=tar.gz", f"--prefix=harness-{sha}/", sha
    )


# -- this install ------------------------------------------------------------


class _Unreachable:
    """A ``GitTransport`` for the read-only store the assertions bind.

    ``ImmutableGitStore`` refuses ``None``, and reading a published tree
    touches no transport, so anything reached through this one means an
    assertion helper started fetching.
    """

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        raise AssertionError("reading the store must not resolve a ref")

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        raise AssertionError("reading the store must not fetch an archive")


def _install(cache: Path, *harness: dict[str, str]) -> None:
    """Write the user settings file this install syncs from."""
    st.write_settings_file(
        st.user_settings_path(),
        {"cacheDir": str(cache), "watch": False, "harness": list(harness)},
    )


def _pin_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Aim the *other* spelling of "the user's home" at the ``home`` fixture.

    That fixture pins :meth:`Path.home`, which is how this package finds home
    when it looks it up. It is not how ``~`` is *expanded*:
    :meth:`Path.expanduser` delegates to :func:`os.path.expanduser`, which
    reads the ``HOME`` / ``USERPROFILE`` environment and never consults
    :meth:`Path.home`. A test that pinned only one of the two would leave the
    developer's real home reachable through the other.

    Mirrored from ``tests/test_harness.py``'s ``_hermetic_home`` rather than
    imported: that is a private name in the module mirroring
    ``assert_servable``, and two ``setenv`` calls are cheaper than coupling
    this suite to it.

    Args:
        home: The ``home`` fixture's tree, already created and already the
            answer :meth:`Path.home` gives.
        monkeypatch: The test's patcher.

    Returns:
        *home*, so a caller can name it in one expression.
    """
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _store(cache: Path) -> ImmutableGitStore:
    """The one shared store, at the root ``molmcp.harness`` serves out of.

    ``activated_checkouts`` builds ``ImmutableGitStore(root=<cache>/harness)``,
    so this is not an arbitrary directory: publishing anywhere else would
    leave ``molmcp serve`` unable to find the commit that was just activated.
    """
    return ImmutableGitStore(root=cache / "harness", transport=_Unreachable())


def _activation(cache: Path, name: str) -> Activation:
    """Bind ``<cache>/harness.<name>.pointer`` with the real reader."""
    return Activation.bind(
        pointer_path(cache, name),
        store=_store(cache),
        supported_capabilities=SUPPORTED_CAPABILITIES,
    )


@pytest.fixture
def cache(home, monkeypatch, tmp_path) -> Path:
    """A scratch cache root, with the working directory pointed away from it."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return tmp_path / "cache"


class TestHarnessSync:
    """The happy path: fetch, publish, activate — over real artifacts.

    Nothing is faked between the verb and the disk. The store is the real
    ``ImmutableGitStore`` at the root ``molmcp serve`` reads, and the pointer
    is the real file ``Activation`` binds, because a seam standing in for
    either would keep passing while the two commands disagreed about where a
    commit lives.
    """

    def test_sync_publishes_head_and_activates_it(self, cache, tmp_path):
        """One local source, one command: the commit is served-ready after it.

        A local source is SHA-pinned exactly like a remote one, so the thing
        published is a *commit* — ``HEAD`` of the checkout — and the pointer
        names that commit rather than the directory.
        """
        root, head = _checkout(tmp_path / "checkout")
        _install(cache, {"name": "official", "path": str(root)})

        assert cli.main(["harness", "sync", "official"]) == 0

        store = _store(cache)
        assert store.has(head)
        assert (store.tree_path(head) / "harness.toml").read_text() == _MANIFEST
        assert (
            store.tree_path(head) / "skills" / "daily" / "SKILL.md"
        ).read_text() == (_SKILL)

    def test_sync_leaves_the_named_pointer_file_naming_that_commit(
        self, cache, tmp_path
    ):
        """The pointer is ``<cache>/harness.official.pointer`` and it is *promoted*.

        The file name is the contract ``activated_checkouts`` reads by, so it
        is asserted literally as well as through the namer. ``staged is None``
        is the other half: a SHA left staged is a SHA nothing serves, which is
        indistinguishable from a sync that never ran.
        """
        root, head = _checkout(tmp_path / "checkout")
        _install(cache, {"name": "official", "path": str(root)})

        assert cli.main(["harness", "sync", "official"]) == 0

        pointer = cache / "harness.official.pointer"
        assert pointer == pointer_path(cache, "official")
        assert pointer.is_file()
        activation = _activation(cache, "official")
        assert activation.current == head
        assert activation.staged is None
        assert activation.previous is None

    def test_a_second_sync_with_no_new_commit_republishes_nothing(
        self, cache, tmp_path
    ):
        """Idempotent: same commit, same published directory, same pointer.

        ``previous`` is the sharp assertion. A verb that stages and promotes
        unconditionally would leave ``current`` looking right while quietly
        overwriting the one SHA ``rollback`` had to return to — the second run
        would set ``previous`` to the commit that is already current, and the
        install would lose its way back. ``st_ino`` is the other half: the SHA
        directory ``publish`` installed is still the one on disk, so nothing
        was re-fetched and re-``os.replace``d underneath a running server.
        """
        root, head = _checkout(tmp_path / "checkout")
        _install(cache, {"name": "official", "path": str(root)})
        assert cli.main(["harness", "sync", "official"]) == 0
        published = (cache / "harness" / "commits" / head).stat().st_ino
        pointer = json.loads(
            pointer_path(cache, "official").read_text(encoding="utf-8")
        )

        assert cli.main(["harness", "sync", "official"]) == 0

        activation = _activation(cache, "official")
        assert activation.current == head
        assert activation.previous is None
        assert activation.staged is None
        assert (cache / "harness" / "commits" / head).stat().st_ino == published
        assert (
            json.loads(pointer_path(cache, "official").read_text(encoding="utf-8"))
            == pointer
        )

    def test_a_new_commit_moves_the_pointer_and_keeps_the_previous_sha(
        self, cache, tmp_path
    ):
        """Rollback is why ``previous`` exists; a second sync is what fills it.

        Both trees stay published, and the older one still does *not* carry
        the file the newer commit added — so ``rollback`` restores a tree, not
        just a name.
        """
        root, first = _checkout(tmp_path / "checkout")
        _install(cache, {"name": "official", "path": str(root)})
        assert cli.main(["harness", "sync", "official"]) == 0
        _write(root / "skills" / "spec" / "SKILL.md", "# spec\n")
        second = _commit(root, "second")

        assert cli.main(["harness", "sync", "official"]) == 0

        activation = _activation(cache, "official")
        assert activation.current == second
        assert activation.previous == first
        assert activation.staged is None
        store = _store(cache)
        assert store.has(first)
        assert (store.tree_path(second) / "skills" / "spec" / "SKILL.md").is_file()
        assert not (store.tree_path(first) / "skills" / "spec" / "SKILL.md").exists()

    def test_the_working_tree_is_not_what_gets_published(self, cache, tmp_path):
        """ "Local" means SHA-pinned, not "whatever is on disk right now".

        This is the property that makes a local source rollbackable at all. If
        an uncommitted edit could reach the store, the SHA in the pointer
        would name a tree that never existed in the repository, and activating
        the same commit twice could serve two different sets of files.
        """
        root, head = _checkout(tmp_path / "checkout")
        _write(root / "scratch.txt", _SCRATCH)
        _install(cache, {"name": "official", "path": str(root)})

        assert cli.main(["harness", "sync", "official"]) == 0

        tree = _store(cache).tree_path(head)
        assert not (tree / "scratch.txt").exists()
        assert (tree / "harness.toml").is_file()


class TestHarnessSyncTransportChoice:
    """Origin picks the transport; nothing the operator types does.

    ``HarnessSource`` already refuses an entry that carries both a path and a
    coordinate, so the entry's *shape* is a total answer to "where does this
    come from". A flag would be a second answer, and two answers to one
    question is how an install ends up fetching from a repository nobody
    named.

    Both classes are patched on :mod:`molmcp.components.git` where they are
    defined, so the assertions hold however the verb imports them.
    """

    @pytest.fixture
    def local_transports(self, monkeypatch) -> list[Path]:
        """Record every ``LocalGitTransport`` root, leaving behaviour intact.

        ``record`` is annotated exactly as the ``__init__`` it stands in for,
        ``root: Path``. Widening it to ``Path | str`` would let this fixture
        accept a root the real constructor's signature refuses and hand it
        straight on, so the recorded value could be a shape production never
        passes and the assertions would be checking a call that cannot happen.
        """
        roots: list[Path] = []
        original = LocalGitTransport.__init__

        def record(self: LocalGitTransport, root: Path) -> None:
            roots.append(root)
            original(self, root)

        monkeypatch.setattr(LocalGitTransport, "__init__", record)
        return roots

    def test_a_local_source_gets_the_local_transport_and_no_other(
        self, cache, tmp_path, monkeypatch, local_transports
    ):
        """Constructed on the source's own ``path``, and GitHub is never spoken to."""

        def refuse(*args: object, **kwargs: object) -> object:
            raise AssertionError("a local source must not reach GitHub")

        monkeypatch.setattr(GitHubTransport, "resolve_commit", refuse)
        monkeypatch.setattr(GitHubTransport, "fetch_archive", refuse)
        root, head = _checkout(tmp_path / "checkout")
        _install(cache, {"name": "official", "path": str(root)})

        assert cli.main(["harness", "sync", "official"]) == 0

        assert local_transports == [root]
        assert _activation(cache, "official").current == head

    def test_a_home_relative_source_is_rooted_at_the_expanded_checkout(
        self, cache, home, monkeypatch, local_transports
    ):
        """``~/checkout`` is the checkout under home, not a literal ``~`` directory.

        ``assert_servable`` accepts a home-relative ``path`` — home is the
        same directory in every session, so the entry names one checkout
        rather than a different one per client — which makes this the one
        servable spelling that is *not* already the directory to read.
        Unexpanded, ``~/checkout`` is an ordinary two-segment relative path
        read against whatever working directory the client that launched this
        process happened to stand in.

        The published ``HEAD`` is the assertion, not a bare exit code: a
        literal ``~`` directory does not exist, so a wrongly-rooted transport
        fails at ``git`` and a test asserting only "no traceback" would pass
        against the bug. The SHA can only have come from the checkout under
        home.
        """
        root, head = _checkout(_pin_home(home, monkeypatch) / "checkout")
        _install(cache, {"name": "official", "path": "~/checkout"})

        assert cli.main(["harness", "sync", "official"]) == 0

        assert local_transports == [root]
        assert _store(cache).has(head)
        assert _activation(cache, "official").current == head

    def test_a_remote_source_gets_the_github_transport_and_no_other(
        self, cache, tmp_path, monkeypatch, local_transports
    ):
        """The coordinate arm, with the socket replaced and nothing else.

        The fakes stand exactly where the network would: they are handed the
        entry's own ``owner``/``repo``/``ref`` and answer with a commit and an
        archive built from a repository in ``tmp_path``. Everything after them
        — flatten, publish, stage, promote — is the real code.
        """
        root, head = _checkout(tmp_path / "origin")
        resolved: list[tuple[str, str, str | None]] = []
        fetched: list[tuple[str, str, str]] = []

        def resolve(
            self: GitHubTransport, owner: str, repo: str, ref: str | None
        ) -> str:
            resolved.append((owner, repo, ref))
            return head

        def fetch(self: GitHubTransport, owner: str, repo: str, sha: str) -> bytes:
            fetched.append((owner, repo, sha))
            return _archive(root, sha)

        monkeypatch.setattr(GitHubTransport, "resolve_commit", resolve)
        monkeypatch.setattr(GitHubTransport, "fetch_archive", fetch)
        _install(
            cache,
            {
                "name": "official",
                "owner": "molcrafts",
                "repo": "harness",
                "ref": "main",
            },
        )

        assert cli.main(["harness", "sync", "official"]) == 0

        assert resolved == [("molcrafts", "harness", "main")]
        assert fetched == [("molcrafts", "harness", head)]
        assert local_transports == []
        assert _activation(cache, "official").current == head


class TestHarnessSyncErrors:
    """Every failure is a sentence on stderr and a non-zero exit.

    A traceback out of the CLI is a bug report about molmcp; what an operator
    of a half-configured install needs is the name of the thing that is wrong.
    """

    def test_an_unknown_source_name_lists_the_configured_ones(
        self, cache, tmp_path, capsys
    ):
        """Naming the typo is half the message; naming the alternatives is the rest.

        Sources are addressed by an operator-chosen label, so a
        ``ConfigurationError`` that only says "unknown" leaves them to go and
        read the settings file to find out what they should have typed.
        """
        root, _ = _checkout(tmp_path / "checkout")
        _install(
            cache,
            {"name": "official", "path": str(root)},
            {"name": "private", "owner": "acme", "repo": "tooling", "ref": "trunk"},
        )

        assert cli.main(["harness", "sync", "ghost"]) != 0

        err = capsys.readouterr().err
        assert err.startswith("molmcp:")
        assert "ghost" in err
        assert "official" in err
        assert "private" in err
        assert not pointer_path(cache, "official").exists()

    def test_a_ref_that_does_not_resolve_is_reported_not_raised(
        self, cache, tmp_path, capsys
    ):
        """A checkout with no commits: ``HEAD`` names nothing, so git fails.

        The real ``LocalGitTransport`` raises ``GitError`` here, which is a
        ``RuntimeError`` and so is *not* in the tuple ``cli.main`` already
        catches. This test is red twice over until the verb exists and until
        that error is mapped onto the CLI's own register: if it escapes,
        ``cli.main`` never returns and this fails as an error rather than an
        assertion.
        """
        root = _empty_repo(tmp_path / "checkout")
        _install(cache, {"name": "official", "path": str(root)})

        assert cli.main(["harness", "sync", "official"]) != 0

        err = capsys.readouterr().err
        assert err.startswith("molmcp:")
        assert not pointer_path(cache, "official").exists()

    def test_a_remote_ref_that_does_not_resolve_surfaces_the_transport_error(
        self, cache, capsys, monkeypatch
    ):
        """The same contract on the coordinate arm, from the transport itself.

        The message the transport wrote is what reaches the operator: a
        rewrite here would hide which ref, or which repository, git could not
        answer for.
        """

        def refuse(
            self: GitHubTransport, owner: str, repo: str, ref: str | None
        ) -> str:
            raise GitError(f"could not resolve {ref} in {owner}/{repo}")

        monkeypatch.setattr(GitHubTransport, "resolve_commit", refuse)
        _install(
            cache,
            {
                "name": "official",
                "owner": "molcrafts",
                "repo": "harness",
                "ref": "nope",
            },
        )

        assert cli.main(["harness", "sync", "official"]) != 0

        err = capsys.readouterr().err
        assert err.startswith("molmcp:")
        assert "nope" in err
        assert not pointer_path(cache, "official").exists()

    def test_a_local_path_that_is_no_checkout_is_reported(
        self, cache, tmp_path, capsys
    ):
        """The verb refuses the same half-authored entry ``molmcp serve`` does.

        A ``path`` naming a directory that is not a repository is the local
        analogue of a missing ``ref``. Both commands read the same settings
        file, so an entry one of them refuses cannot be one the other syncs.
        """
        root = tmp_path / "not-a-repo"
        root.mkdir()
        _install(cache, {"name": "official", "path": str(root)})

        assert cli.main(["harness", "sync", "official"]) != 0

        err = capsys.readouterr().err
        assert err.startswith("molmcp:")
        assert "official" in err
        assert not pointer_path(cache, "official").exists()
