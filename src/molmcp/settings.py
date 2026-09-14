"""Layered molmcp settings — what this install indexes, and where.

Three files, merged low to high, in the shape Claude Code established:

* ``~/.molmcp/settings.json`` — the user's install
* ``<project>/.molmcp/settings.json`` — checked in with a repo
* ``<project>/.molmcp/settings.local.json`` — that repo, this machine

The *user* file is the primary surface here, which is the one place this
departs from Claude Code's emphasis. A plane server is launched by an MCP
client, so its working directory is whatever the client happened to be
started in — a project-scoped default would make "what do I index" depend
on an accident. ``molmcp config`` therefore writes the user file unless
asked for ``--project``.

Keys are camelCase, and unknown ones are an error rather than a silent
no-op: a mistyped ``indexWorkspaces`` that quietly does nothing is worse
than one that says so.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .components.locator import LocatorError, parse_harness_locator
from .components.models import COMPONENT_NAME_PATTERN

#: Directory name used for both the user home and a project checkout.
CONFIG_DIR_NAME = ".molmcp"
SETTINGS_NAME = "settings.json"
LOCAL_SETTINGS_NAME = "settings.local.json"


class SettingsError(ValueError):
    """Raised when a settings file is unreadable or names an unknown key."""


#: Every settable key, with the type its value must parse to. Anything not
#: listed is rejected — see the module docstring.
_SCHEMA: dict[str, type] = {
    "sources": dict,
    "indexWorkspace": bool,
    "knowledgeScope": list,
    "excludes": list,
    "cacheDir": str,
    "watch": bool,
    "maxCacheBytes": int,
    "maxCacheAgeDays": int,
    "pythonEnv": str,
    "discoverInclude": list,
    "discoverExclude": list,
    "harness": list,
    "molexp": dict,
    "molq": dict,
}

#: Members allowed inside each dict-valued setting whose keys are a fixed set.
#: ``sources`` is deliberately absent — its members are user-chosen names.
#: Without this, `config set molq.allowsubmit true` would be accepted, stored,
#: echoed back by `config list`, and read by nothing.
_NESTED_SCHEMA: dict[str, frozenset[str]] = {
    "molq": frozenset({"database", "allowSubmit"}),
    "molexp": frozenset({"workspace"}),
}

#: Keys whose layers combine instead of replacing one another.
_MERGED_DICTS = ("sources", "molexp", "molq")
_MERGED_LISTS = ("excludes", "knowledgeScope", "discoverInclude", "discoverExclude")

#: List-valued settings whose *elements are objects*, which the string-valued
#: editing verbs cannot author: `config set harness x` would store the list
#: ``["x"]`` and `config add harness x` would append the bare string, and both
#: write before anything validates — leaving a file every later read rejects.
#: This is a declaration, not a merge channel: ``load_settings`` never consults
#: it, so the next list-of-objects setting closes the same hole by joining this
#: tuple rather than by someone remembering to add a second branch. Joining it
#: also means shipping a ``molmcp config <member> set`` leaf: the refusal
#: ``_reject_object_list_write`` raises derives that command from the key, so a
#: member added without its verb would hand out a command nothing resolves —
#: which a test asserts against this tuple rather than a docstring promising it.
#: The entry-key list in that same message is still ``harness``'s own, and
#: nothing fails if it goes on naming only these.
#:
#: ``harness`` is deliberately in no merge channel at all. The default branch of
#: ``load_settings`` makes the last assignment win, and ``settings_layers``
#: yields lowest precedence first, so the most specific layer's list replaces
#: the others whole. That is the opposite of ``_MERGED_LISTS`` one line above,
#: on purpose: ``extend`` on a first-wins list would land the user file's
#: entries at the front and make the user file outrank the project file.
_OBJECT_LISTS = ("harness",)

#: Alias given to the first harness source authored without ``--alias``.
DEFAULT_HARNESS_ALIAS = "origin"

#: Coordinate keys the locator model retired. A file that still carries one
#: is a hard cut: re-author with ``molmcp config harness set <locator>``.
_RETIRED_HARNESS_KEYS = frozenset({"owner", "path", "ref", "repo"})


@dataclass(frozen=True, slots=True)
class HarnessSource:
    """One named harness repository this install may serve components from.

    A harness is the git repository of the operator's own agent tooling —
    skills, agents, rules, provider planes, discovery overlays. An install
    may name several, and the order they are written in is the order they
    are read in.

    The dataclass stores only what the operator wrote: an alias, a locator,
    and an optional enable list. GitHub identity and the local path are
    parsed from ``locator`` at construction and are properties, not fields —
    they do not appear in :func:`dataclasses.asdict` or in the settings file.
    Construction requires a locator; a name-only half-authored entry is not
    a thing this type can represent.

    ``name`` is held to no grammar beyond "non-empty, no whitespace",
    deliberately: it is user-chosen in exactly the way a ``sources`` key is,
    and an operator who may name an index source ``MolCrafts`` may name a
    harness source ``MolCrafts`` too. ``/`` is still refused later, when
    the alias becomes a pointer path.

    ``enable`` is ``None`` (all bundles, the default), ``()`` (explicitly
    none; the source remains), or a tuple of bundle names. Names match
    :data:`~molmcp.components.models.COMPONENT_NAME_PATTERN` and are stored
    first-seen unique. Catalog membership is not checked here.

    Attributes:
        name: Non-empty, whitespace-free alias chosen by the operator.
        locator: Origin as the operator wrote it.
        enable: ``None`` means all bundles; ``()`` means none; a non-empty
            tuple is bundle names.

    Raises:
        ValueError: If ``name`` is empty or carries whitespace, if
            ``locator`` is not an accepted locator, or if ``enable`` is not
            ``None`` or a sequence of component names.
    """

    name: str
    locator: str
    enable: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise ValueError(
                f"harness source name must be a string, got {type(self.name).__name__}"
            )
        if not self.name:
            raise ValueError("a harness source must have a non-empty name")
        if any(character.isspace() for character in self.name):
            raise ValueError(
                f"harness source name must not contain whitespace: {self.name!r}"
            )
        if not isinstance(self.locator, str):
            raise ValueError(
                "harness source locator must be a string, "
                f"got {type(self.locator).__name__}"
            )
        parse_harness_locator(self.locator)
        object.__setattr__(self, "enable", _enable_names(self.enable))

    @property
    def origin_key(self) -> str:
        """Canonical origin identity parsed from ``locator``."""
        return parse_harness_locator(self.locator).origin_key

    @property
    def ref(self) -> str:
        """Git ref from the locator, or ``""``."""
        return parse_harness_locator(self.locator).ref

    @property
    def owner(self) -> str:
        """Lowercase GitHub owner, or ``""`` for a local locator."""
        return parse_harness_locator(self.locator).owner

    @property
    def repo(self) -> str:
        """Lowercase GitHub repo without ``.git``, or ``""`` for a local locator."""
        return parse_harness_locator(self.locator).repo

    @property
    def path(self) -> str:
        """Resolved local path, or ``""`` for a GitHub locator."""
        parsed = parse_harness_locator(self.locator)
        return parsed.origin_key if parsed.kind == "local" else ""

    @property
    def is_local(self) -> bool:
        """Whether ``locator`` named a filesystem path."""
        return parse_harness_locator(self.locator).kind == "local"


#: Keys one ``harness`` entry may carry, derived from the dataclass rather than
#: written out: a hand-written literal would silently reject a new field the
#: day someone adds it to :class:`HarnessSource`.
_HARNESS_ENTRY_KEYS: frozenset[str] = frozenset(f.name for f in fields(HarnessSource))


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved settings for one molmcp invocation."""

    sources: dict[str, str] = field(default_factory=dict)
    index_workspace: bool = False
    excludes: tuple[str, ...] = ()
    cache_dir: str | None = None
    watch: bool = True
    max_cache_bytes: int | None = None
    max_cache_age_days: int | None = None
    #: Narrow which configured sources the knowledge tools surface. Empty
    #: means all of them; this never widens past ``sources``.
    knowledge_scope: tuple[str, ...] = ()
    #: Locator for the Python environment auto-discovery reads.
    python_env: str | None = None
    discover_include: tuple[str, ...] = ()
    discover_exclude: tuple[str, ...] = ()
    #: The autonomous harness repositories this install may serve from, in
    #: the order the most specific settings file wrote them; the empty tuple
    #: is the un-harnessed install. Each entry is a locator plus an alias;
    #: identity is the locator's origin key, not the alias.
    harness: tuple[HarnessSource, ...] = field(default_factory=tuple)
    molexp: dict[str, str] = field(default_factory=dict)
    molq: dict[str, str] = field(default_factory=dict)
    #: Files that actually contributed, lowest precedence first.
    layers: tuple[Path, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources": dict(self.sources),
            "indexWorkspace": self.index_workspace,
            "excludes": list(self.excludes),
            "cacheDir": self.cache_dir,
            "watch": self.watch,
            "maxCacheBytes": self.max_cache_bytes,
            "maxCacheAgeDays": self.max_cache_age_days,
            "knowledgeScope": list(self.knowledge_scope),
            "pythonEnv": self.python_env,
            "discoverInclude": list(self.discover_include),
            "discoverExclude": list(self.discover_exclude),
            "harness": [asdict(source) for source in self.harness],
            "molexp": dict(self.molexp),
            "molq": dict(self.molq),
            "layers": [str(path) for path in self.layers],
        }


def user_settings_path() -> Path:
    """The install-wide settings file."""
    return Path.home() / CONFIG_DIR_NAME / SETTINGS_NAME


def project_settings_path(root: str | Path, *, local: bool = False) -> Path:
    """Settings for one checkout; ``local`` is the untracked override."""
    name = LOCAL_SETTINGS_NAME if local else SETTINGS_NAME
    return Path(root) / CONFIG_DIR_NAME / name


def read_settings_file(path: Path) -> dict[str, Any]:
    """Parse one settings file; missing is empty, malformed is an error."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SettingsError(f"cannot read settings at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"settings at {path} must be a JSON object")
    _reject_unknown(data, path)
    return data


def write_settings_file(path: Path, data: dict[str, Any]) -> None:
    """Write settings, creating the directory, with a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def settings_layers(project_root: str | Path | None = None) -> list[Path]:
    """The candidate files, lowest precedence first."""
    layers = [user_settings_path()]
    if project_root is not None:
        layers.append(project_settings_path(project_root))
        layers.append(project_settings_path(project_root, local=True))
    return layers


def load_settings(project_root: str | Path | None = None) -> Settings:
    """Merge every layer that exists into one resolved :class:`Settings`."""
    merged: dict[str, Any] = {}
    contributing: list[Path] = []
    for path in settings_layers(project_root):
        data = read_settings_file(path)
        if not data:
            continue
        contributing.append(path)
        for key, value in data.items():
            if key in _MERGED_DICTS and isinstance(value, dict):
                merged.setdefault(key, {}).update(value)
            elif key in _MERGED_LISTS and isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            else:
                merged[key] = value
    return Settings(
        sources={str(k): str(v) for k, v in (merged.get("sources") or {}).items()},
        index_workspace=bool(merged.get("indexWorkspace", False)),
        excludes=tuple(dict.fromkeys(str(x) for x in merged.get("excludes") or ())),
        cache_dir=(
            str(merged["cacheDir"]) if merged.get("cacheDir") is not None else None
        ),
        watch=bool(merged.get("watch", True)),
        max_cache_bytes=_optional_int(merged.get("maxCacheBytes")),
        max_cache_age_days=_optional_int(merged.get("maxCacheAgeDays")),
        knowledge_scope=_str_tuple(merged.get("knowledgeScope")),
        python_env=(
            str(merged["pythonEnv"]) if merged.get("pythonEnv") is not None else None
        ),
        discover_include=_str_tuple(merged.get("discoverInclude")),
        discover_exclude=_str_tuple(merged.get("discoverExclude")),
        harness=_harness_sources(merged.get("harness") or []),
        molexp={str(k): str(v) for k, v in (merged.get("molexp") or {}).items()},
        molq={str(k): str(v) for k, v in (merged.get("molq") or {}).items()},
        layers=tuple(contributing),
    )


# -- editing (the `molmcp config` verbs) --------------------------------


def set_value(path: Path, key: str, value: str) -> dict[str, Any]:
    """Set ``key`` (dotted for nested) to a parsed ``value``.

    Args:
        path: The settings file to edit; created if it does not exist.
        key: A top-level key, or ``parent.member`` for a dict-valued setting.
        value: The command-line string, coerced to the declared type.

    Returns:
        The whole file as written.

    Raises:
        SettingsError: If ``key`` names a member of :data:`_OBJECT_LISTS` — a
            list of entry objects a string cannot author — or if it is
            unknown, unsettable, or ``value`` does not parse. Nothing is
            written when it raises: the object-list refusal comes before
            :func:`_resolve`, so a refused write leaves no file behind.
    """
    _reject_object_list_write(key, leaf="set")
    root, leaf, container = _resolve(path, key, create=True)
    container[leaf] = _parse(key, value)
    write_settings_file(path, root)
    return root


def add_value(path: Path, key: str, value: str) -> dict[str, Any]:
    """Append to a list-valued ``key``, ignoring a duplicate.

    Args:
        path: The settings file to edit; created if it does not exist.
        key: A list-valued top-level key.
        value: The string to append, appended only if not already present.

    Returns:
        The whole file as written.

    Raises:
        SettingsError: If ``key`` names a member of :data:`_OBJECT_LISTS`,
            whose elements are objects rather than strings, or if it is not a
            list-valued setting at all. Nothing is written when it raises.
    """
    _reject_object_list_write(key, leaf="set")
    top = key.split(".", 1)[0]
    if _SCHEMA.get(top) is not list:
        raise SettingsError(f"{key!r} is not a list-valued setting; use `config set`")
    root, leaf, container = _resolve(path, key, create=True)
    current = container.get(leaf)
    items = list(current) if isinstance(current, list) else []
    if value not in items:
        items.append(value)
    container[leaf] = items
    write_settings_file(path, root)
    return root


def remove_value(path: Path, key: str, value: str | None = None) -> dict[str, Any]:
    """Drop ``key`` outright, or one ``value`` from a list-valued key.

    Args:
        path: The settings file to edit; it must already carry the key.
        key: The key to drop, or the list-valued key to drop ``value`` from.
        value: One element to drop, or ``None`` to drop ``key`` itself.

    Returns:
        The whole file as written.

    Raises:
        SettingsError: If ``key`` is not set in ``path``, if ``value`` is not
            in its list, or if ``value`` was given for a member of
            :data:`_OBJECT_LISTS`, whose elements are entry objects that a
            string cannot address. Only the value arm is guarded — dropping
            the whole ``harness`` key is a different operation and keeps
            working — and the guard comes before :func:`_resolve`, so a
            refused call leaves the file byte for byte as it was.
    """
    if value is not None:
        _reject_object_list_write(key, leaf="remove")
    root, leaf, container = _resolve(path, key, create=False)
    if leaf not in container:
        raise SettingsError(f"{key!r} is not set in {path}")
    if value is None:
        del container[leaf]
    else:
        current = container[leaf]
        if not isinstance(current, list) or value not in current:
            raise SettingsError(f"{value!r} is not present in {key!r}")
        container[leaf] = [item for item in current if item != value]
    write_settings_file(path, root)
    return root


def set_harness_source(
    path: Path,
    locator: str,
    *,
    alias: str | None = None,
    enable: Sequence[str] = (),
    disable: Sequence[str] = (),
) -> dict[str, Any]:
    """Upsert one ``harness`` entry, addressed by the locator's origin key.

    Empty ``enable`` / ``disable`` sequences mean "leave the field as it
    was"; on insert that is ``None`` (all), which the file records by
    omitting the key. ``("all",)`` is a sentinel on either flag, and must
    not share the call with named tokens or with the other flag's ``all``.
    Named ``enable`` unions an explicit list and replaces the all-sentinel;
    named ``disable`` subtracts from an explicit list and is refused while
    the field is still all.

    A locator not already configured is appended **last**. ``alias is None``
    names a new source :data:`DEFAULT_HARNESS_ALIAS` (``origin``) and keeps
    the stored alias on an update; ``origin`` already taken by another
    origin requires ``--alias``.

    Two orderings are the contract. The arguments are validated by
    constructing a :class:`HarnessSource` *before* :func:`_resolve`, the way
    :func:`set_value` refuses ahead of it, so a refused call leaves no file
    behind at all. The merged entry is then constructed a second time, after
    the read and still before the write, which is what leaves the dataclass —
    never this function — deciding whether the result is legal.

    Args:
        path: The settings file to edit; created if it does not exist.
        locator: Origin as the operator wrote it; identity is its origin key.
        alias: The entry's name, or ``None`` to default on insert and keep
            the stored name on update.
        enable: Bundle names to turn on, ``("all",)`` for every bundle, or
            empty to leave the field as it was.
        disable: Bundle names to turn off, ``("all",)`` for none, or empty
            to leave the field as it was.

    Returns:
        The whole file as written.

    Raises:
        SettingsError: If :class:`HarnessSource` refuses the arguments or the
            merged entry — carrying the type's own message — if ``all`` is
            mixed with named tokens, if named ``disable`` is aimed at the
            all-sentinel, if the default alias is taken, or if the file
            already on disk fails :func:`read_settings_file`. Nothing is
            written when it raises.
    """
    enable_tokens = tuple(enable)
    disable_tokens = tuple(disable)
    enable_all = _is_all_flag(enable_tokens, flag="enable")
    disable_all = _is_all_flag(disable_tokens, flag="disable")
    if enable_all and disable_all:
        raise SettingsError("cannot pass --enable all and --disable all together")
    if (enable_all and disable_tokens) or (disable_all and enable_tokens):
        raise SettingsError("cannot mix 'all' with named --enable / --disable")
    named_enable = () if enable_all else enable_tokens
    named_disable = () if disable_all else disable_tokens
    if disable_all:
        offered_enable: tuple[str, ...] | None = ()
    elif named_enable or named_disable:
        offered_enable = tuple(dict.fromkeys((*named_enable, *named_disable)))
    else:
        offered_enable = None
    offered_name = alias if alias is not None else DEFAULT_HARNESS_ALIAS
    _harness_entry({"name": offered_name, "locator": locator, "enable": offered_enable})
    root, leaf, container = _resolve(path, "harness", create=True)
    entries: list[dict[str, Any]] = list(container.get(leaf, []))
    sources = _harness_sources(entries)
    origin_key = parse_harness_locator(locator).origin_key
    at = next(
        (
            index
            for index, source in enumerate(sources)
            if source.origin_key == origin_key
        ),
        None,
    )
    if alias is None:
        new_name = DEFAULT_HARNESS_ALIAS if at is None else sources[at].name
    else:
        new_name = alias
    if at is None and alias is None:
        if any(source.name == DEFAULT_HARNESS_ALIAS for source in sources):
            raise SettingsError(
                f"the default harness alias {DEFAULT_HARNESS_ALIAS!r} is "
                "already used; pass --alias to name this source"
            )
    elif any(
        index != at and source.name == new_name for index, source in enumerate(sources)
    ):
        raise SettingsError(f"harness source name {new_name!r} is already used")
    current_enable = None if at is None else sources[at].enable
    merged = _harness_entry(
        {
            "name": new_name,
            "locator": locator,
            "enable": _merge_enable(
                current_enable,
                named_enable=named_enable,
                named_disable=named_disable,
                disable_all=disable_all,
                enable_all=enable_all,
            ),
        }
    )
    if at is None:
        entries.append(merged)
    else:
        entries[at] = merged
    container[leaf] = entries
    write_settings_file(path, root)
    return root


def match_harness_source(
    sources: Sequence[HarnessSource], token: str
) -> HarnessSource | None:
    """Return the source whose alias or origin matches ``token``.

    Name is tried first, exact. A token that is also a locator spelling is
    still a name if some source uses it as one. Only then is ``token``
    parsed as a locator and compared by ``origin_key``, so a ref is not
    identity.

    Args:
        sources: Configured harness sources, in file order.
        token: An alias, or any accepted locator spelling of an origin.

    Returns:
        The first matching source, or ``None`` if none match.
    """
    for source in sources:
        if source.name == token:
            return source
    try:
        origin_key = parse_harness_locator(token).origin_key
    except LocatorError:
        return None
    for source in sources:
        if source.origin_key == origin_key:
            return source
    return None


def remove_harness_source(path: Path, token: str) -> dict[str, Any]:
    """Drop the one ``harness`` entry matching ``token``, keeping the rest.

    ``token`` is an alias or a locator spelling; matching is
    :func:`match_harness_source`. Removing the last entry leaves
    ``"harness": []`` rather than a missing key: dropping the key is
    ``remove_value(path, "harness")``, a different operation, and an empty
    list is how a file says it named no source.

    Args:
        path: The settings file to edit; it must already carry the key.
        token: The entry's alias, or any accepted locator spelling of its
            origin.

    Returns:
        The whole file as written.

    Raises:
        SettingsError: If the file has no ``harness`` key, or carries no
            entry matching ``token``, or fails :func:`read_settings_file`.
            Nothing is written when it raises.
    """
    root, leaf, container = _resolve(path, "harness", create=False)
    if leaf not in container:
        raise SettingsError(f"'harness' is not set in {path}")
    entries: list[dict[str, Any]] = container[leaf]
    sources = _harness_sources(entries)
    matched = match_harness_source(sources, token)
    if matched is None:
        raise SettingsError(f"{token!r} is not present in 'harness'")
    container[leaf] = [
        entry
        for entry, source in zip(entries, sources, strict=True)
        if source.name != matched.name
    ]
    write_settings_file(path, root)
    return root


def get_value(data: dict[str, Any], key: str) -> Any:
    """Read a dotted ``key`` out of already-parsed settings data.

    A key the data does not carry and a path *through* something that is not
    an object are different answers, and one condition used to give them the
    same one. An undeclared key is ``None``, which reads as "unset";
    ``harness.owner`` is not a path at all now that ``harness`` is a list of
    named entries, and answering ``None`` there would say that coordinate is
    unset rather than unreachable — the wrong of the two, and the one that
    sends an operator looking for a verb to set it with.

    Which case each arm serves is easy to get backwards.
    :meth:`Settings.to_dict` carries every key, ``cacheDir`` among them, so a
    bare ``cacheDir`` read answers ``None`` because the *value* is ``None``
    and the walk ends — never through the missing-key arm at all. That arm is
    reachable only for keys ``to_dict`` does not carry, ``nope`` and
    ``sources.nope`` among them. The head is deliberately not checked against
    :data:`_SCHEMA` either: ``to_dict`` emits ``layers``, which the schema
    does not declare, so validating here would break a read that works.

    Args:
        data: One already-merged settings mapping, as
            :meth:`Settings.to_dict` renders it.
        key: A top-level key, or ``parent.member`` for a nested read.

    Returns:
        The value found, or ``None`` if ``key`` names nothing ``data`` carries.

    Raises:
        SettingsError: If segments remain but the node they would descend
            into is not an object; the message names the whole key and the
            segment that is not one.
    """
    node: Any = data
    parts = key.split(".")
    for index, part in enumerate(parts):
        if not isinstance(node, dict):
            walked = ".".join(parts[:index])
            raise SettingsError(
                f"{key!r} is not a readable path: {walked!r} is not an object"
            )
        elif part not in node:
            return None
        node = node[part]
    return node


# -- internals ----------------------------------------------------------


def _reject_unknown(data: dict[str, Any], path: Path) -> None:
    unknown = sorted(set(data) - set(_SCHEMA))
    if unknown:
        raise SettingsError(
            f"unknown setting(s) in {path}: {', '.join(unknown)}. "
            f"Known keys: {', '.join(sorted(_SCHEMA))}"
        )
    for parent, allowed in _NESTED_SCHEMA.items():
        section = data.get(parent)
        if not isinstance(section, dict):
            continue
        strays = sorted(set(section) - allowed)
        if strays:
            raise SettingsError(
                f"unknown setting(s) in {path}: "
                f"{', '.join(f'{parent}.{k}' for k in strays)}. "
                f"Known {parent} keys: {', '.join(sorted(allowed))}"
            )
    _reject_bad_harness_entries(data, path)


def _reject_bad_harness_entries(data: dict[str, Any], path: Path) -> None:
    """Check one file's ``harness`` value entry by entry.

    Every rejection is a :class:`SettingsError` naming the file and the
    offending entry by position (``harness[1].onwer``), because a list has no
    other address to report. The entry rules themselves are not restated here:
    each entry is handed to :class:`HarnessSource`, whose ``ValueError`` is
    re-raised as a ``SettingsError``, so the type's rules are the only rules.

    Args:
        data: One already-parsed settings file.
        path: Where it came from, for the message.

    Raises:
        SettingsError: If ``harness`` is not a list — a table from the retired
            three-key model included — if an element is not an object, carries
            a key outside :data:`_HARNESS_ENTRY_KEYS`, carries a retired
            coordinate key, omits ``name`` or ``locator``, fails
            :class:`HarnessSource` construction, or repeats a ``name`` or
            origin key another entry in this same file already used.
    """
    if "harness" not in data:
        return
    entries: list[Any] = data["harness"]
    if not isinstance(entries, list):
        raise SettingsError(
            f"'harness' in {path} must be a list of entry objects "
            f"({{{', '.join(sorted(_HARNESS_ENTRY_KEYS))}}}), "
            f"not {type(entries).__name__}"
        )
    seen_names: set[str] = set()
    seen_origins: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SettingsError(
                f"harness[{index}] in {path} must be an entry object, "
                f"not {type(entry).__name__}"
            )
        retired = sorted(set(entry) & _RETIRED_HARNESS_KEYS)
        if retired:
            raise SettingsError(
                f"harness[{index}] in {path} uses retired coordinate keys "
                f"({', '.join(retired)}); re-run molmcp config harness set "
                f"<locator>"
            )
        strays = sorted(set(entry) - _HARNESS_ENTRY_KEYS)
        if strays:
            raise SettingsError(
                f"unknown setting(s) in {path}: "
                f"{', '.join(f'harness[{index}].{k}' for k in strays)}. "
                f"Known harness entry keys: {', '.join(sorted(_HARNESS_ENTRY_KEYS))}"
            )
        if "name" not in entry:
            raise SettingsError(f"harness[{index}] in {path} has no 'name'")
        if "locator" not in entry:
            raise SettingsError(f"harness[{index}] in {path} has no 'locator'")
        try:
            source = HarnessSource(**entry)
        except ValueError as exc:
            raise SettingsError(f"harness[{index}] in {path}: {exc}") from exc
        if source.name in seen_names:
            raise SettingsError(
                f"harness[{index}] in {path} repeats the name {source.name!r}; "
                f"harness names are typed by hand and are not renamed for you"
            )
        if source.origin_key in seen_origins:
            raise SettingsError(
                f"harness[{index}] in {path} repeats the origin {source.origin_key!r}"
            )
        seen_names.add(source.name)
        seen_origins.add(source.origin_key)


def _reject_object_list_write(key: str, *, leaf: str) -> None:
    """Refuse a string-valued edit verb aimed at a list of entry objects.

    Called first by :func:`set_value`, :func:`add_value` and the value arm of
    :func:`remove_value`, before :func:`_resolve` and therefore before
    :func:`read_settings_file` and any write. For the two writing verbs,
    reaching the write would store ``["x"]`` or append the bare string
    ``"x"``, and the per-entry validator then rejects that value on the *next*
    read — under ``load_settings``, hence under ``config list``, ``get``,
    ``set``, ``remove`` and ``serve`` alike. ``remove_value`` is guarded for
    the opposite reason: it corrupts nothing, it compares a string against
    entry objects and reports that ``'official'`` is not present while an
    entry named ``official`` sits in the file.

    Which keys are refused is read from :data:`_OBJECT_LISTS`, so the next
    list-of-objects setting closes this hole by joining that tuple rather than
    by someone remembering to add a second branch here. Only the *bare* key is
    matched: a dotted ``harness.owner`` cannot equal a top-level table entry
    and is already refused by :func:`_resolve`, whose message names the full
    key and carries its own pointer at the same verb group. Shadowing that
    path here would replace a precise message with a vaguer one.

    The command is **derived** — ``molmcp config {key} {leaf}`` — rather than
    written as a ``harness`` literal, so a second member of
    :data:`_OBJECT_LISTS` gets a correct message only if it also ships that
    verb, which a test asserts rather than a comment promising it. The
    ``leaf`` is the *calling* verb's: answering a refused
    ``config remove harness official`` with ``... harness set`` would be a
    precise misdirection, worse than the vague message it replaces. The shape
    sentence enumerates :data:`_HARNESS_ENTRY_KEYS`, the only entry type
    declared today; a second member has to generalize that line as it joins.

    Args:
        key: The key the caller asked to write, dotted or bare.
        leaf: The ``config <key>`` leaf that does the job the caller was
            attempting — ``"set"`` for :func:`set_value` and :func:`add_value`
            alike, since one entry is authored by name and there is no ``add``
            leaf, and ``"remove"`` for :func:`remove_value`.

    Raises:
        SettingsError: If ``key`` is a bare member of :data:`_OBJECT_LISTS`.
    """
    if key not in _OBJECT_LISTS:
        return
    raise SettingsError(
        f"{key!r} is a list of entry objects, not of strings, so it cannot be "
        f"written one string at a time. Use `molmcp config {key} {leaf}`, "
        f"which addresses one entry by name; an entry carries the keys "
        f"{{{', '.join(sorted(_HARNESS_ENTRY_KEYS))}}}."
    )


def _resolve(
    path: Path, key: str, *, create: bool
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Return ``(root, leaf_name, owning_container)`` for a dotted key.

    A dotted key whose head is a member of :data:`_OBJECT_LISTS` earns one
    extra sentence pointing at that key's own verb group. It names no leaf,
    because this function cannot see whether :func:`set_value` or
    :func:`remove_value` called it; and a head outside the table keeps the
    generic message, because pointing ``excludes.foo`` at a harness verb
    would be a worse error than the vague one it gets today.
    """
    parts = key.split(".")
    if parts[0] not in _SCHEMA:
        raise SettingsError(
            f"unknown setting {parts[0]!r}. Known keys: {', '.join(sorted(_SCHEMA))}"
        )
    if len(parts) > 2 or (len(parts) == 2 and _SCHEMA[parts[0]] is not dict):
        pointer = (
            f"; {parts[0]!r} is a list of named entries, edited one at a "
            f"time by `molmcp config {parts[0]}`"
            if parts[0] in _OBJECT_LISTS
            else ""
        )
        raise SettingsError(f"{key!r} is not a settable path{pointer}")
    allowed = _NESTED_SCHEMA.get(parts[0]) if len(parts) == 2 else None
    if allowed is not None and parts[1] not in allowed:
        raise SettingsError(
            f"unknown setting {key!r}. "
            f"Known {parts[0]} keys: {', '.join(sorted(allowed))}"
        )
    root = read_settings_file(path)
    if len(parts) == 1:
        return root, parts[0], root
    if create:
        container = root.setdefault(parts[0], {})
    else:
        container = root.get(parts[0], {})
    if not isinstance(container, dict):
        raise SettingsError(f"{parts[0]!r} is not an object in {path}")
    return root, parts[1], container


def _parse(key: str, value: str) -> Any:
    """Coerce a command-line string to the type the schema declares."""
    parts = key.split(".")
    expected = _SCHEMA[parts[0]]
    if len(parts) == 2:  # a member of a dict-valued setting is always a string
        return value
    if expected is bool:
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise SettingsError(f"{key!r} expects a boolean, got {value!r}")
    if expected is int:
        try:
            return int(value)
        except ValueError as exc:
            raise SettingsError(f"{key!r} expects an integer, got {value!r}") from exc
    if expected is list:
        return [value]
    if expected is dict:
        raise SettingsError(f"set a member instead, e.g. {key}.<name> <value>")
    return value


def _harness_entry(values: dict[str, Any]) -> dict[str, Any]:
    """Build one ``harness`` entry, letting the type own every field rule.

    The editing verbs call this both before they read and again on the merged
    result, so the rules an operator's message quotes are :class:`HarnessSource`'s
    own — restated nowhere. The ``ValueError`` is re-raised carrying its text,
    the way :func:`_reject_bad_harness_entries` does for a file on disk;
    only the address differs, since a verb knows a name where a file knows a
    position.

    Only the operator fields are written. ``enable is None`` (all) omits the
    key; ``()`` is written as ``[]``; a named tuple is written as a list.
    Derived identity never lands in the file.

    Args:
        values: The fields to construct with; an omitted one takes the
            dataclass default rather than being invented here.

    Returns:
        The entry as a plain dict of operator fields, ``enable`` omitted
        when it is ``None``.

    Raises:
        SettingsError: If :class:`HarnessSource` refuses ``values``.
    """
    operator = {key: values[key] for key in _HARNESS_ENTRY_KEYS if key in values}
    try:
        entry = asdict(HarnessSource(**operator))
    except ValueError as exc:
        raise SettingsError(
            f"harness source {operator.get('name', '')!r}: {exc}"
        ) from exc
    if entry["enable"] is None:
        del entry["enable"]
    else:
        entry["enable"] = list(entry["enable"])
    return entry


def _harness_sources(entries: list[Any]) -> tuple[HarnessSource, ...]:
    """Build the entry tuple from a ``harness`` value every layer accepted.

    Args:
        entries: The merged ``harness`` list. Each layer passed through
            :func:`_reject_bad_harness_entries` on the way in, so every
            element here is already known to construct.

    Returns:
        One :class:`HarnessSource` per element, in file order.
    """
    return tuple(HarnessSource(**entry) for entry in entries)


def _enable_names(value: Any) -> tuple[str, ...] | None:
    """Normalize ``enable`` to ``None`` or a first-seen-unique name tuple."""
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(
            "harness source enable must be a list of names or None, "
            f"got {type(value).__name__}"
        )
    names: list[str] = []
    seen: set[str] = set()
    for token in value:
        if not isinstance(token, str):
            raise ValueError(
                "harness source enable names must be strings, "
                f"got {type(token).__name__}"
            )
        if COMPONENT_NAME_PATTERN.fullmatch(token) is None:
            raise ValueError(
                f"harness source enable name {token!r} is not a component name"
            )
        if token not in seen:
            seen.add(token)
            names.append(token)
    return tuple(names)


def _is_all_flag(tokens: tuple[str, ...], *, flag: str) -> bool:
    """Return whether ``tokens`` is the ``all`` sentinel for one flag."""
    if "all" not in tokens:
        return False
    if tokens != ("all",):
        raise SettingsError(f"cannot mix 'all' with named --{flag} tokens: {tokens}")
    return True


def _merge_enable(
    current: tuple[str, ...] | None,
    *,
    named_enable: tuple[str, ...],
    named_disable: tuple[str, ...],
    enable_all: bool,
    disable_all: bool,
) -> tuple[str, ...] | None:
    """Apply one call's enable/disable flags onto the stored field."""
    if enable_all:
        result: tuple[str, ...] | None = None
    elif named_enable:
        if current is None:
            result = tuple(dict.fromkeys(named_enable))
        else:
            result = tuple(dict.fromkeys((*current, *named_enable)))
    else:
        result = current
    if disable_all:
        return ()
    if named_disable:
        if result is None:
            raise SettingsError(
                "cannot --disable named bundles while enable is all; "
                "pass --enable with the names to keep, or --disable all"
            )
        drop = set(named_disable)
        return tuple(name for name in result if name not in drop)
    return result


def _str_tuple(value: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in value or ()))


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


__all__ = [
    "CONFIG_DIR_NAME",
    "DEFAULT_HARNESS_ALIAS",
    "LOCAL_SETTINGS_NAME",
    "SETTINGS_NAME",
    "HarnessSource",
    "Settings",
    "SettingsError",
    "add_value",
    "get_value",
    "load_settings",
    "match_harness_source",
    "project_settings_path",
    "read_settings_file",
    "remove_harness_source",
    "remove_value",
    "set_harness_source",
    "set_value",
    "settings_layers",
    "user_settings_path",
    "write_settings_file",
]
