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
import json

import pytest

from molmcp import cli
from molmcp import settings as st
from molmcp.config import ConfigurationError


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
        every field rather than the ones the operator typed: a remote source
        reports the empty ``path`` of the local origin it did not name, the
        same way a half-authored one reports an empty ``ref``. The written
        *file* is the narrower shape, which the two write tests below pin.
        """
        monkeypatch.chdir(tmp_path)
        entry = {"name": "mine", "owner": "acme", "repo": "harness", "ref": "main"}
        cli.main(
            [
                "config",
                "harness",
                "set",
                "--name",
                "mine",
                "--owner",
                "acme",
                "--repo",
                "harness",
                "--ref",
                "main",
            ]
        )
        capsys.readouterr()

        assert cli.main(["config", "list"]) == 0

        harness = json.loads(capsys.readouterr().out)["harness"]
        assert isinstance(harness, list)
        assert len(harness) == 1
        assert isinstance(harness[0], dict)
        assert set(harness[0]) == {"name", "owner", "repo", "ref", "path"}
        assert harness[0] == {**entry, "path": ""}

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
    """

    def test_set_writes_the_named_entry_to_the_user_file(
        self, home, monkeypatch, tmp_path
    ):
        """The verb drives the real ``settings.set_harness_source``.

        Nothing is monkeypatched, deliberately: a spy standing in for the
        writer would keep passing while the file on disk carried a shape
        no reader accepts, which is the ``faked-seam-hides-broken-reader``
        failure this exact key has already had once.
        """
        monkeypatch.chdir(tmp_path)

        assert (
            cli.main(
                [
                    "config",
                    "harness",
                    "set",
                    "--name",
                    "official",
                    "--owner",
                    "MolCrafts",
                    "--repo",
                    "harness",
                    "--ref",
                    "main",
                ]
            )
            == 0
        )

        assert _user_settings() == {
            "harness": [
                {
                    "name": "official",
                    "owner": "MolCrafts",
                    "repo": "harness",
                    "ref": "main",
                }
            ]
        }

    def test_project_flag_writes_beside_the_project(self, home, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        assert (
            cli.main(
                ["config", "harness", "set", "--project", "--name", "mine"],
            )
            == 0
        )

        assert _user_settings() == {}
        written = st.project_settings_path(tmp_path)
        assert json.loads(written.read_text())["harness"][0]["name"] == "mine"

    def test_local_flag_writes_the_untracked_override(
        self, home, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)

        assert (
            cli.main(
                ["config", "harness", "set", "--local", "--name", "mine"],
            )
            == 0
        )

        assert _user_settings() == {}
        written = st.project_settings_path(tmp_path, local=True)
        assert json.loads(written.read_text())["harness"][0]["name"] == "mine"

    def test_the_remove_leaf_takes_the_scope_flags_too(
        self, home, monkeypatch, tmp_path
    ):
        """Both leaves compose with ``_scope_arguments``, not just ``set``."""
        monkeypatch.chdir(tmp_path)
        cli.main(["config", "harness", "set", "--project", "--name", "mine"])

        assert (
            cli.main(["config", "harness", "remove", "--project", "--name", "mine"])
            == 0
        )

        assert _user_settings() == {}
        written = st.project_settings_path(tmp_path)
        assert json.loads(written.read_text()) == {"harness": []}

    def test_a_name_alone_writes_a_name_only_entry(self, home, monkeypatch, tmp_path):
        """Partial authoring survives the CLI.

        The coordinates arrive by separate edits, so none of them may be
        defaulted to a value nobody typed. Whether the entry is complete
        enough to fetch from is a serve-time question this verb does not
        answer.
        """
        monkeypatch.chdir(tmp_path)

        assert cli.main(["config", "harness", "set", "--name", "mine"]) == 0

        assert _user_settings() == {
            "harness": [{"name": "mine", "owner": "", "repo": "", "ref": ""}]
        }

    def test_the_path_flag_writes_a_local_entry(self, home, monkeypatch, tmp_path):
        """`--path` is the CLI's only route to the local origin.

        A checkout on disk is the one way to name a harness that is not
        published anywhere, so it is the first thing an operator writing
        their own harness types — and until now the flag had no test at all,
        which left the whole local install resting on a ``dest=`` spelling
        (``--path`` maps to ``source_path``, because ``path`` is already the
        settings file being edited) that nothing checked.

        Nothing is monkeypatched: the assertion is the file on disk, for the
        same reason the coordinate test above gives.
        """
        monkeypatch.chdir(tmp_path)
        checkout = tmp_path / "harness"

        assert (
            cli.main(
                ["config", "harness", "set", "--name", "mine", "--path", str(checkout)]
            )
            == 0
        )

        assert _user_settings() == {
            "harness": [
                {
                    "name": "mine",
                    "owner": "",
                    "repo": "",
                    "ref": "",
                    "path": str(checkout),
                }
            ]
        }

    def test_a_path_and_a_coordinate_in_one_invocation_is_refused(
        self, home, monkeypatch, tmp_path, capsys
    ):
        """One entry names one origin, and argparse is not what says so.

        The two flags are deliberately *not* an
        ``add_mutually_exclusive_group``: that would only police the one
        invocation being typed and would miss the coordinate already sitting
        in the file. The rule lives on ``HarnessSource``, so the refusal has
        to arrive as a ``molmcp:`` sentence rather than an argparse usage
        line, and it has to leave nothing behind.
        """
        monkeypatch.chdir(tmp_path)

        assert (
            cli.main(
                [
                    "config",
                    "harness",
                    "set",
                    "--name",
                    "mine",
                    "--owner",
                    "acme",
                    "--path",
                    str(tmp_path / "harness"),
                ]
            )
            == 2
        )

        assert capsys.readouterr().err.startswith("molmcp:")
        assert _user_settings() == {}

    def test_a_path_added_to_an_existing_coordinate_entry_leaves_the_file_alone(
        self, home, monkeypatch, tmp_path, capsys
    ):
        """The second edit is where the one-origin rule earns its keep.

        An entry is authored across several invocations, so the illegal pair
        is usually assembled rather than typed: a remote source already on
        disk, then ``--path`` on the same name. The merged entry is the one
        that must be refused, and the already-configured remote source must
        survive the refusal intact.
        """
        monkeypatch.chdir(tmp_path)
        cli.main(
            [
                "config",
                "harness",
                "set",
                "--name",
                "official",
                "--owner",
                "MolCrafts",
                "--repo",
                "harness",
                "--ref",
                "main",
            ]
        )
        before = _user_settings()
        capsys.readouterr()

        assert (
            cli.main(
                [
                    "config",
                    "harness",
                    "set",
                    "--name",
                    "official",
                    "--path",
                    str(tmp_path / "harness"),
                ]
            )
            == 2
        )

        assert capsys.readouterr().err.startswith("molmcp:")
        assert _user_settings() == before

    def test_remove_drops_the_entry_and_leaves_an_empty_list(
        self, home, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)
        cli.main(["config", "harness", "set", "--name", "official"])

        assert cli.main(["config", "harness", "remove", "--name", "official"]) == 0

        assert _user_settings() == {"harness": []}

    def test_removing_an_unknown_name_is_reported(
        self, home, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.chdir(tmp_path)
        cli.main(["config", "harness", "set", "--name", "official"])
        capsys.readouterr()

        assert cli.main(["config", "harness", "remove", "--name", "nope"]) == 2

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
