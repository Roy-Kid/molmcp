"""Layered molmcp settings — the control surface for what gets indexed.

Modelled on Claude Code: a user file at ``~/.molmcp/settings.json``, an
optional checked-in project file, and an untracked local override, merged
in that order. Unlike Claude Code the *user* file is the primary surface,
because a plane server is launched by an MCP client whose working
directory is arbitrary.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import pathlib
import sys

import pytest

from molmcp import settings as st


def _write(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class TestSettingsLocations:
    def test_user_settings_live_under_a_dot_molmcp_home_directory(self, home):
        assert st.user_settings_path() == home / ".molmcp" / "settings.json"

    def test_project_settings_live_beside_the_project(self, home, tmp_path):
        project = tmp_path / "repo"
        assert (
            st.project_settings_path(project) == project / ".molmcp" / "settings.json"
        )
        assert (
            st.project_settings_path(project, local=True)
            == project / ".molmcp" / "settings.local.json"
        )


class TestSettingsMerge:
    def test_missing_files_yield_documented_defaults(self, home, tmp_path):
        loaded = st.load_settings(tmp_path / "repo")

        assert loaded.sources == {}
        assert loaded.index_workspace is False

    def test_project_overrides_user(self, home, tmp_path):
        _write(st.user_settings_path(), {"indexWorkspace": True})
        project = tmp_path / "repo"
        _write(st.project_settings_path(project), {"indexWorkspace": False})

        assert st.load_settings(project).index_workspace is False

    def test_local_overrides_project(self, home, tmp_path):
        project = tmp_path / "repo"
        _write(st.project_settings_path(project), {"indexWorkspace": False})
        _write(st.project_settings_path(project, local=True), {"indexWorkspace": True})

        assert st.load_settings(project).index_workspace is True

    def test_sources_merge_across_layers_rather_than_replacing(self, home, tmp_path):
        _write(st.user_settings_path(), {"sources": {"molpy": "pkg:molpy"}})
        project = tmp_path / "repo"
        _write(st.project_settings_path(project), {"sources": {"local": "./src"}})

        assert st.load_settings(project).sources == {
            "molpy": "pkg:molpy",
            "local": "./src",
        }

    def test_a_later_layer_can_shadow_one_source_name(self, home, tmp_path):
        _write(st.user_settings_path(), {"sources": {"molpy": "pkg:molpy"}})
        project = tmp_path / "repo"
        _write(st.project_settings_path(project), {"sources": {"molpy": "./vendor"}})

        assert st.load_settings(project).sources == {"molpy": "./vendor"}

    def test_excludes_accumulate_across_layers(self, home, tmp_path):
        _write(st.user_settings_path(), {"excludes": ["*.min.js"]})
        project = tmp_path / "repo"
        _write(st.project_settings_path(project), {"excludes": ["vendor"]})

        assert st.load_settings(project).excludes == ("*.min.js", "vendor")

    def test_unreadable_settings_are_reported_not_swallowed(self, home, tmp_path):
        path = st.user_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert str(path) in str(excinfo.value)

    def test_unknown_keys_are_rejected_so_typos_do_not_go_silent(self, home, tmp_path):
        _write(st.user_settings_path(), {"indexWorkspaces": True})

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "indexWorkspaces" in str(excinfo.value)


class TestSettingsEdit:
    def test_set_creates_the_file_and_the_directory(self, home):
        st.set_value(st.user_settings_path(), "indexWorkspace", "true")

        assert json.loads(st.user_settings_path().read_text()) == {
            "indexWorkspace": True
        }

    def test_set_a_nested_source_by_dotted_key(self, home):
        st.set_value(st.user_settings_path(), "sources.molpy", "pkg:molpy")

        assert json.loads(st.user_settings_path().read_text()) == {
            "sources": {"molpy": "pkg:molpy"}
        }

    def test_add_appends_to_a_list_valued_key(self, home):
        st.add_value(st.user_settings_path(), "excludes", "vendor")
        st.add_value(st.user_settings_path(), "excludes", "*.min.js")

        assert json.loads(st.user_settings_path().read_text()) == {
            "excludes": ["vendor", "*.min.js"]
        }

    def test_add_is_idempotent(self, home):
        st.add_value(st.user_settings_path(), "excludes", "vendor")
        st.add_value(st.user_settings_path(), "excludes", "vendor")

        assert json.loads(st.user_settings_path().read_text())["excludes"] == ["vendor"]

    def test_remove_drops_a_key(self, home):
        st.set_value(st.user_settings_path(), "sources.molpy", "pkg:molpy")
        st.set_value(st.user_settings_path(), "sources.molvis", "pkg:molvis")

        st.remove_value(st.user_settings_path(), "sources.molpy")

        assert json.loads(st.user_settings_path().read_text()) == {
            "sources": {"molvis": "pkg:molvis"}
        }

    def test_remove_reports_an_absent_key(self, home):
        with pytest.raises(st.SettingsError):
            st.remove_value(st.user_settings_path(), "sources.nope")

    def test_set_rejects_an_unknown_key(self, home):
        with pytest.raises(st.SettingsError):
            st.set_value(st.user_settings_path(), "indexWorkspaces", "true")

    def test_booleans_and_integers_are_parsed_from_the_command_line(self, home):
        st.set_value(st.user_settings_path(), "indexWorkspace", "false")
        st.set_value(st.user_settings_path(), "maxCacheBytes", "1048576")

        data = json.loads(st.user_settings_path().read_text())
        assert data["indexWorkspace"] is False
        assert data["maxCacheBytes"] == 1048576


class TestHarnessSourceEdit:
    """The verbs that address one ``harness`` entry by origin, not by name.

    ``harness`` is a list of objects, so the string-valued verbs one class
    below refuse it outright; these are what authors an entry instead of an
    editor. They do not retire the editor: they write into a file that
    already parses, so one that fails validation on read still needs one.

    The address is the locator's ``origin_key``. A second spelling of the
    same GitHub repository updates that one entry in place; a new origin is
    appended **last**. The alias is optional: the first insert without one
    is named ``origin``, and a later insert without one is refused once that
    alias is taken. ``enable`` / ``disable`` empty means "leave as it was"
    — on insert that is ``None`` (all), which the file records by omitting
    the key. ``disable=("all",)`` stores ``[]`` and keeps the source.

    Two orderings are binding rather than incidental. Arguments are
    validated by constructing a :class:`~molmcp.settings.HarnessSource`
    *before* the file is read, so a refused call leaves no file behind at
    all; and dropping the last entry leaves ``"harness": []`` rather than
    removing the key, which is ``remove_value``'s different job.
    """

    def test_set_harness_source_takes_a_locator_and_keyword_only_edits(self):
        parameters = inspect.signature(st.set_harness_source).parameters

        assert list(parameters) == ["path", "locator", "alias", "enable", "disable"]
        assert parameters["locator"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert parameters["alias"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_the_default_alias_constant_is_origin(self):
        assert st.DEFAULT_HARNESS_ALIAS == "origin"

    def test_a_first_insert_without_alias_is_named_origin_and_omits_enable(
        self, home, tmp_path
    ):
        path = st.user_settings_path()

        st.set_harness_source(path, "molcrafts/harness")

        assert json.loads(path.read_text()) == {
            "harness": [{"name": "origin", "locator": "molcrafts/harness"}]
        }
        loaded = st.load_settings(tmp_path / "repo").harness
        assert loaded == (st.HarnessSource(name="origin", locator="molcrafts/harness"),)
        assert loaded[0].enable is None
        assert "enable" not in json.loads(path.read_text())["harness"][0]

    def test_the_same_origin_under_a_new_spelling_updates_that_entry_in_place(
        self, home
    ):
        path = st.user_settings_path()
        st.set_harness_source(path, "MolCrafts/harness", alias="official")

        st.set_harness_source(path, "https://github.com/MolCrafts/harness.git")

        entries = json.loads(path.read_text())["harness"]
        assert len(entries) == 1
        assert entries[0] == {
            "name": "official",
            "locator": "https://github.com/MolCrafts/harness.git",
        }

    def test_an_update_without_alias_keeps_the_name_already_stored(self, home):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness", alias="official")

        st.set_harness_source(path, "molcrafts/harness@dev")

        assert json.loads(path.read_text())["harness"][0]["name"] == "official"
        assert json.loads(path.read_text())["harness"][0]["locator"] == (
            "molcrafts/harness@dev"
        )

    def test_a_new_origin_with_an_alias_is_appended_last(self, home):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness")

        st.set_harness_source(path, "acme/harness", alias="mine")

        entries = json.loads(path.read_text())["harness"]
        assert [entry["name"] for entry in entries] == ["origin", "mine"]
        assert [entry["locator"] for entry in entries] == [
            "molcrafts/harness",
            "acme/harness",
        ]

    def test_a_second_origin_without_alias_is_refused_once_origin_is_taken(self, home):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness")
        before = path.read_text(encoding="utf-8")

        with pytest.raises(st.SettingsError) as excinfo:
            st.set_harness_source(path, "acme/harness")

        assert "alias" in str(excinfo.value)
        assert path.read_text(encoding="utf-8") == before

    def test_a_second_origin_without_alias_is_named_origin_when_that_alias_is_free(
        self, home
    ):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness", alias="official")

        st.set_harness_source(path, "acme/harness")

        assert [entry["name"] for entry in json.loads(path.read_text())["harness"]] == [
            "official",
            "origin",
        ]

    def test_an_alias_renames_the_matched_origin(self, home):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness")

        st.set_harness_source(path, "molcrafts/harness", alias="official")

        assert json.loads(path.read_text())["harness"] == [
            {"name": "official", "locator": "molcrafts/harness"}
        ]

    def test_an_alias_that_another_entry_already_uses_is_refused(self, home):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness", alias="official")
        st.set_harness_source(path, "acme/harness", alias="mine")
        before = path.read_text(encoding="utf-8")

        with pytest.raises(st.SettingsError) as excinfo:
            st.set_harness_source(path, "acme/harness", alias="official")

        assert "official" in str(excinfo.value)
        assert path.read_text(encoding="utf-8") == before

    def test_disable_all_persists_an_empty_enable_list_and_keeps_the_entry(
        self, home, tmp_path
    ):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness")

        st.set_harness_source(path, "molcrafts/harness", disable=("all",))

        assert json.loads(path.read_text())["harness"] == [
            {"name": "origin", "locator": "molcrafts/harness", "enable": []}
        ]
        loaded = st.load_settings(tmp_path / "repo").harness
        assert len(loaded) == 1
        assert loaded[0].enable == ()
        assert loaded[0].name == "origin"

    def test_disable_all_on_insert_still_writes_the_source(self, home, tmp_path):
        path = st.user_settings_path()

        st.set_harness_source(path, "molcrafts/harness", disable=("all",))

        assert json.loads(path.read_text())["harness"][0]["enable"] == []
        assert st.load_settings(tmp_path / "repo").harness[0].enable == ()

    def test_named_enable_on_insert_is_the_list_that_lands_in_the_file(self, home):
        path = st.user_settings_path()

        st.set_harness_source(path, "molcrafts/harness", enable=("sci", "dev"))

        assert json.loads(path.read_text())["harness"] == [
            {
                "name": "origin",
                "locator": "molcrafts/harness",
                "enable": ["sci", "dev"],
            }
        ]

    def test_named_enable_replaces_the_all_sentinel(self, home, tmp_path):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness")

        st.set_harness_source(path, "molcrafts/harness", enable=("sci",))

        assert st.load_settings(tmp_path / "repo").harness[0].enable == ("sci",)

    def test_named_enable_unions_an_already_explicit_list(self, home, tmp_path):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness", enable=("sci",))

        st.set_harness_source(path, "molcrafts/harness", enable=("dev",))

        assert st.load_settings(tmp_path / "repo").harness[0].enable == ("sci", "dev")

    def test_named_disable_subtracts_from_an_explicit_list(self, home, tmp_path):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness", enable=("sci", "dev"))

        st.set_harness_source(path, "molcrafts/harness", disable=("sci",))

        assert st.load_settings(tmp_path / "repo").harness[0].enable == ("dev",)

    def test_named_disable_of_the_last_name_leaves_the_empty_tuple_not_all(
        self, home, tmp_path
    ):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness", enable=("sci",))

        st.set_harness_source(path, "molcrafts/harness", disable=("sci",))

        loaded = st.load_settings(tmp_path / "repo").harness[0]
        assert loaded.enable == ()
        assert json.loads(path.read_text())["harness"][0]["enable"] == []

    def test_named_disable_on_the_all_sentinel_is_refused(self, home):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness")
        before = path.read_text(encoding="utf-8")

        with pytest.raises(st.SettingsError):
            st.set_harness_source(path, "molcrafts/harness", disable=("sci",))

        assert path.read_text(encoding="utf-8") == before

    def test_enable_all_restores_the_omitted_key(self, home, tmp_path):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness", enable=("sci",))

        st.set_harness_source(path, "molcrafts/harness", enable=("all",))

        assert "enable" not in json.loads(path.read_text())["harness"][0]
        assert st.load_settings(tmp_path / "repo").harness[0].enable is None

    def test_enable_all_must_not_share_the_call_with_a_named_enable(self, home):
        with pytest.raises(st.SettingsError):
            st.set_harness_source(
                st.user_settings_path(),
                "molcrafts/harness",
                enable=("all", "sci"),
            )

        assert not st.user_settings_path().exists()

    def test_enable_all_must_not_share_the_call_with_disable_all(self, home):
        with pytest.raises(st.SettingsError):
            st.set_harness_source(
                st.user_settings_path(),
                "molcrafts/harness",
                enable=("all",),
                disable=("all",),
            )

        assert not st.user_settings_path().exists()

    def test_empty_enable_and_disable_on_update_leave_the_field_as_it_was(
        self, home, tmp_path
    ):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness", enable=("sci",))

        st.set_harness_source(path, "molcrafts/harness")

        assert st.load_settings(tmp_path / "repo").harness[0].enable == ("sci",)

    def test_a_refused_locator_creates_no_file_at_all(self, home):
        with pytest.raises(st.SettingsError):
            st.set_harness_source(st.user_settings_path(), "./checkout")

        assert not st.user_settings_path().exists()

    def test_a_refused_enable_token_creates_no_file_at_all(self, home):
        with pytest.raises(st.SettingsError):
            st.set_harness_source(
                st.user_settings_path(), "molcrafts/harness", enable=("foo_bar",)
            )

        assert not st.user_settings_path().exists()

    def test_the_dataclass_message_is_the_one_the_operator_reads(self, home):
        with pytest.raises(ValueError) as from_the_type:
            st.HarnessSource(name="my harness", locator="molcrafts/harness")

        with pytest.raises(st.SettingsError) as from_the_verb:
            st.set_harness_source(
                st.user_settings_path(), "molcrafts/harness", alias="my harness"
            )

        assert str(from_the_type.value) in str(from_the_verb.value)

    def test_match_harness_source_is_exported_beside_the_edit_verbs(self):
        assert callable(st.match_harness_source)
        assert "match_harness_source" in st.__all__

    def test_match_harness_source_hits_an_exact_name_first(self):
        sources = (
            st.HarnessSource(name="official", locator="molcrafts/harness"),
            st.HarnessSource(name="mine", locator="acme/harness"),
        )

        assert st.match_harness_source(sources, "mine") == sources[1]

    def test_match_harness_source_hits_origin_key_when_the_token_is_not_a_name(self):
        sources = (st.HarnessSource(name="official", locator="molcrafts/harness"),)

        matched = st.match_harness_source(
            sources, "https://github.com/MolCrafts/harness.git"
        )

        assert matched == sources[0]

    def test_match_harness_source_treats_a_ref_as_not_part_of_identity(self):
        sources = (st.HarnessSource(name="official", locator="molcrafts/harness"),)

        assert st.match_harness_source(sources, "MolCrafts/harness@dev") == sources[0]

    def test_match_harness_source_prefers_name_when_a_token_could_be_either(self):
        sources = (
            st.HarnessSource(name="molcrafts/harness", locator="acme/other"),
            st.HarnessSource(name="official", locator="molcrafts/harness"),
        )

        assert st.match_harness_source(sources, "molcrafts/harness") == sources[0]

    def test_remove_drops_the_named_entry_and_leaves_the_others_in_order(self, home):
        path = st.user_settings_path()
        st.set_harness_source(path, "acme/first", alias="first")
        st.set_harness_source(path, "acme/second", alias="second")
        st.set_harness_source(path, "acme/third", alias="third")

        st.remove_harness_source(path, "second")

        entries = json.loads(path.read_text())["harness"]
        assert [entry["name"] for entry in entries] == ["first", "third"]

    def test_remove_accepts_a_locator_for_the_same_origin(self, home, tmp_path):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness", alias="official")

        st.remove_harness_source(path, "https://github.com/molcrafts/harness")

        assert json.loads(path.read_text())["harness"] == []
        assert st.load_settings(tmp_path / "repo").harness == ()

    def test_removing_the_last_entry_leaves_an_empty_list_not_a_missing_key(
        self, home, tmp_path
    ):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness")

        st.remove_harness_source(path, "origin")

        assert json.loads(path.read_text())["harness"] == []
        assert st.load_settings(tmp_path / "repo").harness == ()

    def test_removing_an_absent_token_reports_that_token(self, home):
        path = st.user_settings_path()
        st.set_harness_source(path, "molcrafts/harness")

        with pytest.raises(st.SettingsError) as excinfo:
            st.remove_harness_source(path, "official")

        assert "official" in str(excinfo.value)

    def test_removing_from_a_file_with_no_harness_key_reports_the_file(self, home):
        path = st.user_settings_path()
        _write(path, {"indexWorkspace": True})

        with pytest.raises(st.SettingsError) as excinfo:
            st.remove_harness_source(path, "mine")

        assert str(path) in str(excinfo.value)

    @pytest.mark.parametrize("retired", ["owner", "repo", "ref", "path"])
    def test_old_coordinate_keys_are_a_hard_cut(self, home, tmp_path, retired):
        _write(
            st.user_settings_path(),
            {"harness": [{"name": "official", retired: "MolCrafts"}]},
        )

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "molmcp config harness set <locator>" in str(excinfo.value)

    def test_a_retired_key_is_a_hard_cut_even_when_locator_is_also_present(
        self, home, tmp_path
    ):
        _write(
            st.user_settings_path(),
            {
                "harness": [
                    {
                        "name": "official",
                        "locator": "molcrafts/harness",
                        "owner": "MolCrafts",
                    }
                ]
            },
        )

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "molmcp config harness set <locator>" in str(excinfo.value)

    def test_an_entry_without_a_locator_is_refused(self, home, tmp_path):
        _write(
            st.user_settings_path(),
            {"harness": [{"name": "official"}]},
        )

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "locator" in str(excinfo.value)

    def test_both_verbs_join_all_beside_the_verb_they_extend(self):
        assert (
            st.__all__.index("remove_harness_source")
            == st.__all__.index("remove_value") - 1
        )
        assert (
            st.__all__.index("set_harness_source") == st.__all__.index("set_value") - 1
        )


class TestGetValueWalk:
    """The dotted read, whose one condition was doing the work of two.

    A key the data does not carry and a path *through* something that is
    not an object are different answers. An undeclared key is ``null``,
    which reads as "unset"; ``harness.owner`` is not a path at all now that
    ``harness`` is a list of named entries, and answering ``null`` there
    tells an operator the coordinate is unset rather than unreachable —
    the wrong of the two errors, and the one that sends them looking for a
    verb to set it with.

    Which case each arm actually serves is easy to get backwards.
    ``Settings.to_dict()`` always carries every key, ``cacheDir`` among
    them, so a bare ``cacheDir`` read answers ``None`` because the *value*
    is ``None`` and the walk ends — never through the missing-key arm at
    all. That arm is reachable only for keys ``to_dict()`` does not carry:
    ``nope`` and ``sources.nope``. Both are pinned below, because they are
    what keeps this fix narrow.

    The head key is deliberately not checked against ``_SCHEMA``:
    ``to_dict()`` emits ``layers``, which ``_SCHEMA`` does not declare, so
    validating there would break a read that works today.
    """

    @pytest.mark.parametrize(
        "key",
        ["harness.owner", "cacheDir.x", "excludes.x", "indexWorkspace.x", "layers.x"],
    )
    def test_descending_through_a_non_object_names_the_key_it_cannot_walk(self, key):
        with pytest.raises(st.SettingsError) as excinfo:
            st.get_value(st.Settings().to_dict(), key)

        assert key in str(excinfo.value)

    def test_an_undeclared_top_level_key_still_reads_as_null(self):
        data = st.Settings().to_dict()

        assert "nope" not in data
        assert st.get_value(data, "nope") is None

    def test_an_undeclared_member_of_a_dict_setting_still_reads_as_null(self):
        data = st.Settings().to_dict()

        assert "nope" not in data["sources"]
        assert st.get_value(data, "sources.nope") is None

    def test_an_unset_value_reads_as_null_by_the_other_route_entirely(self):
        data = st.Settings().to_dict()

        assert "cacheDir" in data
        assert st.get_value(data, "cacheDir") is None

    def test_a_key_the_schema_does_not_declare_is_read_rather_than_validated(
        self, tmp_path
    ):
        layer = tmp_path / "settings.json"
        data = st.Settings(layers=(layer,)).to_dict()

        assert "layers" not in st._SCHEMA
        assert st.get_value(data, "layers") == [str(layer)]

    def test_a_dotted_read_into_a_dict_setting_still_returns_the_member(self):
        data = st.Settings(sources={"molpy": "pkg:molpy"}).to_dict()

        assert st.get_value(data, "sources.molpy") == "pkg:molpy"


class TestHarnessWriteGuard:
    """The string-valued edit verbs cannot author a list of objects.

    ``harness`` became a ``list``, which unlocked two write paths that were
    safely refused while it was a ``dict``: ``config set harness x`` parses
    to ``["x"]`` and ``config add harness x`` appends the bare string. Both
    reach ``write_settings_file`` *before* anything validates, and the
    per-entry validator then rejects ``"x"`` on the next read — under
    ``load_settings``, hence under ``config list``, ``get``, ``set``,
    ``remove`` and ``serve`` alike. ``config harness set`` is no rescue
    from that state: it reads through ``read_settings_file`` like every
    other verb, so it cannot repair a file it cannot load, and that file
    still has to be hand-edited to make the install usable again. The
    binding assertions are therefore that the call raises, that **no file
    is created**, and that a later ``load_settings`` still works.

    What the guard protects is the line between the two kinds of verb, not
    the absence of a writer. ``set`` and ``add`` take a string and still
    refuse this key, because a string verb cannot author a list of
    objects; the verb that can is ``config harness set``, which addresses
    one entry by its ``name`` (``TestHarnessSourceEdit``, above). That is
    why the refusals below name a command rather than an editor.

    The refusal is reached through the declared ``_OBJECT_LISTS`` table
    rather than a ``"harness"`` literal in either function body, so the next
    list of objects closes the same hole by joining the tuple instead of by
    someone remembering to add a second branch.
    """

    def test_the_refusal_is_declared_in_a_table_rather_than_branched_on(self):
        assert "harness" in st._OBJECT_LISTS

    def test_set_refuses_to_write_a_bare_string_to_the_harness_key(self, home):
        with pytest.raises(st.SettingsError):
            st.set_value(st.user_settings_path(), "harness", "x")

        assert not st.user_settings_path().exists()

    def test_add_refuses_to_append_a_bare_string_to_the_harness_key(self, home):
        with pytest.raises(st.SettingsError):
            st.add_value(st.user_settings_path(), "harness", "x")

        assert not st.user_settings_path().exists()

    @pytest.mark.parametrize("write", [st.set_value, st.add_value], ids=["set", "add"])
    def test_a_refused_write_leaves_the_install_loadable(self, home, tmp_path, write):
        with pytest.raises(st.SettingsError):
            write(st.user_settings_path(), "harness", "x")

        assert st.load_settings(tmp_path / "repo").harness == ()

    @pytest.mark.parametrize("member", st._OBJECT_LISTS)
    def test_the_refusal_names_the_verb_it_derives_from_the_key(self, home, member):
        """The command is built from ``key``, so the table stays truthful.

        A message that hand-wrote ``harness`` would go stale the day a
        second list of objects joined :data:`_OBJECT_LISTS`, which is the
        drift the table exists to prevent. Naming a verb is only possible
        now that one resolves; until this link there was none, which is
        why the message pointed at an editor instead.
        """
        with pytest.raises(st.SettingsError) as excinfo:
            st.set_value(st.user_settings_path(), member, "x")

        assert f"molmcp config {member} set" in str(excinfo.value)
        assert "by editing" not in str(excinfo.value)

    def test_the_add_refusal_names_the_set_leaf_the_parser_registers(self, home):
        """``config add harness x`` is answered with the leaf that exists.

        There is no ``config harness add``: one entry is authored by name,
        and appending is what ``config harness set`` does with a name it
        has not seen. Naming an unregistered leaf here would turn this
        error message into the next error.
        """
        with pytest.raises(st.SettingsError) as excinfo:
            st.add_value(st.user_settings_path(), "harness", "x")

        assert "molmcp config harness set" in str(excinfo.value)
        assert "by editing" not in str(excinfo.value)

    @pytest.mark.parametrize("member", ["owner", "dev"])
    def test_set_refuses_every_dotted_harness_key_not_only_a_stray_one(
        self, home, member
    ):
        with pytest.raises(st.SettingsError) as excinfo:
            st.set_value(st.user_settings_path(), f"harness.{member}", "x")

        assert f"harness.{member}" in str(excinfo.value)
        assert "molmcp config harness" in str(excinfo.value)
        assert not st.user_settings_path().exists()

    def test_the_dotted_refusal_is_reached_from_remove_as_well_as_set(self, home):
        """One sentence serves both leaves, because ``_resolve`` serves both.

        ``_resolve`` is where a dotted key is refused and it cannot see
        which verb called it, so its sentence names the ``config harness``
        verbs rather than only ``set``.
        """
        with pytest.raises(st.SettingsError) as excinfo:
            st.remove_value(st.user_settings_path(), "harness.owner")

        assert "harness.owner" in str(excinfo.value)
        assert "molmcp config harness" in str(excinfo.value)

    @pytest.mark.parametrize("key", ["excludes.foo", "cacheDir.x"])
    def test_a_dotted_key_outside_the_table_keeps_the_generic_message(self, home, key):
        """Only an ``_OBJECT_LISTS`` head earns the friendlier sentence.

        ``excludes`` and ``cacheDir`` are not lists of entry objects, and
        pointing them at a harness verb would be a worse error than the
        vague one they get today.
        """
        with pytest.raises(st.SettingsError) as excinfo:
            st.set_value(st.user_settings_path(), key, "x")

        assert str(excinfo.value) == f"{key!r} is not a settable path"

    def test_remove_refuses_its_value_arm_and_names_the_remove_leaf(self, home):
        """A remove is answered with a remove, not with a set.

        ``remove_value``'s list arm compares a string against entry
        objects, so ``config remove harness official`` reported that
        ``'official'`` was not present while an entry named ``official``
        sat in the file — vague when entries were unnamed, actively false
        now that they are named. The guard extends to this arm only:
        dropping the whole key is a different operation, pinned by
        ``test_remove_still_clears_the_key_and_leaves_a_loadable_file``
        below. Answering a remove with ``config harness set`` would be a
        precise misdirection, which is worse than the vague message it
        replaces.
        """
        path = st.user_settings_path()
        _write(path, {"harness": [{"name": "official", "locator": "acme/harness"}]})
        before = path.read_text(encoding="utf-8")

        with pytest.raises(st.SettingsError) as excinfo:
            st.remove_value(path, "harness", "official")

        assert "molmcp config harness remove" in str(excinfo.value)
        assert "molmcp config harness set" not in str(excinfo.value)
        assert "is not present in" not in str(excinfo.value)
        assert path.read_text(encoding="utf-8") == before

    def test_remove_still_clears_the_key_and_leaves_a_loadable_file(
        self, home, tmp_path
    ):
        _write(
            st.user_settings_path(),
            {
                "harness": [{"name": "mine", "locator": "acme/harness"}],
                "indexWorkspace": True,
            },
        )

        st.remove_value(st.user_settings_path(), "harness")

        assert "harness" not in json.loads(st.user_settings_path().read_text())
        assert st.load_settings(tmp_path / "repo").harness == ()


class TestHarnessSource:
    """One named harness source: three operator fields, identity derived.

    Dataclass fields are exactly ``name``, ``locator``, ``enable``. GitHub
    identity and the local path are parsed from ``locator`` at construction
    and are not fields — they do not appear in ``asdict`` or in the file.
    ``enable`` defaults to ``None`` (all); ``()`` is explicit all-off; a
    non-empty tuple is bundle names matching ``COMPONENT_NAME_PATTERN``.
    Construction requires a locator: a name-only half-authored entry is no
    longer a thing this type can represent.
    """

    def test_fields_are_exactly_name_locator_enable(self):
        assert [field.name for field in dataclasses.fields(st.HarnessSource)] == [
            "name",
            "locator",
            "enable",
        ]

    def test_coordinate_fields_are_gone_from_the_type_and_the_module(self):
        names = {field.name for field in dataclasses.fields(st.HarnessSource)}
        for retired in ("owner", "repo", "ref", "path", "origin_key"):
            assert retired not in names
        assert not hasattr(st, "HARNESS_COORDINATES")

    def test_enable_defaults_to_none(self):
        source = st.HarnessSource(name="official", locator="molcrafts/harness")

        assert source.enable is None

    def test_a_github_locator_keeps_operator_fields_and_derives_identity(self):
        source = st.HarnessSource(name="official", locator="MolCrafts/harness@dev")

        assert source.name == "official"
        assert source.locator == "MolCrafts/harness@dev"
        assert source.enable is None
        assert source.origin_key == "molcrafts/harness"
        assert source.owner == "molcrafts"
        assert source.repo == "harness"
        assert source.ref == "dev"
        assert source.path == ""
        assert source.is_local is False

    def test_asdict_is_only_the_operator_fields(self):
        source = st.HarnessSource(name="official", locator="MolCrafts/harness@dev")

        assert dataclasses.asdict(source) == {
            "name": "official",
            "locator": "MolCrafts/harness@dev",
            "enable": None,
        }

    def test_a_name_alone_is_not_constructible(self):
        with pytest.raises(TypeError):
            st.HarnessSource(name="mine")

    @pytest.mark.parametrize("name", ["", "   ", "my harness"])
    def test_an_empty_or_whitespace_bearing_name_is_rejected(self, name):
        with pytest.raises(ValueError):
            st.HarnessSource(name=name, locator="molcrafts/harness")

    def test_a_mixed_case_name_is_as_legal_as_a_mixed_case_source_key(self):
        assert (
            st.HarnessSource(name="MolCrafts", locator="molcrafts/harness").name
            == "MolCrafts"
        )
        assert not hasattr(st, "HARNESS_SOURCE_NAME_PATTERN")

    def test_a_local_locator_derives_the_resolved_path(self, tmp_path):
        raw = str(tmp_path / "harness")
        source = st.HarnessSource(name="mine", locator=raw)
        resolved = str(pathlib.Path(raw).expanduser().resolve())

        assert source.is_local is True
        assert source.path == resolved
        assert source.origin_key == resolved
        assert source.owner == ""
        assert source.repo == ""
        assert source.ref == ""

    def test_an_invalid_locator_is_rejected(self):
        with pytest.raises(ValueError):
            st.HarnessSource(name="mine", locator="./checkout")

    def test_a_locator_that_is_not_a_string_is_refused(self):
        with pytest.raises(ValueError):
            st.HarnessSource(name="mine", locator=pathlib.Path("/home/me/harness"))

    @pytest.mark.parametrize(
        "value", [" ", "/home/me/my harness", "/home/me/harness\t"]
    )
    def test_a_locator_carrying_whitespace_is_refused(self, value):
        with pytest.raises(ValueError):
            st.HarnessSource(name="mine", locator=value)

    def test_a_github_locator_carrying_a_backslash_is_refused(self):
        with pytest.raises(ValueError):
            st.HarnessSource(name="mine", locator=r"MolCrafts\harness")

    def test_a_windows_drive_locator_is_local_only_on_windows(self):
        raw = r"C:\harness"
        if sys.platform == "win32":
            source = st.HarnessSource(name="mine", locator=raw)
            assert source.is_local is True
            assert source.locator == raw
        else:
            with pytest.raises(ValueError):
                st.HarnessSource(name="mine", locator=raw)

    def test_enable_empty_tuple_is_stored_as_the_all_off_sentinel(self):
        source = st.HarnessSource(
            name="official", locator="molcrafts/harness", enable=()
        )

        assert source.enable == ()
        assert dataclasses.asdict(source)["enable"] == ()

    def test_enable_named_tuple_is_stored(self):
        source = st.HarnessSource(
            name="official", locator="molcrafts/harness", enable=("sci", "dev")
        )

        assert source.enable == ("sci", "dev")

    @pytest.mark.parametrize("token", ["foo_bar", "Sci", "sci_dev"])
    def test_an_enable_name_outside_the_component_pattern_is_refused(self, token):
        with pytest.raises(ValueError):
            st.HarnessSource(
                name="official", locator="molcrafts/harness", enable=(token,)
            )

    def test_https_spelling_shares_origin_key_with_owner_repo(self):
        source = st.HarnessSource(
            name="official",
            locator="https://github.com/MolCrafts/harness.git/",
        )

        assert source.origin_key == "molcrafts/harness"
        assert source.owner == "molcrafts"
        assert source.repo == "harness"
        assert source.ref == ""

    def test_the_instance_is_frozen(self):
        source = st.HarnessSource(name="official", locator="molcrafts/harness")

        with pytest.raises(dataclasses.FrozenInstanceError):
            source.name = "other"  # type: ignore[misc]


class TestSettingsHarnessSources:
    """``harness`` as a settings key: a list of objects, and no merge channel.

    The list is not merged across layers — the most specific file's list
    replaces the others whole — which is the opposite of the ``_MERGED_LISTS``
    members twelve lines above it in the module. The asymmetry is intended:
    ``extend`` on a first-wins list would land the user file's entries at the
    front and make the user file outrank the project file, the inverse of
    every other setting.
    """

    def test_harness_is_a_list_setting_with_no_merge_channel(self):
        assert st._SCHEMA.get("harness") is list
        assert "harness" not in st._MERGED_DICTS
        assert "harness" not in st._MERGED_LISTS
        assert "harness" not in st._NESTED_SCHEMA
        assert "harness" in st._OBJECT_LISTS

    def test_the_entry_keys_are_derived_from_the_dataclass_fields(self):
        assert st._HARNESS_ENTRY_KEYS == {
            f.name for f in dataclasses.fields(st.HarnessSource)
        }

    def test_operator_fields_are_the_entry_keys_and_identity_is_not(self):
        assert st._HARNESS_ENTRY_KEYS == {"name", "locator", "enable"}
        for derived in ("origin_key", "ref", "owner", "repo", "path"):
            assert derived not in st._HARNESS_ENTRY_KEYS

    def test_two_entries_in_one_file_load_in_file_order(self, home, tmp_path):
        _write(
            st.user_settings_path(),
            {
                "harness": [
                    {"name": "official", "locator": "molcrafts/harness"},
                    {"name": "team", "locator": "acme/harness@v2"},
                ]
            },
        )

        loaded = st.load_settings(tmp_path / "repo")

        assert [source.name for source in loaded.harness] == ["official", "team"]
        assert [source.origin_key for source in loaded.harness] == [
            "molcrafts/harness",
            "acme/harness",
        ]

    def test_the_most_specific_layer_replaces_the_list_rather_than_merging(
        self, home, tmp_path
    ):
        _write(
            st.user_settings_path(),
            {"harness": [{"name": "user", "locator": "user/harness"}]},
        )
        project = tmp_path / "repo"
        _write(
            st.project_settings_path(project),
            {"harness": [{"name": "project", "locator": "project/harness"}]},
        )
        _write(
            st.project_settings_path(project, local=True),
            {"harness": [{"name": "local", "locator": "local/harness"}]},
        )

        loaded = st.load_settings(project)

        assert [source.name for source in loaded.harness] == ["local"]

    def test_two_entries_sharing_a_name_in_one_file_are_refused(self, home, tmp_path):
        _write(
            st.user_settings_path(),
            {
                "harness": [
                    {"name": "twin", "locator": "molcrafts/harness"},
                    {"name": "twin", "locator": "acme/harness"},
                ]
            },
        )

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "twin" in str(excinfo.value)

    def test_two_entries_sharing_an_origin_key_in_one_file_are_refused(
        self, home, tmp_path
    ):
        _write(
            st.user_settings_path(),
            {
                "harness": [
                    {"name": "official", "locator": "MolCrafts/harness"},
                    {
                        "name": "also",
                        "locator": "https://github.com/molcrafts/harness.git",
                    },
                ]
            },
        )

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "molcrafts/harness" in str(excinfo.value)

    def test_an_omitted_enable_key_loads_as_none(self, home, tmp_path):
        _write(
            st.user_settings_path(),
            {"harness": [{"name": "mine", "locator": "acme/harness"}]},
        )

        loaded = st.load_settings(tmp_path / "repo")

        assert loaded.harness == (
            st.HarnessSource(name="mine", locator="acme/harness"),
        )
        assert loaded.harness[0].enable is None

    def test_an_empty_enable_list_loads_as_an_empty_tuple(self, home, tmp_path):
        _write(
            st.user_settings_path(),
            {"harness": [{"name": "mine", "locator": "acme/harness", "enable": []}]},
        )

        loaded = st.load_settings(tmp_path / "repo")

        assert loaded.harness[0].enable == ()

    def test_a_named_enable_list_loads_as_a_tuple(self, home, tmp_path):
        _write(
            st.user_settings_path(),
            {
                "harness": [
                    {
                        "name": "mine",
                        "locator": "acme/harness",
                        "enable": ["sci", "dev"],
                    }
                ]
            },
        )

        loaded = st.load_settings(tmp_path / "repo")

        assert loaded.harness[0].enable == ("sci", "dev")

    @pytest.mark.parametrize(
        "member", ["dev", "cacheDir", "token", "daily", "telemetry"]
    )
    def test_a_stray_entry_member_is_rejected_by_indexed_name(
        self, home, tmp_path, member
    ):
        _write(
            st.user_settings_path(),
            {"harness": [{"name": "mine", "locator": "acme/harness", member: "x"}]},
        )

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert f"harness[0].{member}" in str(excinfo.value)

    def test_an_entry_without_a_name_is_refused(self, home, tmp_path):
        _write(
            st.user_settings_path(),
            {"harness": [{"locator": "molcrafts/harness"}]},
        )

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "harness[0]" in str(excinfo.value)
        assert "name" in str(excinfo.value)

    @pytest.mark.parametrize("table", [{"locator": "molcrafts/harness"}, {}])
    def test_a_harness_table_is_refused_with_the_list_shape(
        self, home, tmp_path, table
    ):
        _write(st.user_settings_path(), {"harness": table})

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "harness" in str(excinfo.value)
        assert "list" in str(excinfo.value)

    def test_to_dict_emits_only_the_operator_fields(self):
        settings = st.Settings(
            harness=(st.HarnessSource(name="official", locator="molcrafts/harness"),)
        )

        assert settings.to_dict()["harness"] == [
            {
                "name": "official",
                "locator": "molcrafts/harness",
                "enable": None,
            }
        ]
        emitted = settings.to_dict()["harness"][0]
        for derived in ("origin_key", "ref", "owner", "repo", "path"):
            assert derived not in emitted

    def test_a_local_entry_loads_as_written(self, home, tmp_path):
        locator = "/opt/harness/mine"
        _write(
            st.user_settings_path(),
            {"harness": [{"name": "mine", "locator": locator}]},
        )

        loaded = st.load_settings(tmp_path / "repo")

        assert loaded.harness == (st.HarnessSource(name="mine", locator=locator),)
        assert loaded.harness[0].is_local is True

    def test_a_local_entry_round_trips_through_load_and_to_dict(self, home, tmp_path):
        locator = "/opt/harness/mine"
        _write(
            st.user_settings_path(),
            {"harness": [{"name": "mine", "locator": locator}]},
        )

        loaded = st.load_settings(tmp_path / "repo")

        assert loaded.to_dict()["harness"] == [
            {"name": "mine", "locator": locator, "enable": None}
        ]

    def test_a_local_and_a_remote_entry_coexist_in_one_file(self, home, tmp_path):
        local = "/opt/harness/mine"
        _write(
            st.user_settings_path(),
            {
                "harness": [
                    {"name": "official", "locator": "MolCrafts/harness"},
                    {"name": "mine", "locator": local},
                ]
            },
        )

        loaded = st.load_settings(tmp_path / "repo")
        resolved = str(pathlib.Path(local).expanduser().resolve())

        assert [(s.name, s.origin_key, s.path) for s in loaded.harness] == [
            ("official", "molcrafts/harness", ""),
            ("mine", resolved, resolved),
        ]

    def test_an_install_that_names_no_source_has_an_empty_tuple(self):
        assert st.Settings().harness == ()


class TestSettingsHarness:
    """The autonomous harness: an ordered list of named sources.

    Each entry is a ``HarnessSource`` — a ``name``, a ``locator``, and an
    optional ``enable`` list — and neighbouring settings do not live on
    it. A cache location is ``cacheDir`` at the top level, a credential
    belongs in the environment rather than a file that can be committed,
    and the rest were never molmcp settings at all.
    """

    def test_the_harness_did_not_smuggle_in_neighbouring_settings(self):
        for stray in ("shareReceipts", "daily", "telemetry"):
            assert stray not in st._SCHEMA

    def test_share_receipts_is_not_a_setting(self, home, tmp_path):
        _write(st.user_settings_path(), {"shareReceipts": True})

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "shareReceipts" in str(excinfo.value)


class TestNestedSchemaFirstParty:
    """First-party planes are named settings, not a generic ``providers`` bag.

    ``molq`` and ``molexp`` each configure one plane, and each knows which
    members it reads. A single ``providers`` dict keyed by plane name would
    accept any key for any plane: `config set providers.molq.allowsubmit`
    would be stored, echoed by `config list`, and read by nothing. The plane
    catalog's membership moved to the entry-point group (spec 14); the
    settings surface deliberately did not follow it.
    """

    def test_molq_and_molexp_are_dict_valued_first_party_settings(self):
        assert st._SCHEMA.get("molq") is dict
        assert st._SCHEMA.get("molexp") is dict

    def test_there_is_no_generic_providers_bag(self):
        assert "providers" not in st._SCHEMA
        assert "providers" not in st._NESTED_SCHEMA

    def test_molq_members_are_exactly_database_and_allow_submit(self):
        assert st._NESTED_SCHEMA["molq"] == frozenset({"database", "allowSubmit"})

    def test_molexp_members_are_exactly_workspace(self):
        assert st._NESTED_SCHEMA["molexp"] == frozenset({"workspace"})
