"""Activation pointer: current, previous, and staged SHA on disk.

The only public constructor is :meth:`Activation.bind`. Each mutation
reloads the frozen record from the pointer file, writes a new record
atomically, then refreshes the instance properties from what was
written. Catalog eligibility is checked on ``stage``; this module does
not own a capability universe.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .catalog import CatalogError, load_harness_catalog
from .store import ImmutableGitStore

#: Version field written into the pointer JSON; unknown values raise
#: ActivationVersionError.
ACTIVATION_VERSION = 1
_POINTER_KEYS = frozenset({"version", "active", "staging", "previous"})


class ActivationError(Exception):
    """Base error for :class:`Activation` operations."""


class ActivationVersionError(ActivationError):
    """Raised when the pointer file is not a version-1 activation record.

    Unknown ``version``, extra or missing fields, and invalid JSON all
    fail here. A missing file is not an error; it is an empty record.
    """


class IneligibleShaError(ActivationError):
    """Raised when ``stage`` cannot accept a SHA.

    ``stage`` maps two failures onto this type and does not raise
    :class:`~molmcp.components.store.UnknownShaError`: (1)
    ``store.has(sha)`` is false (no complete SHA directory); (2)
    :func:`load_harness_catalog` raises :class:`CatalogError` (invalid
    ``harness.toml``, or a ``requires`` token this process cannot
    honor). The pointer file is left unchanged.
    """


class NothingStagedError(ActivationError):
    """Raised when ``promote`` runs with no staged SHA."""


class NothingToRollbackError(ActivationError):
    """Raised when ``rollback`` runs with no previous SHA."""


@dataclass(frozen=True, slots=True)
class _ActivationRecord:
    current: str | None
    previous: str | None
    staged: str | None


def _empty_record() -> _ActivationRecord:
    return _ActivationRecord(current=None, previous=None, staged=None)


def _optional_sha(value: object, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ActivationVersionError(f"pointer field {field!r} must be a string or null")


def _record_from_payload(payload: object) -> _ActivationRecord:
    if not isinstance(payload, dict):
        raise ActivationVersionError("activation pointer is not an object")
    if set(payload) != _POINTER_KEYS:
        raise ActivationVersionError("activation pointer has unknown or missing fields")
    if payload["version"] != ACTIVATION_VERSION:
        raise ActivationVersionError(
            f"unsupported activation version {payload['version']!r}"
        )
    return _ActivationRecord(
        current=_optional_sha(payload["active"], "active"),
        previous=_optional_sha(payload["previous"], "previous"),
        staged=_optional_sha(payload["staging"], "staging"),
    )


def _load_record(path: Path) -> _ActivationRecord:
    if not path.is_file():
        return _empty_record()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ActivationVersionError("activation pointer is not valid JSON") from exc
    return _record_from_payload(payload)


def _write_record(path: Path, record: _ActivationRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.partial")
    payload = {
        "version": ACTIVATION_VERSION,
        "active": record.current,
        "staging": record.staged,
        "previous": record.previous,
    }
    temp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temp, path)


class Activation:
    """Read-only view of the activation pointer, mutated only via methods.

    Construct only via :meth:`bind`; ``Activation(...)`` raises
    ``TypeError``. The pointer is a JSON file of three SHA names, not a
    copy of the trees:

    * *current* (JSON ``active``): SHA now in effect
    * *staged* (JSON ``staging``): SHA that passed ``stage`` and is
      waiting for ``promote``; does not change current
    * *previous* (JSON ``previous``): SHA ``rollback`` would restore
      (one-level; a new promote overwrites it)

    ``stage`` sets staged only. ``promote`` does staged→current,
    current→previous, staged=None. ``rollback`` does previous→current,
    previous=None, staged unchanged.
    """

    __slots__ = ("_path", "_record", "_store", "_supported_capabilities")

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        """Always raises ``TypeError``; use :meth:`bind`.

        Raises:
            TypeError: every call.
        """
        raise TypeError("use Activation.bind")

    @classmethod
    def bind(
        cls,
        path: Path | str,
        *,
        store: ImmutableGitStore,
        supported_capabilities: frozenset[str],
    ) -> Activation:
        """Only constructor; attach this instance to a pointer path.

        Does not write the file. A missing file becomes an in-memory
        empty record (``current`` / ``previous`` / ``staged`` all
        ``None``) and is not created.

        Args:
            path: Pointer file path.
            store: Published SHA store. Keyword-only; ``None`` is
                refused.
            supported_capabilities: Tokens this process can honor,
                passed through to :func:`load_harness_catalog` on
                ``stage``. Keyword-only; no default; ``None`` is
                refused.

        Returns:
            An :class:`Activation` whose properties match the file, or
            all ``None`` when the file is missing.

        Raises:
            TypeError: ``store`` or ``supported_capabilities`` is
                ``None``.
            ActivationVersionError: The file exists but is not a
                version-1 pointer record.
        """
        if store is None or supported_capabilities is None:
            raise TypeError("store and supported_capabilities are required")
        resolved = Path(path)
        return cls._from_record(
            resolved,
            store=store,
            supported_capabilities=supported_capabilities,
            record=_load_record(resolved),
        )

    @classmethod
    def _from_record(
        cls,
        path: Path,
        *,
        store: ImmutableGitStore,
        supported_capabilities: frozenset[str],
        record: _ActivationRecord,
    ) -> Activation:
        instance = object.__new__(cls)
        instance._path = path
        instance._store = store
        instance._supported_capabilities = supported_capabilities
        instance._record = record
        return instance

    @property
    def current(self) -> str | None:
        """SHA currently activated, or ``None``."""
        return self._record.current

    @property
    def previous(self) -> str | None:
        """SHA that ``rollback`` would restore, or ``None``."""
        return self._record.previous

    @property
    def staged(self) -> str | None:
        """SHA waiting for ``promote``, or ``None``."""
        return self._record.staged

    def stage(self, sha: str) -> None:
        """Mark ``sha`` as staged after catalog eligibility succeeds.

        Reloads the pointer from disk first. Loads
        ``{tree_path(sha)}/harness.toml`` via :func:`load_harness_catalog`
        (language gate, then every ``requires`` token ⊆
        ``supported_capabilities``). A new ``stage`` replaces any
        already-staged SHA and leaves current/previous unchanged.

        Args:
            sha: Commit SHA to stage. Must be a complete published tree.

        Raises:
            IneligibleShaError: ``store.has(sha)`` is false, or
                :func:`load_harness_catalog` raises
                :class:`CatalogError`. The pointer file is not written.
        """
        record = _load_record(self._path)
        if not self._store.has(sha):
            raise IneligibleShaError(sha)
        try:
            load_harness_catalog(
                self._store.tree_path(sha),
                sha,
                self._supported_capabilities,
            )
        except CatalogError as exc:
            raise IneligibleShaError(sha) from exc
        written = _ActivationRecord(
            current=record.current,
            previous=record.previous,
            staged=sha,
        )
        _write_record(self._path, written)
        self._record = written

    def promote(self) -> None:
        """Move staged to current; the old current becomes previous.

        Reloads the pointer from disk first. ``staged`` is cleared.
        The previous previous is discarded; a second ``rollback`` then
        has nothing to restore.

        Raises:
            NothingStagedError: Reloaded ``staged`` is ``None``.
        """
        record = _load_record(self._path)
        if record.staged is None:
            raise NothingStagedError("nothing staged")
        written = _ActivationRecord(
            current=record.staged,
            previous=record.current,
            staged=None,
        )
        _write_record(self._path, written)
        self._record = written

    def rollback(self) -> None:
        """Restore previous as current and clear previous.

        Reloads the pointer from disk first. ``staged`` is unchanged.

        Raises:
            NothingToRollbackError: Reloaded ``previous`` is ``None``.
        """
        record = _load_record(self._path)
        if record.previous is None:
            raise NothingToRollbackError("nothing to rollback")
        written = _ActivationRecord(
            current=record.previous,
            previous=None,
            staged=record.staged,
        )
        _write_record(self._path, written)
        self._record = written
