"""child.py — the path-launched worker script, driven as a real subprocess.

Every live test here spawns the script a Supervisor spawns
(``sys.executable -P child.py --entrypoint echo:EchoProvider --path
<fixtures>``) and speaks duplex v1 over its stdio. The child is a *script*:
the package has no ``__main__.py`` and the launch vector carries no ``-m``.

The static tests guard what the child must never grow: a fastmcp import, an
``instance.register(mcp)`` call, a faked ``molmcp`` module, a
``spec_from_file_location`` loader, environment-driven configuration, or a
hand-written JSON Schema. ``hello`` carries signature *facts*; FastMCP
produces the schema in the parent, from the rebuilt callable.
"""

from __future__ import annotations

import ast
import contextlib
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

from molmcp.provider_worker.protocol import decode, encode_invoke, encode_shutdown

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
_CHILD = _SRC / "molmcp" / "provider_worker" / "child.py"
_MAIN = _SRC / "molmcp" / "provider_worker" / "__main__.py"
_FIXTURES = Path(__file__).parent / "fixtures"

#: The one launch vector: a filesystem path, never ``python -m``. ``-P`` keeps
#: the script's own directory out of ``sys.path``, so ``--path`` is the only
#: root the fixture can be imported from.
_ARGV = [
    sys.executable,
    "-P",
    str(_CHILD),
    "--entrypoint",
    "echo:EchoProvider",
    "--path",
    str(_FIXTURES),
]

#: Seconds a single read or exit may take before the test fails instead of
#: wedging the suite behind a hung child.
_TIMEOUT = 15.0

#: Literals that would mean the child hand-rolled a JSON Schema.
_SCHEMA_LITERALS = ("properties", "inputSchema", "additionalProperties", "$schema")


def _environment() -> dict[str, str]:
    """The parent environment plus ``src`` on ``PYTHONPATH``.

    The child imports ``molmcp`` for real; nothing here configures it.
    """
    return {**os.environ, "PYTHONPATH": str(_SRC)}


@contextlib.contextmanager
def _child_process() -> Iterator[subprocess.Popen[str]]:
    """Spawn the real child script, reaping it however the test leaves it."""
    process = subprocess.Popen(
        list(_ARGV),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=_environment(),
    )
    try:
        yield process
    finally:
        _reap(process)


def _reap(process: subprocess.Popen[str]) -> None:
    """Terminate the child and close its pipes, whatever state it is in."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=_TIMEOUT)
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            with contextlib.suppress(OSError, ValueError):
                stream.close()


def _readline(process: subprocess.Popen[str]) -> str:
    """One NDJSON line from the child, failing the test if it never comes."""
    stdout = process.stdout
    if stdout is None:
        raise AssertionError("child was spawned without a stdout pipe")
    lines: list[str] = []

    def read() -> None:
        lines.append(stdout.readline())

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(_TIMEOUT)
    if reader.is_alive():
        raise AssertionError(f"child wrote no line within {_TIMEOUT}s")
    if not lines[0]:
        raise AssertionError("child closed stdout instead of answering")
    return lines[0]


def _send(process: subprocess.Popen[str], line: str) -> None:
    """Write one NDJSON line to the child and flush it."""
    stdin = process.stdin
    if stdin is None:
        raise AssertionError("child was spawned without a stdin pipe")
    stdin.write(line)
    stdin.flush()


def _source() -> str:
    """The child script as text; it must exist to be launchable by path."""
    if not _CHILD.is_file():
        raise AssertionError(f"{_CHILD} does not exist")
    return _CHILD.read_text(encoding="utf-8")


def _imported_modules(tree: ast.Module) -> set[str]:
    """Every dotted module name the child imports, in either form."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def _called_names(tree: ast.Module) -> set[str]:
    """Every name called in the child, bare or as an attribute."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _string_constants(tree: ast.Module) -> set[str]:
    """Every string literal in the child."""
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _reads_environment(tree: ast.Module) -> bool:
    """Whether the child reads ``os.environ`` / ``os.getenv`` at all."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                return True
        if isinstance(node, ast.Name) and node.id == "getenv":
            return True
    return False


class TestChild:
    """The worker child script, spoken to over duplex v1."""

    # -- hello ---------------------------------------------------------

    def test_hello_is_the_first_line_and_names_the_plane(self):
        """The child announces itself before anything is asked of it."""
        with _child_process() as process:
            hello = decode(_readline(process))

        assert hello["type"] == "hello"
        assert hello["protocol"] == 1
        assert hello["name"] == "echo"

    def test_hello_declares_exactly_the_bare_echo_tool(self):
        """One tool, wire-named ``echo`` — never a namespaced ``echo_echo``."""
        with _child_process() as process:
            hello = decode(_readline(process))

        assert {spec["name"] for spec in hello["tools"]} == {"echo"}

    def test_hello_carries_the_method_docstring(self):
        """``doc`` is the method's docstring; the parent uses it as-is."""
        with _child_process() as process:
            hello = decode(_readline(process))

        (spec,) = hello["tools"]
        assert spec["doc"] == "Echo text back."

    def test_hello_carries_the_four_annotation_hints(self):
        """READ_ONLY reaches the parent as four JSON booleans, not an object."""
        with _child_process() as process:
            hello = decode(_readline(process))

        (spec,) = hello["tools"]
        assert spec["annotations"] == {
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
            "open_world_hint": False,
        }

    def test_hello_parameters_are_signature_facts_without_self(self):
        """The signature is read off the *bound* method, so ``self`` is gone."""
        with _child_process() as process:
            hello = decode(_readline(process))

        (spec,) = hello["tools"]
        parameters = spec["parameters"]
        assert isinstance(parameters, list)
        names = [fact["name"] for fact in parameters]
        assert "self" not in names
        assert names == ["text"]
        (text,) = parameters
        assert text["kind"] == "POSITIONAL_OR_KEYWORD"
        assert text["annotation"] == "str"

    # -- invoke / shutdown ---------------------------------------------

    def test_invoke_echoes_the_argument_back(self):
        """An ``invoke`` frame dispatches to the method and answers ``result``."""
        with _child_process() as process:
            decode(_readline(process))
            _send(
                process,
                encode_invoke(call_id="1", name="echo", args={"text": "ping"}),
            )
            frame = decode(_readline(process))

        assert frame["type"] == "result"
        assert frame["id"] == "1"
        assert frame["value"] == {"text": "ping"}

    def test_shutdown_exits_the_child_with_zero(self):
        """A v1 ``shutdown`` frame ends the loop cleanly, without a signal."""
        with _child_process() as process:
            decode(_readline(process))
            _send(process, encode_shutdown())
            try:
                returncode = process.wait(timeout=_TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                raise AssertionError("child ignored the v1 shutdown frame") from exc

        assert returncode == 0

    # -- edge ----------------------------------------------------------

    def test_unknown_tool_answers_with_an_error_frame(self):
        """A name the plane does not offer is an ``error``, not a crash."""
        with _child_process() as process:
            decode(_readline(process))
            _send(process, encode_invoke(call_id="7", name="nope", args={}))
            frame = decode(_readline(process))

        assert frame["type"] == "error"
        assert frame["id"] == "7"
        assert isinstance(frame["error"], str)
        assert frame["error"] != ""

    def test_child_survives_an_error_and_serves_the_next_invoke(self):
        """One bad call must not cost the supervisor its worker."""
        with _child_process() as process:
            decode(_readline(process))
            _send(process, encode_invoke(call_id="7", name="nope", args={}))
            assert decode(_readline(process))["type"] == "error"
            _send(
                process,
                encode_invoke(call_id="8", name="echo", args={"text": "again"}),
            )
            frame = decode(_readline(process))

        assert frame["type"] == "result"
        assert frame["id"] == "8"
        assert frame["value"] == {"text": "again"}

    # -- isolation, read off the source --------------------------------

    def test_source_never_imports_fastmcp(self):
        """The whole point of the subprocess: no server library inside it."""
        source = _source()
        assert "import fastmcp" not in source
        assert "from fastmcp" not in source
        imported = _imported_modules(ast.parse(source))
        offenders = {
            name
            for name in imported
            if name == "fastmcp" or name.startswith("fastmcp.")
        }
        assert offenders == set()

    def test_source_never_calls_register(self):
        """``register(mcp)`` belongs to the parent; the child only reports."""
        source = _source()
        assert "register(" not in source
        assert "register" not in _called_names(ast.parse(source))

    def test_source_never_fakes_a_module(self):
        """The child imports the real molmcp — no stub, no ad-hoc loader."""
        source = _source()
        assert "types.ModuleType(" not in source
        assert "spec_from_file_location" not in source
        called = _called_names(ast.parse(source))
        assert "ModuleType" not in called
        assert "spec_from_file_location" not in called

    def test_source_never_reads_the_environment(self):
        """Configuration arrives on argv; the environment is only inherited."""
        source = _source()
        assert "os.environ" not in source
        assert "os.getenv" not in source
        assert not _reads_environment(ast.parse(source))

    def test_source_never_builds_a_json_schema(self):
        """Only signature facts travel; FastMCP owns the schema, in the parent."""
        source = _source()
        constants = _string_constants(ast.parse(source))
        for literal in _SCHEMA_LITERALS:
            assert literal not in source
            assert literal not in constants

    # -- script, not module --------------------------------------------

    def test_the_package_has_no_main_module(self):
        """``python -m molmcp.provider_worker`` must stay impossible."""
        assert not _MAIN.exists()

    def test_the_launch_vector_never_uses_dash_m(self):
        """The child is launched by path, with ``-P`` guarding sys.path."""
        assert "-m" not in _ARGV
        assert _ARGV[1] == "-P"
        assert _ARGV[2] == str(_CHILD)
