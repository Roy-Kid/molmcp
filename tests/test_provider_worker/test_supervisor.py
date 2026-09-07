"""Supervisor — the one owner of the child process, driven through a fake spawn.

Every test injects ``spawn=``: no real ``subprocess.Popen`` is created here, so
the module is proved in isolation from ``child.py``. The fake process is a
Popen stand-in — text ``stdin`` / ``stdout`` plus ``wait`` / ``terminate`` /
``poll`` — and it records what the Supervisor did to it.

The wire lines are hard-coded duplex v1 text rather than ``protocol`` encoder
output: a Supervisor that agrees with a broken encoder is still broken.
"""

from __future__ import annotations

import ast
import io
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from molmcp.provider_worker import supervisor as supervisor_module

_FIXTURES = Path(__file__).parent / "fixtures"
_ENTRYPOINT = "echo:EchoProvider"

#: A duplex v1 greeting, written out by hand.
_HELLO_LINE = '{"type": "hello", "protocol": 1, "name": "echo", "tools": []}\n'

#: The same greeting from a child speaking a protocol this parent does not
#: know. Version mismatch is a hard failure, never a silent downgrade.
_STALE_HELLO_LINE = '{"type": "hello", "protocol": 0, "name": "echo", "tools": []}\n'

#: A queued child line: fixed text, or a callable resolved when it is read
#: (so a reply can echo back the call id the Supervisor just wrote).
Reply = str | Callable[[], str]


class _RecordingStdin(io.StringIO):
    """Child stdin that keeps every chunk written to it, even once closed."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []
        self.flushes = 0

    def write(self, s: str) -> int:
        written = super().write(s)
        self.writes.append(s)
        return written

    def flush(self) -> None:
        super().flush()
        self.flushes += 1

    @property
    def lines(self) -> list[str]:
        """Every complete NDJSON line the Supervisor sent, in order."""
        return "".join(self.writes).splitlines()


class _ReplyStream:
    """Child stdout: one queued line per ``readline``, then EOF."""

    def __init__(self, replies: list[Reply]) -> None:
        self._replies: list[Reply] = list(replies)

    def queue(self, reply: Reply) -> None:
        """Make one more line available to the next ``readline``."""
        self._replies.append(reply)

    def readline(self) -> str:
        if not self._replies:
            return ""
        reply = self._replies.pop(0)
        return reply() if callable(reply) else reply

    def close(self) -> None:
        self._replies.clear()


class _FakeProcess:
    """A ``Popen`` stand-in that records its own lifecycle calls."""

    def __init__(self, replies: list[Reply], *, wait_times_out: bool = False) -> None:
        self.stdin = _RecordingStdin()
        self.stdout = _ReplyStream(replies)
        self.wait_calls: list[float | None] = []
        self.terminate_calls = 0
        self.wait_times_out = wait_times_out
        self.returncode: int | None = None

    def queue(self, reply: Reply) -> None:
        """Queue one more line for the Supervisor to read."""
        self.stdout.queue(reply)

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        # A terminated child is reaped; only the first wait can hang.
        if self.wait_times_out and not self.terminate_calls:
            raise subprocess.TimeoutExpired(cmd="child.py", timeout=timeout or 0.0)
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15

    def poll(self) -> int | None:
        return self.returncode


class _SpawnRecorder:
    """The ``spawn`` seam: records each argv, hands back one prepared process."""

    def __init__(self, process: _FakeProcess) -> None:
        self.process = process
        self.argvs: list[list[str]] = []

    def __call__(self, argv: list[str]) -> _FakeProcess:
        self.argvs.append(list(argv))
        return self.process


def _sent(process: _FakeProcess) -> list[dict[str, object]]:
    """Every frame the Supervisor wrote to the child, decoded."""
    return [json.loads(line) for line in process.stdin.lines]


def _sent_of_type(process: _FakeProcess, kind: str) -> list[dict[str, object]]:
    return [frame for frame in _sent(process) if frame.get("type") == kind]


def _answer(process: _FakeProcess, **payload: object) -> str:
    """A child reply to the frame just written, echoing its call id back."""
    last = _sent(process)[-1]
    return json.dumps({"protocol": 1, "id": last["id"], **payload}) + "\n"


def _reads_environment(tree: ast.AST) -> bool:
    """True if the module reads ``os.environ`` or ``os.getenv`` anywhere."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                return True
        if isinstance(node, ast.Name) and node.id == "getenv":
            return True
    return False


class TestSupervisor:
    """One Supervisor concern per test; the child is always a fake."""

    def test_argv_is_a_path_launch_of_the_child_script(self) -> None:
        spawn = _SpawnRecorder(_FakeProcess([_HELLO_LINE]))
        supervisor = supervisor_module.Supervisor(
            entrypoint=_ENTRYPOINT, path=_FIXTURES, spawn=spawn
        )

        assert supervisor.argv == [
            sys.executable,
            "-P",
            str(supervisor_module.CHILD_SCRIPT),
            "--entrypoint",
            _ENTRYPOINT,
            "--path",
            str(_FIXTURES),
        ]
        assert "-m" not in supervisor.argv
        # Reading argv must not start anything.
        assert spawn.argvs == []

    def test_child_script_is_a_file_on_disk(self) -> None:
        assert supervisor_module.CHILD_SCRIPT.name == "child.py"
        assert supervisor_module.CHILD_SCRIPT.is_file()

    def test_start_returns_the_decoded_hello_frame(self) -> None:
        process = _FakeProcess([_HELLO_LINE])
        spawn = _SpawnRecorder(process)
        supervisor = supervisor_module.Supervisor(
            entrypoint=_ENTRYPOINT, path=_FIXTURES, spawn=spawn
        )

        hello = supervisor.start()

        assert spawn.argvs == [supervisor.argv]
        assert hello == {
            "type": "hello",
            "protocol": 1,
            "name": "echo",
            "tools": [],
        }

    def test_invoke_writes_one_frame_and_returns_the_result_value(self) -> None:
        process = _FakeProcess([_HELLO_LINE])
        supervisor = supervisor_module.Supervisor(
            entrypoint=_ENTRYPOINT,
            path=_FIXTURES,
            spawn=_SpawnRecorder(process),
        )
        supervisor.start()
        process.queue(
            lambda: _answer(process, type="result", ok=True, value={"text": "ping"})
        )

        value = supervisor.invoke("echo", {"text": "ping"})

        assert value == {"text": "ping"}
        invokes = _sent_of_type(process, "invoke")
        assert len(invokes) == 1
        assert invokes[0]["protocol"] == 1
        assert invokes[0]["name"] == "echo"
        assert invokes[0]["args"] == {"text": "ping"}
        assert invokes[0]["id"]
        assert process.stdin.writes[-1].endswith("\n")
        assert process.stdin.flushes >= 1

    def test_invoke_raises_runtime_error_carrying_the_error_text(self) -> None:
        process = _FakeProcess([_HELLO_LINE])
        supervisor = supervisor_module.Supervisor(
            entrypoint=_ENTRYPOINT,
            path=_FIXTURES,
            spawn=_SpawnRecorder(process),
        )
        supervisor.start()
        process.queue(
            lambda: _answer(
                process, type="error", ok=False, error="ValueError: no text"
            )
        )

        with pytest.raises(RuntimeError, match="ValueError: no text"):
            supervisor.invoke("echo", {})

    def test_shutdown_is_idempotent(self) -> None:
        process = _FakeProcess([_HELLO_LINE])
        supervisor = supervisor_module.Supervisor(
            entrypoint=_ENTRYPOINT,
            path=_FIXTURES,
            spawn=_SpawnRecorder(process),
        )
        supervisor.start()

        supervisor.shutdown()
        supervisor.shutdown()

        assert len(_sent_of_type(process, "shutdown")) <= 1
        assert process.wait_calls
        assert process.terminate_calls == 0
        assert process.poll() is not None

    def test_shutdown_terminates_a_child_that_will_not_exit(self) -> None:
        process = _FakeProcess([_HELLO_LINE], wait_times_out=True)
        supervisor = supervisor_module.Supervisor(
            entrypoint=_ENTRYPOINT,
            path=_FIXTURES,
            spawn=_SpawnRecorder(process),
        )
        supervisor.start()

        supervisor.shutdown()

        assert process.wait_calls
        assert process.terminate_calls >= 1

    def test_a_stale_protocol_hello_reaps_the_child_and_raises(self) -> None:
        process = _FakeProcess([_STALE_HELLO_LINE])
        supervisor = supervisor_module.Supervisor(
            entrypoint=_ENTRYPOINT,
            path=_FIXTURES,
            spawn=_SpawnRecorder(process),
        )

        with pytest.raises(RuntimeError):
            supervisor.start()

        reaped = bool(_sent_of_type(process, "shutdown")) or (
            process.terminate_calls > 0
        )
        assert reaped, "start() must shut the child down before it raises"

    def test_supervisor_never_reads_the_environment(self) -> None:
        source = Path(supervisor_module.__file__).read_text(encoding="utf-8")

        assert not _reads_environment(ast.parse(source))
