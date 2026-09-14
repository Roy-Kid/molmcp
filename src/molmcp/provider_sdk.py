"""Public Provider SDK — declare tools, probe upstream, register on FastMCP.

MCP (Model Context Protocol) is the wire protocol an AI client uses to call
tools. FastMCP is the Python library that hosts an MCP server. A *plane* is
one product's MCP server (``molvis``, ``molq``, ``molexp``). A *Provider* is
the class that declares that plane's tools.

A client decides whether to call a tool unattended from MCP
``ToolAnnotations``: read-only vs writing, local vs open-world (the call
reaches a network, scheduler, or browser), destructive vs additive,
idempotent vs not. Six named constants in this module cover every
first-party tool.

*probe* is an import-system availability check: ``importlib.util.find_spec``
asks whether an upstream science package *could* be imported, without
importing it. Catalogs omit a missing plane; only an explicit
``molmcp serve <plane>`` fails loudly.

Providers are discovered through the ``molmcp.providers`` entry-point group
(a packaging hook that lists Provider classes). The entry-point name must
equal :attr:`ProviderBase.name`. Tools always register *bare* (``open``,
never ``molvis_open``). Clients then see two forms: a focused
``molmcp serve molvis`` / :func:`~molmcp.create_plane` process shows
``molvis__open``; the composed ``molmcp serve`` / :func:`~molmcp.create_stack`
core mounts with a FastMCP namespace, so clients show ``molvis_open`` (some
clients ``molcrafts__molvis_open``).

A plane author subclasses :class:`ProviderBase`, marks methods with
:func:`tool`, and picks annotations from this module. The runtime protocol
:class:`Provider` is defined in :mod:`molmcp.provider` and re-exported here;
entry-point discovery, namespace authority, and availability filtering stay
there.

Each provider used to spend most of its class on one ``register()`` method —
349, 402 and 191 lines — holding every tool as a nested function, plus its
own copy of the availability probe, the missing-package guard, and a set of
hand-rolled annotations. The duplication drifted: three probes with three
signatures, three guard messages (one of which never said how to install
anything), and annotation values that disagreed between planes.

Here a tool is a method carrying a :func:`tool` declaration. The base
collects them, checks the upstream package once, and registers. Providers
are left holding only what is theirs: what the tools do.

Nothing here imports a science package. Importing it to find out whether it
exists would drag a whole scientific stack into a process that only wanted
to print a list.

A provider that needs a seventh annotation should add it here, with the
reason, rather than build one inline — the whole point is that a named
vocabulary cannot drift the way inline literals did.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from mcp.types import ToolAnnotations

from .provider import Provider

if TYPE_CHECKING:
    from fastmcp import FastMCP

#: Attribute a declared tool carries. Private by convention; read only here.
_MARKER = "__molmcp_tool__"

#: Reads local state and nothing else. Safe to call, safe to repeat.
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

#: Reads, but reaches a scheduler, a browser, or the network to do it
#: (open-world: ``open_world_hint=True``). Still safe to call; the answer
#: can change underneath you.
READ_REMOTE = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)

#: Changes state beyond this machine and cannot be trivially undone —
#: submitting to a cluster, cancelling a remote job, driving a browser. A
#: client should confirm before calling one of these.
MUTATION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)

#: Rewrites or removes local state, and resumes rather than duplicating when
#: called again.
#:
#: Destructiveness and reach are independent axes, and the first cut of this
#: vocabulary fused them: every destructive tool had to claim it touched an
#: open world (``open_world_hint=True``: the call reaches a network,
#: scheduler, or browser). molexp's ``run_adoption`` is the case that
#: exposed it — move mode unlinks source files, it resumes from a ledger,
#: and it never leaves the filesystem. Forcing it onto MUTATION would have
#: made it lie twice.
LOCAL_MUTATION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)

#: Adds to a local record; calling it twice adds twice.
#:
#: Additive, so *not* destructive — MCP ``ToolAnnotations`` treats
#: ``destructive_hint`` and a purely additive write as opposites. What a
#: caller needs to know is that a retry is not free, which is what
#: ``idempotent_hint=False`` says. Flagging it destructive instead would make
#: a client confirm every append, which is noise.
APPEND_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)

#: Create-or-get. Writes, but calling it twice leaves the same state, so it
#: is not a destructive surface even though it is not a read.
IDEMPOTENT_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool a provider offers.

    Attributes:
        name: Bare tool identifier registered on the server (``open``, never
            ``molvis_open``). A focused process is named after the plane, so
            clients show ``<plane>__<name>``; the composed core mounts with a
            FastMCP namespace, so clients show ``<plane>_<name>``.
        annotations: MCP ``ToolAnnotations`` the client uses to decide
            whether to confirm before calling. Use one of :data:`READ_ONLY`,
            :data:`READ_REMOTE`, :data:`MUTATION`, :data:`LOCAL_MUTATION`,
            :data:`APPEND_WRITE`, :data:`IDEMPOTENT_WRITE`.
        attribute: Name of the method implementing it.
    """

    name: str
    annotations: ToolAnnotations
    attribute: str


def tool(
    annotations: ToolAnnotations, *, name: str | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a method as one of this plane's MCP tools.

    Args:
        annotations: What a client needs to decide whether to confirm first.
            Use a constant from this module.
        name: Bare wire identifier (``open``, not ``molvis_open``). Required
            when the method cannot be that identifier (``open`` shadows a
            builtin, ``exec`` is a keyword); otherwise defaults to the
            method name. Startup rejects a prefixed name.

    Returns:
        Decorator that records ``(name, annotations)`` on the method and
        returns that method unchanged (not a wrapper), so FastMCP sees the
        original signature and docstring.
    """

    def declare(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(fn, _MARKER, (name or fn.__name__, annotations))
        return fn

    return declare


class ProviderBase:
    """Base for a plane's provider.

    Subclasses set :attr:`name`, optionally :attr:`upstream` /
    :attr:`import_name`, and declare tools with :func:`tool`.

    Attributes:
        name: Plane id and MCP server name. Must equal the
            ``molmcp.providers`` entry-point name. Tools still register
            bare. Clients then see two forms: ``<name>__<tool>`` on a
            focused ``molmcp serve <name>`` process, and ``<name>_<tool>``
            (FastMCP namespace) on the composed core (``create_stack`` /
            ``molmcp serve``).
        upstream: Distribution to install when the plane is unavailable, as
            it would be typed after ``pip install``. ``None`` means the plane
            needs nothing beyond molmcp.
        import_name: Module name :meth:`probe` passes to
            ``importlib.util.find_spec``. When omitted, hyphens in
            *upstream* become underscores (``molcrafts-foo`` →
            ``molcrafts_foo``). Set this when the importable module is a
            different name (``upstream='molcrafts-molq'``,
            ``import_name='molq'``).
    """

    name: ClassVar[str]
    upstream: ClassVar[str | None] = None
    import_name: ClassVar[str | None] = None

    # -- availability -------------------------------------------------

    def probe(self) -> bool:
        """Whether this plane can be served here.

        A plane whose science package is missing is a normal state, not an
        error: plane catalogs and generated client configs omit it silently.
        Only an explicit ``molmcp serve <plane>`` fails, and then loudly.

        Override when availability is not just "the package is present" —
        molvis is available whenever a caller-supplied stage factory (the
        function that builds the viewer, used by embedders and tests) has
        been injected, browser or no browser.

        Returns:
            True if this plane can be served here: no ``upstream``, or
            ``importlib.util.find_spec`` finds ``import_name`` (or
            ``upstream`` with hyphens turned into underscores). A missing
            or broken package yields False — catalogs omit the plane; only
            an explicit ``molmcp serve <plane>`` fails loudly.
        """
        module = self.import_name or (
            self.upstream.replace("-", "_") if self.upstream else None
        )
        if module is None:
            return True
        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            # A package present but broken is not one we can serve.
            return False

    def require_upstream(self) -> None:
        """Raise unless :meth:`probe` reports this plane can be served.

        The default probe means the upstream package is importable;
        overrides are honored.

        Raises:
            RuntimeError: when :meth:`probe` is false, naming the
                ``upstream`` distribution (or :attr:`name`) and
                ``pip install ...``.
        """
        if self.probe():
            return
        target = self.upstream or self.name
        raise RuntimeError(
            f"the {self.name!r} plane requires the {target!r} package. "
            f"Install with: pip install {target}"
        )

    # -- registration --------------------------------------------------

    def tool_specs(self) -> Iterator[ToolSpec]:
        """Every declared tool, base classes first, in declaration order.

        Returns:
            :class:`ToolSpec` values, base classes first, in class-body
            order. Redefining the same attribute on a subclass replaces that
            spec; two different attributes that claim one wire name are both
            yielded here and rejected later by :meth:`register`.
        """
        found: dict[str, ToolSpec] = {}
        for klass in reversed(type(self).__mro__):
            for attribute, value in vars(klass).items():
                marker = getattr(value, _MARKER, None)
                if marker is None:
                    continue
                wire_name, annotations = marker
                found[attribute] = ToolSpec(
                    name=wire_name, annotations=annotations, attribute=attribute
                )
        return iter(found.values())

    def register(self, mcp: FastMCP) -> None:
        """Attach this plane's tools to its server.

        Bound methods are handed to FastMCP directly: ``self`` is already
        applied, so it never reaches the parameter list FastMCP publishes to
        the MCP client, and the docstring the MCP client reads is the one on
        the method.

        Args:
            mcp: FastMCP server this plane attaches tools to (the server
                whose name is :attr:`name`).

        Raises:
            RuntimeError: :meth:`require_upstream` failed (``probe()`` is
                false).
            ValueError: two methods claim the same wire name. Overriding by
                *attribute* is intended — a subclass redefining a tool
                replaces it — but two distinct methods claiming one name is
                one tool shadowing another, and which survives would depend
                on method resolution order (the class's base-class chain).
        """
        self.require_upstream()
        claimed: dict[str, str] = {}
        for spec in self.tool_specs():
            previous = claimed.get(spec.name)
            if previous is not None:
                raise ValueError(
                    f"{type(self).__name__} declares the tool name "
                    f"{spec.name!r} twice: {previous}() and {spec.attribute}()"
                )
            claimed[spec.name] = spec.attribute
            mcp.tool(name=spec.name, annotations=spec.annotations)(
                getattr(self, spec.attribute)
            )


__all__ = [
    "APPEND_WRITE",
    "IDEMPOTENT_WRITE",
    "LOCAL_MUTATION",
    "MUTATION",
    "Provider",
    "ProviderBase",
    "READ_ONLY",
    "READ_REMOTE",
    "ToolSpec",
    "tool",
]
