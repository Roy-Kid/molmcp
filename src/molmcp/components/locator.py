"""Parse a harness locator into one origin key.

A locator is the string an operator writes: a GitHub URL or
``owner/repo[@ref]``, or a ``~/`` / absolute path. Spellings of the
same GitHub repository share one lowercase ``owner/repo`` origin key;
a *ref* is stored separately and is not identity. Local locators key
on the resolved path, which need not exist.

Stdlib only — host and path are split by hand (no ``urllib``, no git).
Callers import :func:`parse_harness_locator` from this module; it is
not on :mod:`molmcp.components` ``__all__``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_HOST_PREFIXES = ("www.github.com/", "github.com/")


class LocatorError(ValueError):
    """Raised when a harness locator cannot be accepted."""


@dataclass(frozen=True, slots=True)
class ParsedHarnessLocator:
    """One accepted harness locator, with identity separated from spelling.

    ``origin_key`` is the identity: lowercase ``owner/repo`` for GitHub
    (``.git`` already stripped) or the resolved local path. ``ref`` is
    empty when the locator did not name one. ``owner`` and ``repo`` are
    empty for local locators.

    Attributes:
        locator: Original text.
        kind: ``"github"`` or ``"local"``.
        origin_key: Canonical identity.
        ref: Git ref, or ``""``.
        owner: Lowercase GitHub owner, or ``""``.
        repo: Lowercase GitHub repo without ``.git``, or ``""``.
    """

    locator: str
    kind: Literal["github", "local"]
    origin_key: str
    ref: str
    owner: str
    repo: str


def parse_harness_locator(text: str) -> ParsedHarnessLocator:
    """Parse one harness locator into a canonical origin.

    GitHub spellings (``https://github.com/Owner/repo``,
    ``github.com/Owner/repo``, ``Owner/repo[@ref]``) share one lowercase
    ``owner/repo`` origin key. ``.git`` and a trailing slash are stripped
    from URL and host-prefixed forms. ``www.github.com`` is the same
    host as ``github.com``. A *ref* after ``@`` on the shorthand form is
    stored on the result and is not part of the origin key.

    Absolute paths (a leading ``/``, a Windows drive or UNC share) and
    ``~/…`` (also ``~\\…`` on Windows) are local. The
    origin key is ``str(Path(text).expanduser().resolve())``; the path
    need not exist. A platform-absolute path may contain backslashes —
    that is how ``Path`` stringifies on Windows. Relative paths,
    whitespace, ``http://``, a ``github:`` prefix, SSH, extra URL path
    segments, and backslashes *in a GitHub locator* raise.

    Args:
        text: Locator as the operator wrote it.

    Returns:
        Frozen parse result. ``locator`` is ``text`` unchanged.

    Raises:
        LocatorError: If ``text`` is not an accepted locator.
    """
    _reject_surface(text)
    if _is_local_locator(text):
        return ParsedHarnessLocator(
            locator=text,
            kind="local",
            origin_key=str(Path(text).expanduser().resolve()),
            ref="",
            owner="",
            repo="",
        )
    if "\\" in text:
        raise LocatorError(f"harness locator must be POSIX (no backslash): {text!r}")
    owner, repo, ref = _parse_github(text)
    return ParsedHarnessLocator(
        locator=text,
        kind="github",
        origin_key=f"{owner}/{repo}",
        ref=ref,
        owner=owner,
        repo=repo,
    )


def _reject_surface(text: str) -> None:
    if not text:
        raise LocatorError("harness locator must not be empty")
    if any(ch.isspace() for ch in text):
        raise LocatorError(f"harness locator must not contain whitespace: {text!r}")
    if (
        text in {".", ".."}
        or text.startswith("./")
        or text.startswith("../")
        or text.startswith(".\\")
        or text.startswith("..\\")
    ):
        raise LocatorError(f"relative path is not a harness locator: {text!r}")
    lowered = text.lower()
    if lowered.startswith("http://"):
        raise LocatorError(f"http:// is not a harness locator: {text!r}")
    if lowered.startswith("github:"):
        raise LocatorError(f"github: prefix is not a harness locator: {text!r}")
    if lowered.startswith("ssh://") or lowered.startswith("git@"):
        raise LocatorError(f"SSH is not a harness locator: {text!r}")


def _is_local_locator(text: str) -> bool:
    """True when *text* names a filesystem path rather than a GitHub origin.

    ``~/…`` and a leading ``/`` are local on every platform — a
    settings file that spells ``/opt/harness`` must not become a GitHub
    shorthand just because Windows ``Path.is_absolute()`` is False
    without a drive letter. ``Path.is_absolute()`` covers the rest: a
    drive letter or UNC share on Windows. Existence is not required.
    """
    if text.startswith(("~/", "~\\", "/")):
        return True
    return Path(text).is_absolute()


def _parse_github(text: str) -> tuple[str, str, str]:
    lowered = text.lower()
    if lowered.startswith("https://"):
        owner, repo = _github_url_owner_repo(text, text[8:])
        return owner, repo, ""
    for prefix in _HOST_PREFIXES:
        if lowered.startswith(prefix):
            owner, repo = _github_path_owner_repo(text, text[len(prefix) :])
            return owner, repo, ""
    return _github_shorthand(text)


def _github_url_owner_repo(text: str, rest: str) -> tuple[str, str]:
    if "/" not in rest:
        raise LocatorError(f"invalid harness locator: {text!r}")
    host, path = rest.split("/", 1)
    if host.lower() not in _GITHUB_HOSTS:
        raise LocatorError(f"invalid harness locator: {text!r}")
    return _github_path_owner_repo(text, path)


def _github_path_owner_repo(text: str, path: str) -> tuple[str, str]:
    if "?" in path or "#" in path or "@" in path:
        raise LocatorError(f"invalid harness locator: {text!r}")
    if path.endswith("/"):
        path = path[:-1]
    parts = path.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise LocatorError(f"invalid harness locator: {text!r}")
    return _normalize_owner_repo(text, parts[0], parts[1])


def _github_shorthand(text: str) -> tuple[str, str, str]:
    ref = ""
    body = text
    if "@" in text:
        if text.count("@") != 1:
            raise LocatorError(f"invalid harness locator: {text!r}")
        body, ref = text.split("@", 1)
        if not ref:
            raise LocatorError(f"invalid harness locator: {text!r}")
    if "?" in body or "#" in body or ":" in body:
        raise LocatorError(f"invalid harness locator: {text!r}")
    parts = body.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise LocatorError(f"invalid harness locator: {text!r}")
    owner, repo = _normalize_owner_repo(text, parts[0], parts[1])
    return owner, repo, ref


def _normalize_owner_repo(text: str, owner: str, repo: str) -> tuple[str, str]:
    owner = owner.lower()
    repo = repo.lower()
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _is_github_owner(owner) or not _is_github_repo(repo):
        raise LocatorError(f"invalid harness locator: {text!r}")
    return owner, repo


def _is_github_owner(value: str) -> bool:
    if not value or value[0] == "-" or value[-1] == "-":
        return False
    return all(ch.isalnum() or ch == "-" for ch in value)


def _is_github_repo(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return all(ch.isalnum() or ch in "._-" for ch in value)
