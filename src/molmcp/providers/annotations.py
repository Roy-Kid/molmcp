"""Re-export of the six annotation constants from the public SDK.

The implementation lives in :mod:`molmcp.provider_sdk`; this module exists
so existing ``molmcp.providers.annotations`` imports keep working and
resolve to :data:`~molmcp.provider_sdk.READ_ONLY`,
:data:`~molmcp.provider_sdk.READ_REMOTE`,
:data:`~molmcp.provider_sdk.MUTATION`,
:data:`~molmcp.provider_sdk.LOCAL_MUTATION`,
:data:`~molmcp.provider_sdk.APPEND_WRITE`, and
:data:`~molmcp.provider_sdk.IDEMPOTENT_WRITE`.
"""

from molmcp.provider_sdk import (
    APPEND_WRITE,
    IDEMPOTENT_WRITE,
    LOCAL_MUTATION,
    MUTATION,
    READ_ONLY,
    READ_REMOTE,
)

__all__ = [
    "APPEND_WRITE",
    "IDEMPOTENT_WRITE",
    "LOCAL_MUTATION",
    "MUTATION",
    "READ_ONLY",
    "READ_REMOTE",
]
