"""``molpack`` MCP provider — read-only packing-script inspector.

The suite owns molpack's MCP integration. ``molpack`` itself ships **no**
MCP code; installing ``molcrafts-mcp-suite`` is the supported path.

Tools (all read-only):

* ``list_restraints`` — enumerate the restraint types ``molpack``
  exposes, with constructor signatures.
* ``list_formats`` — enumerate the structure-file formats molpack can
  read and write, with file-extension hints.
* ``inspect_script`` — parse a Packmol-compatible ``.inp`` script via
  :func:`molpack.load_script` and return a summary (targets, per-target
  atom counts, output path, ``nloop``).

The provider never invokes :meth:`molpack.Molpack.pack` — packing is
compute-heavy and mutates files. Use the ``molpack`` CLI or Python API
directly when you actually want to run a pack.

molpack's source code is *also* exposed by molmcp's source-introspection
tools (``read_file``, ``get_source``, ``list_modules``, …) when
``molpack`` is on the suite's ``import_roots``. The two surfaces are
complementary: introspection answers "how does this work?", these
tools answer "what's in this script?".

Heavy ``molpack`` imports stay inside :meth:`register` and tool bodies
so the provider remains cheap to instantiate — for example when the
CLI builds a server just to print the resolved configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


# Catalog of restraints we surface. Each entry maps the restraint class
# name (as exported by ``molpack``) to the constructor parameter list
# (a list of ``{name, type, default}`` dicts) and a one-line summary
# of the geometric region it constrains.
#
# Keep the parameter list aligned with ``molpack.molpack.pyi``; the
# catalog is the single source of truth the LLM consults before
# building a Target.
_RESTRAINTS: dict[str, dict[str, object]] = {
    "InsideBoxRestraint": {
        "summary": (
            "Axis-aligned box. Atoms must stay inside "
            "[min, max]. Optional per-axis periodic flags."
        ),
        "parameters": [
            {"name": "min", "type": "Sequence[float] (length 3)"},
            {"name": "max", "type": "Sequence[float] (length 3)"},
            {
                "name": "periodic",
                "type": "tuple[bool, bool, bool]",
                "default": "(False, False, False)",
            },
        ],
        "inp_keyword": "inside box x0 y0 z0 x1 y1 z1",
    },
    "InsideSphereRestraint": {
        "summary": "Atoms must stay inside a sphere of given centre and radius.",
        "parameters": [
            {"name": "center", "type": "Sequence[float] (length 3)"},
            {"name": "radius", "type": "float"},
        ],
        "inp_keyword": "inside sphere cx cy cz r",
    },
    "OutsideSphereRestraint": {
        "summary": "Atoms must stay outside a sphere of given centre and radius.",
        "parameters": [
            {"name": "center", "type": "Sequence[float] (length 3)"},
            {"name": "radius", "type": "float"},
        ],
        "inp_keyword": "outside sphere cx cy cz r",
    },
    "AbovePlaneRestraint": {
        "summary": (
            "Half-space above the plane n·x = d "
            "(``over plane`` in Packmol scripts)."
        ),
        "parameters": [
            {"name": "normal", "type": "Sequence[float] (length 3)"},
            {"name": "distance", "type": "float"},
        ],
        "inp_keyword": "over plane nx ny nz d",
    },
    "BelowPlaneRestraint": {
        "summary": "Half-space below the plane n·x = d.",
        "parameters": [
            {"name": "normal", "type": "Sequence[float] (length 3)"},
            {"name": "distance", "type": "float"},
        ],
        "inp_keyword": "below plane nx ny nz d",
    },
}


# Catalog of structure formats molpack speaks. ``read``/``write`` mirror
# the README support matrix; ``filetype`` is the string a ``.inp``
# script passes to the ``filetype`` keyword (or that
# :func:`molpack.load_script` infers from the file extension).
_FORMATS: dict[str, dict[str, object]] = {
    "pdb": {
        "extensions": (".pdb",),
        "filetype": "pdb",
        "read": True,
        "write": True,
        "summary": "Protein Data Bank format.",
    },
    "xyz": {
        "extensions": (".xyz",),
        "filetype": "xyz",
        "read": True,
        "write": True,
        "summary": "Plain XYZ coordinate file.",
    },
    "sdf": {
        "extensions": (".sdf", ".mol"),
        "filetype": "sdf",
        "read": True,
        "write": False,
        "summary": "SDF / MOL connection-table format. Read-only.",
    },
    "lammps_dump": {
        "extensions": (".lammpstrj",),
        "filetype": "lammps_dump",
        "read": True,
        "write": True,
        "summary": "LAMMPS dump trajectory (single frame consumed).",
    },
    "lammps_data": {
        "extensions": (".data",),
        "filetype": "lammps_data",
        "read": True,
        "write": False,
        "summary": "LAMMPS data file. Read-only.",
    },
}


def _summarize_target(target: object, idx: int) -> dict:
    """Build a small JSON-serialisable summary of a molpack ``Target``."""
    summary: dict[str, object] = {"index": idx}
    for attr in ("name", "natoms", "count", "is_fixed"):
        try:
            summary[attr] = getattr(target, attr)
        except Exception:
            pass
    try:
        elements = list(target.elements)  # type: ignore[attr-defined]
        summary["element_counts"] = _element_counts(elements)
    except Exception:
        pass
    return summary


def _element_counts(elements: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for el in elements:
        counts[el] = counts.get(el, 0) + 1
    return counts


class MolPackProvider:
    """Provider for molpack domain tools."""

    name = "molpack"

    def register(self, mcp: "FastMCP") -> None:
        from mcp.types import ToolAnnotations

        ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

        @mcp.tool(annotations=ro)
        def list_restraints() -> dict:
            """List the molpack restraint catalog.

            Returns:
                Dict with ``restraints`` (each entry has ``name``,
                ``parameters`` listing constructor args, ``summary``,
                and ``inp_keyword`` showing the matching ``.inp``
                script syntax) plus ``guidance`` text.
            """
            return {
                "restraints": [
                    {
                        "name": name,
                        "parameters": list(info["parameters"]),  # type: ignore[arg-type]
                        "summary": info["summary"],
                        "inp_keyword": info["inp_keyword"],
                    }
                    for name, info in _RESTRAINTS.items()
                ],
                "guidance": (
                    "Restraints are attached to a Target via "
                    "``target.with_restraint(...)`` or to all targets "
                    "via ``Molpack.with_global_restraint(...)``. "
                    "``.inp`` scripts use the keyword forms shown."
                ),
            }

        @mcp.tool(annotations=ro)
        def list_formats() -> dict:
            """List molpack's structure-file format support.

            Returns:
                Dict with ``formats`` (each entry has ``filetype``,
                ``extensions``, ``read``/``write`` capability flags,
                ``summary``) and ``guidance`` text describing how
                ``.inp`` scripts pick a format.
            """
            return {
                "formats": [
                    {
                        "filetype": key,
                        "extensions": list(info["extensions"]),  # type: ignore[arg-type]
                        "read": info["read"],
                        "write": info["write"],
                        "summary": info["summary"],
                    }
                    for key, info in _FORMATS.items()
                ],
                "guidance": (
                    "In a ``.inp`` script, set ``filetype <fmt>`` to "
                    "force a format for all structure files; otherwise "
                    "the format is inferred from each file extension. "
                    "The ``output`` keyword's extension determines the "
                    "writer."
                ),
            }

        @mcp.tool(annotations=ro)
        def inspect_script(path: str) -> dict:
            """Parse a Packmol-compatible ``.inp`` script and summarise it.

            Args:
                path: Filesystem path to the ``.inp`` script. Subject
                    to the suite's path-safety policy. Relative paths
                    inside the script are resolved by molpack against
                    the script's parent directory.

            Returns:
                Dict with ``path``, ``output`` (resolved output file),
                ``nloop`` (max outer iterations), ``num_targets``,
                ``num_atoms_total``, and ``targets`` (one entry per
                target with ``index``, ``name``, ``natoms``, ``count``,
                ``is_fixed``, ``element_counts``). Returns an
                ``error`` key on failure.
            """
            p = Path(path)
            if not p.exists():
                return {"error": f"file not found: {path}", "path": str(p)}
            if not p.is_file():
                return {"error": f"not a file: {path}", "path": str(p)}

            try:
                from molpack import load_script
            except ImportError as exc:
                return {
                    "error": f"molpack import failed: {exc}",
                    "path": str(p),
                }

            try:
                job = load_script(str(p))
            except Exception as exc:  # pragma: no cover — exercised via tests
                return {
                    "error": f"load_script failed: {type(exc).__name__}: {exc}",
                    "path": str(p),
                }

            targets = list(job.targets)
            target_summaries = [_summarize_target(t, i) for i, t in enumerate(targets)]
            num_atoms_total = 0
            for s in target_summaries:
                natoms = s.get("natoms")
                count = s.get("count")
                if isinstance(natoms, int) and isinstance(count, int):
                    num_atoms_total += natoms * count
            return {
                "path": str(p),
                "output": job.output,
                "nloop": job.nloop,
                "num_targets": len(targets),
                "num_atoms_total": num_atoms_total,
                "targets": target_summaries,
            }
