"""Immutable SHA-keyed store of published git archives.

``publish`` is the only write path. Each SHA is one directory under
``<root>/commits/<sha>/``, swapped in with a single ``os.replace`` of
the whole directory (``metadata.json`` plus flattened ``tree/``).
Incomplete directories are not hits. The store does not write pointer
files and does not create ``refs/`` or ``pointers/``.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from .git import GitTransport, extract_git_archive

_RESERVED_SHA_KEYS = frozenset({".", "..", "refs", "pointers", "hints"})


class StoreError(Exception):
    """Base error for :class:`ImmutableGitStore` operations."""


class UnknownShaError(StoreError):
    """Raised when a SHA has no complete published directory.

    Complete means ``metadata.json`` is a file and ``tree/`` is a
    directory. Missing or incomplete SHAs fail at the store boundary.
    """


class ShaConflictError(StoreError):
    """Raised when ``publish`` would change a SHA's owner or repo.

    Provenance is the ``owner`` and ``repo`` kwargs stored in
    ``metadata.json``. The existing tree is left unchanged.
    """


class ImmutableGitStore:
    """Disk store that publishes one complete SHA directory at a time.

    A *SHA directory* is ``<root>/commits/<sha>/`` containing
    ``metadata.json`` (provenance ``owner`` / ``repo``) and ``tree/``
    (the catalog root). The *inner tree* is the single top-level
    directory inside the commit tarball returned by
    :func:`extract_git_archive`. *Flatten* means relocating that
    directory to ``tree/`` so ``harness.toml`` is a direct child of
    :meth:`tree_path` and ``tree/<repo>-<sha>/`` does not exist.

    Construct with :meth:`__init__`. ``publish`` fetches, flattens, and
    ``os.replace``s the whole SHA directory.

    Args:
        root: Store directory. Created as needed when publishing.
        transport: :class:`GitTransport` supplying ``fetch_archive``.

    Raises:
        TypeError: ``root`` or ``transport`` is ``None``.
    """

    def __init__(self, root: Path | str, transport: GitTransport) -> None:
        """Store ``root`` as a :class:`~pathlib.Path` and the transport.

        Args:
            root: Store directory (string or path).
            transport: Git archive transport.

        Raises:
            TypeError: ``root`` or ``transport`` is ``None``.
        """
        if root is None or transport is None:
            raise TypeError("root and transport are required")
        self._root = Path(root)
        self._transport = transport

    def has(self, sha: str) -> bool:
        """Return whether ``sha`` has a complete published directory.

        Args:
            sha: Commit SHA used as the directory key.

        Returns:
            ``True`` only when ``metadata.json`` is a file and ``tree/``
            is a directory under ``commits/<sha>/``.
        """
        sha_dir = self._sha_dir(sha)
        return (sha_dir / "metadata.json").is_file() and (sha_dir / "tree").is_dir()

    def tree_path(self, sha: str) -> Path:
        """Return the flattened catalog root for a complete SHA.

        Args:
            sha: Commit SHA used as the directory key.

        Returns:
            Path of ``commits/<sha>/tree/``.

        Raises:
            UnknownShaError: ``sha`` is missing or incomplete.
        """
        if not self.has(sha):
            raise UnknownShaError(sha)
        return self._sha_dir(sha) / "tree"

    def publish(self, sha: str, *, owner: str, repo: str) -> Path:
        """Fetch, flatten, and atomically install ``sha`` if needed.

        A complete directory with the same ``owner`` and ``repo`` is a
        no-op (no fetch, no tree replace). A complete directory with a
        different provenance raises :class:`ShaConflictError` and does
        not replace the tree. Missing or incomplete directories are
        fetched, assembled in a temp directory under ``commits/``, and
        installed with one ``os.replace`` of the whole SHA directory.

        Args:
            sha: Commit SHA to publish (directory key).
            owner: Provenance owner written to ``metadata.json``.
            repo: Provenance repository written to ``metadata.json``.

        Returns:
            Catalog root (``tree/``) for ``sha``.

        Raises:
            ShaConflictError: ``sha`` is already published under a
                different owner or repo.
            StoreError: ``sha`` is not a usable directory key.
        """
        if self.has(sha):
            payload = self._read_metadata(sha)
            if payload.get("owner") == owner and payload.get("repo") == repo:
                return self.tree_path(sha)
            raise ShaConflictError(
                f"{sha} already published as "
                f"{payload.get('owner')}/{payload.get('repo')}"
            )

        commits = self._root / "commits"
        commits.mkdir(parents=True, exist_ok=True)
        dest = self._sha_dir(sha)
        tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=commits))
        try:
            staging = tmp / "sha"
            unpack = tmp / "unpack"
            staging.mkdir()
            unpack.mkdir()
            data = self._transport.fetch_archive(owner, repo, sha)
            inner = extract_git_archive(data, unpack)
            os.replace(inner, staging / "tree")
            (staging / "metadata.json").write_text(
                json.dumps({"sha": sha, "owner": owner, "repo": repo}),
                encoding="utf-8",
            )
            if dest.exists():
                aside = Path(tempfile.mkdtemp(prefix=".tmp-old-", dir=commits))
                try:
                    os.replace(dest, aside / "sha")
                    os.replace(staging, dest)
                finally:
                    shutil.rmtree(aside, ignore_errors=True)
            else:
                os.replace(staging, dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return self.tree_path(sha)

    def _sha_dir(self, sha: str) -> Path:
        if (
            not sha
            or sha in _RESERVED_SHA_KEYS
            or Path(sha).is_absolute()
            or os.sep in sha
            or "/" in sha
            or "\\" in sha
            or (os.altsep is not None and os.altsep in sha)
        ):
            raise StoreError(f"invalid sha {sha!r}")
        return self._root / "commits" / sha

    def _read_metadata(self, sha: str) -> dict[str, object]:
        path = self._sha_dir(sha) / "metadata.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise StoreError(f"metadata for {sha} is not an object")
        return payload
