"""Where a harness commit, its activation, and its checkout live — spelled once.

Three commands meet on the same files. ``molmcp harness sync`` publishes a
commit and promotes a pointer, ``molmcp serve`` reads that pointer, and
``molmcp init`` reads it again to install what the commit's catalog declares.
A commit published anywhere but :func:`store_path` is one nothing serves, a
pointer written anywhere but :func:`pointer_path` is one nothing reads, and a
checkout one command probes at ``~/harness`` cannot be one another reads at
``./~/harness`` — so each of those answers has exactly one home, and it is
this module.

:data:`SUPPORTED_CAPABILITIES` rides with them because it travels with them:
every caller that names a pointer also hands that set to
:meth:`~molmcp.components.Activation.bind` and to
:func:`~molmcp.components.load_harness_catalog` in the same breath. Two light
modules for one import would be a split with no seam in it.

**This module is the light one.** :mod:`molmcp.harness` carries
``from .provider_worker.worker import WorkerProvider``, so importing it drags
the whole FastMCP-bearing worker stack into the importing process. That is a
cost ``molmcp serve`` pays anyway and ``molmcp init`` — which mounts no plane
— must not, which is why these four names live below it rather than in it:
:mod:`molmcp.harness_install` reaches them without inheriting the worker
stack, while :mod:`molmcp.harness` and :mod:`molmcp.harness_sync` reach the
very same objects. Nothing beyond :mod:`molmcp.config` and
:mod:`molmcp.settings` may be imported here, or the shelter this module
exists to give is gone.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import ConfigurationError
from .settings import HarnessSource

#: Capability tokens this runtime can honor, named once here and passed as
#: this object to :meth:`Activation.bind` and to every catalog load.
#:
#: A *harness* is a git repository holding the user's own agent tooling —
#: skills, agents, rules, provider planes, discovery overlays — that this
#: install can be pointed at. Its ``harness.toml`` *catalog* declares those
#: pieces, and the catalog (and each named bundle inside it) may list
#: *capability tokens*: machinery a piece needs from whatever process loads
#: it. The two this build honors are ``provider-sdk``, the public
#: :mod:`molmcp.provider_sdk` a checkout plane is written against, and
#: ``harness-catalog``, the catalog format read by
#: :func:`~molmcp.components.load_harness_catalog`.
#:
#: This set is deliberately not ``molmcp.components.ALLOWED_REQUIRES``. That
#: set is what a harness catalog is *allowed to declare* — the grammar. This
#: one is what this process can *deliver* — eligibility. They happen to hold
#: the same two tokens today; aliasing them would make a token added to the
#: grammar tomorrow claim runtime support that nothing here implements.
SUPPORTED_CAPABILITIES = frozenset({"provider-sdk", "harness-catalog"})

#: The shared store's directory name under the resolved cache root. Spelled
#: once here and read through :func:`store_path`; see that function for why it
#: is not a literal at its call sites.
_STORE_DIR_NAME = "harness"

#: Source names :func:`pointer_path` refuses outright, kept for symmetry with
#: :data:`molmcp.components.store._RESERVED_SHA_KEYS` rather than because
#: either one escapes a directory — see that function's docstring.
_RESERVED_SOURCE_NAMES = frozenset({".", ".."})


def pointer_path(root: Path, name: str) -> Path:
    """Name the activation pointer file of one harness source.

    Each source owns ``<root>/harness.<name>.pointer``, a direct child of the
    cache root and a sibling of the one shared store at ``<root>/harness``.

    The name is guarded here rather than on
    :class:`~molmcp.settings.HarnessSource`, because this is the only place
    that knows the name is about to become path *structure* instead of a
    label: that class governs it as "non-empty and whitespace-free" on
    purpose, so an operator who may name an index source ``MolCrafts`` may
    name a harness source ``MolCrafts``, and ``molmcp config`` must keep
    working on a settings file this function refuses.

    The guard is :meth:`ImmutableGitStore._sha_dir`'s, and **which half of it
    is load-bearing is worth stating**, so that nobody later "simplifies" it
    by dropping the half that matters. ``.`` and ``..`` are refused for
    symmetry with that method's reserved set, **not** because they traverse:
    interpolated into ``harness.{name}.pointer`` neither is a path segment at
    all — ``harness....pointer`` is one ordinary filename inside *root*. The
    separator, absolute-path and empty checks are the ones that close the
    hole, since ``a/b`` and ``../../evil`` do turn the name into structure
    and would write outside the cache root.

    Nothing is created, and nothing is created on the way to a refusal: this
    function computes a path and never touches the filesystem.

    Args:
        root: The resolved cache root the store already hangs off.
        name: The harness source's name, as the ``harness`` settings list
            spells it.

    Returns:
        The pointer file for that source.

    Raises:
        ConfigurationError: The name cannot be one path segment — it is
            empty, reserved, absolute, or contains a path separator. The
            message names it with ``repr``, this repo's register for a
            rejected value and the only form that can name the empty string
            at all.
    """
    if (
        not name
        or name in _RESERVED_SOURCE_NAMES
        or Path(name).is_absolute()
        or os.sep in name
        or "/" in name
        or "\\" in name
        or (os.altsep is not None and os.altsep in name)
    ):
        raise ConfigurationError(
            f"the harness source named {name!r} cannot name an activation "
            f"pointer file: a source name must be a single path segment, so "
            f"it may not be empty, `.`, `..`, absolute, or contain a path "
            f"separator. Rename that entry of the `harness` list in your "
            f"settings file."
        )
    return root / f"harness.{name}.pointer"


def store_path(root: Path) -> Path:
    """Name the one shared store every harness source publishes into.

    Every source's commits land under ``<root>/harness``, a sibling of the
    per-source pointer files :func:`pointer_path` names. One directory, not
    one per source: :class:`~molmcp.components.ImmutableGitStore` keys a
    commit on its SHA alone, so a second root would buy no isolation and
    would strand every already-published tree.

    It is a function rather than a literal spelled at each call site because
    it has three callers that must agree exactly — ``molmcp harness sync``,
    which publishes into it, and the two readers, :mod:`molmcp.harness` at
    serve time and :mod:`molmcp.harness_install` at ``molmcp init`` time. A
    sync writing anywhere else would leave both readers unable to find the
    commit that was just activated, and the failure would look like a corrupt
    pointer rather than like a typo.

    Nothing is created here: this computes a path and never touches the
    filesystem.

    Args:
        root: The resolved cache root.

    Returns:
        The shared store directory under *root*.
    """
    return root / _STORE_DIR_NAME


def local_checkout_path(source: HarnessSource) -> Path:
    """Name the directory one local harness entry's ``path`` points at.

    The string an operator stores is not always the directory to read.
    :func:`~molmcp.harness.assert_servable` accepts ``~/harness`` — home is
    the same directory in every session, so that entry names one checkout
    rather than a different one per client — which makes the home-relative
    spelling the one servable ``path`` that must be expanded before anything
    opens it. Handed to a transport as written, ``~/harness`` is an ordinary
    two-segment relative path read against whatever working directory the
    client that launched the process happened to stand in.

    A function rather than an ``expanduser()`` at each call site, for the
    reason :func:`store_path` is one: it has two callers that must agree
    exactly — the servability check in :mod:`molmcp.harness` and ``molmcp
    harness sync``'s choice of transport root. A checkout ``molmcp serve``
    probes at one location cannot be one ``molmcp harness sync`` clones from
    another, which is the failure two spellings drift into.

    **Only ``~`` is expanded.** :meth:`Path.resolve` would turn the
    working-directory-relative spellings
    :func:`~molmcp.harness.assert_servable` exists to refuse into absolute
    paths, so the refusal would stop firing; it would also normalise the
    operator's stored string — possibly authored on another machine — into
    this machine's answer, which is the bug in the same family. Nothing is
    created and nothing is read here: this computes a path and never touches
    the filesystem.

    Args:
        source: One entry of the ``harness`` settings list, whose ``path``
            the caller has already found non-empty. An entry naming a GitHub
            coordinate has no local checkout at all, and its empty ``path``
            would come back as the working directory rather than as nothing.

    Returns:
        The directory that entry's ``path`` names, with a leading ``~``
        expanded to this session's home.
    """
    return Path(source.path).expanduser()


__all__ = [
    "SUPPORTED_CAPABILITIES",
    "local_checkout_path",
    "pointer_path",
    "store_path",
]
