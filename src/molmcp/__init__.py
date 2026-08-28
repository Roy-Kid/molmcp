"""MolMCP — molcrafts core with FastMCP-mounted provider planes."""

from __future__ import annotations

import importlib.metadata

from .client_config import PlaneToggle, resolve_plane_toggles
from .collection import CollectionIndex, ContextPack, SearchHit, SourceBinding
from .config import AppConfig, ConfigurationError, load_config
from .mcp_provider import MolCraftsContextProvider
from .planes import (
    CORE_PLANE_ID,
    PlaneInfo,
    known_plane_ids,
    list_plane_infos,
    route_task,
)
from .provider import (
    PROVIDER_ENTRY_POINT_GROUP,
    Provider,
    discover_providers,
    provider_available,
)
from .server import create_plane, create_server, create_stack

__version__ = importlib.metadata.version("molcrafts-molmcp")

__all__ = [
    "AppConfig",
    "CORE_PLANE_ID",
    "CollectionIndex",
    "ConfigurationError",
    "ContextPack",
    "MolCraftsContextProvider",
    "PROVIDER_ENTRY_POINT_GROUP",
    "PlaneInfo",
    "PlaneToggle",
    "Provider",
    "SearchHit",
    "SourceBinding",
    "__version__",
    "create_plane",
    "create_server",
    "create_stack",
    "discover_providers",
    "known_plane_ids",
    "list_plane_infos",
    "load_config",
    "provider_available",
    "resolve_plane_toggles",
    "route_task",
]
