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
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

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
#: also means generalizing the key list in the message
#: ``_reject_object_list_write`` raises — that message names this setting's entry
#: keys, and nothing fails if it goes on naming only these.
#:
#: ``harness`` is deliberately in no merge channel at all. The default branch of
#: ``load_settings`` makes the last assignment win, and ``settings_layers``
#: yields lowest precedence first, so the most specific layer's list replaces
#: the others whole. That is the opposite of ``_MERGED_LISTS`` one line above,
#: on purpose: ``extend`` on a first-wins list would land the user file's
#: entries at the front and make the user file outrank the project file.
_OBJECT_LISTS = ("harness",)


@dataclass(frozen=True, slots=True)
class HarnessSource:
    """One named harness repository this install may serve components from.

    A harness is the git repository of the operator's own agent tooling —
    skills, agents, rules, provider planes, discovery overlays. An install
    may name several, and the order they are written in is the order they
    are read in.

    Construction is strict about shape and permissive about absence. The
    coordinates arrive by separate edits, so an empty one is a half-authored
    entry rather than an error; ``name`` is the entry's address — the place
    those remaining fields get filled in later — so it is the one field that
    cannot be deferred. Whether an entry is complete enough to fetch with is
    a serve-time question, not a load-time one.

    ``name`` is held to no grammar beyond "non-empty, no whitespace",
    deliberately: it is user-chosen in exactly the way a ``sources`` key is,
    and an operator who may name an index source ``MolCrafts`` may name a
    harness source ``MolCrafts`` too. A non-empty coordinate must be an
    opaque token — no ``/``, no ``@`` — which is what keeps a second
    ``owner/repo@ref`` parser out of this module; the one that exists lives
    in ``discovery/source/github.py``. Values are rejected, never rewritten.

    There are four fields and no more. A cache location is ``cacheDir`` at
    the top level, and a credential belongs in the environment rather than a
    settings file that can be committed.

    Attributes:
        name: Non-empty, whitespace-free label chosen by the operator.
        owner: GitHub account or organization; ``""`` while unwritten.
        repo: GitHub repository name; ``""`` while unwritten.
        ref: Branch or tag a commit is resolved from — not the commit being
            served, which the activation pointer under the cache directory
            names. ``""`` while unwritten.

    Raises:
        ValueError: If a field is not a string, carries whitespace, is an
            empty ``name``, or is a coordinate holding ``/`` or ``@``.
    """

    name: str
    owner: str = ""
    repo: str = ""
    ref: str = ""

    def __post_init__(self) -> None:
        for entry_field in fields(self):
            value = getattr(self, entry_field.name)
            if not isinstance(value, str):
                raise ValueError(
                    f"harness source {entry_field.name} must be a string, "
                    f"got {type(value).__name__}"
                )
            if any(character.isspace() for character in value):
                raise ValueError(
                    f"harness source {entry_field.name} must not contain "
                    f"whitespace: {value!r}"
                )
            if entry_field.name == "name":
                if not value:
                    raise ValueError("a harness source must have a non-empty name")
            elif "/" in value or "@" in value:
                raise ValueError(
                    f"harness source {entry_field.name} must be an opaque token "
                    f"with no '/' or '@': {value!r}"
                )


#: Keys one ``harness`` entry may carry, derived from the dataclass rather than
#: written out: a hand-written literal would silently reject a fifth field the
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
    #: is the un-harnessed install. Entries are stored as written, half-filled
    #: included — a source's coordinates arrive by separate edits, and under a
    #: list the completion address is the entry's ``name``, which is why
    #: ``name`` is the only field a file cannot leave out. Whether an entry is
    #: complete enough to fetch with is decided at serve time.
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
    _reject_object_list_write(path, key)
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
    _reject_object_list_write(path, key)
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
    """Drop ``key`` outright, or one ``value`` from a list-valued key."""
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


def get_value(data: dict[str, Any], key: str) -> Any:
    """Read a dotted ``key`` out of already-parsed settings data."""
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
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
            a key outside :data:`_HARNESS_ENTRY_KEYS`, omits ``name``, fails
            :class:`HarnessSource` construction, or repeats a ``name`` another
            entry in this same file already used.
    """
    if "harness" not in data:
        return
    entries = data["harness"]
    if not isinstance(entries, list):
        raise SettingsError(
            f"'harness' in {path} must be a list of entry objects "
            f"({{{', '.join(sorted(_HARNESS_ENTRY_KEYS))}}}), "
            f"not {type(entries).__name__}"
        )
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SettingsError(
                f"harness[{index}] in {path} must be an entry object, "
                f"not {type(entry).__name__}"
            )
        strays = sorted(set(entry) - _HARNESS_ENTRY_KEYS)
        if strays:
            raise SettingsError(
                f"unknown setting(s) in {path}: "
                f"{', '.join(f'harness[{index}].{k}' for k in strays)}. "
                f"Known harness entry keys: {', '.join(sorted(_HARNESS_ENTRY_KEYS))}"
            )
        if "name" not in entry:
            raise SettingsError(
                f"harness[{index}] in {path} has no 'name'; a harness source is "
                f"named before its coordinates are filled in"
            )
        try:
            source = HarnessSource(**entry)
        except ValueError as exc:
            raise SettingsError(f"harness[{index}] in {path}: {exc}") from exc
        if source.name in seen:
            raise SettingsError(
                f"harness[{index}] in {path} repeats the name {source.name!r}; "
                f"harness names are typed by hand and are not renamed for you"
            )
        seen.add(source.name)


def _reject_object_list_write(path: Path, key: str) -> None:
    """Refuse a string-valued edit verb aimed at a list of entry objects.

    Called first by :func:`set_value` and :func:`add_value`, before
    :func:`_resolve` and therefore before :func:`read_settings_file` and any
    write. Reaching the write would store ``["x"]`` or append the bare string
    ``"x"``, and the per-entry validator then rejects that value on the *next*
    read — under ``load_settings``, hence under ``config list``, ``get``,
    ``set``, ``remove`` and ``serve`` alike, with no verb left to undo it.

    Which keys are refused is read from :data:`_OBJECT_LISTS`, so the next
    list-of-objects setting closes this hole by joining that tuple rather than
    by someone remembering to add a second branch here. Only the *bare* key is
    matched: a dotted ``harness.owner`` cannot equal a top-level table entry
    and is already refused by :func:`_resolve`, whose message names the full
    key. Shadowing that path here would replace a precise message with a
    vaguer one.

    The shape sentence enumerates :data:`_HARNESS_ENTRY_KEYS`, the only entry
    type declared today; a second member of :data:`_OBJECT_LISTS` has to
    generalize that line as it joins. The message names the settings-file
    shape and no command, because a hint pointing at a verb nothing resolves
    turns the error into the next error.

    Args:
        path: The settings file the caller was about to edit, named in the
            message because editing it is the only way to author an entry.
        key: The key the caller asked to write, dotted or bare.

    Raises:
        SettingsError: If ``key`` is a bare member of :data:`_OBJECT_LISTS`.
    """
    if key not in _OBJECT_LISTS:
        return
    raise SettingsError(
        f"{key!r} is a list of entry objects, not of strings, so it cannot be "
        f"written one string at a time. Author it by editing {path}: give "
        f"{key!r} a JSON array whose elements are objects with the keys "
        f"{{{', '.join(sorted(_HARNESS_ENTRY_KEYS))}}}."
    )


def _resolve(
    path: Path, key: str, *, create: bool
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Return ``(root, leaf_name, owning_container)`` for a dotted key."""
    parts = key.split(".")
    if parts[0] not in _SCHEMA:
        raise SettingsError(
            f"unknown setting {parts[0]!r}. Known keys: {', '.join(sorted(_SCHEMA))}"
        )
    if len(parts) > 2 or (len(parts) == 2 and _SCHEMA[parts[0]] is not dict):
        raise SettingsError(f"{key!r} is not a settable path")
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


def _harness_sources(entries: list[dict[str, str]]) -> tuple[HarnessSource, ...]:
    """Build the entry tuple from a ``harness`` value every layer accepted.

    Args:
        entries: The merged ``harness`` list. Each layer passed through
            :func:`_reject_bad_harness_entries` on the way in, so every
            element here is already known to construct.

    Returns:
        One :class:`HarnessSource` per element, in file order.
    """
    return tuple(HarnessSource(**entry) for entry in entries)


def _str_tuple(value: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in value or ()))


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


__all__ = [
    "CONFIG_DIR_NAME",
    "LOCAL_SETTINGS_NAME",
    "SETTINGS_NAME",
    "HarnessSource",
    "Settings",
    "SettingsError",
    "add_value",
    "get_value",
    "load_settings",
    "project_settings_path",
    "read_settings_file",
    "remove_value",
    "set_value",
    "settings_layers",
    "user_settings_path",
    "write_settings_file",
]
