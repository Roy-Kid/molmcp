"""Symbol and package-path resolution for introspection tools."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any


def resolve_package_paths(import_root: str) -> list[Path]:
    """Return the on-disk source locations for an importable package.

    Uses ``importlib.util.find_spec`` so it works for namespace packages
    (which expose ``submodule_search_locations``) and editable installs.
    """
    spec = importlib.util.find_spec(import_root)
    if spec is None:
        return []
    locations = list(spec.submodule_search_locations or [])
    if not locations and spec.origin and spec.origin != "built-in":
        locations = [str(Path(spec.origin).parent)]
    return [Path(p) for p in locations]


def resolve_symbol(dotted: str) -> Any | None:
    """Import ``dotted`` (module, module.attr, or module.Class.method)."""
    try:
        return importlib.import_module(dotted)
    except ImportError:
        pass
    parts = dotted.split(".")
    for i in range(len(parts) - 1, 0, -1):
        mod_path = ".".join(parts[:i])
        try:
            obj = importlib.import_module(mod_path)
        except ImportError:
            continue
        for attr in parts[i:]:
            obj = getattr(obj, attr, None)
            if obj is None:
                return None
        return obj
    return None
