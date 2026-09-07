"""Evolution wiki — one page per ``pattern_key``, append-only, fenced on read.

Mirrors ``src/molmcp/evolution/wiki.py``; one class per public symbol
(``WikiStore``, ``Maintainer``, ``render_page``).

Three disciplines are pinned here that no single assertion makes obvious.

*The page is the authority.* A receipt is folded into the page named by its
``pattern_key`` — never written as a file of its own — so two rejections
followed by an acceptance are one file, three records, and a derived
"current". ``current()`` is therefore absent from the JSON: a second
independently writable field is a second truth to keep in sync.

*The fence lives on the read path.* Disk holds pointer strings; only
``render_page`` wraps them, and it wraps them with the shared
``molmcp.helpers.fence_untrusted`` rather than a second copy of the marker.
Persisting the wrapper would make the fence part of the data it guards.

*Runtime cannot see this package.* The isolation checks at the bottom are
static and read imports, not prose: a dependency is what a module imports,
and a substring scan both over- and under-approximates that. A scan for
``github`` fails the file whose regex refuses a forge URL — the code that
refuses the scheme has to name it — and still passes a file that reaches
the source module through ``importlib.import_module``. So the checks walk
the AST for the dependency and keep one text check for the dotted path a
dynamic import would hide. They never boot the server stack, and the same
walk is turned on this file, because a test that starts the thing it
claims is absent proves the opposite.

Nothing here reads a clock, the environment, or a user cache: the store root
is always ``tmp_path / "wiki"``, and every sha and pointer is a literal.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from molmcp.evolution.wiki import (
    Maintainer,
    WikiError,
    WikiPage,
    WikiReceipt,
    WikiStore,
    render_page,
)
from molmcp.helpers import fence_untrusted

_REPO = Path(__file__).resolve().parents[2]
_PACKAGE = _REPO / "src" / "molmcp"
_EVOLUTION = _PACKAGE / "evolution"
_WIKI = _EVOLUTION / "wiki.py"

#: The one pattern every test folds receipts into, and its slug on disk.
_PATTERN_KEY = "demo.pattern"
_PAGE_NAME = "demo.pattern.json"

#: The fence tokens this module greps for. ``TestRenderPage`` proves they are
#: the shared helper's tokens rather than a second spelling of them.
_FENCE_OPEN = "<!-- BEGIN"
_FENCE_CLOSE = "<!-- END"

#: What ``render_page`` must say when ``current()`` is ``None`` — an empty
#: section reads as "not rendered yet", which is a different claim.
_NO_CURRENT = "no accepted hypothesis"

#: Marks an attribute the stub must not carry at all, as opposed to one it
#: carries with a bad value. ``getattr`` has to fail, not return ``None``.
_ABSENT = object()

#: Every remote spelling the constructor must refuse. Note ``Path`` collapses
#: ``//`` to ``/`` (``Path("https://h/x")`` stringifies as ``https:/h/x``), so
#: the guard cannot simply match the two-slash form it was handed.
_REMOTE_PATHS: tuple[str, ...] = (
    "github:owner/repo",
    "GitHub:owner/repo",
    "http://example.invalid/wiki",
    "HTTP://example.invalid/wiki",
    "https://example.invalid/wiki",
    "HTTPS://example.invalid/wiki",
    "ssh://git@example.invalid/wiki",
    "SSH://git@example.invalid/wiki",
    "git@example.invalid:owner/repo",
    "GIT@example.invalid:owner/repo",
)

#: ``pattern_key`` → page file name. Only ``[A-Za-z0-9._-]`` survives; the
#: last three pairs are the reason the slug is not the authoritative name.
_SLUGS: tuple[tuple[str, str], ...] = (
    ("demo.pattern", "demo.pattern.json"),
    ("Ab-1_2.x", "Ab-1_2.x.json"),
    ("a/b", "a_b.json"),
    ("a b", "a_b.json"),
    ("../escape", ".._escape.json"),
)

#: Overrides that make a receipt unusable. Each must raise ``WikiError`` and
#: leave the filesystem exactly as it was.
_INVALID_OVERRIDES: tuple[tuple[str, dict[str, object]], ...] = (
    ("pattern_key-absent", {"pattern_key": _ABSENT}),
    ("pattern_key-none", {"pattern_key": None}),
    ("pattern_key-blank", {"pattern_key": "   "}),
    ("outcome-absent", {"outcome": _ABSENT}),
    ("outcome-none", {"outcome": None}),
    ("outcome-blank", {"outcome": "  "}),
    ("outcome-unknown", {"outcome": "maybe"}),
    ("snapshot_sha-absent", {"snapshot_sha": _ABSENT}),
    ("snapshot_sha-none", {"snapshot_sha": None}),
    ("snapshot_sha-blank", {"snapshot_sha": " "}),
    ("evidence_refs-absent", {"evidence_refs": _ABSENT}),
    ("evidence_refs-none", {"evidence_refs": None}),
    ("evidence_refs-non-str-element", {"evidence_refs": ("fixtures/a.log", 7)}),
)

_INVALID_PARAMS = [
    pytest.param(overrides, id=name) for name, overrides in _INVALID_OVERRIDES
]

#: Runtime agent surfaces, relative to ``src/molmcp``. None may name the wiki.
_RUNTIME_FILES: tuple[str, ...] = (
    "mcp_provider.py",
    "cli.py",
    "server.py",
    "planes.py",
    "skill/SKILL.md",
    "__init__.py",
)

#: The names whose absence proves the wiki is not wired into runtime.
_WIKI_NAMES: tuple[str, ...] = ("molmcp.evolution.wiki", "WikiStore", "Maintainer")

#: Package the scanned files belong to. Relative imports are resolved
#: against it, so ``from ..helpers import x`` is compared as
#: ``molmcp.helpers`` rather than as the two dots it was written with.
_EVOLUTION_PACKAGE = "molmcp.evolution"

#: Package this module is imported as — ``tests`` is on pytest's pythonpath,
#: so the test package is the directory name — for the same resolution when
#: the import walk is turned on this file.
_TEST_PACKAGE = "test_evolution"

#: Packages a leaf application module may not import. ``molmcp.discovery``
#: is the retrieval layer, and the source that speaks to a hosted git
#: service lives inside it — isolation from the package is isolation from
#: that source, and from every sibling it could be reached through.
_FORBIDDEN_IMPORTS: tuple[str, ...] = (
    "molmcp.discovery",
    "molmcp.mcp_provider",
    "fastmcp",
)

#: Runtime symbols the leaf may neither import nor name. Spelled plainly:
#: the walk below reads identifiers, so a module that merely mentions one
#: in a docstring does not depend on it — and neither does this file.
_FORBIDDEN_SYMBOLS: frozenset[str] = frozenset({"create_stack"})

#: The dotted path of the forge source module, checked as text as well.
#: ``importlib.import_module("molmcp.discovery.source.github")`` is an
#: import that the AST walk can only see as a string constant.
_FORGE_SOURCE_MODULE = "discovery.source.github"

_EVOLUTION_FILES: tuple[str, ...] = ("wiki.py", "__init__.py")

_FENCED = re.compile(r"<!-- BEGIN .*?-->(.*?)<!-- END .*?-->", re.DOTALL)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """The store directory: under ``tmp_path``, and not yet created."""
    return tmp_path / "wiki"


@pytest.fixture
def store(root: Path) -> WikiStore:
    return WikiStore(root)


@pytest.fixture
def maintainer(store: WikiStore) -> Maintainer:
    return Maintainer(store)


def _stub(**overrides: object) -> SimpleNamespace:
    """A duck-typed receipt: the four names ``ingest`` is allowed to read.

    Built here rather than imported so the wiki's contract stays its own —
    the episode receipt type is free to grow or drop fields without this
    module noticing.
    """
    fields: dict[str, object] = {
        "pattern_key": _PATTERN_KEY,
        "outcome": "rejected",
        "snapshot_sha": "sha-fail-1",
        "evidence_refs": ("fixtures/a.log",),
    }
    fields.update(overrides)
    return SimpleNamespace(**{k: v for k, v in fields.items() if v is not _ABSENT})


def _receipt(
    *,
    outcome: str = "rejected",
    snapshot_sha: str = "sha-fail-1",
    evidence_refs: tuple[str, ...] = ("fixtures/a.log",),
) -> WikiReceipt:
    """Build a receipt by keyword only — field order is the module's business."""
    return WikiReceipt(
        outcome=outcome,
        snapshot_sha=snapshot_sha,
        evidence_refs=evidence_refs,
    )


def _page(pattern_key: str = _PATTERN_KEY, *receipts: WikiReceipt) -> WikiPage:
    return WikiPage(
        pattern_key=pattern_key,
        receipts=receipts or (_receipt(),),
    )


def _page_text(root: Path, name: str = _PAGE_NAME) -> str:
    return (root / name).read_text(encoding="utf-8")


def _page_json(root: Path, name: str = _PAGE_NAME) -> dict[str, object]:
    loaded = json.loads(_page_text(root, name))
    assert isinstance(loaded, dict)
    return loaded


def _stored_receipts(root: Path, name: str = _PAGE_NAME) -> list[dict[str, object]]:
    receipts = _page_json(root, name)["receipts"]
    assert isinstance(receipts, list)
    return receipts


def _stored_shas(root: Path, name: str = _PAGE_NAME) -> list[str]:
    return [entry["snapshot_sha"] for entry in _stored_receipts(root, name)]


def _names(root: Path) -> list[str]:
    return sorted(entry.name for entry in root.iterdir())


def _section(rendered: str, word: str) -> str:
    """Everything after the first heading line naming *word*, lowercased match."""
    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#") and word in line.lower():
            return "\n".join(lines[index + 1 :])
    raise AssertionError(f"no heading names {word!r} in:\n{rendered}")


def _fenced_regions(rendered: str) -> list[str]:
    return _FENCED.findall(rendered)


def _read(path: Path) -> str:
    assert path.is_file(), f"{path} does not exist yet"
    return path.read_text(encoding="utf-8")


def _module_of(node: ast.ImportFrom, package: str) -> str:
    """Absolute dotted path of an ``ImportFrom``, relative levels resolved.

    ``from ..helpers import x`` inside ``molmcp.evolution`` is a dependency
    on ``molmcp.helpers``; comparing the written form against a package
    name would miss it.
    """
    if not node.level:
        return node.module or ""
    parts = package.split(".")
    base = ".".join(parts[: len(parts) - node.level + 1])
    if not base:
        return node.module or ""
    return f"{base}.{node.module}" if node.module else base


def _imported_modules(path: Path, package: str) -> list[str]:
    """Every module *path* imports, as absolute dotted paths, in file order."""
    modules: list[str] = []
    for node in ast.walk(ast.parse(_read(path))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(_module_of(node, package))
    return modules


def _forbidden_imports(path: Path, package: str = _EVOLUTION_PACKAGE) -> list[str]:
    """The forbidden packages *path* imports, submodules included."""
    return [
        module
        for module in _imported_modules(path, package)
        if any(
            module == root or module.startswith(f"{root}.")
            for root in _FORBIDDEN_IMPORTS
        )
    ]


def _forbidden_symbols(path: Path) -> list[str]:
    """The forbidden runtime names *path* imports, binds, reads, or calls.

    Identifiers only. A name inside a string or a docstring is a mention,
    not a dependency, and this walk never sees one.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(_read(path))):
        if isinstance(node, ast.ImportFrom):
            found.extend(
                alias.name for alias in node.names if alias.name in _FORBIDDEN_SYMBOLS
            )
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_SYMBOLS:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_SYMBOLS:
            found.append(node.attr)
    return found


class TestWikiStore:
    def test_path_has_no_default(self) -> None:
        """No cwd, no cacheDir, no graph.db: the caller names the directory."""
        with pytest.raises(TypeError):
            WikiStore()  # type: ignore[call-arg]

    @pytest.mark.parametrize("candidate", _REMOTE_PATHS)
    def test_rejects_a_remote_shaped_path(
        self, candidate: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A wiki store is a local directory; a remote is somebody else's job."""
        monkeypatch.chdir(tmp_path)

        with pytest.raises(WikiError):
            WikiStore(Path(candidate))

    @pytest.mark.parametrize("candidate", _REMOTE_PATHS)
    def test_a_remote_shaped_path_touches_no_disk(
        self, candidate: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rejection happens before any IO — cwd stays empty, mkdir or not."""
        monkeypatch.chdir(tmp_path)

        with pytest.raises(WikiError):
            WikiStore(Path(candidate))

        assert list(tmp_path.iterdir()) == []

    def test_construction_creates_no_directory(self, root: Path) -> None:
        WikiStore(root)

        assert not root.exists()

    def test_the_first_save_creates_the_directory(
        self, store: WikiStore, root: Path
    ) -> None:
        store.save(_page())

        assert root.is_dir()

    @pytest.mark.parametrize(
        ("pattern_key", "name"), _SLUGS, ids=[key for key, _ in _SLUGS]
    )
    def test_save_writes_the_slugged_page_file(
        self, store: WikiStore, root: Path, pattern_key: str, name: str
    ) -> None:
        store.save(_page(pattern_key))

        assert _names(root) == [name]

    def test_save_leaves_no_partial_behind(self, store: WikiStore, root: Path) -> None:
        store.save(_page())

        assert list(root.glob("*.partial")) == []
        assert _names(root) == [_PAGE_NAME]

    def test_save_then_load_round_trips_the_page(self, store: WikiStore) -> None:
        page = _page(_PATTERN_KEY, _receipt(), _receipt(snapshot_sha="sha-fail-2"))

        store.save(page)

        assert store.load(_PATTERN_KEY) == page

    def test_load_returns_none_for_an_unknown_pattern_key(
        self, store: WikiStore
    ) -> None:
        assert store.load(_PATTERN_KEY) is None

    def test_a_slug_collision_on_a_different_key_raises(
        self, store: WikiStore, root: Path
    ) -> None:
        """``a/b`` and ``a:b`` slug alike; the document's key is the authority."""
        store.save(_page("a/b"))

        with pytest.raises(WikiError):
            store.save(_page("a:b"))

    def test_a_slug_collision_on_a_different_key_writes_nothing(
        self, store: WikiStore, root: Path
    ) -> None:
        store.save(_page("a/b"))
        before = (root / "a_b.json").read_bytes()

        with pytest.raises(WikiError):
            store.save(_page("a:b"))

        assert (root / "a_b.json").read_bytes() == before
        assert _names(root) == ["a_b.json"]

    def test_the_page_json_holds_only_the_key_and_the_receipts(
        self, store: WikiStore, root: Path
    ) -> None:
        store.save(_page())

        assert set(_page_json(root)) == {"pattern_key", "receipts"}
        assert _page_json(root)["pattern_key"] == _PATTERN_KEY

    def test_the_receipts_json_holds_only_the_receipt_fields(
        self, store: WikiStore, root: Path
    ) -> None:
        store.save(_page())

        entry = _stored_receipts(root)[0]

        assert set(entry) == {"outcome", "snapshot_sha", "evidence_refs"}

    def test_the_receipts_json_keeps_the_saved_order(
        self, store: WikiStore, root: Path
    ) -> None:
        store.save(
            _page(
                _PATTERN_KEY,
                _receipt(snapshot_sha="sha-fail-1"),
                _receipt(snapshot_sha="sha-fail-2"),
            )
        )

        assert _stored_shas(root) == ["sha-fail-1", "sha-fail-2"]

    def test_the_page_json_carries_no_fence(self, store: WikiStore, root: Path) -> None:
        """Disk is data. The fence belongs to whoever shows it to an LLM."""
        store.save(_page())

        text = _page_text(root)

        assert _FENCE_OPEN not in text
        assert _FENCE_CLOSE not in text
        assert "fixtures/a.log" in text


class TestMaintainer:
    def test_ingest_returns_the_page_for_the_receipts_key(
        self, maintainer: Maintainer
    ) -> None:
        page = maintainer.ingest(_stub(outcome="accepted", snapshot_sha="sha-ok-1"))

        assert page.pattern_key == _PATTERN_KEY

    def test_two_receipts_on_one_key_make_one_page_file(
        self, maintainer: Maintainer, root: Path
    ) -> None:
        maintainer.ingest(_stub(snapshot_sha="sha-fail-1"))
        maintainer.ingest(_stub(snapshot_sha="sha-fail-2"))

        assert _names(root) == [_PAGE_NAME]

    def test_two_receipts_on_one_key_stay_in_ingest_order(
        self, maintainer: Maintainer
    ) -> None:
        maintainer.ingest(_stub(snapshot_sha="sha-fail-1"))
        page = maintainer.ingest(_stub(snapshot_sha="sha-fail-2"))

        assert [item.snapshot_sha for item in page.receipts] == [
            "sha-fail-1",
            "sha-fail-2",
        ]

    def test_current_is_none_without_an_accepted_receipt(
        self, maintainer: Maintainer
    ) -> None:
        maintainer.ingest(_stub(snapshot_sha="sha-fail-1"))
        page = maintainer.ingest(_stub(snapshot_sha="sha-fail-2"))

        assert page.current() is None

    def test_current_is_the_last_accepted_receipt(self, maintainer: Maintainer) -> None:
        maintainer.ingest(_stub(outcome="accepted", snapshot_sha="sha-ok-1"))
        maintainer.ingest(_stub(snapshot_sha="sha-fail-2"))
        page = maintainer.ingest(_stub(outcome="accepted", snapshot_sha="sha-ok-2"))

        current = page.current()

        assert current is not None
        assert current.snapshot_sha == "sha-ok-2"

    def test_an_accepted_receipt_keeps_the_earlier_rejections(
        self, maintainer: Maintainer, store: WikiStore
    ) -> None:
        """Success does not get to edit the record of the failures."""
        maintainer.ingest(_stub(snapshot_sha="sha-fail-1"))
        maintainer.ingest(_stub(snapshot_sha="sha-fail-2"))
        maintainer.ingest(_stub(outcome="accepted", snapshot_sha="sha-ok-1"))

        reloaded = store.load(_PATTERN_KEY)

        assert reloaded is not None
        assert [item.snapshot_sha for item in reloaded.receipts] == [
            "sha-fail-1",
            "sha-fail-2",
            "sha-ok-1",
        ]

    def test_an_accepted_receipt_still_leaves_one_page_file(
        self, maintainer: Maintainer, root: Path
    ) -> None:
        maintainer.ingest(_stub(snapshot_sha="sha-fail-1"))
        maintainer.ingest(_stub(snapshot_sha="sha-fail-2"))
        maintainer.ingest(_stub(outcome="accepted", snapshot_sha="sha-ok-1"))

        assert _names(root) == [_PAGE_NAME]

    def test_current_is_derived_rather_than_stored(
        self, maintainer: Maintainer, root: Path
    ) -> None:
        """A second writable field is a second truth to keep in sync."""
        maintainer.ingest(_stub(outcome="accepted", snapshot_sha="sha-ok-1"))

        assert "current" not in _page_json(root)
        assert "current" not in _page_text(root)

    @pytest.mark.parametrize("overrides", _INVALID_PARAMS)
    def test_an_invalid_receipt_raises(
        self, maintainer: Maintainer, overrides: dict[str, object]
    ) -> None:
        with pytest.raises(WikiError):
            maintainer.ingest(_stub(**overrides))

    @pytest.mark.parametrize("overrides", _INVALID_PARAMS)
    def test_an_invalid_receipt_creates_no_directory(
        self, maintainer: Maintainer, root: Path, overrides: dict[str, object]
    ) -> None:
        with pytest.raises(WikiError):
            maintainer.ingest(_stub(**overrides))

        assert not root.exists()

    @pytest.mark.parametrize("overrides", _INVALID_PARAMS)
    def test_an_invalid_receipt_leaves_an_existing_page_byte_identical(
        self, maintainer: Maintainer, root: Path, overrides: dict[str, object]
    ) -> None:
        maintainer.ingest(_stub(snapshot_sha="sha-fail-1"))
        before = (root / _PAGE_NAME).read_bytes()

        with pytest.raises(WikiError):
            maintainer.ingest(_stub(**overrides))

        assert (root / _PAGE_NAME).read_bytes() == before
        assert _names(root) == [_PAGE_NAME]

    @pytest.mark.parametrize("outcome", ["accepted", "rejected"])
    def test_outcome_normalizes_from_a_plain_string(
        self, maintainer: Maintainer, outcome: str
    ) -> None:
        page = maintainer.ingest(_stub(outcome=outcome))

        assert page.receipts[-1].outcome == outcome

    @pytest.mark.parametrize("outcome", ["accepted", "rejected"])
    def test_outcome_normalizes_from_an_enum_like_value(
        self, maintainer: Maintainer, outcome: str
    ) -> None:
        """``str(SimpleNamespace(...))`` is not the outcome; ``.value`` is."""
        page = maintainer.ingest(_stub(outcome=SimpleNamespace(value=outcome)))

        assert page.receipts[-1].outcome == outcome

    def test_an_enum_like_unknown_outcome_raises(self, maintainer: Maintainer) -> None:
        with pytest.raises(WikiError):
            maintainer.ingest(_stub(outcome=SimpleNamespace(value="maybe")))

    def test_an_empty_evidence_refs_sequence_is_legal(
        self, maintainer: Maintainer
    ) -> None:
        """Having no pointer is a fact about the episode, not a broken receipt."""
        page = maintainer.ingest(_stub(evidence_refs=[]))

        assert page.receipts[-1].evidence_refs == ()

    def test_evidence_refs_become_a_tuple(self, maintainer: Maintainer) -> None:
        page = maintainer.ingest(
            _stub(evidence_refs=["fixtures/a.log", "fixtures/b.log"])
        )

        refs = page.receipts[-1].evidence_refs

        assert isinstance(refs, tuple)
        assert refs == ("fixtures/a.log", "fixtures/b.log")

    def test_extra_attributes_are_discarded(self, maintainer: Maintainer) -> None:
        """Only four names are copied; a skill pointer is not a wiki field."""
        page = maintainer.ingest(
            _stub(skill_pointer="skills/demo/SKILL.md", episode_id="ep-001")
        )

        assert not hasattr(page, "skill_pointer")
        assert not hasattr(page.receipts[-1], "skill_pointer")
        assert not hasattr(page.receipts[-1], "episode_id")

    def test_a_skill_pointer_never_reaches_the_page_json(
        self, maintainer: Maintainer, root: Path
    ) -> None:
        maintainer.ingest(_stub(skill_pointer="skills/demo/SKILL.md"))

        text = _page_text(root)

        assert "skill_pointer" not in text
        assert "skills/demo/SKILL.md" not in text

    def test_rejections_survive_a_rewrite_of_an_outside_skill_pointer(
        self, maintainer: Maintainer, store: WikiStore, tmp_path: Path
    ) -> None:
        """The pointer file lives outside the store, so editing it proves
        nothing about the history — which is the point."""
        pointer = tmp_path / "skills" / "demo" / "SKILL.md"
        pointer.parent.mkdir(parents=True)
        pointer.write_text("first hypothesis\n", encoding="utf-8")
        maintainer.ingest(_stub(snapshot_sha="sha-fail-1"))
        maintainer.ingest(_stub(outcome="accepted", snapshot_sha="sha-ok-1"))

        pointer.write_text("rewritten hypothesis\n", encoding="utf-8")
        reloaded = store.load(_PATTERN_KEY)

        assert reloaded is not None
        assert [item.snapshot_sha for item in reloaded.receipts] == [
            "sha-fail-1",
            "sha-ok-1",
        ]

    def test_receipt_is_frozen(self) -> None:
        receipt = _receipt()

        with pytest.raises(dataclasses.FrozenInstanceError):
            receipt.outcome = "accepted"  # type: ignore[misc]

    def test_receipt_uses_slots(self) -> None:
        assert hasattr(WikiReceipt, "__slots__")
        assert not hasattr(_receipt(), "__dict__")

    def test_page_is_frozen(self) -> None:
        page = _page()

        with pytest.raises(dataclasses.FrozenInstanceError):
            page.pattern_key = "other"  # type: ignore[misc]

    def test_page_uses_slots(self) -> None:
        assert hasattr(WikiPage, "__slots__")
        assert not hasattr(_page(), "__dict__")


class TestRenderPage:
    def test_the_shared_fence_is_the_marker_this_module_greps(self) -> None:
        """The grep tokens are read off the helper, not a second spelling."""
        fenced = fence_untrusted("fixtures/a.log")

        assert fenced.startswith(_FENCE_OPEN)
        assert _FENCE_CLOSE in fenced

    def test_the_title_names_the_pattern_key(self) -> None:
        first = render_page(_page()).splitlines()[0]

        assert first.startswith("#")
        assert _PATTERN_KEY in first

    def test_the_current_section_names_the_accepted_snapshot(self) -> None:
        page = _page(
            _PATTERN_KEY,
            _receipt(snapshot_sha="sha-fail-1"),
            _receipt(outcome="accepted", snapshot_sha="sha-ok-1"),
        )

        assert "sha-ok-1" in _section(render_page(page), "current")

    def test_it_says_so_when_there_is_no_accepted_hypothesis(self) -> None:
        page = _page(
            _PATTERN_KEY,
            _receipt(snapshot_sha="sha-fail-1"),
            _receipt(snapshot_sha="sha-fail-2"),
        )

        assert _NO_CURRENT in render_page(page).lower()

    def test_it_does_not_say_so_once_something_is_accepted(self) -> None:
        page = _page(
            _PATTERN_KEY, _receipt(outcome="accepted", snapshot_sha="sha-ok-1")
        )

        assert _NO_CURRENT not in render_page(page).lower()

    def test_the_history_lists_every_receipt_in_ingest_order(self) -> None:
        page = _page(
            _PATTERN_KEY,
            _receipt(snapshot_sha="sha-fail-1"),
            _receipt(snapshot_sha="sha-fail-2"),
            _receipt(outcome="accepted", snapshot_sha="sha-ok-1"),
        )

        history = _section(render_page(page), "history")

        assert history.index("sha-fail-1") < history.index("sha-fail-2")
        assert history.index("sha-fail-2") < history.index("sha-ok-1")

    def test_every_evidence_ref_is_fenced(self) -> None:
        page = _page(
            _PATTERN_KEY,
            _receipt(evidence_refs=("fixtures/a.log",)),
            _receipt(
                outcome="accepted",
                snapshot_sha="sha-ok-1",
                evidence_refs=("fixtures/b.log", "fixtures/ok.log"),
            ),
        )

        rendered = render_page(page)
        regions = _fenced_regions(rendered)

        assert regions != []
        for ref in ("fixtures/a.log", "fixtures/b.log", "fixtures/ok.log"):
            assert any(ref in region for region in regions), ref

    def test_a_page_without_evidence_still_renders(self) -> None:
        """An empty pointer list is legal, so the renderer may not assume one."""
        page = _page(_PATTERN_KEY, _receipt(evidence_refs=()))

        assert _PATTERN_KEY in render_page(page)

    def test_the_page_on_disk_is_not_fenced(self, store: WikiStore, root: Path) -> None:
        """The same page: fenced when rendered, raw pointers when stored."""
        page = _page(_PATTERN_KEY, _receipt(evidence_refs=("fixtures/a.log",)))
        store.save(page)

        text = _page_text(root)

        assert _FENCE_OPEN in render_page(page)
        assert _FENCE_OPEN not in text
        assert "fixtures/a.log" in text

    def test_render_never_names_a_skill_pointer(self, maintainer: Maintainer) -> None:
        page = maintainer.ingest(_stub(skill_pointer="skills/demo/SKILL.md"))

        rendered = render_page(page)

        assert "skill_pointer" not in rendered
        assert "skills/demo/SKILL.md" not in rendered

    def test_render_reuses_the_shared_fence(self) -> None:
        """Reimplementing the marker would fork it the next time it changes."""
        source = _read(_WIKI)

        assert "fence_untrusted" in source
        assert _FENCE_OPEN not in source


@pytest.mark.parametrize("relative", _RUNTIME_FILES)
def test_a_runtime_surface_never_names_a_wiki_symbol(relative: str) -> None:
    """Static grep on purpose: booting the stack to prove the wiki is absent
    from it would be the one way to make it present."""
    source = _read(_PACKAGE / relative)

    assert [name for name in _WIKI_NAMES if name in source] == []


@pytest.mark.parametrize("name", _EVOLUTION_FILES)
def test_the_evolution_leaf_imports_no_runtime_package(name: str) -> None:
    """Isolation is a dependency claim, so imports are what it is read from."""
    imported = _forbidden_imports(_EVOLUTION / name)

    assert imported == [], f"evolution/{name} imports {imported}"


@pytest.mark.parametrize("name", _EVOLUTION_FILES)
def test_the_evolution_leaf_names_no_runtime_symbol(name: str) -> None:
    """Naming the stack factory is depending on it, however it was reached."""
    named = _forbidden_symbols(_EVOLUTION / name)

    assert named == [], f"evolution/{name} names {named}"


@pytest.mark.parametrize("name", _EVOLUTION_FILES)
def test_the_evolution_leaf_never_spells_the_forge_source_module(name: str) -> None:
    """The one text check left: a dotted path handed to ``import_module`` is
    an import the walk above sees only as a string."""
    source = _read(_EVOLUTION / name)

    assert _FORGE_SOURCE_MODULE not in source, (
        f"evolution/{name} spells {_FORGE_SOURCE_MODULE!r}; a leaf may not "
        f"reach the retrieval layer, dynamically either"
    )


def test_this_module_never_boots_the_server_stack() -> None:
    """The same walk, turned on this file: a test that starts the stack to
    prove it absent proves the opposite."""
    here = Path(__file__)

    assert _forbidden_imports(here, _TEST_PACKAGE) == []
    assert _forbidden_symbols(here) == []
