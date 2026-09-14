"""The worker child — one provider plane in its own process, speaking duplex v1.

A supervisor launches this file *by path*
(``python -P child.py --entrypoint package.module:ClassName --path <root>``),
never as a module of an installed package: the plane's code may live anywhere
on disk, and ``--path`` is the only root it is imported from. ``-P`` keeps this
script's own directory off ``sys.path``, so nothing sitting next to it can
shadow that root.

The launch vector is the whole configuration. Nothing here reads the
environment: two planes started by two different clients would otherwise
disagree about a setting no ``molmcp config list`` could report.

*Duplex v1* is the wire format both sides speak: one JSON object per line
(NDJSON, newline-delimited JSON) in each direction, frozen in
:mod:`molmcp.provider_worker.protocol`.

The child imports the real provider base and the real wire format, and nothing
else of molmcp — no server library, no composition, no stub standing in for
either. It answers three things: a ``hello`` catalog of signature facts, one
``result`` or ``error`` per ``invoke``, and exit 0 on ``shutdown``. Turning
those facts into MCP tools is the parent's half of the job, so the schema a
client finally sees is built by the same machinery an in-process plane uses.

Isolation is asserted, not assumed. If the MCP server library or molmcp's own
composition module is resident once the plane has been constructed, the child
reports which modules leaked and exits non-zero *without* saying hello: a
worker that drags the server library into its own process has bought nothing,
and failing loudly at startup is cheaper than discovering it in production.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from molmcp.provider_sdk import ProviderBase

#: The MCP server library this process exists to stay out of. Named as a
#: string because the child must be able to detect it without importing it.
_SERVER_LIBRARY = "fastmcp"

#: molmcp's own composition module. Loading it here would mean the child had
#: gone through the server rather than straight to the plane.
_SERVER_MODULE = "molmcp.server"

#: Wire id for a frame that answers no call — a startup failure, or a line
#: that could not be decoded far enough to carry an id.
_NO_CALL = ""

#: Exit status of a child that could not prove its own isolation.
_ISOLATION_FAILURE = 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Read the launch vector.

    Args:
        argv: Arguments to parse, or None to read ``sys.argv[1:]``.

    Returns:
        A namespace carrying ``entrypoint`` and ``path``. Both are required:
        a default for either would let a mislaunched child serve a plane
        nobody asked for.
    """
    parser = argparse.ArgumentParser(
        prog="child.py",
        description="Serve one provider plane over duplex v1 on stdio.",
    )
    parser.add_argument(
        "--entrypoint",
        required=True,
        help="Provider class to serve, as 'package.module:ClassName'.",
    )
    parser.add_argument(
        "--path",
        required=True,
        help="Directory prepended to sys.path — the only root the entrypoint "
        "is imported from.",
    )
    return parser.parse_args(argv)


def _instantiate(entrypoint: str, base: type[ProviderBase]) -> ProviderBase:
    """Import the module the entrypoint names and construct its class.

    Nothing below is caught. Every one of these failures happens before the
    child says hello, so the parent sees a single symptom — a handshake that
    never completed — and the child's traceback on the inherited stderr says
    which failure it was.

    Args:
        entrypoint: ``package.module:ClassName``.
        base: Provider base class the named class must derive from.

    Returns:
        A new instance of that class.

    Raises:
        ValueError: If *entrypoint* is not two non-empty parts, or names
            something that is not a *base* subclass. Both are broken launch
            vectors.
        ImportError: If the module cannot be imported from the ``--path``
            root — the usual shape of "this checkout does not run here".
        AttributeError: If that module has no attribute named ``ClassName``.
    """
    module_name, separator, class_name = entrypoint.partition(":")
    if not (separator and module_name and class_name):
        raise ValueError(
            f"--entrypoint must be 'package.module:ClassName', got {entrypoint!r}"
        )
    candidate = getattr(importlib.import_module(module_name), class_name)
    if not (isinstance(candidate, type) and issubclass(candidate, base)):
        raise ValueError(f"{entrypoint} is not a {base.__name__} subclass")
    return candidate()


def _leaked_modules() -> list[str]:
    """Loaded modules that betray the isolation this process exists for.

    Returns:
        Sorted names of every resident module belonging to the MCP server
        library or to molmcp's composition layer. Empty means the child got
        to its plane without going through a server.
    """
    return [
        name
        for name in sorted(sys.modules)
        if name in (_SERVER_LIBRARY, _SERVER_MODULE)
        or name.startswith(f"{_SERVER_LIBRARY}.")
    ]


def _tool_facts(instance: ProviderBase) -> list[dict[str, Any]]:
    """Describe every tool the plane declares, as hello catalog entries.

    Each signature is read off the *bound* method, so ``self`` never reaches
    the parent. Facts are all that travel: names, kinds, annotations and
    defaults. The parent rebuilds a callable from them, and its MCP server
    derives the schema — the child never spells one out.

    Args:
        instance: The provider this child serves.

    Returns:
        One entry per declared tool, in declaration order, each with
        ``name``, ``attribute``, ``doc``, ``annotations`` and ``parameters``.
    """
    from molmcp.provider_worker.protocol import ANNOTATION_KEYS, signature_facts

    facts: list[dict[str, Any]] = []
    for spec in instance.tool_specs():
        method = getattr(instance, spec.attribute)
        facts.append(
            {
                "name": spec.name,
                "attribute": spec.attribute,
                "doc": method.__doc__ or "",
                "annotations": {
                    key: bool(getattr(spec.annotations, key)) for key in ANNOTATION_KEYS
                },
                "parameters": signature_facts(inspect.signature(method)),
            }
        )
    return facts


def _tool_methods(
    instance: ProviderBase, facts: Sequence[Mapping[str, Any]]
) -> dict[str, Callable[..., Any]]:
    """Bind each catalog entry to the method that implements it.

    Args:
        instance: The provider this child serves.
        facts: The catalog entries sent in hello.

    Returns:
        Bound methods by *bare* tool name — the same name the parent invokes
        by, never a namespaced one.
    """
    return {fact["name"]: getattr(instance, fact["attribute"]) for fact in facts}


def _write(stream: IO[str], line: str) -> None:
    """Write one NDJSON line and flush it.

    A frame the parent cannot read yet is a frame it will block on, so every
    write is flushed rather than left to the pipe's buffer.

    Args:
        stream: Where the answer goes.
        line: One complete NDJSON line, newline included.
    """
    stream.write(line)
    stream.flush()


def _render(exc: BaseException) -> str:
    """Render an exception for the wire.

    Args:
        exc: The failure to report.

    Returns:
        ``"TypeName: message"``. The exception object cannot cross a pipe and
        the two processes share no traceback, so this text is the whole
        diagnosis the parent gets to re-raise.
    """
    return f"{type(exc).__name__}: {exc}"


def _call(frame: Mapping[str, Any], methods: Mapping[str, Callable[..., Any]]) -> Any:
    """Run the tool an ``invoke`` frame names.

    Args:
        frame: A decoded frame, expected to be an ``invoke``.
        methods: Bound methods by bare tool name.

    Returns:
        Whatever the tool returned, to travel as the ``result`` value.

    Raises:
        ValueError: If the frame is not an ``invoke``.
        LookupError: If no tool answers to that name. The parent bound its
            tools from this child's own hello, so the message lists what is
            actually offered.
        Exception: Whatever the tool itself raises.
    """
    if frame["type"] != "invoke":
        raise ValueError(f"expected an invoke frame, got {frame['type']!r}")
    name = frame["name"]
    if name not in methods:
        raise LookupError(
            f"no tool named {name!r}; this plane offers {sorted(methods)}"
        )
    return methods[name](**frame["args"])


def _serve(
    methods: Mapping[str, Callable[..., Any]], stdin: IO[str], stdout: IO[str]
) -> int:
    """Answer frames until the parent says shutdown or closes the pipe.

    A failing call is an ``error`` frame, never an exit: one bad call must
    not cost the parent its worker. The same holds for a line that is not a
    frame at all — it is reported against no call id and the loop goes on.

    Args:
        methods: Bound methods by bare tool name.
        stdin: Stream the parent's frames arrive on.
        stdout: Stream every answer is written and flushed to.

    Returns:
        0 — reached on a ``shutdown`` frame, or on end of input, which is the
        parent having closed the pipe.
    """
    from molmcp.provider_worker.protocol import (
        ProtocolError,
        decode,
        encode_error,
        encode_result,
    )

    for line in iter(stdin.readline, ""):
        try:
            frame = decode(line)
        except ProtocolError as exc:
            _write(stdout, encode_error(call_id=_NO_CALL, error=_render(exc)))
            continue
        if frame["type"] == "shutdown":
            return 0
        call_id = frame.get("id", _NO_CALL)
        try:
            value = _call(frame, methods)
        except Exception as exc:
            _write(stdout, encode_error(call_id=call_id, error=_render(exc)))
            continue
        _write(stdout, encode_result(call_id=call_id, value=value))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Serve one provider plane on stdio.

    Args:
        argv: Arguments after the script path, or None to read
            ``sys.argv[1:]``.

    Returns:
        0 after a clean shutdown or a closed pipe; 2 when the isolation
        assertion fails, in which case no hello was ever sent — the parent
        gets an ``error`` frame carrying the names of the leaked modules.

    Raises:
        SystemExit: ``--entrypoint`` or ``--path`` is missing or unparsable.
            argparse prints usage to stderr and raises this itself; the child
            never reaches the plane.
        Exception: Whatever loading the entrypoint raises — see
            :func:`_instantiate`. Deliberately not caught: the child ends
            without a hello, and the parent reports the handshake failure.
    """
    args = _parse_args(argv)
    sys.path.insert(0, args.path)

    try:
        from molmcp.provider_sdk import ProviderBase
    except ImportError:  # molmcp predating the public SDK module
        from molmcp.providers.base import ProviderBase
    from molmcp.provider_worker.protocol import encode_error, encode_hello

    instance = _instantiate(args.entrypoint, ProviderBase)

    leaked = _leaked_modules()
    if leaked:
        _write(
            sys.stdout,
            encode_error(
                call_id=_NO_CALL,
                error=(
                    f"worker isolation broken: serving {args.entrypoint} "
                    f"loaded {', '.join(leaked)} in the child process"
                ),
            ),
        )
        return _ISOLATION_FAILURE

    facts = _tool_facts(instance)
    _write(sys.stdout, encode_hello(name=instance.name, tools=facts))
    return _serve(_tool_methods(instance, facts), sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
