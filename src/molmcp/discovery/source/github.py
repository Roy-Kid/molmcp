"""GitHub source resolution.

Turn a ``github:owner/repo[@ref]`` spec into an immutable snapshot.
A *ref* is a branch name, tag, or SHA (Secure Hash Algorithm hex
digest, the git commit id). The snapshot id is ``github:commit:<sha>``,
not the SHA alone.

Network access is a :class:`~molmcp.components.git.GitTransport`
(default :class:`~molmcp.components.git.GitHubTransport`), built only
by :func:`_transport` from ``config.github_token`` (optional GitHub
personal access token, PAT). This module parses the spec, extracts the
*inner tree* (the single top-level directory inside the commit tarball)
into ``SnapshotCache.raw_dir`` (``<cache_dir>/snapshots/<slug>/raw/``),
records that path in a ``.extracted`` marker, and maps
:class:`~molmcp.components.git.GitError` to :class:`SourceError`.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from molmcp.components.git import (
    GitError,
    GitHubTransport,
    GitTransport,
    extract_git_archive,
)

from ..config import DiscoveryConfig
from .resolver import Snapshot, SnapshotId, SourceError
from .walk import walk_files


def _parse_github_spec(spec: str) -> tuple[str, str, str | None]:
    """Parse ``github:owner/repo[@ref]`` into ``(owner, repo, ref)``."""
    body = spec[len("github:") :] if spec.startswith("github:") else spec
    ref: str | None = None
    if "@" in body:
        body, ref = body.rsplit("@", 1)
    parts = [p for p in body.strip("/").split("/") if p]
    if len(parts) != 2:
        raise SourceError(
            f"invalid github spec {spec!r} (expected github:owner/repo[@ref])"
        )
    return parts[0], parts[1], (ref or None)


def _transport(config: DiscoveryConfig) -> GitTransport:
    """Build the :class:`GitTransport` for this config (PAT from ``github_token``)."""
    return GitHubTransport(token=config.github_token)


def latest_commit(spec: str, config: DiscoveryConfig) -> str:
    """Return the current commit SHA a ``github:`` spec points at.

    A SHA (Secure Hash Algorithm) here is the hex digest GitHub uses as a
    commit id. This call only resolves the ref; it does not download the
    archive. Network access goes through :func:`_transport`.

    Args:
        spec: ``github:owner/repo[@ref]``. A *ref* is a branch name, tag, or
            SHA; omitted means the repository default branch.
        config: Discovery settings. ``github_token`` is the optional GitHub
            personal access token (PAT) handed to the transport.

    Returns:
        Commit SHA as a hex digest.

    Raises:
        SourceError: Invalid spec, or a :class:`~molmcp.components.git.GitError`
            from the transport (mapped with ``raise SourceError(str(exc))
            from exc``).
    """
    owner, repo, ref = _parse_github_spec(spec)
    transport = _transport(config)
    try:
        return transport.resolve_commit(owner, repo, ref)
    except GitError as exc:
        raise SourceError(str(exc)) from exc


def _ensure_source(
    owner: str, repo: str, sha: str, raw_dir: Path, transport: GitTransport
) -> Path:
    """Return the inner-tree root, fetching the archive only when needed.

    ``raw_dir / ".extracted"`` is a marker file holding the inner-tree
    absolute path. If that file exists and the path is a directory, return
    it. Otherwise delete ``raw_dir``, download via ``transport.fetch_archive``,
    extract with :func:`extract_git_archive`, and rewrite the marker.
    """
    marker = raw_dir / ".extracted"
    if marker.is_file():
        root = Path(marker.read_text(encoding="utf-8").strip())
        if root.is_dir():
            return root
    if raw_dir.exists():
        shutil.rmtree(raw_dir, ignore_errors=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    data = transport.fetch_archive(owner, repo, sha)
    root = extract_git_archive(data, raw_dir)
    marker.write_text(str(root), encoding="utf-8")
    return root


def resolve_github(spec: str, config: DiscoveryConfig) -> Snapshot:
    """Resolve a ``github:owner/repo[@ref]`` spec to an immutable snapshot.

    Resolves the ref to a commit SHA via :func:`_transport`, then places
    that commit's files under ``SnapshotCache.raw_dir`` — the per-snapshot
    directory ``<cache_dir>/snapshots/<slug>/raw/``. GitHub tarballs wrap
    the repo in one top-level folder (for example ``owner-repo-sha/``);
    that folder is the *inner tree* and becomes ``Snapshot.root_dir``, not
    ``raw/`` itself. A ``.extracted`` marker file inside ``raw/`` stores
    the inner-tree absolute path so a later call can skip the download.

    Args:
        spec: ``github:owner/repo[@ref]``. A *ref* is a branch name, tag, or
            SHA; omitted means the repository default branch.
        config: Discovery settings, including ``cache_dir`` and optional
            ``github_token`` (GitHub PAT).

    Returns:
        Snapshot whose ``snapshot_id`` is ``github:commit:<sha>``,
        ``commit`` is that SHA, and ``root_dir`` is the inner tree.

    Raises:
        SourceError: Invalid spec, or a :class:`~molmcp.components.git.GitError`
            from resolve/fetch/extract (mapped with ``from exc``).
    """
    from ..cache.snapshotcache import SnapshotCache

    owner, repo, ref = _parse_github_spec(spec)
    transport = _transport(config)
    try:
        sha = transport.resolve_commit(owner, repo, ref)
    except GitError as exc:
        raise SourceError(str(exc)) from exc
    snapshot_id = SnapshotId("github", "commit", sha)
    raw_dir = SnapshotCache(config).raw_dir(str(snapshot_id))
    try:
        root = _ensure_source(owner, repo, sha, raw_dir, transport)
    except GitError as exc:
        raise SourceError(str(exc)) from exc
    files = tuple(walk_files(root, config))
    return Snapshot(
        snapshot_id=str(snapshot_id),
        origin="github",
        spec=spec,
        root_dir=root,
        scope=None,
        ref=ref,
        commit=sha,
        created_at=time.time(),
        files=files,
    )
