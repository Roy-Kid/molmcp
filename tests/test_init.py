"""``molmcp`` re-exports its public names lazily; the façades only forward.

Importing ``molmcp`` today pulls in ``.server`` and ``.provider``, and both of
those reach ``from fastmcp import FastMCP`` at module scope. A provider running
in a worker subprocess needs ``molmcp.provider_worker.protocol`` and nothing
else: the moment the package body imports FastMCP for it, the child pays for a
server it never builds and the isolation assertion in ``child.py`` can no longer
tell a leak from the import that always happened.

So both package bodies resolve names through PEP 562 ``__getattr__``. The
public surface (``__all__``) is unchanged — this is a resolution change, not an
API change — and ``__version__`` stays eager because it is metadata, not a
module.
"""

from __future__ import annotations

import ast
import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

import pytest

import molmcp

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
PACKAGE_INIT = SRC / "molmcp" / "__init__.py"
WORKER_INIT = SRC / "molmcp" / "provider_worker" / "__init__.py"

#: Relative submodules that must never be imported by the package body: each
#: one drags FastMCP (directly or transitively) into every ``import molmcp``.
_EAGER_SUBMODULES = frozenset({"mcp_provider", "planes", "provider", "server"})

#: The public surface as it stands before the lazy rewrite. Hard-coded so that
#: "resolve it later" can never quietly become "drop it".
_PUBLIC_NAMES = frozenset(
    {
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
    }
)

#: Imports ``protocol`` the way ``child.py`` will, then reports any FastMCP
#: module that came along for the ride.
_ISOLATION_PROBE = (
    "import molmcp.provider_worker.protocol, sys; "
    'print([m for m in sys.modules if m == "fastmcp" or m.startswith("fastmcp.")])'
)


def _parse(path: Path) -> ast.Module:
    """Parse ``path``, failing with its name rather than an OSError.

    Args:
        path: Source file to parse.

    Returns:
        The parsed module.
    """
    assert path.is_file(), f"{path} does not exist"
    return ast.parse(path.read_text(encoding="utf-8"))


def _module_body(path: Path) -> list[ast.stmt]:
    """Return the top-level statements of ``path`` — what runs on import."""
    return _parse(path).body


def _describe(node: ast.stmt) -> str:
    """Name a top-level statement in the vocabulary the façade is allowed."""
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        if isinstance(node.value.value, str):
            return "docstring"
    if isinstance(node, ast.ImportFrom) and node.module == "__future__":
        return "future-import"
    if isinstance(node, ast.Assign):
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if targets == ["__all__"]:
            return "__all__"
    if isinstance(node, ast.FunctionDef):
        return f"def {node.name}"
    return f"<{type(node).__name__}>"


def _imports_fastmcp(tree: ast.AST) -> bool:
    """Report whether any import anywhere in ``tree`` names ``fastmcp``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "fastmcp" for a in node.names):
                return True
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.split(".")[0] == "fastmcp":
                return True
    return False


class TestLazyExports:
    """``molmcp/__init__.py`` and ``provider_worker/__init__.py`` as façades."""

    def test_package_body_imports_no_server_or_provider_submodule(self) -> None:
        offenders = sorted(
            str(node.module)
            for node in _module_body(PACKAGE_INIT)
            if isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module in _EAGER_SUBMODULES
        )

        assert offenders == [], (
            f"molmcp/__init__.py eagerly imports {offenders}; every one of them "
            f"reaches FastMCP, so a worker child that only wants "
            f"provider_worker.protocol pays for the whole server. Resolve them "
            f"in __getattr__ instead."
        )

    def test_public_names_are_unchanged(self) -> None:
        assert {
            "create_plane",
            "create_stack",
            "Provider",
            "discover_providers",
        } <= set(molmcp.__all__)
        assert set(molmcp.__all__) == _PUBLIC_NAMES, (
            "moving to lazy resolution must not add or drop a public name"
        )

    def test_lazily_resolved_names_are_still_callable(self) -> None:
        from molmcp import Provider, create_plane, create_stack, discover_providers

        resolved: list[tuple[str, object]] = [
            ("create_plane", create_plane),
            ("create_stack", create_stack),
            ("Provider", Provider),
            ("discover_providers", discover_providers),
        ]

        assert [name for name, obj in resolved if not callable(obj)] == []

    def test_unknown_attribute_raises_and_dir_lists_the_public_names(self) -> None:
        module_getattr = getattr(molmcp, "__getattr__", None)

        assert callable(module_getattr), (
            "molmcp must define a PEP 562 module __getattr__ to resolve its "
            "public names on first use"
        )
        with pytest.raises(AttributeError):
            module_getattr("no_such_name")
        assert set(dir(molmcp)) >= set(molmcp.__all__)

    def test_worker_facade_body_is_only_a_lazy_reexport(self) -> None:
        described = [_describe(node) for node in _module_body(WORKER_INIT)]

        assert described[:3] == ["docstring", "future-import", "__all__"], described
        assert sorted(described[3:]) == ["def __dir__", "def __getattr__"], described

    def test_worker_facade_defines_no_worker_and_imports_no_sibling(self) -> None:
        tree = _parse(WORKER_INIT)

        classes = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "WorkerProvider"
        ]
        # ``.worker`` is banned in the module body only: __getattr__ is exactly
        # where ``from .worker import WorkerProvider`` is supposed to happen.
        body_imports = sorted(
            str(node.module)
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module in {"proxy", "supervisor", "worker"}
        )
        sibling_imports = sorted(
            str(node.module)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module in {"proxy", "supervisor"}
        )

        assert classes == [], "WorkerProvider lives in worker.py, not the façade"
        assert body_imports == [], body_imports
        assert sibling_imports == [], sibling_imports
        assert not _imports_fastmcp(tree), (
            "the façade must stay importable from a child process that has no "
            "FastMCP loaded"
        )

    def test_importing_the_protocol_module_loads_no_fastmcp(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", _ISOLATION_PROBE],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={**os.environ, "PYTHONPATH": str(SRC)},
            check=False,
        )

        assert result.returncode == 0, result.stderr
        printed = result.stdout.splitlines()
        assert printed and printed[-1] == "[]", (
            f"importing molmcp.provider_worker.protocol loaded FastMCP: "
            f"{result.stdout!r}"
        )

    def test_version_still_comes_from_the_distribution_metadata(self) -> None:
        assert molmcp.__version__ == importlib.metadata.version("molcrafts-molmcp")
