"""Supervisor — the parent's single owner of one worker plane's child process.

Exactly one object in the parent holds the child: it launches it, reads its
``hello``, turns each tool call into an ``invoke`` frame, and reaps it. Nothing
else touches the pipes, so "is the child alive, and who is allowed to end it?"
has one answer instead of one per caller.

Those frames are *duplex v1*: one JSON object per line (NDJSON,
newline-delimited JSON) in each direction, frozen in
:mod:`molmcp.provider_worker.protocol`.

The launch is a **path launch** — ``python -P child.py --entrypoint ... --path
...`` — never ``python -m``. A worker plane's code lives in a checkout, not in
an installed distribution, so there is no module path to name it by; ``-P``
additionally keeps the script's own directory off ``sys.path`` so nothing
sitting beside ``child.py`` can shadow the checkout that ``--path`` names.

The launch vector is the entire configuration. This module reads nothing from
the surrounding process: a setting that lives only in one shell cannot be
reported by ``molmcp config list``, and two planes started by two different
clients would silently disagree about it.

The subprocess itself is injected (``spawn=``), so the wire behaviour above can
be proved against a fake pair of streams without ever forking.
"""

from __future__ import annotations

import itertools
import logging
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol

from .protocol import ProtocolError, decode, encode_invoke, encode_shutdown

logger = logging.getLogger(__name__)

#: The script every Supervisor launches. Resolved from this module's own
#: location, so a checkout, a wheel and an editable install all find the child
#: that matches the protocol module they are about to speak.
CHILD_SCRIPT: Path = Path(__file__).with_name("child.py")

#: How long a child gets to exit on its own after being asked to. Long enough
#: for an in-flight call to finish, short enough that shutting a server down
#: does not look like a hang.
_EXIT_TIMEOUT: float = 5.0

#: How long a *terminated* child gets before it is written off. A process that
#: ignores SIGTERM this long is reported rather than waited on forever: a
#: parent blocked in teardown is worse than a leaked child it has named.
_TERMINATE_TIMEOUT: float = 5.0


class _ChildStdin(Protocol):
    """The write half of the pipe, as this module uses it."""

    def write(self, data: str, /) -> int: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


class _ChildStdout(Protocol):
    """The read half of the pipe: one NDJSON line at a time, ``""`` at EOF."""

    def readline(self) -> str: ...

    def close(self) -> None: ...


class _ChildProcess(Protocol):
    """What a spawned child must expose — a narrow slice of ``Popen``.

    Both streams are required, not optional: the only spawn this module ships
    opens them as pipes, and a seam that hands back a child it cannot talk to
    has not spawned anything useful.

    The two streams plus ``wait`` and ``terminate`` are what this module
    calls. ``poll`` — "has it exited yet, and with what status?" — is never
    called here; it is part of the seam's contract because it is how a caller
    holding the spawned object asks whether the child is still alive.
    """

    stdin: _ChildStdin
    stdout: _ChildStdout

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def poll(self) -> int | None: ...


def _default_spawn(argv: list[str]) -> _ChildProcess:
    """Launch the child for real, over text pipes.

    No ``env=`` is passed: the child inherits the parent's environment
    unchanged. Configuration travels in *argv*, which the parent can print and
    a reader can reproduce.

    Args:
        argv: The launch vector, as built by :attr:`Supervisor.argv`.

    Returns:
        The running child, line-buffered so a flushed frame arrives whole.
    """
    return subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


class Supervisor:
    """One child process serving one provider plane, over duplex v1.

    Args:
        entrypoint: The provider class the child serves, as
            ``package.module:ClassName``.
        path: Directory the child imports that module from — the checkout
            root, passed through to ``--path``.
        spawn: Seam that turns a launch vector into a running process. Defaults
            to a real ``subprocess.Popen`` over text pipes; tests pass a fake
            pair of streams so the frame handling here is proved without a
            fork.
    """

    def __init__(
        self,
        *,
        entrypoint: str,
        path: str | Path,
        spawn: Callable[[list[str]], _ChildProcess] | None = None,
    ) -> None:
        self._entrypoint = entrypoint
        self._path = path
        self._spawn = spawn if spawn is not None else _default_spawn
        self._process: _ChildProcess | None = None
        self._stopped = False
        self._call_ids: Iterator[int] = itertools.count(1)

    @property
    def argv(self) -> list[str]:
        """The launch vector, and the whole of the child's configuration.

        Returns:
            ``[sys.executable, "-P", <child.py>, "--entrypoint", ...,
            "--path", ...]``. Never contains ``-m``: the plane is a checkout
            on disk, not an installed module. Reading this property starts
            nothing.
        """
        return [
            sys.executable,
            "-P",
            str(CHILD_SCRIPT),
            "--entrypoint",
            self._entrypoint,
            "--path",
            str(self._path),
        ]

    def start(self) -> dict[str, Any]:
        """Launch the child and read the catalog it greets with.

        Returns:
            The decoded ``hello`` frame — the plane id and one entry per tool.
            It is the only tool declaration there is; the parent asks the child
            nothing else about them.

        Raises:
            RuntimeError: The first line was not a valid duplex v1 ``hello``:
                unreadable, a mismatched protocol version, or some other frame
                type. The child is shut down first — a child that cannot be
                talked to is still a process, and leaving it running to report
                a handshake failure trades one problem for two.
        """
        process = self._spawn(self.argv)
        self._process = process

        line = process.stdout.readline()
        try:
            frame = decode(line)
        except ProtocolError as exc:
            self.shutdown()
            raise RuntimeError(
                f"the worker child for {self._entrypoint!r} did not greet in "
                f"duplex v1: {exc}"
            ) from exc

        if frame["type"] != "hello":
            self.shutdown()
            raise RuntimeError(
                f"the worker child for {self._entrypoint!r} opened with a "
                f"{frame['type']!r} frame; duplex v1 opens with 'hello'"
            )
        return frame

    def invoke(self, name: str, args: Mapping[str, Any]) -> Any:
        """Call one tool in the child and wait for its answer.

        Calls are strictly one at a time: one ``invoke`` written, one frame
        read back, so the reply can only belong to the call just made. Duplex
        v1 still puts a call id on both frames. This method does not compare
        them — with a single outstanding call there is nothing to disambiguate
        — but the id is on the wire, so a recorded exchange can be paired up
        afterwards without counting lines.

        Args:
            name: Bare tool name, as it appeared in the hello catalog.
            args: Keyword arguments for the call.

        Returns:
            Whatever the tool returned, decoded from the ``result`` frame.

        Raises:
            RuntimeError: The child has not been started, has already been
                shut down, has closed the pipe, answered with an ``error``
                frame (whose text is re-raised verbatim — the two processes
                share no traceback), or answered with a frame that does not
                answer a call at all.
            ProtocolError: The reply was not a valid duplex v1 frame. It is
                itself a ``RuntimeError``, so one ``except RuntimeError``
                covers every failure listed here.
        """
        process = self._started()
        call_id = str(next(self._call_ids))
        process.stdin.write(encode_invoke(call_id=call_id, name=name, args=args))
        process.stdin.flush()

        line = process.stdout.readline()
        if not line:
            raise RuntimeError(
                f"the worker child for {self._entrypoint!r} closed the pipe "
                f"while answering {name!r}"
            )
        frame = decode(line)
        if frame["type"] == "result":
            return frame["value"]
        if frame["type"] == "error":
            raise RuntimeError(frame["error"])
        raise RuntimeError(
            f"the worker child answered {name!r} with a {frame['type']!r} "
            f"frame; duplex v1 answers an invoke with 'result' or 'error'"
        )

    def shutdown(self) -> None:
        """Ask the child to exit, then make sure it did.

        Idempotent: a second call is a no-op, so the explicit abort path and
        the server's teardown can both call it without racing to reap the same
        process twice.

        The child is *asked* first (a ``shutdown`` frame, then EOF on its
        stdin) so an in-flight call can finish; only a child that ignores both
        is terminated.
        """
        process = self._process
        if process is None or self._stopped:
            return
        self._stopped = True

        self._tell(process, encode_shutdown())
        self._close(process.stdin)
        try:
            process.wait(timeout=_EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=_TERMINATE_TIMEOUT)
            except subprocess.TimeoutExpired:
                # Named rather than waited on: teardown must finish, and a
                # child this deaf is a bug report, not a thing to block on.
                logger.warning(
                    "worker child for %r ignored terminate; giving up on it",
                    self._entrypoint,
                )
        self._close(process.stdout)

    def _started(self) -> _ChildProcess:
        """The running child.

        Returns:
            The process :meth:`start` spawned.

        Raises:
            RuntimeError: Nothing has been started, or it has already been
                reaped. Either way there is no one to talk to, and saying so
                beats an AttributeError on a ``None`` pipe.
        """
        if self._process is None:
            raise RuntimeError(
                f"the worker child for {self._entrypoint!r} has not been "
                f"started; call start() first"
            )
        if self._stopped:
            raise RuntimeError(
                f"the worker child for {self._entrypoint!r} has been shut down"
            )
        return self._process

    def _tell(self, process: _ChildProcess, line: str) -> None:
        """Write one frame to the child, tolerating a pipe that is already gone.

        Args:
            process: The child being told.
            line: One complete NDJSON line.
        """
        try:
            process.stdin.write(line)
            process.stdin.flush()
        except (OSError, ValueError):
            # A child that already exited took its pipe with it. That is the
            # outcome this frame was asking for, so it is not a failure.
            logger.debug(
                "worker child for %r closed its pipe before shutdown was sent",
                self._entrypoint,
            )

    def _close(self, stream: _ChildStdin | _ChildStdout) -> None:
        """Close one end of the pipe, tolerating one that is already closed.

        Args:
            stream: The stream to close.
        """
        try:
            stream.close()
        except (OSError, ValueError):
            logger.debug(
                "worker child for %r had already closed a stream",
                self._entrypoint,
            )
