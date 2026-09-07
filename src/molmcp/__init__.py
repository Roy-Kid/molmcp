"""MolMCP — molcrafts core with FastMCP-mounted provider planes."""

from __future__ import annotations

import importlib
import importlib.metadata

__version__ = importlib.metadata.version("molcrafts-molmcp")

#: Public name -> the submodule that defines it. Resolution is deferred so that
#: ``import molmcp`` does not drag ``.server`` — and through it FastMCP, the
#: library that hosts an MCP server — into a process that only wants a leaf
#: such as ``molmcp.provider_worker.protocol``. Membership here mirrors
#: ``__all__`` minus ``__version__``, which is metadata rather than a module.
_LAZY_EXPORTS: dict[str, str] = {
    "PlaneToggle": "client_config",
    "resolve_plane_toggles": "client_config",
    "CollectionIndex": "collection",
    "ContextPack": "collection",
    "SearchHit": "collection",
    "SourceBinding": "collection",
    "AppConfig": "config",
    "ConfigurationError": "config",
    "load_config": "config",
    "MolCraftsContextProvider": "mcp_provider",
    "CORE_PLANE_ID": "planes",
    "PlaneInfo": "planes",
    "known_plane_ids": "planes",
    "list_plane_infos": "planes",
    "route_task": "planes",
    "PROVIDER_ENTRY_POINT_GROUP": "provider",
    "Provider": "provider",
    "discover_providers": "provider",
    "provider_available": "provider",
    "create_plane": "server",
    "create_server": "server",
    "create_stack": "server",
}

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


def __getattr__(name: str) -> object:
    """Resolve a public name by importing its submodule on first use.

    Args:
        name: Attribute requested on the ``molmcp`` package.

    Returns:
        The resolved object, cached into the module globals so the import
        happens at most once.

    Raises:
        AttributeError: If ``name`` is not one of the lazy public exports.
            Raising here is load-bearing: CPython's ``_handle_fromlist`` only
            falls back to importing a submodule after the package refuses the
            attribute, which is what keeps ``from molmcp import cli`` (and
            ``settings`` / ``provider`` / ``runtime`` / ``client_config``)
            working.
    """
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List the public surface plus whatever has already been resolved.

    Returns:
        Sorted attribute names, including every entry of ``__all__`` whether
        or not its submodule has been imported yet.
    """
    return sorted(set(globals()) | set(__all__))
