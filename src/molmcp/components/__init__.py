"""Stdlib shared leaf: harness catalog types and GitHub HTTP transport.

This package is not a new architecture layer. Outer and inner modules
import it; it imports only the standard library (plus relative siblings).
It is not re-exported from :mod:`molmcp`.

The catalog half reads one checkout's ``harness.toml`` into frozen types.
A *harness catalog* lists installable pieces and named groups of those
pieces. Identity is the commit *SHA* (Secure Hash Algorithm fingerprint:
40 lowercase hex characters) the caller passes in; the TOML file must
not contain a ``sha`` key. Catalog loading does not inspect git.

Two catalog checks, in order, and they are not the same:

* *Language gate* — the file must match the catalog grammar (known
  keys, known kinds, ``requires`` tokens drawn only from
  ``ALLOWED_REQUIRES``).
* *Eligibility* — every ``requires`` token that survived the language
  gate must also be one the caller currently supports
  (``supported_capabilities``). An unknown token still fails the
  language gate even if the caller listed it as supported.

A *component* is one installable piece. ``ComponentKind`` is the enum
of the five kinds (``skill``, ``agent``, ``rule``, ``provider``,
``overlay``). A *bundle* is a named grouping of component ids; it is
not a ``ComponentKind``. An *entrypoint* is a ``module:object`` string
stored for a later import; this package never imports it.

The git half is :class:`GitTransport` / :class:`GitHubTransport` plus
:func:`extract_git_archive`. Network access is stdlib ``urllib``; the
caller supplies an optional GitHub personal access token. This package
never reads the environment.
"""

from .catalog import HarnessCatalog, ResolvedBundle, load_harness_catalog
from .git import GitError, GitHubTransport, GitTransport, extract_git_archive
from .models import (
    ALLOWED_REQUIRES,
    COMPONENT_NAME_PATTERN,
    KIND_PATH_PREFIX,
    SHA_PATTERN,
    BundleSpec,
    CatalogError,
    ComponentKind,
    ComponentSpec,
)

__all__ = [
    "ALLOWED_REQUIRES",
    "BundleSpec",
    "COMPONENT_NAME_PATTERN",
    "CatalogError",
    "ComponentKind",
    "ComponentSpec",
    "GitError",
    "GitHubTransport",
    "GitTransport",
    "HarnessCatalog",
    "KIND_PATH_PREFIX",
    "ResolvedBundle",
    "SHA_PATTERN",
    "extract_git_archive",
    "load_harness_catalog",
]
