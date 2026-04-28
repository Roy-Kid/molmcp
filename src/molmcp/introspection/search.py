"""Substring search across package source files with mtime-based caching."""

from __future__ import annotations

from pathlib import Path

from ._resolve import resolve_package_paths

_FILE_CACHE: dict[Path, tuple[float, list[str]]] = {}


def _read_lines_cached(path: Path) -> list[str]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _FILE_CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    _FILE_CACHE[path] = (mtime, lines)
    return lines


def search_in_sources(
    import_roots: list[str],
    query: str,
    module_prefix: str | None = None,
    max_results: int = 50,
) -> list[dict[str, str]]:
    """Case-insensitive substring search across .py files under import roots.

    Args:
        import_roots: Top-level packages to search.
        query: Text to find (case-insensitive).
        module_prefix: Optional dotted-path filter (e.g. ``"molpy.core"``).
        max_results: Maximum number of hits to return.

    Returns:
        List of ``{"file", "line", "text"}`` dicts; first ``max_results``.
        Returns ``[{"error": ...}]`` if no roots have on-disk locations.
    """
    q = query.lower()
    results: list[dict[str, str]] = []

    found_any_root = False
    for root in import_roots:
        for pkg_dir in resolve_package_paths(root):
            found_any_root = True
            base = pkg_dir.parent
            for py_file in sorted(pkg_dir.rglob("*.py")):
                rel = py_file.relative_to(base)
                mod_guess = str(rel).replace("/", ".").removesuffix(".py")
                if module_prefix and not mod_guess.startswith(module_prefix):
                    continue
                lines = _read_lines_cached(py_file)
                for i, line in enumerate(lines, 1):
                    if q in line.lower():
                        results.append(
                            {
                                "file": str(rel),
                                "line": str(i),
                                "text": line.strip(),
                            }
                        )
                        if len(results) >= max_results:
                            return results
    if not found_any_root:
        return [{"error": "No source directories found for given import roots"}]
    return results
