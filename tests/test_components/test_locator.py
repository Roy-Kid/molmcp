"""parse_harness_locator — GitHub and local locators to one origin key."""

from __future__ import annotations

import ast
import dataclasses
import sys
from pathlib import Path

import pytest

from molmcp.components.locator import (
    LocatorError,
    ParsedHarnessLocator,
    parse_harness_locator,
)

_LOCATOR_PY = (
    Path(__file__).resolve().parents[2] / "src" / "molmcp" / "components" / "locator.py"
)
_SRC = Path(__file__).resolve().parents[2] / "src" / "molmcp"
_FORBIDDEN_LAYERS = ("molmcp.discovery", "molmcp.settings")
_FORBIDDEN_LIBS = ("urllib", "git")


def _imported_targets(path: Path) -> tuple[str, ...]:
    """Absolute dotted import targets, with relative imports resolved."""
    package = ".".join(("molmcp", *path.relative_to(_SRC).parent.parts))
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
    return any(
        target == dotted or target.startswith(f"{dotted}.") for target in targets
    )


def _locator_source() -> Path:
    if not _LOCATOR_PY.is_file():
        pytest.skip("src/molmcp/components/locator.py is not present")
    return _LOCATOR_PY


class TestParseHarnessLocator:
    def test_locator_error_is_a_value_error(self):
        assert issubclass(LocatorError, ValueError)

    def test_parsed_locator_fields_are_the_six_documented_ones(self):
        assert {field.name for field in dataclasses.fields(ParsedHarnessLocator)} == {
            "locator",
            "kind",
            "origin_key",
            "ref",
            "owner",
            "repo",
        }

    def test_owner_repo_lowercases_origin_key(self):
        parsed = parse_harness_locator("MolCrafts/harness")
        assert parsed.locator == "MolCrafts/harness"
        assert parsed.kind == "github"
        assert parsed.origin_key == "molcrafts/harness"
        assert parsed.owner == "molcrafts"
        assert parsed.repo == "harness"
        assert parsed.ref == ""

    def test_https_git_url_with_trailing_slash_shares_origin_key(self):
        raw = "https://github.com/MolCrafts/harness.git/"
        parsed = parse_harness_locator(raw)
        assert parsed.locator == raw
        assert parsed.kind == "github"
        assert parsed.origin_key == "molcrafts/harness"
        assert parsed.owner == "molcrafts"
        assert parsed.repo == "harness"
        assert parsed.ref == ""

    def test_host_prefixed_owner_repo_shares_origin_key(self):
        parsed = parse_harness_locator("github.com/MolCrafts/harness")
        assert parsed.origin_key == "molcrafts/harness"
        assert parsed.kind == "github"
        assert parsed.owner == "molcrafts"
        assert parsed.repo == "harness"
        assert parsed.ref == ""

    def test_already_lowercase_owner_repo_shares_origin_key(self):
        parsed = parse_harness_locator("molcrafts/harness")
        assert parsed.origin_key == "molcrafts/harness"
        assert parsed.kind == "github"
        assert parsed.owner == "molcrafts"
        assert parsed.repo == "harness"
        assert parsed.ref == ""

    def test_www_host_shares_origin_key(self):
        parsed = parse_harness_locator("www.github.com/MolCrafts/harness")
        assert parsed.origin_key == "molcrafts/harness"
        assert parsed.kind == "github"

    def test_at_ref_is_not_part_of_origin_key(self):
        parsed = parse_harness_locator("Owner/repo@dev")
        assert parsed.locator == "Owner/repo@dev"
        assert parsed.kind == "github"
        assert parsed.origin_key == "owner/repo"
        assert parsed.owner == "owner"
        assert parsed.repo == "repo"
        assert parsed.ref == "dev"

    def test_absolute_path_is_local_with_resolved_origin_key(self, tmp_path: Path):
        raw = str(tmp_path / "harness")
        parsed = parse_harness_locator(raw)
        assert parsed.kind == "local"
        assert parsed.origin_key == str(Path(raw).expanduser().resolve())
        assert parsed.locator == raw
        assert parsed.ref == ""
        assert parsed.owner == ""
        assert parsed.repo == ""

    def test_home_relative_path_is_local_with_resolved_origin_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        raw = "~/harness"
        parsed = parse_harness_locator(raw)
        assert parsed.kind == "local"
        assert parsed.origin_key == str(Path(raw).expanduser().resolve())
        assert parsed.locator == raw
        assert parsed.ref == ""
        assert parsed.owner == ""
        assert parsed.repo == ""

    def test_parsed_locator_is_frozen(self):
        parsed = parse_harness_locator("molcrafts/harness")
        with pytest.raises(dataclasses.FrozenInstanceError):
            parsed.origin_key = "other"  # type: ignore[misc]

    @pytest.mark.parametrize("raw", ["./checkout", "../checkout"])
    def test_relative_path_raises(self, raw: str):
        with pytest.raises(LocatorError):
            parse_harness_locator(raw)

    def test_empty_string_raises(self):
        with pytest.raises(LocatorError):
            parse_harness_locator("")

    @pytest.mark.parametrize(
        "raw",
        [
            " MolCrafts/harness",
            "MolCrafts/harness ",
            "\tMolCrafts/harness",
            "MolCrafts/harness\n",
            "molcrafts / harness",
        ],
    )
    def test_whitespace_raises(self, raw: str):
        with pytest.raises(LocatorError):
            parse_harness_locator(raw)

    def test_http_url_raises(self):
        with pytest.raises(LocatorError):
            parse_harness_locator("http://github.com/MolCrafts/harness")

    def test_github_scheme_prefix_raises(self):
        with pytest.raises(LocatorError):
            parse_harness_locator("github:owner/repo")

    def test_url_with_extra_path_segment_raises(self):
        with pytest.raises(LocatorError):
            parse_harness_locator("https://github.com/MolCrafts/harness/tree/main")

    def test_backslash_in_a_github_locator_raises(self):
        with pytest.raises(LocatorError):
            parse_harness_locator(r"MolCrafts\harness")

    def test_a_windows_drive_path_is_local_only_on_windows(self):
        raw = r"C:\harness"
        if sys.platform == "win32":
            parsed = parse_harness_locator(raw)
            assert parsed.kind == "local"
            assert parsed.locator == raw
            assert parsed.origin_key == str(Path(raw).expanduser().resolve())
        else:
            with pytest.raises(LocatorError):
                parse_harness_locator(raw)

    def test_ssh_locator_raises(self):
        with pytest.raises(LocatorError):
            parse_harness_locator("git@github.com:MolCrafts/harness.git")

    @pytest.mark.parametrize("dotted", _FORBIDDEN_LAYERS)
    def test_module_does_not_import_discovery_or_settings(self, dotted: str):
        imported = _imported_targets(_locator_source())
        assert not _reaches(imported, dotted)

    @pytest.mark.parametrize("dotted", _FORBIDDEN_LIBS)
    def test_module_does_not_import_urllib_or_git(self, dotted: str):
        imported = _imported_targets(_locator_source())
        assert not _reaches(imported, dotted)

    def test_module_source_does_not_name_harness_source(self):
        source = _locator_source().read_text(encoding="utf-8")
        assert "HarnessSource" not in source

    def test_module_performs_no_import_the_walk_cannot_see(self):
        tree = ast.parse(_locator_source().read_text(encoding="utf-8"))
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
