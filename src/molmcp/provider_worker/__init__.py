"""Subprocess-hosted provider plane — lazy façade.

``WorkerProvider`` lives in the sibling ``worker`` module, which is free to
import FastMCP and the supervisor/proxy machinery. This package body must not —
the worker child process imports ``molmcp.provider_worker.protocol``, and every
statement executed here is a statement the child pays for. Resolving the one
public name through PEP 562 keeps the child's ``sys.modules`` free of FastMCP,
so ``child.py``'s isolation assertion measures a real leak rather than the
import that always happens.
"""

from __future__ import annotations

__all__ = ["WorkerProvider"]


def __getattr__(name: str) -> object:
    """Resolve ``WorkerProvider`` from the sibling ``worker`` module.

    Args:
        name: Attribute requested on the ``molmcp.provider_worker`` package.

    Returns:
        The ``WorkerProvider`` class, cached into the module globals so the
        import happens at most once.

    Raises:
        AttributeError: For any other name, which is also what lets CPython
            fall back to importing a submodule such as ``protocol``.
    """
    if name != "WorkerProvider":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from .worker import WorkerProvider

    globals()[name] = WorkerProvider
    return WorkerProvider


def __dir__() -> list[str]:
    """List the public surface plus whatever has already been resolved.

    Returns:
        Sorted attribute names, including ``WorkerProvider`` whether or not
        the ``worker`` module has been imported yet.
    """
    return sorted(set(globals()) | set(__all__))
