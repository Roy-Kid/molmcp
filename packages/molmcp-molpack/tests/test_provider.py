"""Tests for ``MolPackProvider``.

The provider is a packing-script inspector: it surfaces the restraint
catalog, the supported file formats, and parses ``.inp`` scripts via
:func:`molpack.load_script`. It never runs a pack; tests use
``tmp_path`` fixtures to avoid touching the working tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("molmcp", reason="molcrafts-molmcp not installed")
pytest.importorskip("fastmcp", reason="fastmcp not installed")
pytest.importorskip("molpack", reason="molpack not installed")

from molmcp_molpack import MolPackProvider  # noqa: E402


def _build_server():
    from molmcp import create_server

    return create_server(
        name="molpack-test",
        providers=[MolPackProvider()],
        discover_entry_points=False,
        import_roots=None,
    )


def _list_tools(server):
    import asyncio

    return asyncio.run(server.list_tools())


def _get_tool(server, name: str):
    import asyncio

    tool = asyncio.run(server.get_tool(name))
    if tool is None:
        raise KeyError(f"tool {name!r} not registered")
    return tool.fn


def _write_xyz(p: Path, atoms: list[tuple[str, float, float, float]]) -> Path:
    lines = [str(len(atoms)), "molpack provider test"]
    for el, x, y, z in atoms:
        lines.append(f"{el} {x} {y} {z}")
    p.write_text("\n".join(lines) + "\n")
    return p


def _write_minimal_script(tmp_path: Path) -> tuple[Path, Path]:
    """Write a tiny .inp + structure pair, return (script, output_path)."""
    structure = _write_xyz(
        tmp_path / "water.xyz",
        [("H", 0.0, 0.0, 0.0), ("H", 0.0, 0.0, 1.0), ("O", 0.0, 0.0, 0.5)],
    )
    output = tmp_path / "packed.xyz"
    script = tmp_path / "mixture.inp"
    script.write_text(
        "tolerance 2.0\n"
        "seed 42\n"
        f"output {output.name}\n"
        "filetype xyz\n"
        "\n"
        f"structure {structure.name}\n"
        "  number 5\n"
        "  inside box 0.0 0.0 0.0 20.0 20.0 20.0\n"
        "end structure\n"
    )
    return script, output


def test_provider_implements_molmcp_protocol():
    from molmcp import Provider

    provider = MolPackProvider()
    assert isinstance(provider, Provider)
    assert provider.name == "molpack"


def test_three_tools_registered():
    server = _build_server()
    names = {t.name for t in _list_tools(server)}
    assert names == {"list_restraints", "list_formats", "inspect_script"}


def test_all_tools_have_read_only_annotation():
    server = _build_server()
    for tool in _list_tools(server):
        annotations = getattr(tool, "annotations", None)
        assert annotations is not None
        assert getattr(annotations, "readOnlyHint", False) is True
        assert getattr(annotations, "openWorldHint", True) is False


def test_list_restraints_covers_known_types():
    server = _build_server()
    fn = _get_tool(server, "list_restraints")
    out = fn()
    names = {r["name"] for r in out["restraints"]}
    expected = {
        "InsideBoxRestraint",
        "InsideSphereRestraint",
        "OutsideSphereRestraint",
        "AbovePlaneRestraint",
        "BelowPlaneRestraint",
    }
    assert expected.issubset(names)
    for entry in out["restraints"]:
        assert entry["parameters"], f"{entry['name']!r} missing parameters"
        assert entry["inp_keyword"], f"{entry['name']!r} missing inp_keyword"


def test_list_formats_covers_known_formats():
    server = _build_server()
    fn = _get_tool(server, "list_formats")
    out = fn()
    keys = {f["filetype"] for f in out["formats"]}
    assert {"pdb", "xyz", "sdf", "lammps_dump", "lammps_data"}.issubset(keys)
    for entry in out["formats"]:
        assert entry["extensions"], f"format {entry['filetype']!r} missing extensions"
        assert isinstance(entry["read"], bool)
        assert isinstance(entry["write"], bool)


def test_inspect_script_summarises_targets(tmp_path):
    script, output = _write_minimal_script(tmp_path)
    server = _build_server()
    fn = _get_tool(server, "inspect_script")
    out = fn(str(script))
    assert "error" not in out, out
    assert out["path"] == str(script)
    assert out["num_targets"] == 1
    assert out["targets"][0]["count"] == 5
    assert out["targets"][0]["natoms"] == 3
    assert out["num_atoms_total"] == 15
    # output may be returned absolute or relative; just check the basename matches
    assert Path(out["output"]).name == output.name


def test_inspect_script_missing_path(tmp_path):
    server = _build_server()
    fn = _get_tool(server, "inspect_script")
    out = fn(str(tmp_path / "does_not_exist.inp"))
    assert "error" in out
    assert "not found" in out["error"]


def test_inspect_script_directory_path(tmp_path):
    server = _build_server()
    fn = _get_tool(server, "inspect_script")
    out = fn(str(tmp_path))
    assert "error" in out
    assert "not a file" in out["error"]


def test_inspect_script_malformed(tmp_path):
    script = tmp_path / "broken.inp"
    script.write_text("this is not a valid packmol script\n")
    server = _build_server()
    fn = _get_tool(server, "inspect_script")
    out = fn(str(script))
    assert "error" in out
    assert "load_script failed" in out["error"]
