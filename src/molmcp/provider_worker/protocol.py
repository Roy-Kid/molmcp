"""Duplex v1 — the NDJSON wire format between a provider child and its parent.

A worker provider runs a plain :class:`~molmcp.provider_sdk.ProviderBase`
subclass in a child process and proxies its tools onto a FastMCP server in the
parent. This module is the one place that wire format is written down, and it
is imported by the child, so it depends on the standard library only: every
import here is an import the child pays for before it can say hello.

One frame is one line of JSON followed by ``"\\n"`` (NDJSON). Every frame
carries ``"type"`` and ``"protocol"``; a line missing either is refused rather
than guessed at.

Frame table (v1)
----------------

===========  ================  =========================================
type         direction         payload
===========  ================  =========================================
``hello``    child -> parent   ``name`` (the plane id) and ``tools``: the
                               catalog, one entry per declared tool, each
                               with ``name`` (the bare tool name),
                               ``attribute`` (the method that implements
                               it), ``doc`` (that method's docstring),
                               ``annotations`` (the four booleans in
                               :data:`ANNOTATION_KEYS`) and
                               ``parameters`` (signature facts, see
                               :func:`signature_facts`).
``invoke``   parent -> child   ``id`` (the call id), ``name`` (bare tool
                               name) and ``args`` (keyword arguments).
``result``   child -> parent   ``id``, ``ok`` (true) and ``value`` — what
                               the tool returned.
``error``    child -> parent   ``id``, ``ok`` (false) and ``error`` — the
                               failure rendered as a string.
``shutdown`` parent -> child   nothing. The child exits; the parent waits
                               and may terminate it if it does not.
===========  ================  =========================================

The call id travels under the wire key ``"id"``; the Python keyword argument
is ``call_id``, so no function here shadows the builtin.

Version mismatch
----------------

Either side reading ``protocol != 1`` fails: :func:`decode` raises
:class:`ProtocolError` and the parent shuts the child down and raises. There
is no negotiation and no downgrade — a silent downgrade would let a child
built against a different frame table answer as if it agreed.

Signature facts, not JSON Schema
--------------------------------

The child sends what it knows — parameter names, kinds, annotations and
defaults — and never a JSON Schema. The parent rebuilds an
:class:`inspect.Signature` from those facts and hands FastMCP a callable;
FastMCP stays the only producer of JSON Schema, so the schema a client sees
comes from the same machinery an in-process provider would have used.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping, Sequence
from typing import Any

#: The wire format this module speaks. Bumped only by a spec that changes the
#: frame table; both sides refuse anything else.
PROTOCOL_VERSION: int = 1

#: Every frame type duplex v1 admits. :func:`decode` refuses the rest, so a
#: typo in a ``type`` is an error at the boundary rather than a silent no-op.
MESSAGE_TYPES: frozenset[str] = frozenset(
    {"hello", "invoke", "result", "error", "shutdown"}
)

#: The MCP ``ToolAnnotations`` hints a hello catalog carries, in the order the
#: catalog writes them. Ordered, because it is also the order a reader reasons
#: about a tool in: what it reads, what it destroys, whether repeating it is
#: safe, and how far it reaches.
ANNOTATION_KEYS: tuple[str, str, str, str] = (
    "read_only_hint",
    "destructive_hint",
    "idempotent_hint",
    "open_world_hint",
)

#: Annotation names that survive the round trip as real objects. Anything else
#: stays the string the child sent: the parent cannot import a provider's own
#: classes, and a string annotation is honest about that.
_BUILTIN_ANNOTATIONS: dict[str, type | None] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "None": None,
}

#: ``inspect.Parameter`` kinds by attribute name — the vocabulary
#: :func:`signature_facts` writes and :func:`rebuild_signature` reads.
_PARAMETER_KINDS = {
    kind.name: kind
    for kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.VAR_KEYWORD,
    )
}

#: How much of an offending line an error message quotes. Long enough to
#: recognise the frame, short enough not to bury the reason in it.
_EXCERPT = 120


class ProtocolError(RuntimeError):
    """Something duplex v1 cannot accept.

    :func:`decode` raises it for a line that is not JSON, is not a JSON
    object, lacks ``type`` or ``protocol``, names a type outside
    :data:`MESSAGE_TYPES`, or declares a protocol other than
    :data:`PROTOCOL_VERSION`. :func:`rebuild_signature` raises it for a
    catalog entry whose ``kind`` is not an :class:`inspect.Parameter` kind.
    The message names which of those it was: the two processes share no
    traceback, so the text is the whole diagnosis.

    It subclasses ``RuntimeError``, so a caller that only wants "talking to
    the worker went wrong" can catch that one type.
    """


def _excerpt(line: str) -> str:
    """Trim ``line`` to a quotable length for an error message.

    Args:
        line: The raw line that failed to decode.

    Returns:
        The line stripped of surrounding whitespace, truncated with an
        ellipsis when it is longer than :data:`_EXCERPT`.
    """
    text = line.strip()
    if len(text) <= _EXCERPT:
        return text
    return f"{text[:_EXCERPT]}..."


def _line(payload: dict[str, Any]) -> str:
    """Render one frame as a single NDJSON line.

    Args:
        payload: Frame body. ``type`` must already be set; ``protocol`` is
            added here so no encoder can forget it.

    Returns:
        A single line of JSON ending in a newline. ``json.dumps`` escapes
        every newline inside the payload, so the result is always exactly
        one line.
    """
    return json.dumps({**payload, "protocol": PROTOCOL_VERSION}) + "\n"


def encode_hello(*, name: str, tools: Sequence[Mapping[str, Any]]) -> str:
    """Encode the child's opening catalog.

    ``hello`` is the only place tools are declared. The parent builds its
    FastMCP tools from this frame and asks the child nothing else about them.

    Args:
        name: The plane id the child provider answers to.
        tools: One catalog entry per declared tool — ``name``, ``attribute``,
            ``doc``, ``annotations`` and ``parameters``.

    Returns:
        One NDJSON line carrying ``type``, ``protocol``, ``name`` and
        ``tools``.
    """
    return _line({"type": "hello", "name": name, "tools": [dict(t) for t in tools]})


def encode_invoke(*, call_id: str, name: str, args: Mapping[str, Any]) -> str:
    """Encode a parent-to-child tool call.

    Args:
        call_id: Identifier the matching ``result`` or ``error`` echoes back.
            It travels under the wire key ``"id"``.
        name: Bare tool name, as it appeared in the hello catalog.
        args: Keyword arguments for the call.

    Returns:
        One NDJSON line carrying ``type``, ``protocol``, ``id``, ``name`` and
        ``args``.
    """
    return _line({"type": "invoke", "id": call_id, "name": name, "args": dict(args)})


def encode_result(*, call_id: str, value: Any) -> str:
    """Encode a successful call's return value.

    Args:
        call_id: The ``id`` of the ``invoke`` being answered.
        value: What the tool returned. Must be JSON-serializable — a provider
            tool returns MCP payloads, so this is the same constraint MCP
            already puts on it.

    Returns:
        One NDJSON line carrying ``type``, ``protocol``, ``id``, ``ok`` (true)
        and ``value``.
    """
    return _line({"type": "result", "id": call_id, "ok": True, "value": value})


def encode_error(*, call_id: str, error: str) -> str:
    """Encode a failed call.

    Args:
        call_id: The ``id`` of the ``invoke`` being answered.
        error: The failure as a string. The exception object cannot cross a
            pipe, so the child renders it and the parent re-raises the text.

    Returns:
        One NDJSON line carrying ``type``, ``protocol``, ``id``, ``ok``
        (false) and ``error``.
    """
    return _line({"type": "error", "id": call_id, "ok": False, "error": error})


def encode_shutdown() -> str:
    """Encode the parent's request that the child exit.

    Returns:
        One NDJSON line carrying ``type`` and ``protocol`` and nothing else —
        the frame is the whole message.
    """
    return _line({"type": "shutdown"})


def decode(line: str) -> dict[str, Any]:
    """Parse and validate one NDJSON frame.

    Args:
        line: A single line read from the pipe, newline included or not.

    Returns:
        The parsed frame, with ``type`` and ``protocol`` known good.

    Raises:
        ProtocolError: If the line is not JSON, is not a JSON object, is
            missing ``type`` or ``protocol``, names a type outside
            :data:`MESSAGE_TYPES`, or declares a protocol other than
            :data:`PROTOCOL_VERSION`.
    """
    try:
        frame = json.loads(line)
    except ValueError as exc:
        raise ProtocolError(f"frame is not JSON: {_excerpt(line)!r} ({exc})") from exc
    if not isinstance(frame, dict):
        raise ProtocolError(
            f"frame is not a JSON object but {type(frame).__name__}: {_excerpt(line)!r}"
        )
    if "type" not in frame:
        raise ProtocolError(f'frame is missing "type": {_excerpt(line)!r}')
    kind = frame["type"]
    if kind not in MESSAGE_TYPES:
        raise ProtocolError(
            f"unknown frame type {kind!r}; duplex v1 speaks {sorted(MESSAGE_TYPES)}"
        )
    if "protocol" not in frame:
        raise ProtocolError(f'frame is missing "protocol": {_excerpt(line)!r}')
    version = frame["protocol"]
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"protocol version mismatch: frame declares {version!r}, this side "
            f"speaks {PROTOCOL_VERSION}"
        )
    return frame


def _is_json_safe(value: Any) -> bool:
    """Report whether ``value`` can cross the wire as JSON.

    Args:
        value: A parameter default taken from a live signature.

    Returns:
        ``True`` when :func:`json.dumps` accepts it, ``False`` otherwise.
    """
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def _annotation_name(annotation: Any) -> str:
    """Render one parameter annotation as a string.

    Args:
        annotation: The annotation from an :class:`inspect.Parameter`.

    Returns:
        ``""`` for an empty annotation, the type's ``__name__`` when the
        annotation is a type, and ``str(annotation)`` for everything else —
        a typing construct or a forward reference the parent cannot resolve.
    """
    if annotation is inspect.Parameter.empty:
        return ""
    if isinstance(annotation, type):
        name = getattr(annotation, "__name__", None)
        if isinstance(name, str):
            return name
    return str(annotation)


def signature_facts(signature: inspect.Signature) -> list[dict[str, Any]]:
    """Describe a signature as a list of per-parameter facts.

    This is deliberately not a JSON Schema. The child states what its method
    takes; the parent rebuilds a callable and lets FastMCP derive the schema,
    so a proxied tool and an in-process one are described by the same code.

    Args:
        signature: A *bound* method signature — ``self`` is already gone.

    Returns:
        One dict per parameter, in declaration order, with ``name``, ``kind``
        (the :class:`inspect.Parameter` attribute name, e.g.
        ``"POSITIONAL_OR_KEYWORD"``), ``annotation`` (see
        :func:`_annotation_name`) and ``has_default``. A ``default`` key is
        present only when there is a default *and* it is JSON-serializable;
        an unserializable default is omitted rather than approximated.
    """
    facts: list[dict[str, Any]] = []
    for parameter in signature.parameters.values():
        has_default = parameter.default is not inspect.Parameter.empty
        fact: dict[str, Any] = {
            "name": parameter.name,
            "kind": parameter.kind.name,
            "annotation": _annotation_name(parameter.annotation),
            "has_default": has_default,
        }
        if has_default and _is_json_safe(parameter.default):
            fact["default"] = parameter.default
        facts.append(fact)
    return facts


def rebuild_signature(facts: Sequence[Mapping[str, Any]]) -> inspect.Signature:
    """Rebuild an :class:`inspect.Signature` from :func:`signature_facts`.

    Builtin annotation names come back as the type objects, so FastMCP sees
    ``str`` rather than ``"str"``; anything else stays a string, which FastMCP
    treats as an unresolved annotation instead of guessing at a class the
    parent never imported.

    Args:
        facts: The ``parameters`` list from one hello catalog entry.

    Returns:
        A signature with no return annotation. The wire format carries no
        return type — a hello catalog entry has no field for one — and does
        not need to: FastMCP builds the structured result from the value the
        tool actually returned, so a proxied tool that returns a dict reaches
        the client as structured content exactly as an in-process one does.

    Raises:
        ProtocolError: If a fact names a parameter kind that is not an
            :class:`inspect.Parameter` kind.
    """
    parameters: list[inspect.Parameter] = []
    for fact in facts:
        kind_name = fact["kind"]
        kind = _PARAMETER_KINDS.get(kind_name)
        if kind is None:
            raise ProtocolError(
                f"unknown parameter kind {kind_name!r} for {fact['name']!r}; "
                f"expected one of {sorted(_PARAMETER_KINDS)}"
            )
        annotation_name = fact["annotation"]
        if annotation_name == "":
            annotation: Any = inspect.Parameter.empty
        elif annotation_name in _BUILTIN_ANNOTATIONS:
            annotation = _BUILTIN_ANNOTATIONS[annotation_name]
        else:
            annotation = annotation_name
        # A default the child could not serialize left no ``default`` key, so
        # the parameter comes back required. Better a caller that must pass a
        # value than a parent that invents one the provider never chose.
        default = fact.get("default", inspect.Parameter.empty)
        parameters.append(
            inspect.Parameter(
                fact["name"], kind, default=default, annotation=annotation
            )
        )
    return inspect.Signature(parameters)
