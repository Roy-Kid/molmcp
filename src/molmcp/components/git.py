"""Stdlib git transports (GitHub HTTP, local checkout) and tarball extraction.

Two implementations of one :class:`GitTransport` protocol. The GitHub one
reaches the network with ``urllib`` only; the caller supplies an optional
GitHub personal access token (PAT). The local one reaches no network at
all: it shells out to ``git`` inside a checkout already on disk. Neither
reads the environment. Request timeout is in seconds. Commit identity is
a SHA (Secure Hash Algorithm) hex digest.
"""

from __future__ import annotations

import io
import json
import subprocess
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

#: ``git rev-parse`` peel suffix: from any object, walk to the commit it
#: names. Load-bearing on an annotated tag, where a bare ``rev-parse``
#: answers the *tag object's* SHA -- not a commit, and not something an
#: activation may be pinned to.
_TO_COMMIT = "^{commit}"

#: Fallback first half of ``git archive --prefix``, used when the checkout
#: root has no directory name of its own (``/`` or a bare ``.``).
_ARCHIVE_PREFIX_FALLBACK = "harness"


class GitError(RuntimeError):
    """Raised when a git remote request or archive extract fails."""


class GitTransport(Protocol):
    """Structural interface (``typing.Protocol``) for a git repository.

    Two primitives: resolve a *ref* (branch name, tag, or SHA) to a commit
    SHA, and fetch that commit's gzip tarball. Combining them is the
    caller's job. ``ref is None`` means resolve the repository default
    branch first. Implementations raise :class:`GitError` on failure.

    ``owner`` and ``repo`` are the GitHub coordinate every implementation
    is handed; one reading a checkout it was constructed with ignores
    them. Which repository is spoken to is therefore the implementation's
    own business, not something a caller can infer from the arguments.
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


class LocalGitTransport:
    """Local-checkout implementation of :class:`GitTransport`.

    A harness source may be a repository already on disk rather than a
    GitHub coordinate: the way an operator serves a harness they are still
    writing, and the only way to name one before it is published anywhere.
    Both primitives shell out to ``git`` inside ``root``. Nothing here
    opens a socket, and no token is involved.

    ``owner`` and ``repo`` are accepted because the protocol passes them,
    and are ignored: the ``root`` this was constructed with is the whole
    repository selection. Passing the coordinate of some other repository
    does not reach that repository -- it reaches this checkout.

    :meth:`fetch_archive` archives the *committed tree* at a SHA, never the
    working tree, which is what makes a local source pinned and rollbackable
    in the same way a remote one is. Copying the directory instead would
    make "pinned to a commit" mean "whatever the operator had unsaved when
    we looked".
    """

    def __init__(self, root: Path) -> None:
        """Store the checkout to read.

        Args:
            root: Directory of a git repository. It is not validated here:
                a root that is missing or is not a repository surfaces as
                a :class:`GitError` from the first call, which is the same
                failure a bad coordinate gets over HTTP.
        """
        self._root = Path(root)

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        """Return the commit SHA (hex) that ``ref`` names in this checkout.

        The ref is peeled with :data:`_TO_COMMIT` before it is read. Without
        that suffix an annotated tag -- how a harness release gets cut --
        resolves to the tag object's own SHA, which is not a commit and
        names nothing ``git log`` can walk.

        Args:
            owner: Ignored; see the class docstring.
            repo: Ignored; see the class docstring.
            ref: Branch, tag, or SHA. ``None`` selects the checked-out
                revision, which is a local checkout's default branch.

        Returns:
            Commit SHA as a hex digest.

        Raises:
            GitError: ``git`` failed -- unknown ref, ``root`` missing or not
                a repository, no ``git`` on PATH -- or answered nothing.
        """
        target = "HEAD" if ref is None else ref
        sha = self._git("rev-parse", "--verify", f"{target}{_TO_COMMIT}").strip()
        if not sha:
            raise GitError(f"could not resolve {target} in {self._root}")
        return sha

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        """Return the gzip tarball bytes of the tree committed at ``sha``.

        Shaped like a GitHub commit archive: every member sits under one
        top-level directory naming the commit, so
        :func:`extract_git_archive` finds the same inner tree either
        transport produced it.

        Args:
            owner: Ignored; see the class docstring.
            repo: Ignored; see the class docstring.
            sha: Commit SHA (hex) to archive.

        Returns:
            Raw ``tar.gz`` bytes of that commit's tree -- not of the working
            tree, so an uncommitted edit is absent from it.

        Raises:
            GitError: ``git`` failed -- unknown SHA, ``root`` missing or not
                a repository, no ``git`` on PATH.
        """
        prefix = f"{self._root.name or _ARCHIVE_PREFIX_FALLBACK}-{sha}/"
        return self._git_bytes("archive", "--format=tar.gz", f"--prefix={prefix}", sha)

    def _git(self, *args: str) -> str:
        return self._git_bytes(*args).decode("utf-8", "replace")

    def _git_bytes(self, *args: str) -> bytes:
        """Run one git command inside ``root``; every failure is a GitError.

        A ``CalledProcessError`` must not escape: the protocol's contract is
        :class:`GitError`, and a caller written against it would not catch
        the subprocess type. ``git``'s own stderr is carried into the
        message, since it is the only place the reason is written down.
        """
        command = ["git", "-C", str(self._root), *args]
        label = f"git {' '.join(args)} failed in {self._root}"
        try:
            completed = subprocess.run(command, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode("utf-8", "replace").strip()
            raise GitError(f"{label}: {detail}") from exc
        except OSError as exc:
            raise GitError(f"{label}: {exc}") from exc
        return completed.stdout


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
