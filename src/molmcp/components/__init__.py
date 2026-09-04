"""Frozen types and loader for one checkout's ``harness.toml`` catalog.

A *harness catalog* lists installable pieces and named groups of those
pieces. It lives in ``harness.toml``, a TOML file at the root of one git
*checkout* (the directory that holds a single commit of the repo).

This package never talks to git. Identity is the commit *SHA* (Secure
Hash Algorithm fingerprint: 40 lowercase hex characters) the caller
passes in. The TOML file must not contain a ``sha`` key.

Two checks, in order, and they are not the same:

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
"""

from .catalog import HarnessCatalog, ResolvedBundle, load_harness_catalog
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
    "HarnessCatalog",
    "KIND_PATH_PREFIX",
    "ResolvedBundle",
    "SHA_PATTERN",
    "load_harness_catalog",
]
