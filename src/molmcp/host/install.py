"""Write primitives for ``molmcp init``: usage skill, daily, adapter, dev.

Each function here writes exactly one kind of thing and returns what it wrote.
There is deliberately no "do it all" facade: :mod:`molmcp.cli` composes these
in order, so no primitive can quietly grow a second destination.
:mod:`molmcp.host` introduces the vocabulary used below — host, usage skill
(constitution), adapter, daily bundle, dev bundle, checkout.

Two backends supply content. The *packaged* backend reads the usage
constitution from data files shipped inside the installed distribution (the
``molmcp.skill`` package) and always applies. The *checkout* backend is a
directory the caller passes in, holding the daily and dev bundles; when the
caller passes none, every bundle primitive is a no-op that creates no empty
directory. :func:`resolve_bundle_source` is the only place that choice is
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

import shutil
from importlib.resources import files
from pathlib import Path

from .layout import SKILL_NAME, Host, layout_for

ADAPTER_TEXT = """# molmcp adapter

Wired by `molmcp init`. This file is a pointer, not a constitution.

- Usage skill: `molcrafts` (auto-loaded). Do not edit the managed SKILL.md.
- MCP: one `molcrafts` server from `molmcp serve`.
- Daily skills: this host's `skills/` directory.
- Dev harness: `molmcp-dev/` (full bodies) and `commands/` (stubs only).

Do not copy skill, agent, or rule bodies into this file.
"""
"""Body of every host's ``molmcp-adapter.md``, byte-identical everywhere.

It is a pointer to where the real bodies live, so it carries no skill, agent,
or rule text, no timestamp, no home path, and no content hash. Every host in
:data:`~molmcp.host.layout.HOSTS` receives these exact bytes, so two machines
wired by the same molmcp version hold the same file.
"""

_DEV_STUB_TEMPLATE = """# /mol:{stem}

Dev command stub. The full harness body lives under this host's `molmcp-dev/`.
"""
"""Body of one ``commands/<stem>.md`` stub. The dev body stays out of it."""


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


def _copy_files(source: Path, dest: Path) -> tuple[Path, ...]:
    """Copy every file under *source* into *dest*, keeping relative layout.

    Args:
        source: Directory to read from.
        dest: Directory to write into; created on demand.

    Returns:
        The destination paths written, in sorted source order.
    """
    written = []
    for origin in sorted(path for path in source.rglob("*") if path.is_file()):
        target = dest / origin.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
        written.append(target)
    return tuple(written)


def resolve_bundle_source(source: Path | None) -> Path | None:
    """Interpret the caller's ``--source`` checkout, once and only here.

    ``None`` means "use the packaged backend", not "go and find a checkout":
    no working directory, git root, sibling checkout, or environment variable
    is consulted. A path that is not a directory fails here rather than
    quietly degrading into the packaged backend.

    Args:
        source: A directory holding the ``daily/`` and ``dev/`` bundles, or
            ``None`` to select the packaged backend.

    Returns:
        *source* unchanged when it is a directory, or ``None`` for the
        packaged backend.

    Raises:
        FileNotFoundError: If *source* is given but is not a directory.
    """
    if source is None:
        return None
    if not source.is_dir():
        raise FileNotFoundError(f"bundle source is not a directory: {source}")
    return source


def install_skill(host: Host) -> Path:
    """Overwrite the managed usage skill for *host*.

    The packaged ``SKILL.md`` is *copied*, not re-rendered from a template.
    Copying gives a checkout and a PyPI wheel one path — package data places
    the same file beside :mod:`molmcp.skill` either way — so the constitution
    lands byte-identical, mode and modification time included, and there is
    no rendering step that could drift from the file it claims to reproduce.

    Only the usage constitution is written: the adapter pointer and the daily
    bundle have their own primitives.

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
    shutil.copy2(_usage_skill_file(), dest)
    return dest


def materialize_daily(host: Host, source: Path | None) -> tuple[Path, ...]:
    """Copy the checkout's daily skills into *host*'s skills directory.

    Every ``<source>/daily/skills/<name>/`` tree lands beside the managed
    usage skill. A directory named :data:`~molmcp.host.layout.SKILL_NAME` is
    skipped so the constitution written by :func:`install_skill` is never
    clobbered, and the dev bundle is not read at all.

    Args:
        host: One of the known hosts.
        source: A resolved checkout (see :func:`resolve_bundle_source`), or
            ``None`` for the packaged backend, which carries no daily
            bundle and so copies nothing.

    Returns:
        The destination paths written, empty when there is nothing to copy.

    Raises:
        ValueError: If *host* is not a known host. Checked before *source*,
            so an unknown host raises even when nothing would be copied.
    """
    layout = layout_for(host)
    if source is None:
        return ()

    daily_root = source / "daily" / "skills"
    if not daily_root.is_dir():
        return ()

    skills_root = _home_path(layout.skill_dir).parent
    written: list[Path] = []
    for skill in sorted(path for path in daily_root.iterdir() if path.is_dir()):
        if skill.name == SKILL_NAME:
            continue
        written.extend(_copy_files(skill, skills_root / skill.name))
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


def materialize_dev_index(host: Host, source: Path | None) -> tuple[Path, ...]:
    """Write one slash-command stub per dev command into *host*'s commands.

    Each ``<source>/dev/commands/<stem>.md`` becomes a short stub naming
    ``/mol:<stem>``. The dev body itself is never copied here; it belongs to
    :func:`activate_dev`.

    Args:
        host: One of the known hosts.
        source: A resolved checkout (see :func:`resolve_bundle_source`), or
            ``None`` for the packaged backend, which carries no dev bundle
            and so leaves ``commands/`` uncreated.

    Returns:
        The stub paths written, empty when there is no dev command to index.

    Raises:
        ValueError: If *host* is not a known host. Checked before *source*,
            so an unknown host raises even when no stub would be written.
    """
    layout = layout_for(host)
    if source is None:
        return ()

    dev_commands = source / "dev" / "commands"
    if not dev_commands.is_dir():
        return ()

    commands_root = _home_path(layout.commands)
    origins = sorted(path for path in dev_commands.glob("*.md") if path.is_file())
    return tuple(
        _write(
            commands_root / f"{origin.stem}.md",
            _DEV_STUB_TEMPLATE.format(stem=origin.stem),
        )
        for origin in origins
    )


def activate_dev(host: Host, source: Path | None) -> Path | None:
    """Copy the checkout's whole dev tree into *host*'s ``molmcp-dev/``.

    This is the only destination that holds full dev bodies. The host's
    ``agents/`` and ``rules/`` directories are recorded in the layout table
    but are never written, so user files there are safe.

    Args:
        host: One of the known hosts.
        source: A resolved checkout (see :func:`resolve_bundle_source`), or
            ``None`` for the packaged backend, which carries no dev bundle
            and so leaves ``molmcp-dev/`` uncreated.

    Returns:
        The activated ``molmcp-dev/`` directory, or ``None`` when there is no
        dev tree to activate.

    Raises:
        ValueError: If *host* is not a known host. Checked before *source*,
            so an unknown host raises even when nothing would be activated.
    """
    layout = layout_for(host)
    if source is None:
        return None

    dev_source = source / "dev"
    if not dev_source.is_dir():
        return None

    dev_root = _home_path(layout.molmcp_dev)
    shutil.copytree(dev_source, dev_root, dirs_exist_ok=True)
    return dev_root


__all__ = [
    "ADAPTER_TEXT",
    "activate_dev",
    "install_skill",
    "materialize_daily",
    "materialize_dev_index",
    "resolve_bundle_source",
    "write_adapter",
]
