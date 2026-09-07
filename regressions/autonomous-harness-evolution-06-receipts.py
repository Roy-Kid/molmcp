#!/usr/bin/env python3
"""Regression example: receipt redaction, dropped keys, default-off consent.

Standalone (no pytest dependency). Redacts a home path and a token, feeds
``EpisodeReceipt.from_dict`` a payload carrying both a chain of thought and
a ``pattern_key``, writes the receipt into a throwaway ``ReceiptLog`` root,
and asks ``upload_payload`` for something to send. Asserts the hard-coded
goldens below.

Hard-coded goldens (in-repo, 2026-09-07, no third-party oracle; spec
``.claude/specs/autonomous-harness-evolution-06-episode-receipt.md``,
Testing strategy -> Regression example, and acceptance AC-009):

    redact_text(f"{Path.home()}/secret ghp_abcdefghijklmnopqrstuvwxyz012345")
        == "~/secret [REDACTED]"
    Path.home().name not in redact_text(f"user={Path.home().name}"),
        which contains "[USER]"
    from_dict({... "cot": ..., "pattern_key": ...}).to_dict() carries
        neither "cot" nor "pattern_key"
    upload_payload(receipt) is None, and the JSON on disk has an
        "error_detail" with no "<!-- BEGIN"

The first two goldens run against the **real** ``Path.home()``: a
standalone script has no monkeypatch, and pinning an absolute machine path
into the repo is exactly what redaction exists to prevent. So they are
written as relationships — the home prefix collapses to ``~``, the username
is gone and ``[USER]`` stands in its place — and no real home path appears
in this file.

Public surface only: ``molmcp.evolution`` (the package facade), never
``molmcp.evolution.receipts``, plus stdlib ``json`` / ``tempfile`` /
``datetime``. Deliberately absent: ``fence_untrusted`` (this script asserts
its marker is *missing* from disk without importing it), ``load_settings``,
FastMCP, the molexp ledger, pytest, network, environment variables, and any
third-party import or subprocess at runtime.

``append`` is handed an explicit ``now`` equal to the receipt's
``created_at``. The prune that follows every write reads a clock otherwise,
and a machine whose clock sits past the 14-day TTL would delete the very
file this script then reads back.

Run directly::

    uv run python regressions/autonomous-harness-evolution-06-receipts.py

Exits 0 on success, or raises ``AssertionError`` (non-zero exit) on any
mismatch. Also collectable via
``test_autonomous_harness_evolution_06_receipts``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from molmcp.evolution import EpisodeReceipt, ReceiptLog, redact_text, upload_payload

# In-repo goldens, 2026-09-07, no third-party oracle.
_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz012345"
_EXPECTED_HOME_REDACTION = "~/secret [REDACTED]"
_USER_PLACEHOLDER = "[USER]"
_EXPECTED_RECEIPT_KEYS = (
    "version",
    "episode_id",
    "created_at",
    "outcome",
    "task",
    "error_detail",
)
_DROPPED_KEYS = ("cot", "pattern_key")
_EXPECTED_ERROR_DETAIL = "boom: token [REDACTED]"
_FENCE_MARKER = "<!-- BEGIN"

_EPISODE_ID = "episode-06-receipts"
_CREATED_AT = "2026-09-07T00:00:00+00:00"

# One payload carrying both a reasoning trace and the pattern key a later
# spec may want. Neither is a V1 field, so neither may survive from_dict.
_PAYLOAD: dict[str, object] = {
    "version": 1,
    "episode_id": _EPISODE_ID,
    "created_at": _CREATED_AT,
    "outcome": "failed",
    "task": "stage a candidate harness SHA",
    "error_detail": f"boom: token {_TOKEN}",
    "cot": "first I considered rolling back, then I ...",
    "pattern_key": "retry-on-timeout",
}


def _require(condition: bool, message: str) -> None:
    """Assert-equivalent that survives ``python -O`` and exits non-zero."""
    if not condition:
        raise AssertionError(message)


def _check_redaction() -> None:
    """Golden 1 and 2: home prefix, token, and username all disappear."""
    home_redacted = redact_text(f"{Path.home()}/secret {_TOKEN}")
    _require(
        home_redacted == _EXPECTED_HOME_REDACTION,
        f"home+token redaction {home_redacted!r} != {_EXPECTED_HOME_REDACTION!r}",
    )

    username = Path.home().name
    user_redacted = redact_text(f"user={username}")
    _require(
        username not in user_redacted,
        "redacted text still names the user (value withheld on purpose)",
    )
    _require(
        _USER_PLACEHOLDER in user_redacted,
        f"redacted text {user_redacted!r} lacks {_USER_PLACEHOLDER}",
    )

    print(f"redact_text(home/secret token) -> {home_redacted!r}")
    print(f"redact_text(user=...) -> {user_redacted!r}")


def _read_json_object(path: Path) -> dict[str, object]:
    """Return the JSON object at *path*, or raise when it is not one.

    Args:
        path: File written by :meth:`ReceiptLog.append`.

    Returns:
        The decoded document as a plain mapping.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    _require(
        isinstance(document, dict),
        f"on-disk JSON is a {type(document).__name__}, not an object",
    )
    return dict(document)


def _check_dropped_keys() -> EpisodeReceipt:
    """Golden 3: ``from_dict`` keeps V1 fields and drops the rest.

    Returns:
        The receipt built from the payload, for the consent check.
    """
    receipt = EpisodeReceipt.from_dict(_PAYLOAD)
    keys = tuple(receipt.to_dict())
    _require(
        keys == _EXPECTED_RECEIPT_KEYS,
        f"to_dict keys {keys} != {_EXPECTED_RECEIPT_KEYS}",
    )
    for dropped in _DROPPED_KEYS:
        _require(
            dropped not in receipt.to_dict(),
            f"to_dict still carries the dropped key {dropped!r}",
        )
    _require(
        receipt.error_detail == _EXPECTED_ERROR_DETAIL,
        f"error_detail {receipt.error_detail!r} != {_EXPECTED_ERROR_DETAIL!r}",
    )

    print(f"to_dict keys={list(keys)}")
    print(f"dropped={list(_DROPPED_KEYS)}")
    return receipt


def _check_consent_and_disk(receipt: EpisodeReceipt, root: Path) -> None:
    """Golden 4: no consent means no payload, and disk holds plaintext.

    Args:
        receipt: Receipt to offer and to persist.
        root: Throwaway directory the log is rooted in.
    """
    payload = upload_payload(receipt)
    _require(
        payload is None,
        f"upload_payload without consent returned {payload!r}, not None",
    )

    log = ReceiptLog(root)
    written = log.append(receipt, now=datetime.fromisoformat(_CREATED_AT))
    _require(
        written.name == f"{_EPISODE_ID}.json",
        f"receipt written to {written.name!r}, not {_EPISODE_ID}.json",
    )

    document = _read_json_object(written)
    detail = document.get("error_detail")
    _require(
        detail == _EXPECTED_ERROR_DETAIL,
        f"on-disk error_detail {detail!r} != {_EXPECTED_ERROR_DETAIL!r}",
    )
    _require(
        isinstance(detail, str) and _FENCE_MARKER not in detail,
        f"on-disk error_detail is fenced with {_FENCE_MARKER!r}",
    )
    for dropped in _DROPPED_KEYS:
        _require(
            dropped not in document,
            f"on-disk JSON still carries the dropped key {dropped!r}",
        )

    print(f"upload_payload(receipt)={payload}")
    print(f"on-disk error_detail={detail!r}")


def main() -> int:
    _check_redaction()
    receipt = _check_dropped_keys()
    with tempfile.TemporaryDirectory(prefix="molmcp-receipts-regression-") as tmp:
        _check_consent_and_disk(receipt, Path(tmp) / "receipts")

    print("\nOK: redaction, dropped keys, and default-off consent goldens match.")
    return 0


def test_autonomous_harness_evolution_06_receipts() -> None:
    """Pytest-collectable entry point; the script needs no pytest to run."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
