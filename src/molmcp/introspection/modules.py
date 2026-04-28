"""Module/symbol enumeration tools."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from ._resolve import resolve_symbol


def list_modules_under(import_roots: list[str], prefix: str | None = None) -> list[str]:
    """List importable module names under any of ``import_roots``.

    Args:
        import_roots: Top-level packages to walk.
        prefix: Optional substring filter — module name must start with this.

    Returns:
        Sorted unique list of fully-qualified module names.
    """
    collected: set[str] = set()
    for root in import_roots:
        try:
            mod = importlib.import_module(root)
        except ImportError:
            continue
        collected.add(root)
        if not hasattr(mod, "__path__"):
            continue
        for info in pkgutil.walk_packages(mod.__path__, prefix=f"{root}."):
            collected.add(info.name)
    if prefix:
        return sorted(m for m in collected if m.startswith(prefix))
    return sorted(collected)


def list_symbols_in(module: str) -> dict[str, str]:
    """Return ``{symbol_name: one_line_summary}`` for a module's public API."""
    mod = resolve_symbol(module)
    if mod is None or not inspect.ismodule(mod):
        return {"error": f"Module not found: {module}"}

    names = getattr(mod, "__all__", None)
    if names is None:
        names = [n for n in dir(mod) if not n.startswith("_")]

    result: dict[str, str] = {}
    for name in sorted(names):
        obj = getattr(mod, name, None)
        if obj is None:
            continue
        doc = inspect.getdoc(obj)
        result[name] = doc.split("\n", 1)[0] if doc else type(obj).__name__
    return result
