"""WorkerProvider — a plane served from a checkout, in a process of its own.

This is a :class:`~molmcp.provider.Provider`: a ``name`` and a ``register``,
nothing the protocol does not already have. What is unusual is where the tools
come from. Instead of importing the plane, ``register`` starts a child process
for it, reads the catalog it greets with, and publishes proxies. The plane's
code — which may be an arbitrary checkout — never enters this interpreter, so a
plane that fails to import, or imports something incompatible, costs a child
process rather than the server.

Teardown belongs to the server, not to whoever built the adapter. A *lifespan*
is the async context manager a server runs around its whole serving life:
everything before its ``yield`` is startup, everything after is shutdown. Once
``register`` has succeeded, this adapter wraps that context manager, so leaving
the server's lifespan reaps the child in the same place every other server
resource is released. The callable being wrapped is the one a caller passes as
``FastMCP(lifespan=...)``; FastMCP 4 keeps it in the private ``_lifespan``
attribute and enters it inside ``_lifespan_manager``, so ``_lifespan`` is the
attribute this adapter reads and replaces.

A ``FastMCP`` instance *does* also have a public ``lifespan``, inherited from
FastMCP's ``AggregateProvider``: an async context manager that takes no server
argument and combines the lifespans of the providers mounted on that server.
It is a different object with a different signature, and this adapter never
reads it — wrapping it would hang one plane's teardown off the aggregation of
every mounted plane.

Two smaller exits back that lifespan up, and neither replaces it.
:meth:`shutdown` is the *explicit abort*, for the failure path and for a caller
who is done with a plane before the server is. A ``weakref.finalize`` is the last
resort for a server that is dropped without its lifespan ever being entered.
There is no separate ``atexit`` hook: a ``weakref.finalize`` already runs at
interpreter exit as well as on collection, so a second hook would only add a
second reaper to reason about, and :meth:`shutdown` being idempotent means
whichever of them fires first is the only one that does any work.

Failure before ``register`` returns is the adapter's own to clean up: a
``register`` that raises leaves no child behind and leaves the server's
``_lifespan`` exactly as it found it, because a server that never gained this
plane must not owe it a teardown.
"""

from __future__ import annotations

import weakref
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from molmcp.provider import PROVIDER_NAME_PATTERN

from .proxy import bind_tools
from .supervisor import Supervisor

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastmcp import FastMCP


class WorkerProvider:
    """One provider plane, loaded from a checkout in a child process.

    Args:
        name: Plane id this adapter answers to, and the name the child must
            greet with. Must satisfy the same pattern every other plane does.
        entrypoint: The provider class the child constructs, as
            ``package.module:ClassName``.
        path: Directory that class is imported from — the checkout root.

    Attributes:
        name: Plane id and MCP server name. Tools still register bare; a
            composed core adds the namespace when it mounts the plane.

    Raises:
        ValueError: *name* is not a valid plane id. A plane id becomes a
            server name and a tool prefix, so it is checked where it is given
            rather than where a client finally trips over it.
    """

    def __init__(self, *, name: str, entrypoint: str, path: str | Path) -> None:
        if PROVIDER_NAME_PATTERN.match(name) is None:
            raise ValueError(
                f"{name!r} is not a valid plane id; expected a match for "
                f"{PROVIDER_NAME_PATTERN.pattern}"
            )
        self.name = name
        self._entrypoint = entrypoint
        self._path = path
        self._supervisor: Supervisor | None = None

    def probe(self) -> bool:
        """Whether the checkout this plane is served from is present.

        Availability is a question about the filesystem, so it is answered
        from the filesystem: no child is started, because catalogs and client
        configs ask this of every plane and must not pay a process each time.

        Returns:
            True when ``path`` is a directory.
        """
        return Path(self._path).is_dir()

    def register(self, mcp: FastMCP) -> None:
        """Start the child, publish its tools, and take over teardown.

        The order is the contract. Nothing is published until the child has
        greeted as the plane this adapter was built for, and the server's
        lifespan is not touched until publishing has succeeded — so a failure
        anywhere before that leaves no child running and leaves the server
        owing this adapter no teardown.

        Args:
            mcp: FastMCP server for this plane.

        Raises:
            RuntimeError: The checkout is not there. A missing checkout is a
                missing *directory*, not a missing wheel, so the message names
                the path and the entrypoint and sends nobody to a package
                index. Also raised when the child does start but the handshake
                does not hold up: an unreadable first line, a protocol version
                this side does not speak, an opening frame that is not a
                ``hello``, or a catalog entry the proxy cannot rebuild a
                signature from (a ``ProtocolError``, which is itself a
                ``RuntimeError``).
            ValueError: The child greeted as a different plane. Publishing its
                tools here would attach one plane's tools to another's server,
                under a namespace that then lies about where they came from.
        """
        if not self.probe():
            raise RuntimeError(
                f"the {self.name!r} plane is served from a checkout that is "
                f"not there: {self._path} (entrypoint {self._entrypoint!r}). "
                f"Point path= at the directory that module is imported from."
            )

        supervisor = Supervisor(entrypoint=self._entrypoint, path=self._path)
        self._supervisor = supervisor
        try:
            hello = supervisor.start()
            greeting = hello["name"]
            if greeting != self.name:
                raise ValueError(
                    f"the child for {self._entrypoint!r} greeted as the "
                    f"{greeting!r} plane, but this adapter serves "
                    f"{self.name!r}"
                )
            bind_tools(mcp, hello, supervisor.invoke)
        except BaseException:
            # The lifespan swap has not happened, so the server owes this
            # adapter no teardown, and the child is the only live resource
            # the attempt created. Tool names bound before a mid-publish
            # failure do stay on the server, but they proxy to a child that
            # is reaped here, so calling one raises rather than hanging.
            self.shutdown()
            raise

        # The callable a caller passed as ``FastMCP(lifespan=...)``. FastMCP 4
        # always sets ``_lifespan`` — a server built without one gets
        # ``fastmcp.server.server.default_lifespan`` — so in practice this is
        # never None. The ``getattr`` default and the None branch below are
        # defensive: a stub server, or a FastMCP that stopped setting the
        # attribute, degrades to "still reap the child" rather than to an
        # AttributeError raised out of register().
        previous = getattr(mcp, "_lifespan", None)

        @asynccontextmanager
        async def wrapped(server: FastMCP) -> AsyncIterator[Any]:
            """Run the server's own lifespan, then reap this plane's child.

            Args:
                server: The FastMCP server being started.

            Yields:
                Whatever the previous lifespan yielded — that value is the
                server's application state, and swallowing it would silently
                take it away from every other user of the server.
            """
            if previous is None:
                try:
                    yield {}
                finally:
                    self.shutdown()
            else:
                async with previous(server) as value:
                    try:
                        yield value
                    finally:
                        self.shutdown()

        mcp._lifespan = wrapped
        weakref.finalize(mcp, self.shutdown)

    def shutdown(self) -> None:
        """Abort this plane's child now, without waiting for the lifespan.

        This is the explicit abort — the failure path, and a caller done with
        a plane early. It is not how a registered plane is normally torn down;
        that is the lifespan this adapter wrapped in :meth:`register`.

        Idempotent, and safe before ``register`` has ever run.
        """
        supervisor = self._supervisor
        if supervisor is None:
            return
        supervisor.shutdown()
