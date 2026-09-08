"""Host write primitives: usage skill, daily bundle, adapter pointer, dev tree.

Every test drives one function of ``molmcp.host.install`` against a fake
checkout under ``tmp_path``. ``Path.home`` is patched to ``tmp_path`` so the
destination tree is the real layout without touching the developer's home.
No environment variable is ever set: the checkout is caller-supplied.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Literal

import pytest

import molmcp.skill
from molmcp.host.install import (
    ADAPTER_TEXT,
    activate_dev,
    install_skill,
    materialize_daily,
    materialize_dev_index,
    resolve_bundle_source,
    write_adapter,
)

HostName = Literal["grok", "claude", "cursor", "codex"]

#: Every host this spec wires. One adapter body serves all of them.
ALL_HOSTS: tuple[HostName, ...] = ("grok", "claude", "cursor", "codex")

#: Fixture markers. A daily body must never appear in a dev destination and
#: a dev body must never appear in the daily ``skills/`` tree.
DAILY_BODY = "DAILY-SKILL-BODY"
DEV_BODY = "DEV-SKILL-BODY"

#: The managed usage constitution, so an overwrite by another primitive shows.
MANAGED_BODY = "MANAGED-BY-INSTALL-SKILL"

INSTALL_SOURCE = (
    Path(__file__).resolve().parents[2] / "src" / "molmcp" / "host" / "install.py"
)

#: The packaged usage constitution ``install_skill`` copies, named the way the
#: production lookup names it: the file beside ``molmcp/skill/__init__.py``.
PACKAGED_SKILL = Path(molmcp.skill.__file__).resolve().parent / "SKILL.md"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``Path.home()`` at ``tmp_path`` — never at a real home."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A caller-supplied bundle checkout: one daily skill, one dev command."""
    source = tmp_path / "checkout"

    daily_skill = source / "daily" / "skills" / "daily-demo" / "SKILL.md"
    daily_skill.parent.mkdir(parents=True)
    daily_skill.write_text(f"# daily-demo\n\n{DAILY_BODY}\n", encoding="utf-8")

    dev_command = source / "dev" / "commands" / "spec.md"
    dev_command.parent.mkdir(parents=True)
    dev_command.write_text(f"# spec\n\n{DEV_BODY}\n", encoding="utf-8")

    dev_skill = source / "dev" / "skills" / "spec" / "SKILL.md"
    dev_skill.parent.mkdir(parents=True)
    dev_skill.write_text(f"# spec\n\n{DEV_BODY}\n", encoding="utf-8")

    return source


def _file_bodies(root: Path) -> list[str]:
    """Text of every regular file under *root*; empty when *root* is absent."""
    if not root.is_dir():
        return []
    return [
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


class TestResolveBundleSource:
    """The only place a checkout path is interpreted."""

    def test_an_existing_directory_is_returned_unchanged(self, checkout: Path) -> None:
        assert resolve_bundle_source(checkout) == checkout

    def test_none_selects_the_packaged_backend(self) -> None:
        assert resolve_bundle_source(None) is None

    def test_a_file_is_not_a_checkout(self, tmp_path: Path) -> None:
        not_a_dir = tmp_path / "checkout.md"
        not_a_dir.write_text("# not a checkout\n", encoding="utf-8")

        with pytest.raises(FileNotFoundError):
            resolve_bundle_source(not_a_dir)

    def test_a_missing_path_is_not_a_checkout(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            resolve_bundle_source(tmp_path / "nowhere")

    def test_the_module_never_reads_the_environment(self) -> None:
        tree = ast.parse(INSTALL_SOURCE.read_text(encoding="utf-8"))

        os_reads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in {"environ", "getenv"}
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ]
        bare_reads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "getenv"
        ]

        assert (os_reads, bare_reads) == ([], [])


class TestInstallSkill:
    """Writes the usage constitution and nothing else."""

    def test_the_written_file_is_a_copy_of_the_packaged_one(self, home: Path) -> None:
        install_skill("grok")

        skill = home / ".grok" / "skills" / "molcrafts" / "SKILL.md"
        assert skill.read_bytes() == PACKAGED_SKILL.read_bytes()
        assert skill != PACKAGED_SKILL

    def test_the_template_carries_the_packaged_marker(self, home: Path) -> None:
        install_skill("grok")

        skill = home / ".grok" / "skills" / "molcrafts" / "SKILL.md"
        assert "SYMBOL_NOT_FOUND" in skill.read_text(encoding="utf-8")

    def test_it_returns_the_path_it_wrote(self, home: Path) -> None:
        written = install_skill("grok")

        assert written == home / ".grok" / "skills" / "molcrafts" / "SKILL.md"

    def test_it_does_not_write_the_adapter(self, home: Path) -> None:
        install_skill("grok")

        assert not (home / ".grok" / "molmcp-adapter.md").exists()

    def test_it_creates_no_other_skill_directory(self, home: Path) -> None:
        install_skill("grok")

        skills = home / ".grok" / "skills"
        assert sorted(path.name for path in skills.iterdir()) == ["molcrafts"]

    def test_its_signature_takes_no_source_argument(self) -> None:
        assert list(inspect.signature(install_skill).parameters) == ["host"]


class TestMaterializeDaily:
    """Copies ``daily/skills`` only — the dev tree stays out."""

    def test_a_daily_skill_lands_in_the_host_skills_tree(
        self, home: Path, checkout: Path
    ) -> None:
        materialize_daily("grok", checkout)

        skill = home / ".grok" / "skills" / "daily-demo" / "SKILL.md"
        assert DAILY_BODY in skill.read_text(encoding="utf-8")

    def test_the_dev_tree_does_not_leak_into_daily(
        self, home: Path, checkout: Path
    ) -> None:
        materialize_daily("grok", checkout)

        assert not (home / ".grok" / "skills" / "spec").exists()

    def test_the_managed_usage_skill_is_not_overwritten(
        self, home: Path, checkout: Path
    ) -> None:
        managed = home / ".grok" / "skills" / "molcrafts" / "SKILL.md"
        managed.parent.mkdir(parents=True)
        managed.write_text(MANAGED_BODY, encoding="utf-8")

        materialize_daily("grok", checkout)

        assert managed.read_text(encoding="utf-8") == MANAGED_BODY

    def test_it_returns_the_paths_it_wrote(self, home: Path, checkout: Path) -> None:
        written = materialize_daily("grok", checkout)

        skills = home / ".grok" / "skills"
        assert isinstance(written, tuple)
        assert written != ()
        assert all(isinstance(path, Path) and path.exists() for path in written)
        assert all(skills in path.parents for path in written)
        assert any("daily-demo" in path.parts for path in written)

    def test_no_source_writes_no_daily_skill(self, home: Path) -> None:
        written = materialize_daily("grok", None)

        skills = home / ".grok" / "skills"
        assert written == ()
        assert not skills.exists() or [path.name for path in skills.iterdir()] == []


class TestWriteAdapter:
    """A stable pointer file — byte-identical everywhere, forever."""

    def test_the_written_bytes_are_the_constant(self, home: Path) -> None:
        write_adapter("grok")

        adapter = home / ".grok" / "molmcp-adapter.md"
        assert adapter.read_bytes() == ADAPTER_TEXT.encode("utf-8")

    def test_it_returns_the_path_it_wrote(self, home: Path) -> None:
        written = write_adapter("grok")

        assert written == home / ".grok" / "molmcp-adapter.md"

    def test_the_constant_is_the_pointer_preamble(self) -> None:
        assert "# molmcp adapter" in ADAPTER_TEXT
        assert "pointer, not a constitution" in ADAPTER_TEXT

    def test_it_carries_no_skill_bodies(self) -> None:
        assert DEV_BODY not in ADAPTER_TEXT
        assert DAILY_BODY not in ADAPTER_TEXT

    def test_it_carries_no_machine_specific_home_path(self, home: Path) -> None:
        written = write_adapter("grok")

        assert str(home) not in written.read_text(encoding="utf-8")

    def test_it_carries_no_timestamp(self) -> None:
        assert re.search(r"\d{4}-\d{2}-\d{2}", ADAPTER_TEXT) is None

    def test_it_carries_no_content_hash(self) -> None:
        assert re.search(r"\b[0-9a-f]{40,64}\b", ADAPTER_TEXT) is None

    def test_it_takes_no_source_argument(self) -> None:
        assert list(inspect.signature(write_adapter).parameters) == ["host"]

    def test_every_host_gets_byte_identical_content(self, home: Path) -> None:
        bodies = {
            host: write_adapter(host).read_text(encoding="utf-8") for host in ALL_HOSTS
        }

        assert set(bodies.values()) == {ADAPTER_TEXT}


class TestMaterializeDevIndex:
    """``commands/`` holds slash-command stubs, never dev bodies."""

    def test_the_spec_stub_names_the_slash_command(
        self, home: Path, checkout: Path
    ) -> None:
        materialize_dev_index("grok", checkout)

        stub = home / ".grok" / "commands" / "spec.md"
        assert "/mol:spec" in stub.read_text(encoding="utf-8")

    def test_the_stub_does_not_carry_the_dev_body(
        self, home: Path, checkout: Path
    ) -> None:
        materialize_dev_index("grok", checkout)

        stub = home / ".grok" / "commands" / "spec.md"
        assert DEV_BODY not in stub.read_text(encoding="utf-8")

    def test_it_returns_the_paths_it_wrote(self, home: Path, checkout: Path) -> None:
        written = materialize_dev_index("grok", checkout)

        assert isinstance(written, tuple)
        assert all(isinstance(path, Path) for path in written)
        assert home / ".grok" / "commands" / "spec.md" in written

    def test_no_source_creates_no_commands_directory(self, home: Path) -> None:
        written = materialize_dev_index("grok", None)

        assert written == ()
        assert not (home / ".grok" / "commands").exists()


class TestActivateDev:
    """Full dev bodies live under ``molmcp-dev/`` and nowhere else."""

    def test_the_dev_bodies_land_under_molmcp_dev(
        self, home: Path, checkout: Path
    ) -> None:
        activate_dev("grok", checkout)

        bodies = _file_bodies(home / ".grok" / "molmcp-dev")
        assert any(DEV_BODY in body for body in bodies)

    def test_the_daily_skills_tree_never_sees_a_dev_body(
        self, home: Path, checkout: Path
    ) -> None:
        activate_dev("grok", checkout)

        bodies = _file_bodies(home / ".grok" / "skills")
        assert not any(DEV_BODY in body for body in bodies)

    def test_it_returns_the_dev_root(self, home: Path, checkout: Path) -> None:
        written = activate_dev("grok", checkout)

        assert written == home / ".grok" / "molmcp-dev"

    def test_no_source_returns_none_and_writes_nothing(self, home: Path) -> None:
        written = activate_dev("grok", None)

        assert written is None
        assert not (home / ".grok" / "molmcp-dev").exists()

    def test_it_does_not_write_host_agents_or_rules(
        self, home: Path, checkout: Path
    ) -> None:
        activate_dev("grok", checkout)

        assert not (home / ".grok" / "agents").exists()
        assert not (home / ".grok" / "rules").exists()
