"""Write primitives for ``molmcp init``: usage skill, daily, adapter, dev.

Each function here writes exactly one kind of thing and returns what it wrote.
There is deliberately no "do it all" facade: :mod:`molmcp.cli` composes these
in order, so no primitive can quietly grow a second destination.
:mod:`molmcp.host` introduces the vocabulary used below — host, usage skill
(constitution), adapter, daily bundle, dev bundle, checkout.

Two backends supply content. The *packaged* backend reads the usage
constitution and the extra skills in :data:`EXTRA_SKILLS` from data files
shipped inside the installed distribution (the ``molmcp.skill`` package)
and always applies. The *checkout* backend is a directory the caller
passes in, holding the daily and dev bundles; when the caller passes none,
every bundle primitive is a no-op that creates no empty directory.
:func:`resolve_bundle_source` is the only place that choice is
interpreted — nothing here probes the working directory, a git root, a
sibling checkout, or an environment variable.

Validation of the host name happens before that no-op check, so an unknown
host still raises even when there is no checkout to materialize.

This module is Layer 2 and imports the standard library only (plus
``importlib.resources`` reading ``molmcp.skill``). Nothing under
``molmcp.host`` may import ``molmcp.client_config``, ``molmcp.cli``,
``molmcp.server``, ``molmcp.providers``, or ``molmcp.discovery``.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from .layout import Host, layout_for, remap_frontmatter

EXTRA_SKILLS: tuple[str, ...] = ("molexp-plan",)
"""Packaged skills ``molmcp init`` writes beside the usage constitution.

The constitution is :func:`install_skill` and this tuple is
:func:`install_extra_skills`. A catalog row may overlay an extra skill;
it cannot take the constitution's name.
"""

ADAPTER_TEXT = """# molmcp adapter

Wired by `molmcp init`. This file is a pointer, not a constitution.

- Usage skill: `molcrafts` (auto-loaded). Do not edit the managed SKILL.md.
- MCP: one `molcrafts` server from `molmcp serve`.
- Catalog components: this host's `skills/`, `agents/`, and `rules/` directories.

Do not copy skill, agent, or rule bodies into this file.
"""
"""Body of every host's ``molmcp-adapter.md``, byte-identical everywhere.

It is a pointer to where the real bodies live, so it carries no skill, agent,
or rule text, no timestamp, no home path, and no content hash. Every host in
:data:`~molmcp.host.layout.HOSTS` receives these exact bytes, so two machines
wired by the same molmcp version hold the same file.
"""


def _home_path(parts: tuple[str, ...]) -> Path:
    """Resolve a layout path tuple against the current home directory."""
    return Path.home().joinpath(*parts)


def _write(dest: Path, text: str) -> Path:
    """Create *dest*'s parent, write *text* as UTF-8, and return *dest*."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


def _usage_skill_file() -> Path:
    """Locate the packaged usage constitution ``SKILL.md``.

    The lookup happens here and nowhere else, so a checkout and an installed
    wheel name the same file: package data puts ``SKILL.md`` beside
    ``molmcp/skill/__init__.py`` in both, leaving no second location to fall
    back to.

    Returns:
        Path of the ``SKILL.md`` shipped inside :mod:`molmcp.skill`.
    """
    return Path(str(files("molmcp.skill") / "SKILL.md"))


def install_skill(host: Host) -> Path:
    """Overwrite the managed usage skill for *host*.

    The packaged ``SKILL.md`` is *copied*, not re-rendered from a template.
    Copying gives a checkout and a PyPI wheel one path — package data places
    the same file beside :mod:`molmcp.skill` either way — so the constitution
    lands byte-identical, mode and modification time included, and there is
    no rendering step that could drift from the file it claims to reproduce.

    Only the usage constitution is written: extra packaged skills, the
    adapter pointer, and the daily bundle have their own primitives.

    Args:
        host: One of the known hosts.

    Returns:
        The ``SKILL.md`` path written.

    Raises:
        ValueError: If *host* is not a known host.
        OSError: If the packaged ``SKILL.md`` cannot be read; that is a
            broken installation, which :func:`molmcp.cli.main` already
            reports as a message rather than a traceback.
    """
    skill_dir = _home_path(layout_for(host).skill_dir)
    skill_dir.mkdir(parents=True, exist_ok=True)
    dest = skill_dir / "SKILL.md"
    text = _usage_skill_file().read_text(encoding="utf-8")
    return _write(dest, remap_frontmatter(text, host))


def _extra_skill_file(name: str) -> Path:
    """Locate one packaged extra skill ``SKILL.md``.

    Extra skills live in a subdirectory of :mod:`molmcp.skill` named after
    the skill, so the constitution at the package root stays the one file
    :func:`install_skill` copies.

    Args:
        name: A member of :data:`EXTRA_SKILLS`.

    Returns:
        Path of that skill's ``SKILL.md`` inside :mod:`molmcp.skill`.

    Raises:
        ValueError: If *name* is not a packaged extra skill.
    """
    if name not in EXTRA_SKILLS:
        raise ValueError(f"unknown skill {name!r}; known: {', '.join(EXTRA_SKILLS)}")
    return Path(str(files("molmcp.skill") / name / "SKILL.md"))


def install_extra_skills(host: Host) -> tuple[Path, ...]:
    """Overwrite every packaged extra skill for *host*.

    Writes beside the usage constitution, never into its directory. Each
    body is remapped the same way :func:`install_skill` remaps the
    constitution, so a grok install drops ``metadata:`` here too.

    Args:
        host: One of the known hosts.

    Returns:
        The ``SKILL.md`` paths written, in :data:`EXTRA_SKILLS` order.

    Raises:
        ValueError: If *host* is not a known host.
        OSError: If a packaged extra ``SKILL.md`` cannot be read.
    """
    skills_root = _home_path(layout_for(host).skill_dir[:-1])
    written: list[Path] = []
    for name in EXTRA_SKILLS:
        dest = skills_root / name / "SKILL.md"
        text = _extra_skill_file(name).read_text(encoding="utf-8")
        written.append(_write(dest, remap_frontmatter(text, host)))
    return tuple(written)


def write_adapter(host: Host) -> Path:
    """Write *host*'s stable pointer file ``molmcp-adapter.md``.

    The body is :data:`ADAPTER_TEXT` verbatim, so re-running ``molmcp init``
    on the same version is a no-diff write.

    Args:
        host: One of the known hosts.

    Returns:
        The adapter path written.

    Raises:
        ValueError: If *host* is not a known host.
    """
    return _write(_home_path(layout_for(host).adapter), ADAPTER_TEXT)


__all__ = [
    "ADAPTER_TEXT",
    "EXTRA_SKILLS",
    "install_extra_skills",
    "install_skill",
    "write_adapter",
]
