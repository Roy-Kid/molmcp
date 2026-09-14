"""Re-export of ProviderBase, ToolSpec, and tool from the public SDK.

The implementation lives in :mod:`molmcp.provider_sdk`; this module exists
so existing ``molmcp.providers.base`` imports keep working and resolve to
:class:`~molmcp.provider_sdk.ProviderBase`,
:class:`~molmcp.provider_sdk.ToolSpec`, and
:func:`~molmcp.provider_sdk.tool`.
"""

from molmcp.provider_sdk import ProviderBase, ToolSpec, tool

__all__ = ["ProviderBase", "ToolSpec", "tool"]
