"""Stdlib GitHub HTTP transport and gzip tarball extraction.

Network access is ``urllib`` only. The caller supplies an optional
GitHub personal access token (PAT); this module never reads the
environment. Request timeout is in seconds. Commit identity is a SHA
(Secure Hash Algorithm) hex digest.
"""

from __future__ import annotations

import io
import json
import tarfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

_API = "https://api.github.com"
_CODELOAD = "https://codeload.github.com"
_TIMEOUT = 30
_USER_AGENT = "molmcp"
_API_ACCEPT = "application/vnd.github+json"


class GitError(RuntimeError):
    """Raised when a git remote request or archive extract fails."""


class GitTransport(Protocol):
    """Structural interface (``typing.Protocol``) for git remotes over HTTP.

    Two primitives: resolve a *ref* (branch name, tag, or SHA) to a commit
    SHA, and fetch that commit's gzip tarball. Combining them is the
    caller's job. ``ref is None`` means resolve the repository default
    branch first. Implementations raise :class:`GitError` on failure.
    """

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        """Return the commit SHA (hex) for ``owner/repo`` at ``ref``.

        Args:
            owner: Repository owner (user or org).
            repo: Repository name.
            ref: Branch, tag, or SHA. ``None`` selects the default branch.

        Returns:
            Commit SHA as a hex digest.

        Raises:
            GitError: Remote request failed, or the payload has no commit SHA.
        """
        ...

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        """Return the gzip tarball bytes for ``owner/repo`` at ``sha``.

        Args:
            owner: Repository owner (user or org).
            repo: Repository name.
            sha: Commit SHA (hex) to archive.

        Returns:
            Raw ``tar.gz`` bytes.

        Raises:
            GitError: Remote request failed.
        """
        ...


class GitHubTransport:
    """GitHub HTTP implementation of :class:`GitTransport`.

    Uses GitHub's JSON HTTP API (``api.github.com``) to resolve commits and
    GitHub's archive host (``codeload.github.com``) to download a gzip
    tarball. Timeout is :data:`_TIMEOUT` seconds on every request.
    ``User-Agent`` is the literal ``molmcp``. Token is a personal access
    token (PAT) or ``None`` (no ``Authorization`` header).
    """

    def __init__(self, token: str | None = None) -> None:
        """Store an optional GitHub PAT.

        Args:
            token: Personal access token, or ``None`` to send unauthenticated
                requests. Not read from the environment.
        """
        self._token = token

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        """Return the commit SHA (hex) for ``owner/repo`` at ``ref``.

        Args:
            owner: Repository owner (user or org).
            repo: Repository name.
            ref: Branch, tag, or SHA. ``None`` looks up ``default_branch``,
                then the commits URL; if that field is missing, uses
                ``HEAD`` (git's name for the currently checked-out
                revision).

        Returns:
            Commit SHA as a hex digest.

        Raises:
            GitError: HTTP/URL/OS failure, or JSON payload with no ``sha``.
        """
        if ref is None:
            info = self._get_json(f"{_API}/repos/{owner}/{repo}")
            default = info.get("default_branch")
            ref = default if isinstance(default, str) and default else "HEAD"
        payload = self._get_json(f"{_API}/repos/{owner}/{repo}/commits/{ref}")
        sha = payload.get("sha")
        if not isinstance(sha, str) or not sha:
            raise GitError(f"could not resolve {owner}/{repo}@{ref}")
        return sha

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        """Return the gzip tarball bytes for ``owner/repo`` at ``sha``.

        Args:
            owner: Repository owner (user or org).
            repo: Repository name.
            sha: Commit SHA (hex) to archive.

        Returns:
            Raw ``tar.gz`` bytes from ``codeload.github.com``.

        Raises:
            GitError: HTTP/URL/OS failure.
        """
        url = f"{_CODELOAD}/{owner}/{repo}/tar.gz/{sha}"
        return self._http_get(url, accept="application/octet-stream")

    def _get_json(self, url: str) -> dict[str, object]:
        raw = self._http_get(url, accept=_API_ACCEPT)
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise GitError(f"GitHub request failed for {url}: {exc}") from exc
        if not isinstance(payload, dict):
            raise GitError(f"GitHub request failed for {url}: not a JSON object")
        return payload

    def _http_get(self, url: str, *, accept: str) -> bytes:
        headers = {"User-Agent": _USER_AGENT, "Accept": accept}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise GitError(f"GitHub request failed ({exc.code}) for {url}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise GitError(f"GitHub request failed for {url}: {exc}") from exc


def extract_git_archive(data: bytes, dest: Path) -> Path:
    """Extract a gzip git tarball and return the inner-tree root.

    A GitHub commit archive is a gzip-compressed tar whose members sit
    under one top-level directory (for example ``owner-repo-sha/``). That
    directory is the *inner tree* — the repository files — as opposed to
    ``dest`` itself.

    Uses py3.12 ``filter="data"`` (blocks path traversal); older Python
    falls back to unfiltered extract.

    Args:
        data: Gzip-compressed tar bytes (a GitHub-style archive).
        dest: Directory that should receive the extracted tree.

    Returns:
        Path of the single top-level directory inside ``dest``.

    Raises:
        GitError: Empty or corrupt archive, or no directory entry after
            extract.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            try:
                tar.extractall(dest, filter="data")
            except TypeError:
                tar.extractall(dest)
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        raise GitError(f"could not extract git archive: {exc}") from exc
    subdirs = sorted(path for path in dest.iterdir() if path.is_dir())
    if not subdirs:
        raise GitError("git archive contained no source directory")
    return subdirs[0]
