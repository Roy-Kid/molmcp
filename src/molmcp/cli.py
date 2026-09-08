"""MolMCP CLI — molcrafts core plus one process per provider plane."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__, settings
from .client_config import render_init
from .config import AppConfig, ConfigurationError, load_config
from .gate import run_gate
from .host import (
    HOSTS,
    activate_dev,
    default_write_path,
    install_skill,
    materialize_daily,
    materialize_dev_index,
    resolve_bundle_source,
    write_adapter,
)
from .planes import (
    CORE_PLANE_ID,
    GONE_PLANE_IDS,
    gone_plane_message,
    known_plane_ids,
    list_plane_infos,
    route_task,
)
from .runtime import build_collection, resolved_cache_dir
from .server import create_plane, create_stack


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="molmcp",
        description=(
            "MolCrafts MCP: `serve` runs the composed core; "
            "`init <host>` wires the host and installs the usage skill."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser(
        "serve",
        help="Start the composed molcrafts stack (default) or one plane.",
    )
    _config_argument(serve)
    serve.add_argument(
        "plane",
        nargs="?",
        default=None,
        help=(
            "Omit to mount enabled providers onto molcrafts (FastMCP namespace). "
            "Pass molcrafts or a provider name for a single-plane debug server."
        ),
    )
    serve.add_argument(
        "--disable",
        action="append",
        default=[],
        metavar="PLANE",
        help="Omit a provider mount (written by `molmcp init --disable`).",
    )
    serve.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=None,
        help="Override the transport from molcrafts.json.",
    )
    serve.add_argument("--host", default=None, help="Override the HTTP bind host.")
    serve.add_argument("--port", type=int, default=None, help="Override the HTTP port.")
    serve.add_argument(
        "--no-discover",
        action="store_true",
        help="Do not load molmcp.providers entry points (provider planes need inject).",
    )

    planes = commands.add_parser(
        "planes",
        help="List the molcrafts core and optional provider planes.",
    )
    planes.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    route = commands.add_parser(
        "route",
        help="Suggest which plane(s) a task will use (routing hint only).",
    )
    route.add_argument("task", help="User task description.")

    init = commands.add_parser(
        "init",
        help=(
            "Install the usage skill and MCP config for one host. "
            "molcrafts cannot be disabled."
        ),
    )
    init.add_argument(
        "host",
        # The host list has one home: repeating it here would be a second
        # table to keep in step with `molmcp.host.layout.HOSTS`.
        choices=tuple(HOSTS),
        help="Host to wire (user-level skill + MCP JSON).",
    )
    init.add_argument(
        "--enable",
        action="append",
        default=[],
        metavar="PLANE",
        help="Enable a provider mount (after --disable). Repeatable.",
    )
    init.add_argument(
        "--disable",
        action="append",
        default=[],
        metavar="PLANE",
        help="Omit a provider mount. Repeatable.",
    )
    init.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="MCP JSON path (default: that host's user config).",
    )
    init.add_argument(
        "--source",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Checkout holding the daily/ and dev/ bundles to materialize "
            "(default: the packaged usage skill only; nothing is probed for)."
        ),
    )

    info = commands.add_parser("info", help="Show registry and index coverage.")
    _config_argument(info)

    search = commands.add_parser("search", help="Search the full collection.")
    _config_argument(search)
    search.add_argument("query")
    search.add_argument("--kind", action="append", default=[])
    search.add_argument("--namespace", action="append", default=[])
    search.add_argument("--source", action="append", default=[])
    search.add_argument("--limit", type=int, default=20)

    explore = commands.add_parser("explore", help="Build a bounded task context pack.")
    _config_argument(explore)
    explore.add_argument("task")
    explore.add_argument("--namespace", action="append", default=[])
    explore.add_argument("--source", action="append", default=[])
    explore.add_argument("--budget-chars", type=int, default=16_000)

    index = commands.add_parser("index", help="Index configured sources.")
    _config_argument(index)
    index.add_argument(
        "sources",
        nargs="*",
        metavar="SOURCE_NAME",
        help="Configured source names; omit to index all.",
    )
    index.add_argument("--force", action="store_true")

    config_cmd = commands.add_parser(
        "config",
        help="Read or edit molmcp settings (~/.molmcp/settings.json).",
    )
    config_actions = config_cmd.add_subparsers(dest="config_action", required=True)
    config_actions.add_parser("list", help="Show the resolved settings.")
    config_get = config_actions.add_parser("get", help="Read one key.")
    config_get.add_argument("key")
    config_set = config_actions.add_parser("set", help="Set one key.")
    _scope_arguments(config_set)
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_add = config_actions.add_parser("add", help="Append to a list key.")
    _scope_arguments(config_add)
    config_add.add_argument("key")
    config_add.add_argument("value")
    config_remove = config_actions.add_parser("remove", help="Unset a key.")
    _scope_arguments(config_remove)
    config_remove.add_argument("key")
    config_remove.add_argument("value", nargs="?", default=None)
    config_harness = config_actions.add_parser(
        "harness",
        help="Author the named harness sources this install fetches from.",
    )
    harness_actions = config_harness.add_subparsers(
        dest="harness_action", required=True
    )
    harness_set = harness_actions.add_parser(
        "set",
        help="Upsert one harness source, addressed by --name.",
    )
    _scope_arguments(harness_set)
    harness_set.add_argument(
        "--name",
        required=True,
        help="The entry's address; an unknown one is appended last.",
    )
    # Every coordinate defaults to None, never to a value: None means
    # "leave as it was" to `settings.set_harness_source`, which is what
    # lets a source be authored by more than one edit.
    harness_set.add_argument(
        "--owner",
        default=None,
        help="GitHub account or organization; omit to leave it as it was.",
    )
    harness_set.add_argument(
        "--repo",
        default=None,
        help="GitHub repository name; omit to leave it as it was.",
    )
    harness_set.add_argument(
        "--ref",
        default=None,
        help="Branch or tag; omit to leave it as it was.",
    )
    harness_remove = harness_actions.add_parser(
        "remove",
        help="Drop the harness source called --name.",
    )
    _scope_arguments(harness_remove)
    harness_remove.add_argument(
        "--name",
        required=True,
        help="The entry's address, matched exactly.",
    )

    cache = commands.add_parser(
        "cache",
        help="Inspect or reclaim the shared discovery cache.",
    )
    _config_argument(cache)
    cache.add_argument(
        "--prune",
        action="store_true",
        help="Drop extraction payloads past the retention window.",
    )
    cache.add_argument(
        "--vacuum",
        action="store_true",
        help="Return freed pages to the filesystem (implies --prune).",
    )
    cache.add_argument(
        "--gc",
        action="store_true",
        help="Drop cached snapshots for sources that are no longer configured.",
    )

    # No flags, deliberately. There is one profile, so there is nothing to
    # select; a required check with an off switch is not a required check.
    commands.add_parser(
        "gate",
        help="Check the wiring contract this repository's required check runs.",
    )

    return parser


def _scope_arguments(parser: argparse.ArgumentParser) -> None:
    """Which settings file a `config` write targets.

    Neither flag means the user file: a plane server's working directory is
    whatever the MCP client that launched it happened to be started in, so
    project scope has to be asked for.
    """
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--project",
        action="store_true",
        help="Target ./.molmcp/settings.json instead of the user file.",
    )
    scope.add_argument(
        "--local",
        action="store_true",
        help="Target ./.molmcp/settings.local.json (untracked).",
    )


def _config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to a molcrafts.json (not auto-loaded from the working directory).",
    )
    parser.add_argument(
        "--env",
        metavar="LOCATOR",
        default=None,
        help=(
            "Python environment to auto-discover MolCrafts packages from: a "
            "virtualenv root, a python executable, or a site-packages directory "
            "(default: the current environment)."
        ),
    )


def _load(args: argparse.Namespace) -> AppConfig:
    return load_config(args.config, env_locator=args.env)


def _emit(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True))


def _optional(values: list[str]) -> list[str] | None:
    return values or None


def _serve(args: argparse.Namespace) -> int:
    plane_raw = args.plane
    plane = plane_raw.strip().lower() if plane_raw else None
    if plane in GONE_PLANE_IDS:
        raise ConfigurationError(gone_plane_message(plane))
    if plane is not None:
        known = known_plane_ids()
        if plane not in known and plane not in {p.name for p in _discover_safe()}:
            raise ConfigurationError(
                f"unknown plane {plane!r}. Run `molmcp planes` for the list."
            )

    config = None
    try:
        config = _load(args)
    except (ConfigurationError, FileNotFoundError):
        config = None

    if plane is None:
        server = create_stack(
            config=config,
            disable=args.disable or (),
            discover_entry_points=not args.no_discover,
        )
    else:
        if args.disable:
            raise ConfigurationError(
                "--disable applies to composed `molmcp serve` only"
            )
        server = create_plane(
            plane,
            config=config,
            discover_entry_points=not args.no_discover,
        )
    transport = args.transport or (
        config.server.transport if config is not None else "stdio"
    )
    kwargs: dict[str, Any] = {"transport": transport}
    if transport == "stdio":
        kwargs["show_banner"] = False
        kwargs["log_level"] = "ERROR"
    else:
        host = args.host or (config.server.host if config is not None else "127.0.0.1")
        port = args.port or (config.server.port if config is not None else 8787)
        auth_env = config.server.auth_token_env if config is not None else None
        if host not in {"127.0.0.1", "::1", "localhost"} and not auth_env:
            raise ConfigurationError(
                "non-loopback streamable HTTP requires server.auth_token_env"
            )
        kwargs.update(host=host, port=port)
    server.run(**kwargs)
    return 0


def _discover_safe():
    from .provider import discover_providers

    return discover_providers()


def _planes(args: argparse.Namespace) -> int:
    planes = [p.to_dict() for p in list_plane_infos()]
    payload = {
        "ok": True,
        "core": CORE_PLANE_ID,
        "model": "molcrafts core + optional provider planes",
        "planes": planes,
        "hint": (
            "`molmcp serve` mounts enabled providers onto molcrafts. "
            "Disable a mount with:\n"
            "molmcp init grok --disable molq\n"
            "molmcp init grok --disable molq --enable molq  # re-enable"
        ),
    }
    if args.json:
        _emit(payload)
        return 0
    print("MolCrafts MCP — molcrafts core (always on) + provider planes:\n")
    for row in planes:
        flag = "core" if not row.get("disableable", True) else "optional"
        print(f"  {row['id']:12}  {row['serve_command']}  [{flag}]")
        print(f"               {row['purpose']}")
        print(f"               when: {row['when_to_connect']}")
        if row.get("tools_hint"):
            print(f"               tools: {', '.join(row['tools_hint'])}")
        print()
    print(payload["hint"])
    return 0


def _route(args: argparse.Namespace) -> int:
    _emit(route_task(args.task))
    return 0


def _init(args: argparse.Namespace) -> int:
    """Wire one host: MCP JSON, usage skill, daily bundle, adapter, dev harness.

    MCP (Model Context Protocol) is the wire protocol an AI client uses to
    call tools, so the JSON written here is that client's list of servers to
    launch. Every other destination belongs to :mod:`molmcp.host`, whose five
    write primitives are composed here in order rather than hidden behind a
    facade, so each destination has exactly one visible writer.

    ``--source`` is interpreted once, by ``resolve_bundle_source``, and it is
    that resolved value — never the raw flag — that the three bundle
    primitives receive. A checkout that is not a directory therefore fails
    here instead of degrading silently to the packaged backend.

    Args:
        args: Parsed ``init`` arguments: the host, the plane toggles
            (``--enable`` / ``--disable``, a *plane* being one product's MCP
            server), ``-o/--output``, and ``--source``.

    Returns:
        ``0`` once the MCP JSON, the usage skill, and the adapter are written,
        along with whichever daily and dev files the resolved checkout
        supplied — none of them when there is no checkout.

    Raises:
        FileNotFoundError: If ``--source`` is not a directory.
        ValueError: If the host or a plane toggle is unknown.
    """
    resolved = resolve_bundle_source(args.source)
    toggle, text = render_init(
        args.host,
        enable=args.enable,
        disable=args.disable,
    )
    path = (
        args.output.expanduser()
        if args.output is not None
        else default_write_path(args.host)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    skill_path = install_skill(args.host)
    daily = materialize_daily(args.host, resolved)
    adapter_path = write_adapter(args.host)
    stubs = materialize_dev_index(args.host, resolved)
    dev_root = activate_dev(args.host, resolved)
    print(
        f"wrote {path}  enabled={list(toggle.enabled)}  "
        f"disabled={list(toggle.disabled)}\n"
        f"wrote {skill_path}\n"
        f"wrote {adapter_path}, {len(daily)} daily skill file(s), "
        f"{len(stubs)} dev command stub(s), and dev harness "
        f"{dev_root if dev_root is not None else '(none: no checkout given)'}",
        file=sys.stderr,
    )
    return 0


def _collection(args: argparse.Namespace):
    config = _load(args)
    return config, build_collection(config)


def _info(args: argparse.Namespace) -> int:
    _, collection = _collection(args)
    _emit(collection.info())
    return 0


def _search(args: argparse.Namespace) -> int:
    _, collection = _collection(args)
    hits = collection.search(
        args.query,
        kinds=_optional(args.kind),
        namespaces=_optional(args.namespace),
        sources=_optional(args.source),
        limit=args.limit,
    )
    _emit({"query": args.query, "results": [hit.to_dict() for hit in hits]})
    return 0


def _explore(args: argparse.Namespace) -> int:
    _, collection = _collection(args)
    pack = collection.explore(
        args.task,
        namespaces=_optional(args.namespace),
        sources=_optional(args.source),
        budget_chars=args.budget_chars,
    )
    _emit(pack.to_dict())
    return 0


def _index(args: argparse.Namespace) -> int:
    _, collection = _collection(args)
    selected = args.sources or [binding.name for binding in collection.sources]
    unknown = sorted(set(selected) - {binding.name for binding in collection.sources})
    if unknown:
        raise ConfigurationError(f"unknown configured sources: {', '.join(unknown)}")
    results: list[dict[str, Any]] = []
    for binding in collection.sources:
        if binding.name not in selected:
            continue
        result = binding.engine.index(binding.spec, force=args.force)
        results.append(
            {
                "source": binding.name,
                "spec": binding.spec,
                "snapshot": result.snapshot.snapshot_id,
                "cached": result.cached,
                "files": result.file_count,
                "nodes": result.node_count,
                "edges": result.edge_count,
            }
        )
    _emit({"indexed": results})
    return 0


def _config(args: argparse.Namespace) -> int:
    """Read or edit the settings that decide what this install indexes.

    Writes go to the user file unless ``--project``/``--local`` is given:
    a plane server inherits its working directory from whichever MCP client
    launched it, so a project-scoped default would make configuration
    depend on an accident.

    The branch chain is exhaustive by construction: an action with no
    branch raises rather than falling through to ``remove_value``, which
    would delete a setting nobody asked to delete.

    Args:
        args: The parsed ``config`` namespace, carrying ``config_action``
            and whichever arguments that action's subparser declares.

    Returns:
        ``0`` once the read is printed or the write is on disk.

    Raises:
        ConfigurationError: If ``config_action`` names an action this
            handler does not dispatch.
        settings.SettingsError: If the settings layer refuses the write.
    """
    if args.config_action == "list":
        _emit(settings.load_settings(Path.cwd()).to_dict())
        return 0
    if args.config_action == "get":
        _emit(
            settings.get_value(settings.load_settings(Path.cwd()).to_dict(), args.key)
        )
        return 0
    if getattr(args, "project", False) or getattr(args, "local", False):
        target = settings.project_settings_path(Path.cwd(), local=args.local)
    else:
        target = settings.user_settings_path()
    if args.config_action == "set":
        settings.set_value(target, args.key, args.value)
    elif args.config_action == "add":
        settings.add_value(target, args.key, args.value)
    elif args.config_action == "harness":
        _config_harness(args, target)
    elif args.config_action == "remove":
        settings.remove_value(target, args.key, args.value)
    else:
        raise ConfigurationError(
            f"unrecognized `molmcp config` action: {args.config_action!r}"
        )
    print(f"wrote {target}", file=sys.stderr)
    _emit(settings.read_settings_file(target))
    return 0


def _config_harness(args: argparse.Namespace, target: Path) -> None:
    """Author one entry of the ``harness`` list, addressed by its name.

    The string verbs cannot reach this key — ``set`` refuses the bare
    member of an object list and no dotted path into an entry exists — so
    these two leaves are its only authoring route. They hold their own
    branches here rather than inside :func:`_config` so that neither chain
    has to nest.

    Nothing is checked about *completeness*: ``--name`` alone is a legal
    write that leaves ``molmcp serve`` refusing until the coordinates
    arrive. Which entries can be fetched from is ``server``'s question,
    and a second answer to it here is how the two would drift apart.

    Args:
        args: The parsed namespace, carrying ``harness_action``, ``name``
            and — on the ``set`` leaf — ``owner``/``repo``/``ref``, each
            ``None`` when it was not typed.
        target: The settings file the scope flags selected.

    Raises:
        ConfigurationError: If ``harness_action`` names a leaf this
            handler does not implement.
        settings.SettingsError: If the settings layer refuses the write.
    """
    # Read as a bare attribute, never getattr(args, "harness_action", None):
    # tests/test_cli_config.py::test_every_registered_config_action_is_dispatched
    # calls _config(Namespace(config_action="harness")) with nothing else set and
    # treats only ConfigurationError as "this action is unwired". A getattr default
    # would fall through to the terminal raise below and report harness as unwired,
    # turning a green drift guard red. The bare access raises AttributeError, which
    # that test swallows by design.
    if args.harness_action == "set":
        settings.set_harness_source(
            target,
            name=args.name,
            owner=args.owner,
            repo=args.repo,
            ref=args.ref,
        )
        return
    if args.harness_action == "remove":
        settings.remove_harness_source(target, args.name)
        return
    raise ConfigurationError(
        f"unrecognized `molmcp config harness` action: {args.harness_action!r}"
    )


def _cache_hint(
    vacuum_report: dict[str, Any] | None, size: int, used: int
) -> str | None:
    """One actionable sentence, or nothing when there is nothing to say."""
    if vacuum_report is not None and not vacuum_report["checkpointed"]:
        return (
            "a running `molmcp serve` process held the write-ahead log open, "
            "so the rebuilt database could not be folded back — stop the "
            "plane servers and re-run `molmcp cache --vacuum`"
        )
    if vacuum_report is None and size - used >= 64 * 1024 * 1024:
        return (
            "run `molmcp cache --vacuum` (with no plane server running) to "
            "return the freed pages to the filesystem"
        )
    return None


def _cache(args: argparse.Namespace) -> int:
    """Report the shared cache, and optionally reclaim it.

    Indexing prunes conservatively — once per process, never VACUUM — because
    both are too expensive to repeat inside a tool call. Reclaiming a cache
    that already grew large is therefore an explicit operator action.
    """
    from .discovery.cache import ExtractCache, SnapshotCache
    from .discovery.config import DiscoveryConfig
    from .discovery.schema import ANALYZER_VERSION

    vacuum_report: dict[str, Any] | None = None
    config = _load(args)
    discovery = DiscoveryConfig(cache_dir=resolved_cache_dir(config))
    gc_report: dict[str, Any] | None = None
    if args.gc:
        gc_report = SnapshotCache(discovery).collect_out_of_scope(
            set(config.sources.values())
        )
    cache = ExtractCache(SnapshotCache(discovery).extract_db_path(), ANALYZER_VERSION)
    try:
        if not cache.exists():
            _emit(
                {
                    "extract_cache": {
                        "path": str(cache.db_path),
                        "exists": False,
                        "entries": 0,
                        "size_bytes": 0,
                    },
                    "pruned": None,
                    "vacuumed": False,
                }
            )
            return 0

        pruned: int | None = None
        try:
            if args.prune or args.vacuum:
                cutoff = time.time() - discovery.max_cache_age_days * 86400
                pruned = cache.prune_older_than(cutoff)
                # Indexing sheds one decile per process so a tool call stays
                # cheap; an operator reclaiming wants it brought fully under.
                while True:
                    shed = cache.enforce_size_limit(discovery.max_extract_cache_bytes)
                    if not shed:
                        break
                    pruned += shed
            if args.vacuum:
                vacuum_report = cache.vacuum()
        except sqlite3.OperationalError as exc:
            raise ConfigurationError(
                f"cannot reclaim {cache.db_path}: {exc}. A running "
                "`molmcp serve` process is holding the cache — stop the plane "
                "servers (or close the MCP client) and retry."
            ) from exc
        # Live content and file size diverge after a prune: freed pages stay
        # in the file for reuse. Reporting only the file size would read as
        # "the prune did nothing".
        size = cache.size_bytes()
        used = cache.used_bytes()
        _emit(
            {
                "extract_cache": {
                    "path": str(cache.db_path),
                    "exists": True,
                    "entries": cache.entry_count(),
                    "used_bytes": used,
                    "size_bytes": size,
                    "reclaimable_bytes": max(size - used, 0),
                    "retention_days": discovery.max_cache_age_days,
                    "limit_bytes": discovery.max_extract_cache_bytes,
                },
                "pruned": pruned,
                "vacuumed": vacuum_report,
                "gc": gc_report,
                "hint": _cache_hint(vacuum_report, size, used),
            }
        )
    finally:
        cache.close()
    return 0


def _gate(args: argparse.Namespace) -> int:
    """Report whether the working directory's wiring contract still holds.

    The verdict has one owner, :func:`molmcp.gate.run_gate`. This handler
    reads ``ok`` off the report instead of re-deriving it from ``failed``:
    two derivations of one verdict are two things that can later disagree
    about the single required check. Each reported disagreement already
    names its file and its offending token, so they are printed as handed
    over rather than reworded here.

    Args:
        args: Parsed ``gate`` arguments. The subcommand carries no flags,
            so nothing is read from it; it is taken to keep every handler
            one shape.

    Returns:
        ``0`` when the report is ok, ``1`` otherwise.
    """
    report = run_gate(root=Path.cwd())
    for message in report.failed:
        print(f"molmcp: {message}", file=sys.stderr)
    if report.ok:
        print("wiring contract holds")
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        arguments = ["planes"]
    parser = _build_parser()
    args = parser.parse_args(arguments)
    handlers = {
        "serve": _serve,
        "planes": _planes,
        "route": _route,
        "init": _init,
        "info": _info,
        "search": _search,
        "explore": _explore,
        "index": _index,
        "config": _config,
        "cache": _cache,
        "gate": _gate,
    }
    try:
        return handlers[args.command](args)
    except (
        ConfigurationError,
        FileNotFoundError,
        ValueError,
        settings.SettingsError,
        # A busy or corrupt cache is an operating condition, not a crash:
        # the CLI owes the user a sentence, not a traceback.
        sqlite3.Error,
        OSError,
    ) as exc:
        print(f"molmcp: {exc}", file=sys.stderr)
        return 2


__all__ = ["main"]
