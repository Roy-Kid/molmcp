"""Host write primitives: usage skill, daily bundle, adapter pointer, dev tree.

Every test drives one function of ``molmcp.host.install`` against a fake
checkout under ``tmp_path``. ``Path.home`` is patched to ``tmp_path`` so the
destination tree is the real layout without touching the developer's home.
No environment variable is ever set: the checkout is caller-supplied.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Literal

import pytest

import molmcp.skill
from molmcp.host.install import (
    ADAPTER_TEXT,
    install_skill,
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


class TestInstallSkill:
    """Writes the usage constitution and nothing else."""

    def test_the_written_file_is_remapped_for_the_host(self, home: Path) -> None:
        install_skill("grok")

        skill = home / ".grok" / "skills" / "molcrafts" / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        assert "when-to-use:" in text
        assert "metadata:" not in text
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
