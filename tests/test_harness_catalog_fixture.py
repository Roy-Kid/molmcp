"""The published harness example, the licence table, and the exit runbook.

Three documents make a promise this repository has to keep.

``docs/concepts/harness.example.toml`` shows a reader what a harness catalog
looks like. An example that no longer parses teaches the wrong grammar
confidently, so it is loaded here through the *real*
:func:`molmcp.components.load_harness_catalog` rather than a copy of the
parser. This module deliberately defines no catalog type of its own —
``molmcp.components`` owns the schema, and a second definition would be the
one that drifts.

``docs/guides/harness-migration.md`` is a runbook a human follows. It stops
before every operation that mutates a repository on GitHub, because each of
those needs its own authorisation; the stop is pinned here so that a later
edit cannot quietly turn a description into an instruction.

``LICENSE`` is molmcp's grant. The licence table on the concept page describes
it, and must never be read as reissuing it.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

from molmcp.components import (
    CatalogError,
    ComponentKind,
    load_harness_catalog,
)

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "molmcp"
_DOCS = _ROOT / "docs"
_NOTES = _ROOT / ".claude" / "notes"

_EXAMPLE = _DOCS / "concepts" / "harness.example.toml"
_CONCEPT = _DOCS / "concepts" / "harness.md"
_RUNBOOK = _DOCS / "guides" / "harness-migration.md"
_CONTRACT = _NOTES / "harness-contract.md"
_NOTES_INDEX = _NOTES / "README.md"
_LICENSE = _ROOT / "LICENSE"
_INSTALLATION = _DOCS / "get-started" / "installation.md"
_WORKBENCH = _DOCS / "guides" / "molvis-workbench.md"
_ZENSICAL = _ROOT / "zensical.toml"

#: Commit identity a caller supplies. Written out here rather than read from
#: the example on purpose: identity lives outside the catalog file, so a test
#: that took it from the file would be asserting the opposite of the rule.
_SHA = "9f1c3b2a7d4e0165c8a9b3d27e5f10486c73ab92"
_CAPABILITIES = frozenset({"provider-sdk", "harness-catalog"})

#: The keys the concept page names, spelled out again here so that changing
#: one side fails instead of silently agreeing with itself.
_TOP_LEVEL_KEYS = frozenset({"requires", "component"})
_COMPONENT_KEYS = frozenset({"kind", "name", "path", "entrypoint"})
_BUNDLE_KEYS = frozenset({"kind", "name", "members", "requires"})
_REQUIRED_BUNDLES = frozenset({"daily", "dev"})
_ENTRYPOINT_KINDS = frozenset({"provider", "overlay"})

#: Vocabulary that belongs to the concept page and nowhere else.
_LABELS = ("official", "gate", "canary")

#: Remote operations the runbook may only describe *after* it has stopped.
_GITHUB_MUTATIONS = ("create", "archive", "bundle", "delet")

#: An install line for the repository that is being retired.
_MARKETPLACE_ADD = re.compile(r"marketplace\s+add\s+\S*molcrafts-harness", re.I)

#: A registration line of the shape an entry-point table uses.
_HARNESS_ENTRY_POINT = re.compile(r"^harness\s*=\s*\S", re.M)

#: Pages that may only point at the concept page, never restate its contract.
_POINTER_PAGES = (
    _DOCS / "concepts" / "architecture.md",
    _DOCS / "concepts" / "provider-design.md",
    _DOCS / "concepts" / "providers.md",
    _DOCS / "guides" / "write-a-provider.md",
    _DOCS / "reference" / "cli.md",
    _WORKBENCH,
    _INSTALLATION,
)

_DOCSTRING_OWNERS = (
    ast.Module,
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
)


def _docstring_ids(tree: ast.Module) -> set[int]:
    """Identify the string constants that are docstrings rather than code."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_OWNERS) or not node.body:
            continue
        first = node.body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.add(id(value))
    return found


def _modules_naming_the_catalog_file() -> set[str]:
    """Source files that mention ``harness.toml`` in executable code.

    Comments never reach the syntax tree and docstrings are filtered out, so
    what remains is the set of modules that actually resolve the filename.
    """
    naming: set[str] = set()
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstring_ids(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            if not isinstance(node.value, str) or id(node) in skip:
                continue
            if "harness.toml" in node.value:
                naming.add(path.relative_to(_SRC).as_posix())
    return naming


def _markdown_under(*roots: Path) -> list[Path]:
    return [path for root in roots for path in sorted(root.rglob("*.md"))]


def _nav_targets(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [target for item in node for target in _nav_targets(item)]
    if isinstance(node, dict):
        return [target for item in node.values() for target in _nav_targets(item)]
    return []


def _numbered_headings(text: str) -> list[str]:
    return re.findall(r"^##\s*(\d+)\.", text, re.M)


@pytest.fixture(scope="module")
def example_text() -> str:
    return _EXAMPLE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def example_table(example_text: str) -> dict[str, object]:
    return tomllib.loads(example_text)


@pytest.fixture
def catalog(example_text: str, tmp_path: Path):
    """Load the published example under the name a consumer would read.

    The copy is the point. The example is published as
    ``harness.example.toml`` and consumed as ``harness.toml``, and
    ``load_harness_catalog`` only ever joins the second name onto a root the
    caller hands it — never onto the working directory.
    """
    (tmp_path / "harness.toml").write_text(example_text, encoding="utf-8")
    return load_harness_catalog(tmp_path, _SHA, _CAPABILITIES)


class TestHarnessCatalogFixture:
    # --------------------------------------------------------------- example

    def test_published_example_loads_through_the_real_loader(self, catalog):
        assert catalog.sha == _SHA
        assert set(catalog.requires) <= _CAPABILITIES
        assert catalog.components
        assert {b.name for b in catalog.bundles} >= _REQUIRED_BUNDLES

    def test_example_carries_every_key_the_page_names(self, example_table):
        assert set(example_table) == _TOP_LEVEL_KEYS
        rows = example_table["component"]
        assert isinstance(rows, list)
        assert rows

        component_keys: set[str] = set()
        bundle_keys: set[str] = set()
        for row in rows:
            assert isinstance(row, dict)
            if row.get("kind") == "bundle":
                assert set(row) <= _BUNDLE_KEYS, row
                assert {"kind", "name", "members"} <= set(row), row
                bundle_keys |= set(row)
            else:
                assert set(row) <= _COMPONENT_KEYS, row
                assert {"kind", "name", "path"} <= set(row), row
                component_keys |= set(row)

        # Every named key is demonstrated at least once, not merely allowed.
        assert component_keys == _COMPONENT_KEYS
        assert bundle_keys == _BUNDLE_KEYS

    def test_example_demonstrates_every_component_kind(self, catalog):
        assert {spec.kind for spec in catalog.components} == set(ComponentKind)

    def test_entrypoint_is_on_exactly_the_kinds_that_need_one(self, catalog):
        for spec in catalog.components:
            needs = str(spec.kind) in _ENTRYPOINT_KINDS
            assert (spec.entrypoint is not None) is needs, spec

    def test_id_is_derived_and_never_written(self, catalog, example_table):
        for row in example_table["component"]:
            assert "id" not in row, row
        for spec in catalog.components:
            assert spec.id == f"{spec.kind}.{spec.name}"
            assert catalog.get(spec.id) is spec

    def test_identity_and_labels_are_not_catalog_keys(self, example_table):
        assert "sha" not in example_table
        assert "label" not in example_table
        for row in example_table["component"]:
            assert "sha" not in row, row
            assert "label" not in row, row

    def test_a_label_key_stops_the_file_loading(self, example_text, tmp_path):
        """The counter-example: a label cannot be smuggled into the grammar."""
        spiked = f'label = "official"\n{example_text}'
        (tmp_path / "harness.toml").write_text(spiked, encoding="utf-8")
        with pytest.raises(CatalogError):
            load_harness_catalog(tmp_path, _SHA, _CAPABILITIES)

    def test_example_lives_under_docs_and_not_at_the_repo_root(self):
        assert _EXAMPLE.is_file()
        assert not (_ROOT / "harness.toml").exists()
        assert not (_ROOT / "harness.example.toml").exists()

    def test_consumed_filename_is_resolved_in_exactly_one_module(self):
        assert _modules_naming_the_catalog_file() == {"components/catalog.py"}

    # ---------------------------------------------------------- concept page

    def test_page_states_the_two_registries_are_disjoint(self):
        text = _CONCEPT.read_text(encoding="utf-8")
        assert "molmcp.providers" in text
        assert "Git SHA" in text
        assert "molmcp serve harness" in text
        assert "plane id" in text

    def test_page_treats_the_three_words_as_labels_on_a_sha(self):
        text = _CONCEPT.read_text(encoding="utf-8")
        for label in _LABELS:
            assert label in text
        assert "not settings" in text
        assert "not environment variables" in text

    def test_page_maps_the_example_to_the_consumed_filename(self):
        text = _CONCEPT.read_text(encoding="utf-8")
        assert "harness.example.toml" in text
        assert "harness.toml" in text
        assert "working directory" in text

    def test_page_refuses_wikiskill_as_an_init_channel(self):
        text = _CONCEPT.read_text(encoding="utf-8")
        assert "WikiSkill" in text
        for wrapped in ("packages", "molvis_open", "molq_*", "molexp_*"):
            assert wrapped in text

    def test_page_states_a_new_empty_repo_not_a_rename(self):
        text = _CONCEPT.read_text(encoding="utf-8")
        assert "MolCrafts/harness" in text
        assert "molcrafts-harness" in text
        # The page says "a new, empty repository"; match the claim, not one
        # particular way of punctuating it.
        assert re.search(r"new,?\s+empty\s+repository", text) is not None
        assert "rename" in text

    # --------------------------------------------------------------- licence

    def test_root_license_is_still_bsd_3_clause(self):
        text = _LICENSE.read_text(encoding="utf-8")
        assert text.startswith("BSD 3-Clause License")

    def test_license_table_records_the_grant_without_reissuing_it(self):
        text = _CONCEPT.read_text(encoding="utf-8")
        assert "BSD-3-Clause" in text
        assert "MIT" in text
        assert "LICENSE" in text

    # --------------------------------------------------------------- runbook

    def test_runbook_is_five_numbered_steps(self):
        text = _RUNBOOK.read_text(encoding="utf-8")
        assert _numbered_headings(text) == ["1", "2", "3", "4", "5"]

    def test_runbook_stops_before_any_github_mutation(self):
        text = _RUNBOOK.read_text(encoding="utf-8")
        stop = text.index("STOP")
        lowered = text.lower()
        for word in _GITHUB_MUTATIONS:
            first = lowered.find(word)
            # A word the runbook never uses cannot appear too early. find()
            # answers -1 for absent, which is not "before the STOP".
            if first == -1:
                continue
            assert first > stop, (
                f"{word!r} is at {first}, before the STOP at {stop}; the "
                "runbook may only describe remote mutations after it stops"
            )

    def test_runbook_forbids_piling_provider_repos_into_the_new_one(self):
        text = _RUNBOOK.read_text(encoding="utf-8")
        assert "do not pile provider" in text.lower()

    # ---------------------------------------------------------- notes and nav

    def test_contract_note_holds_the_two_rules_and_no_schema(self):
        text = _CONTRACT.read_text(encoding="utf-8")
        assert "new empty repository" in text
        assert "molcrafts-harness" in text
        assert "after cutover" in text
        assert "Git SHA" in text
        for schema_word in ("[[component]]", "entrypoint", "members", "BSD"):
            assert schema_word not in text, schema_word

    def test_contract_note_is_indexed(self):
        assert "harness-contract.md" in _NOTES_INDEX.read_text(encoding="utf-8")

    def test_nav_lists_the_concept_page_and_the_runbook(self):
        site = tomllib.loads(_ZENSICAL.read_text(encoding="utf-8"))
        targets = _nav_targets(site["project"]["nav"])
        assert "concepts/harness.md" in targets
        assert "guides/harness-migration.md" in targets

    # --------------------------------------------------------- pointer pages

    def test_every_pointer_page_links_to_the_concept_page(self):
        for path in _POINTER_PAGES:
            text = path.read_text(encoding="utf-8")
            assert "harness.md)" in text, path

    def test_pointer_pages_add_no_entry_point_and_no_label_words(self):
        for path in _POINTER_PAGES:
            text = path.read_text(encoding="utf-8")
            assert not _HARNESS_ENTRY_POINT.search(text), path
            assert "canary" not in text, path

    def test_workbench_separates_its_playbook_from_the_sha_catalog(self):
        text = _WORKBENCH.read_text(encoding="utf-8")
        assert "molvis-agent-e2e/" in text
        assert "Git SHA plugin catalog" in text
        assert "../concepts/harness.md" in text

    def test_installation_keeps_its_uv_prerelease_warning(self):
        text = _INSTALLATION.read_text(encoding="utf-8")
        assert "Without `--prerelease=allow`, uv will not install 0.6+" in text
        assert "4.0.0b5" in text

    # ------------------------------------------------------- retired address

    def test_no_page_advertises_the_old_marketplace_as_current(self):
        offenders = [
            path.relative_to(_ROOT).as_posix()
            for path in _markdown_under(_DOCS, _NOTES)
            if _MARKETPLACE_ADD.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == []

    def test_new_pages_introduce_no_environment_variable(self):
        for path in (_EXAMPLE, _RUNBOOK, _CONTRACT):
            assert "MOLMCP_" not in path.read_text(encoding="utf-8"), path
        for line in _CONCEPT.read_text(encoding="utf-8").splitlines():
            if "MOLMCP_" in line:
                assert "reads no" in line, line
