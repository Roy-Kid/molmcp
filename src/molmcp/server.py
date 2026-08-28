"""Build MCP servers — one focused FastMCP per plane, composed via mount."""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Iterable
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier
from mcp.types import ToolAnnotations

from .collection import CollectionIndex
from .config import AppConfig, load_config
from .mcp_provider import MolCraftsContextProvider
from .middleware import (
    MissingAnnotationsError,
    PathSafetyMiddleware,
    ResponseLimitMiddleware,
    assert_plane_tool_names,
    validate_tool_annotations,
)
from .planes import (
    BUILTIN_PLANE_IDS,
    CORE_PLANE_ID,
    GONE_PLANE_IDS,
    core_disable_message,
    gone_plane_message,
    list_plane_infos,
    route_task,
)
from .provider import (
    PROVIDER_NAME_PATTERN,
    Provider,
    discover_providers,
)
from .runtime import build_collection

logger = logging.getLogger(__name__)

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def create_plane(
    plane: str,
    *,
    collection: CollectionIndex | None = None,
    config: AppConfig | str | Path | None = None,
    provider: Provider | None = None,
    providers: Iterable[Provider] | None = None,
    discover_entry_points: bool = True,
    enable_path_safety: bool = True,
    enable_response_limit: bool = True,
    response_limit_bytes: int = 256 * 1024,
    validate_annotations: bool = True,
    instructions: str | None = None,
) -> FastMCP:
    """Build a **single-plane** FastMCP server.

    Args:
        plane: Plane id (``molcrafts`` core, or a provider name such
            as ``molvis``). This becomes the MCP server name clients see.
        collection: Injected discovery collection (tests / embedding).
            Required only for ``molcrafts`` when *config* is not used.
        config: ``molcrafts.json`` or :class:`AppConfig`. Used by ``molcrafts``
            for indexing; ignored by pure provider planes unless *collection*
            is built from it.
        provider: Explicit provider instance for provider planes (tests).
        providers: Deprecated alias for a single-item explicit provider list;
            if more than one is passed, raises — use :func:`create_stack`.
        discover_entry_points: Load the matching ``molmcp.providers`` entry
            point when *provider* is not injected.
        enable_path_safety / enable_response_limit / response_limit_bytes:
            Middleware toggles.
        validate_annotations: Fail startup if tools lack ToolAnnotations.
        instructions: Override default plane instructions string.

    Raises:
        ValueError: Unknown plane, multi-provider request, or name contract
            violation.
    """
    plane_id = plane.strip().lower()
    if not plane_id:
        raise ValueError("plane id must be non-empty")
    if plane_id in GONE_PLANE_IDS:
        raise ValueError(gone_plane_message(plane_id))

    if providers is not None:
        explicit_list = list(providers)
        if len(explicit_list) > 1:
            raise ValueError(
                "create_plane serves one plane; compose providers with create_stack"
            )
        if provider is not None and explicit_list:
            raise ValueError("pass provider= or providers=[one], not both")
        if explicit_list:
            provider = explicit_list[0]

    if provider is not None and plane_id in BUILTIN_PLANE_IDS:
        # Silently dropping it would hand back a server that looks configured
        # and is not — the caller's tools would simply never appear.
        raise ValueError(
            f"the {plane_id!r} plane is built in and takes no provider; "
            f"serve {getattr(provider, 'name', 'it')!r} as its own plane"
        )

    if plane_id == CORE_PLANE_ID:
        app_config, coll = _resolve_collection(collection, config)
        auth = _environment_auth(app_config) if app_config is not None else None

        @asynccontextmanager
        async def lifespan(_server):
            coll.start()
            try:
                yield {}
            finally:
                coll.close()

        runtime_status: dict[str, object] = {
            "plane": plane_id,
            "transport": (
                app_config.server.transport if app_config is not None else "injected"
            ),
        }
        mcp = _base_server(
            plane_id,
            instructions=instructions or _molcrafts_instructions(),
            auth=auth,
            lifespan=lifespan,
            enable_path_safety=enable_path_safety,
            enable_response_limit=enable_response_limit,
            response_limit_bytes=response_limit_bytes,
        )
        MolCraftsContextProvider(coll, runtime_status).register(mcp)
        _register_core_routing(mcp)
        _validate(mcp, validate_annotations, plane_id=plane_id)
        return mcp

    # Provider plane — one product, bare tool names, server name = plane id.
    resolved = _resolve_provider(
        plane_id,
        provider=provider,
        discover_entry_points=discover_entry_points,
    )
    app_config = None
    if config is not None:
        app_config = _resolve_config(config)
    auth = _environment_auth(app_config) if app_config is not None else None

    mcp = _base_server(
        plane_id,
        instructions=instructions or _provider_instructions(plane_id),
        auth=auth,
        lifespan=None,
        enable_path_safety=enable_path_safety,
        enable_response_limit=enable_response_limit,
        response_limit_bytes=response_limit_bytes,
    )
    try:
        resolved.register(mcp)
    except Exception:
        logger.exception("Provider plane %r failed to register", plane_id)
        raise
    _validate(mcp, validate_annotations, plane_id=plane_id)
    return mcp


def create_stack(
    *,
    collection: CollectionIndex | None = None,
    config: AppConfig | str | Path | None = None,
    providers: Iterable[Provider] | None = None,
    disable: Iterable[str] = (),
    discover_entry_points: bool = True,
    enable_path_safety: bool = True,
    enable_response_limit: bool = True,
    response_limit_bytes: int = 256 * 1024,
    validate_annotations: bool = True,
    instructions: str | None = None,
) -> FastMCP:
    """Build the molcrafts core and mount enabled providers (FastMCP composition).

    Provider tools are namespaced with the plane id (``molvis_open``). Core
    tools stay bare (``packages``, ``open``, ``route``). ``molcrafts`` cannot
    be disabled.
    """
    skipped = {str(name).strip().lower() for name in disable if str(name).strip()}
    if CORE_PLANE_ID in skipped:
        raise ValueError(core_disable_message())
    for name in skipped:
        if name in GONE_PLANE_IDS:
            raise ValueError(gone_plane_message(name))

    parent = create_plane(
        CORE_PLANE_ID,
        collection=collection,
        config=config,
        discover_entry_points=False,
        enable_path_safety=enable_path_safety,
        enable_response_limit=enable_response_limit,
        response_limit_bytes=response_limit_bytes,
        validate_annotations=validate_annotations,
        instructions=instructions or _stack_instructions(),
    )
    if providers is None:
        if not discover_entry_points:
            mounted: list[Provider] = []
        else:
            mounted = [
                p
                for p in discover_providers(only_available=True)
                if p.name not in skipped
            ]
    else:
        mounted = [p for p in providers if p.name not in skipped]

    for provider in mounted:
        child = create_plane(
            provider.name,
            provider=provider,
            config=config,
            discover_entry_points=False,
            enable_path_safety=enable_path_safety,
            enable_response_limit=enable_response_limit,
            response_limit_bytes=response_limit_bytes,
            validate_annotations=validate_annotations,
        )
        parent.mount(child, namespace=provider.name)
    return parent


def create_server(
    name: str | None = None,
    *,
    plane: str | None = None,
    **kwargs,
) -> FastMCP:
    """Compatibility wrapper: prefer :func:`create_plane`.

    ``name`` is accepted only as an alias for ``plane`` (historical tests).
    Multi-provider mounting is not supported.
    """
    plane_id = plane or name
    if plane_id is None:
        raise ValueError("create_plane requires plane=")
    return create_plane(plane_id, **kwargs)


def _base_server(
    name: str,
    *,
    instructions: str,
    auth: TokenVerifier | None,
    lifespan,
    enable_path_safety: bool,
    enable_response_limit: bool,
    response_limit_bytes: int,
) -> FastMCP:
    mcp_kwargs: dict = {"instructions": instructions, "auth": auth}
    if lifespan is not None:
        mcp_kwargs["lifespan"] = lifespan
    mcp = FastMCP(name, **mcp_kwargs)
    if enable_path_safety:
        mcp.add_middleware(PathSafetyMiddleware())
    if enable_response_limit:
        mcp.add_middleware(ResponseLimitMiddleware(max_bytes=response_limit_bytes))
    return mcp


def _register_core_routing(mcp: FastMCP) -> None:
    @mcp.tool(annotations=_READ_ONLY)
    def list_planes() -> dict[str, object]:
        """List the core connection and optional provider planes.

        Each row has ``id``, ``serve_command``, ``when_to_connect``,
        ``tools_hint``, and ``disableable``. molcrafts is always on;
        only provider planes can be dropped from a client config.
        """
        planes = [p.to_dict() for p in list_plane_infos()]
        return {
            "ok": True,
            "planes": planes,
            "core": CORE_PLANE_ID,
            "model": "molcrafts core + optional provider planes",
            "hint": (
                "Default `molmcp serve` mounts enabled providers onto this "
                "core (FastMCP namespace: molvis_open). "
                "Drop a mount with `molmcp init <host> --disable <plane>`."
            ),
        }

    @mcp.tool(annotations=_READ_ONLY)
    def route(task: str) -> dict[str, object]:
        """Which optional provider plane(s) to connect for *task*.

        Routing only — no science. molcrafts is already this connection.
        Do not invent domain MCP tools for chemistry APIs.
        """
        return route_task(task)


def _resolve_collection(
    collection: CollectionIndex | None,
    config: AppConfig | str | Path | None,
) -> tuple[AppConfig | None, CollectionIndex]:
    if collection is not None:
        app_config = _resolve_config(config) if config is not None else None
        return app_config, collection
    app_config = _resolve_config(config)
    return app_config, build_collection(app_config)


def _resolve_provider(
    plane_id: str,
    *,
    provider: Provider | None,
    discover_entry_points: bool,
) -> Provider:
    if provider is not None:
        if provider.name != plane_id:
            raise ValueError(
                f"provider.name {provider.name!r} does not match plane {plane_id!r}"
            )
        if not PROVIDER_NAME_PATTERN.fullmatch(provider.name):
            raise ValueError(
                f"provider name must match ^[a-z][a-z0-9-]*$: {provider.name!r}"
            )
        return provider
    if not discover_entry_points:
        raise ValueError(
            f"provider plane {plane_id!r} requires provider= when "
            "discover_entry_points=False"
        )
    failures: list[dict[str, str]] = []
    found = {p.name: p for p in discover_providers(failures=failures)}
    if plane_id not in found:
        available = ", ".join(sorted(found)) or "(none)"
        detail = ""
        if failures:
            detail = "; load failures: " + ", ".join(
                f"{f.get('entry_point')}:{f.get('error_type')}" for f in failures
            )
        raise ValueError(
            f"unknown provider plane {plane_id!r}; available: {available}{detail}"
        )
    return found[plane_id]


def _resolve_config(config: AppConfig | str | Path | None) -> AppConfig:
    if isinstance(config, AppConfig):
        return config
    return load_config(config)


def _validate(
    mcp: FastMCP,
    validate_annotations: bool,
    *,
    plane_id: str,
) -> None:
    # Always enforce bare tool names (no molexp_molexp_* / plane_tool prefix).
    assert_plane_tool_names(mcp, plane_id)
    if not validate_annotations:
        return
    warnings = validate_tool_annotations(mcp, strict=False)
    if warnings:
        raise MissingAnnotationsError(
            "Tool annotation validation failed:\n  - " + "\n  - ".join(warnings)
        )


def _molcrafts_instructions() -> str:
    return (
        "MolCrafts knowledge core. Discover real symbols before coding.\n"
        "1) list_planes — which provider mounts exist\n"
        "2) route(task) — which provider namespace a task needs\n"
        "3) packages — package directory; choose sources\n"
        "4) outline(source, path?) — module tree\n"
        "5) open(ref) — symbol page before coding\n"
        "6) compose(task|refs) — budgeted multi-page pack\n"
        "search/suggest are index helpers. "
        "ok=false / SYMBOL_NOT_FOUND → capability gap: report the step, "
        "the package/ref, and the result; do not invent the API.\n"
        "knowledgeScope scopes packages/outline/open/search/compose. "
        "Science APIs are never tools; invoke them in agent Python "
        "or via the namespaced molvis tools."
    )


def _stack_instructions() -> str:
    return (
        _molcrafts_instructions()
        + "\nDefault serve mounts providers with FastMCP namespaces "
        "(molvis_open, molq_list_jobs, molexp_list_projects). "
        "`molmcp init <host> --disable <plane>` omits a mount. "
        "If these tools are missing, tell the user to install or start molmcp."
    )


def _provider_instructions(plane_id: str) -> str:
    return (
        f"MolCrafts '{plane_id}' plane — one product, one MCP connection.\n"
        "Tools are session/runtime primitives only. "
        "Do not expect science methods as MCP tools; discover them on the "
        "molcrafts plane and invoke via agent Python or molvis exec.\n"
        f"Server name is '{plane_id}' so client tool ids look like "
        f"'{plane_id}__<tool>'. On the composed core they appear as "
        f"'{plane_id}_<tool>' (FastMCP namespace)."
    )


class _EnvironmentTokenVerifier(TokenVerifier):
    """Single-secret bearer verification without logging or copying the secret."""

    def __init__(self, environment_name: str) -> None:
        super().__init__(required_scopes=["molmcp"])
        self.environment_name = environment_name

    async def verify_token(self, token: str) -> AccessToken | None:
        expected = os.environ.get(self.environment_name)
        if expected is None or not hmac.compare_digest(token, expected):
            return None
        return AccessToken(
            token=token,
            client_id="molmcp-static-client",
            scopes=["molmcp"],
            claims={"source": "environment"},
        )


def _environment_auth(config: AppConfig) -> TokenVerifier | None:
    environment_name = config.server.auth_token_env
    if environment_name is None:
        return None
    if not os.environ.get(environment_name):
        raise ValueError(
            f"configured bearer token environment variable is unset: {environment_name}"
        )
    return _EnvironmentTokenVerifier(environment_name)


__all__ = ["create_plane", "create_server", "create_stack"]
