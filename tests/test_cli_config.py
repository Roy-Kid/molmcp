"""`molmcp config` — the CLI half of the settings surface.

Verb shape follows ``claude config``: list / get / set / add / remove,
plus ``harness set|remove`` — the one key whose value is a list of objects
gets its own nested pair, because the string verbs take a string and
cannot author an entry. The scope default is the one deliberate departure
— writes land in the user file unless ``--project`` is passed, because a
plane server's working directory belongs to whichever MCP client launched
it.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
from pathlib import Path

import pytest

from molmcp import cli
from molmcp import settings as st
from molmcp.config import AppConfig, ConfigurationError
from molmcp.harness_paths import pointer_path


def _user_settings() -> dict:
    path = st.user_settings_path()
    return json.loads(path.read_text()) if path.is_file() else {}


def _subparser_choices(
    parser: argparse.ArgumentParser,
) -> dict[str, argparse.ArgumentParser]:
    """The sub-commands ``parser`` registers, by name.

    The one place in this suite that reads argparse internals. Two tests
    need the registered ``config`` action names — the dispatch-coverage
    test and the ``_OBJECT_LISTS`` obligation — and one private-API
    surface is enough for both. A parser that registers no sub-commands
    answers ``{}`` rather than raising, so a missing leaf shows up as a
    failed assertion instead of a traversal error.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _config_action_parsers() -> dict[str, argparse.ArgumentParser]:
    """Every ``molmcp config <action>`` the real parser registers."""
    return _subparser_choices(_subparser_choices(cli._build_parser())["config"])


def _run(argv: list[str]) -> int:
    """``cli.main`` for a command that must parse, not argparse-exit."""
    try:
        return cli.main(argv)
    except SystemExit as exc:
        raise AssertionError(
            f"cli.main({argv!r}) raised SystemExit({exc.code})"
        ) from exc


def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    return {flag for action in parser._actions for flag in action.option_strings}


def _positional_dests(parser: argparse.ArgumentParser) -> list[str]:
    return [
        action.dest
        for action in parser._actions
        if action.option_strings == [] and action.dest != "help"
    ]


def _cli_imported_targets() -> tuple[str, ...]:
    """Absolute dotted import targets of ``cli.py``, relative imports resolved."""
    path = Path(cli.__file__).resolve()
    parts = ["molmcp"]
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = parts[: len(parts) - (node.level - 1)]
                tail = node.module.split(".") if node.module else []
                module = ".".join([*base, *tail])
            else:
                module = node.module or ""
            found.append(module)
            found.extend(f"{module}.{alias.name}" for alias in node.names)
    return tuple(found)


def _reaches(targets: tuple[str, ...], dotted: str) -> bool:
    return any(
        target == dotted or target.startswith(f"{dotted}.") for target in targets
    )


class TestConfigScope:
    def test_set_writes_the_user_file_by_default(self, home, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        assert cli.main(["config", "set", "indexWorkspace", "true"]) == 0

        assert _user_settings() == {"indexWorkspace": True}

    def test_project_flag_writes_beside_the_project(self, home, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        assert cli.main(["config", "set", "--project", "indexWorkspace", "true"]) == 0

        assert _user_settings() == {}
        written = st.project_settings_path(tmp_path)
        assert json.loads(written.read_text()) == {"indexWorkspace": True}

    def test_local_flag_writes_the_untracked_override(
        self, home, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)

        assert cli.main(["config", "set", "--local", "indexWorkspace", "true"]) == 0

        written = st.project_settings_path(tmp_path, local=True)
        assert json.loads(written.read_text()) == {"indexWorkspace": True}


class TestConfigVerbs:
    def test_list_reports_the_resolved_settings_and_their_layers(
        self, home, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.chdir(tmp_path)
        cli.main(["config", "set", "sources.molpy", "pkg:molpy"])
        capsys.readouterr()

        assert cli.main(["config", "list"]) == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["sources"] == {"molpy": "pkg:molpy"}
        assert str(st.user_settings_path()) in payload["layers"]

    def test_list_prints_harness_as_an_array_of_entry_objects(
        self, home, monkeypatch, tmp_path, capsys
    ):
        """`harness` reaches the terminal as a JSON array, not an object.

        ``Settings.to_dict`` is the second reader of the setting and
        ``config list`` prints what it returns, so the list-of-objects
        shape is user-visible output rather than an internal detail.

        ``to_dict`` is ``asdict`` over the dataclass, so an entry reports
        the three operator fields — including ``enable: None`` when the
        file omitted the key. Derived identity (owner, repo, path) is not
        a field and does not appear.
        """
        monkeypatch.chdir(tmp_path)
        assert (
            _run(["config", "harness", "set", "acme/harness", "--alias", "mine"]) == 0
        )
        capsys.readouterr()

        assert cli.main(["config", "list"]) == 0

        harness = json.loads(capsys.readouterr().out)["harness"]
        assert isinstance(harness, list)
        assert len(harness) == 1
        assert isinstance(harness[0], dict)
        assert set(harness[0]) == {"name", "locator", "enable"}
        assert harness[0] == {
            "name": "mine",
            "locator": "acme/harness",
            "enable": None,
        }

    def test_get_reads_one_key(self, home, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        cli.main(["config", "set", "sources.molpy", "pkg:molpy"])
        capsys.readouterr()

        assert cli.main(["config", "get", "sources.molpy"]) == 0

        assert json.loads(capsys.readouterr().out) == "pkg:molpy"

    def test_get_an_unset_key_is_null_not_an_error(
        self, home, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.chdir(tmp_path)

        assert cli.main(["config", "get", "cacheDir"]) == 0

        assert json.loads(capsys.readouterr().out) is None

    def test_add_appends_to_a_list(self, home, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        cli.main(["config", "add", "excludes", "vendor"])
        cli.main(["config", "add", "excludes", "*.min.js"])

        assert _user_settings()["excludes"] == ["vendor", "*.min.js"]

    def test_remove_drops_a_source(self, home, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        cli.main(["config", "set", "sources.molpy", "pkg:molpy"])

        assert cli.main(["config", "remove", "sources.molpy"]) == 0

        assert _user_settings() == {"sources": {}}


class TestConfigErrors:
    def test_an_unknown_key_is_rejected_with_the_known_ones(
        self, home, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.chdir(tmp_path)

        assert cli.main(["config", "set", "indexWorkspaces", "true"]) == 2

        err = capsys.readouterr().err
        assert err.startswith("molmcp:")
        assert "indexWorkspace" in err

    def test_a_non_boolean_for_a_boolean_key_is_rejected(
        self, home, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.chdir(tmp_path)

        assert cli.main(["config", "set", "indexWorkspace", "maybe"]) == 2

        assert "boolean" in capsys.readouterr().err

    def test_removing_an_absent_key_is_reported(
        self, home, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.chdir(tmp_path)

        assert cli.main(["config", "remove", "sources.nope"]) == 2

        assert capsys.readouterr().err.startswith("molmcp:")


class TestConfigHarness:
    """`molmcp config harness set|remove` — the writer for the one object list.

    ``harness`` is a list of named entry objects, so the string verbs
    cannot author it: ``set`` refuses the bare key and no dotted path into
    an entry exists. These leaves are the CLI's only route to one; the
    settings file itself is still the other, and stays the only one for a
    file these verbs can no longer read.

    The operator types a locator: ``molmcp config harness set MolCrafts/harness``.
    Optional ``--alias``, optional repeatable ``--enable`` / ``--disable``.
    Coordinate flags (``--name --owner --repo --ref --path``) are gone.
    Renaming an existing origin with ``--alias`` goes through
    ``relocate_pointer`` so an activation pointer follows the new name.
    """

    def test_set_writes_the_typed_locator_under_the_default_origin_alias(
        self, home, monkeypatch, tmp_path
    ):
        """The verb drives the real ``settings.set_harness_source``.

        Nothing is monkeypatched, deliberately: a spy standing in for the
        writer would keep passing while the file on disk carried a shape
        no reader accepts, which is the ``faked-seam-hides-broken-reader``
        failure this exact key has already had once.

        The locator is stored as typed. Identity is derived at load, so
        ``owner`` / ``repo`` / ``path`` never become keys in the file.
        """
        monkeypatch.chdir(tmp_path)

        assert _run(["config", "harness", "set", "MolCrafts/harness"]) == 0

        written = _user_settings()["harness"]
        assert written == [{"name": "origin", "locator": "MolCrafts/harness"}]
        assert "owner" not in written[0]
        assert "repo" not in written[0]
        assert "path" not in written[0]
        assert "ref" not in written[0]
        assert "enable" not in written[0]

    def test_alias_names_the_entry(self, home, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        assert (
            _run(
                [
                    "config",
                    "harness",
                    "set",
                    "MolCrafts/harness",
                    "--alias",
                    "official",
                ]
            )
            == 0
        )

        assert _user_settings()["harness"] == [
            {"name": "official", "locator": "MolCrafts/harness"}
        ]

    def test_repeatable_enable_writes_the_named_list(self, home, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        assert (
            _run(
                [
                    "config",
                    "harness",
                    "set",
                    "MolCrafts/harness",
                    "--enable",
                    "sci",
                    "--enable",
                    "dev",
                ]
            )
            == 0
        )

        assert _user_settings()["harness"] == [
            {
                "name": "origin",
                "locator": "MolCrafts/harness",
                "enable": ["sci", "dev"],
            }
        ]

    def test_repeatable_disable_all_writes_an_empty_enable_list(
        self, home, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)

        assert (
            _run(
                [
                    "config",
                    "harness",
                    "set",
                    "MolCrafts/harness",
                    "--disable",
                    "all",
                ]
            )
            == 0
        )

        assert _user_settings()["harness"] == [
            {"name": "origin", "locator": "MolCrafts/harness", "enable": []}
        ]

    def test_the_set_parser_takes_a_positional_locator_and_drops_the_coordinates(
        self,
    ):
        """Retired coordinate flags are gone; locator is positional."""
        set_parser = _subparser_choices(_config_action_parsers()["harness"])["set"]
        flags = _option_strings(set_parser)
        for retired in ("--name", "--owner", "--repo", "--ref", "--path"):
            assert retired not in flags
        assert "--alias" in flags
        assert "--enable" in flags
        assert "--disable" in flags
        assert "locator" in _positional_dests(set_parser)

    def test_retired_coordinate_flags_are_absent_from_set_help(self, capsys):
        """Argparse itself is what refuses the old flags, not the handler."""
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["config", "harness", "set", "--help"])

        assert excinfo.value.code == 0
        help_text = capsys.readouterr().out
        for retired in ("--name", "--owner", "--repo", "--ref", "--path"):
            assert retired not in help_text

    def test_relocate_pointer_takes_config_and_keyword_only_edits(self):
        import molmcp.harness_sync as harness_sync

        assert hasattr(harness_sync, "relocate_pointer")
        parameters = inspect.signature(harness_sync.relocate_pointer).parameters
        assert list(parameters)[:2] == ["config", "settings_path"]
        assert parameters["locator"].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters["name"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_relocate_pointer_renames_the_pointer_file(
        self, home, monkeypatch, tmp_path
    ):
        import molmcp.harness_sync as harness_sync

        assert hasattr(harness_sync, "relocate_pointer")
        monkeypatch.chdir(tmp_path)
        config = AppConfig.from_dict(
            {"schema_version": "2", "cache_dir": str(tmp_path / "cache")},
            workspace_root=tmp_path,
        )
        assert config.cache_dir is not None
        settings_path = st.user_settings_path()
        st.set_harness_source(settings_path, "MolCrafts/harness")
        old = pointer_path(config.cache_dir, "origin")
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("origin-pointer\n", encoding="utf-8")

        harness_sync.relocate_pointer(
            config,
            settings_path,
            locator="MolCrafts/harness",
            name="official",
        )

        assert not old.exists()
        assert (
            pointer_path(config.cache_dir, "official").read_text(encoding="utf-8")
            == "origin-pointer\n"
        )
        assert json.loads(settings_path.read_text())["harness"] == [
            {"name": "official", "locator": "MolCrafts/harness"}
        ]

    def test_relocate_pointer_refuses_when_the_target_pointer_already_exists(
        self, home, monkeypatch, tmp_path
    ):
        import molmcp.harness_sync as harness_sync

        assert hasattr(harness_sync, "relocate_pointer")
        monkeypatch.chdir(tmp_path)
        config = AppConfig.from_dict(
            {"schema_version": "2", "cache_dir": str(tmp_path / "cache")},
            workspace_root=tmp_path,
        )
        assert config.cache_dir is not None
        settings_path = st.user_settings_path()
        st.set_harness_source(settings_path, "MolCrafts/harness")
        old = pointer_path(config.cache_dir, "origin")
        new = pointer_path(config.cache_dir, "official")
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("origin-pointer\n", encoding="utf-8")
        new.write_text("already-official\n", encoding="utf-8")
        before = settings_path.read_text(encoding="utf-8")

        with pytest.raises((ConfigurationError, st.SettingsError)):
            harness_sync.relocate_pointer(
                config,
                settings_path,
                locator="MolCrafts/harness",
                name="official",
            )

        assert settings_path.read_text(encoding="utf-8") == before
        assert old.read_text(encoding="utf-8") == "origin-pointer\n"
        assert new.read_text(encoding="utf-8") == "already-official\n"

    def test_relocate_pointer_renames_the_entry_when_no_pointer_file_exists(
        self, home, monkeypatch, tmp_path
    ):
        import molmcp.harness_sync as harness_sync

        assert hasattr(harness_sync, "relocate_pointer")
        monkeypatch.chdir(tmp_path)
        config = AppConfig.from_dict(
            {"schema_version": "2", "cache_dir": str(tmp_path / "cache")},
            workspace_root=tmp_path,
        )
        assert config.cache_dir is not None
        settings_path = st.user_settings_path()
        st.set_harness_source(settings_path, "MolCrafts/harness")

        harness_sync.relocate_pointer(
            config,
            settings_path,
            locator="MolCrafts/harness",
            name="official",
        )

        assert json.loads(settings_path.read_text())["harness"] == [
            {"name": "official", "locator": "MolCrafts/harness"}
        ]
        assert not pointer_path(config.cache_dir, "origin").exists()
        assert not pointer_path(config.cache_dir, "official").exists()

    def test_rename_with_alias_relocates_an_existing_pointer_file(
        self, home, monkeypatch, tmp_path
    ):
        """CLI set with a new ``--alias`` moves ``harness.origin.pointer``."""
        monkeypatch.chdir(tmp_path)
        cache = (tmp_path / "cache").resolve()
        assert _run(["config", "set", "cacheDir", str(cache)]) == 0
        assert _run(["config", "harness", "set", "MolCrafts/harness"]) == 0
        old = pointer_path(cache, "origin")
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("origin-pointer\n", encoding="utf-8")

        assert (
            _run(
                [
                    "config",
                    "harness",
                    "set",
                    "MolCrafts/harness",
                    "--alias",
                    "official",
                ]
            )
            == 0
        )

        assert not old.exists()
        assert pointer_path(cache, "official").read_text(encoding="utf-8") == (
            "origin-pointer\n"
        )
        assert _user_settings()["harness"] == [
            {"name": "official", "locator": "MolCrafts/harness"}
        ]

    def test_cli_does_not_import_locator_or_harness_paths(self):
        """``cli.py`` reaches the namer through ``relocate_pointer``, not itself."""
        imported = _cli_imported_targets()
        assert not _reaches(imported, "molmcp.components.locator")
        assert not _reaches(imported, "molmcp.harness_paths")

    def test_project_flag_writes_beside_the_project(self, home, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        assert _run(["config", "harness", "set", "--project", "acme/harness"]) == 0

        assert _user_settings() == {}
        written = st.project_settings_path(tmp_path)
        assert json.loads(written.read_text())["harness"][0]["name"] == "origin"
        assert json.loads(written.read_text())["harness"][0]["locator"] == (
            "acme/harness"
        )

    def test_local_flag_writes_the_untracked_override(
        self, home, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)

        assert _run(["config", "harness", "set", "--local", "acme/harness"]) == 0

        assert _user_settings() == {}
        written = st.project_settings_path(tmp_path, local=True)
        assert json.loads(written.read_text())["harness"][0]["name"] == "origin"

    def test_the_remove_leaf_takes_the_scope_flags_too(
        self, home, monkeypatch, tmp_path
    ):
        """Both leaves compose with ``_scope_arguments``, not just ``set``."""
        monkeypatch.chdir(tmp_path)
        _run(["config", "harness", "set", "--project", "acme/harness"])

        assert _run(["config", "harness", "remove", "--project", "origin"]) == 0

        assert _user_settings() == {}
        written = st.project_settings_path(tmp_path)
        assert json.loads(written.read_text()) == {"harness": []}

    def test_remove_drops_the_entry_by_alias(self, home, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        _run(["config", "harness", "set", "MolCrafts/harness", "--alias", "official"])

        assert _run(["config", "harness", "remove", "official"]) == 0

        assert _user_settings() == {"harness": []}

    def test_remove_drops_the_entry_by_locator(self, home, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        _run(["config", "harness", "set", "MolCrafts/harness", "--alias", "official"])

        assert _run(["config", "harness", "remove", "MolCrafts/harness"]) == 0

        assert _user_settings() == {"harness": []}

    def test_removing_an_unknown_name_is_reported(
        self, home, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.chdir(tmp_path)
        _run(["config", "harness", "set", "MolCrafts/harness"])
        capsys.readouterr()

        assert cli.main(["config", "harness", "remove", "nope"]) == 2

        err = capsys.readouterr().err
        assert err.startswith("molmcp:")
        assert "nope" in err

    def test_get_a_dotted_harness_key_is_an_error_not_null(
        self, home, monkeypatch, tmp_path, capsys
    ):
        """`harness.owner` is a path that cannot exist, so it is not `null`.

        ``harness`` is a list; answering ``null`` for a member read on it
        reports "unset" for a coordinate that no spelling of the settings
        file could ever set.
        """
        monkeypatch.chdir(tmp_path)

        assert cli.main(["config", "get", "harness.owner"]) == 2

        err = capsys.readouterr().err
        assert err.startswith("molmcp:")
        assert "harness.owner" in err

    def test_an_unhandled_config_action_raises_instead_of_removing(
        self, home, monkeypatch, tmp_path
    ):
        """`_config`'s chain ends in a raise, not in a silent `remove_value`.

        ``config_action`` is ``required=True`` with fixed choices, so an
        unknown action cannot reach ``_config`` through ``cli.main`` at
        all — argparse exits 2 first. The Namespace is therefore built by
        hand and handed straight to the handler, which is the only way to
        reach the tail of the chain. The spy is a secondary assertion: the
        criterion is that the raise happens.
        """
        monkeypatch.chdir(tmp_path)
        removed: list[tuple] = []
        monkeypatch.setattr(
            st, "remove_value", lambda *call, **kwargs: removed.append(call)
        )

        with pytest.raises(ConfigurationError):
            cli._config(argparse.Namespace(config_action="teleport"))

        assert removed == []

    def test_every_registered_config_action_is_dispatched(
        self, home, monkeypatch, tmp_path, capsys
    ):
        """A subparser landing without a branch is what this catches.

        The action names are derived from the real parser rather than
        listed, so a new ``config`` leaf is covered the day it is
        registered. Only ``ConfigurationError`` — the terminal raise —
        counts as undispatched: a branch that *is* wired fails instead on
        ``AttributeError`` for the arguments this bare Namespace does not
        carry, and building a full Namespace per action would copy every
        subparser's argument shape into this test.
        """
        monkeypatch.chdir(tmp_path)
        actions = _config_action_parsers()
        assert actions, "no `config` sub-commands found; the traversal broke"

        undispatched = []
        for action in actions:
            try:
                cli._config(argparse.Namespace(config_action=action))
            except ConfigurationError:
                undispatched.append(action)
            except Exception:
                pass
        capsys.readouterr()

        assert undispatched == []

    def test_every_object_list_member_has_a_set_leaf(self):
        """`_reject_object_list_write` derives a command; this keeps it real.

        That message names ``molmcp config {key} set`` for every member of
        ``_OBJECT_LISTS``, so a second member added without its own verb
        would hand the operator a command nothing resolves. Asserting the
        member alone is not enough — a member offering only ``remove``
        would satisfy that while the derived sentence stayed false.
        """
        actions = _config_action_parsers()

        for member in st._OBJECT_LISTS:
            assert member in actions, (
                f"`molmcp config {member} set` is a derived hint with no parser"
            )
            assert "set" in _subparser_choices(actions[member]), (
                f"`molmcp config {member}` registers no `set` leaf"
            )
