"""Safe subprocess wrapper for downstream Provider implementations.

Downstream packages that wrap CLI tools (LAMMPS, Packmol, Antechamber, ...)
should use ``run_safe`` instead of calling ``subprocess.run`` directly:

* ``cmd`` is *always* a list — never a string.
* ``shell=True`` is unreachable.
* A timeout is mandatory.
* ``cwd`` is resolved and validated.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SubprocessResult:
    returncode: int
    stdout: str
    stderr: str
    truncated: bool


def run_safe(
    cmd: list[str],
    *,
    cwd: str | Path,
    timeout: float,
    env: dict[str, str] | None = None,
    max_output_bytes: int = 1_000_000,
) -> SubprocessResult:
    """Run ``cmd`` in ``cwd`` with a hard timeout, capturing bounded output.

    Args:
        cmd: Command and arguments as a list. Strings are rejected.
        cwd: Working directory; must exist.
        timeout: Hard wall-clock timeout in seconds.
        env: Environment overlay (merged onto current env, not replacing).
        max_output_bytes: Truncate stdout/stderr beyond this size each.

    Raises:
        TypeError: if ``cmd`` is not a list of strings.
        FileNotFoundError: if ``cwd`` does not exist.
        subprocess.TimeoutExpired: if the process exceeds ``timeout``.
    """
    if not isinstance(cmd, list) or not all(isinstance(a, str) for a in cmd):
        raise TypeError("cmd must be list[str], not a shell string")
    cwd_path = Path(cwd)
    if not cwd_path.is_dir():
        raise FileNotFoundError(f"cwd does not exist: {cwd_path}")

    full_env = None
    if env is not None:
        import os

        full_env = {**os.environ, **env}

    proc = subprocess.run(
        cmd,
        cwd=str(cwd_path),
        env=full_env,
        timeout=timeout,
        capture_output=True,
        shell=False,
        check=False,
    )
    out = proc.stdout or b""
    err = proc.stderr or b""
    truncated = len(out) > max_output_bytes or len(err) > max_output_bytes
    return SubprocessResult(
        returncode=proc.returncode,
        stdout=out[:max_output_bytes].decode(errors="replace"),
        stderr=err[:max_output_bytes].decode(errors="replace"),
        truncated=truncated,
    )
