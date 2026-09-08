"""Layered molmcp settings — the control surface for what gets indexed.

Modelled on Claude Code: a user file at ``~/.molmcp/settings.json``, an
optional checked-in project file, and an untracked local override, merged
in that order. Unlike Claude Code the *user* file is the primary surface,
because a plane server is launched by an MCP client whose working
directory is arbitrary.
"""

from __future__ import annotations

import dataclasses
import json

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
    """The two verbs that address one ``harness`` entry by its ``name``.

    ``harness`` is a list of objects, so the string-valued verbs one class
    below refuse it outright; these are what authors an entry instead of an
    editor. They do not retire the editor: they write into a file that
    already parses, so one that fails validation on read still needs one.
    The address is the ``name``, never a position: an already-configured name is
    updated in place and an unknown one is appended **last**, so authoring a
    second source never changes which of the existing ones wins.

    A coordinate left out is left alone — ``None`` means "as it was" on an
    entry that exists and the dataclass default on one that does not — so no
    coordinate is ever set to a value nobody typed. Half-authored entries
    are the documented model: a ``name``-only write is accepted here, and
    whether an entry is complete enough to fetch with stays a serve-time
    question.

    Two orderings are binding rather than incidental. Arguments are
    validated by constructing a :class:`~molmcp.settings.HarnessSource`
    *before* the file is read, so a refused call leaves no file behind at
    all; and dropping the last entry leaves ``"harness": []`` rather than
    removing the key, which is ``remove_value``'s different job. No field
    rule is restated here — the message an operator reads is the
    dataclass's own.
    """

    def test_a_four_field_call_writes_one_entry_that_round_trips(self, home, tmp_path):
        st.set_harness_source(
            st.user_settings_path(),
            name="official",
            owner="MolCrafts",
            repo="harness",
            ref="main",
        )

        assert json.loads(st.user_settings_path().read_text()) == {
            "harness": [
                {
                    "name": "official",
                    "owner": "MolCrafts",
                    "repo": "harness",
                    "ref": "main",
                }
            ]
        }
        assert st.load_settings(tmp_path / "repo").harness == (
            st.HarnessSource(
                name="official", owner="MolCrafts", repo="harness", ref="main"
            ),
        )

    def test_a_second_call_with_the_same_name_updates_that_entry_in_place(self, home):
        path = st.user_settings_path()
        st.set_harness_source(
            path, name="mine", owner="acme", repo="harness", ref="main"
        )

        st.set_harness_source(path, name="mine", ref="dev")

        entries = json.loads(path.read_text())["harness"]
        assert len(entries) == 1
        assert entries[0] == {
            "name": "mine",
            "owner": "acme",
            "repo": "harness",
            "ref": "dev",
        }

    def test_an_unknown_name_is_appended_last_leaving_the_first_entry_first(self, home):
        path = st.user_settings_path()
        st.set_harness_source(path, name="official", owner="MolCrafts")

        st.set_harness_source(path, name="mine", owner="acme")

        entries = json.loads(path.read_text())["harness"]
        assert [entry["name"] for entry in entries] == ["official", "mine"]

    def test_a_name_alone_writes_a_half_authored_entry_that_still_loads(
        self, home, tmp_path
    ):
        path = st.user_settings_path()

        st.set_harness_source(path, name="mine")

        assert json.loads(path.read_text())["harness"] == [
            {"name": "mine", "owner": "", "repo": "", "ref": ""}
        ]
        assert st.load_settings(tmp_path / "repo").harness == (
            st.HarnessSource(name="mine"),
        )

    def test_a_refused_call_creates_no_file_at_all(self, home):
        with pytest.raises(st.SettingsError):
            st.set_harness_source(
                st.user_settings_path(), name="mine", owner="acme/harness"
            )

        assert not st.user_settings_path().exists()

    @pytest.mark.parametrize("coordinate", ["owner", "repo", "ref"])
    def test_the_dataclass_message_is_the_one_the_operator_reads(
        self, home, coordinate
    ):
        with pytest.raises(ValueError) as from_the_type:
            st.HarnessSource(name="mine", **{coordinate: "acme harness"})

        with pytest.raises(st.SettingsError) as from_the_verb:
            st.set_harness_source(
                st.user_settings_path(), name="mine", **{coordinate: "acme harness"}
            )

        assert str(from_the_type.value) in str(from_the_verb.value)

    def test_remove_drops_the_named_entry_and_leaves_the_others_in_order(self, home):
        path = st.user_settings_path()
        for name in ("first", "second", "third"):
            st.set_harness_source(path, name=name, owner="acme")

        st.remove_harness_source(path, "second")

        entries = json.loads(path.read_text())["harness"]
        assert [entry["name"] for entry in entries] == ["first", "third"]

    def test_removing_the_last_entry_leaves_an_empty_list_not_a_missing_key(
        self, home, tmp_path
    ):
        path = st.user_settings_path()
        st.set_harness_source(path, name="mine", owner="acme")

        st.remove_harness_source(path, "mine")

        assert json.loads(path.read_text())["harness"] == []
        assert st.load_settings(tmp_path / "repo").harness == ()

    def test_removing_an_absent_name_reports_that_name(self, home):
        path = st.user_settings_path()
        st.set_harness_source(path, name="mine", owner="acme")

        with pytest.raises(st.SettingsError) as excinfo:
            st.remove_harness_source(path, "official")

        assert "official" in str(excinfo.value)

    def test_removing_from_a_file_with_no_harness_key_reports_the_file(self, home):
        path = st.user_settings_path()
        _write(path, {"indexWorkspace": True})

        with pytest.raises(st.SettingsError) as excinfo:
            st.remove_harness_source(path, "mine")

        assert str(path) in str(excinfo.value)

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
        _write(path, {"harness": [{"name": "official", "owner": "acme"}]})
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
            {"harness": [{"name": "mine", "owner": "acme"}], "indexWorkspace": True},
        )

        st.remove_value(st.user_settings_path(), "harness")

        assert "harness" not in json.loads(st.user_settings_path().read_text())
        assert st.load_settings(tmp_path / "repo").harness == ()


class TestHarnessSource:
    """One named harness source: strict about shape, permissive about absence.

    ``name`` is the entry's address — the place the remaining fields get
    filled in later, now that there is no dotted ``harness.owner`` key to
    aim at — so it is the one field that cannot be deferred. The
    coordinates arrive by separate edits, so an empty one is a
    half-authored entry rather than an error. A coordinate that *is*
    written has to be an opaque token — no ``/``, no ``@``, no whitespace —
    which keeps a second ``owner/repo@ref`` parser out of the tree.
    """

    def test_a_four_field_entry_keeps_every_field_it_was_given(self):
        source = st.HarnessSource(
            name="official", owner="molcrafts", repo="harness", ref="main"
        )

        assert (source.name, source.owner, source.repo, source.ref) == (
            "official",
            "molcrafts",
            "harness",
            "main",
        )

    def test_a_name_alone_constructs_with_empty_coordinates(self):
        source = st.HarnessSource(name="mine")

        assert (source.owner, source.repo, source.ref) == ("", "", "")

    @pytest.mark.parametrize("name", ["", "   ", "my harness"])
    def test_an_empty_or_whitespace_bearing_name_is_rejected(self, name):
        with pytest.raises(ValueError):
            st.HarnessSource(name=name)

    def test_a_mixed_case_name_is_as_legal_as_a_mixed_case_source_key(self):
        assert st.HarnessSource(name="MolCrafts").name == "MolCrafts"
        assert not hasattr(st, "HARNESS_SOURCE_NAME_PATTERN")

    @pytest.mark.parametrize("value", ["acme/harness", "acme@main", "acme harness"])
    @pytest.mark.parametrize("coordinate", ["owner", "repo", "ref"])
    def test_a_coordinate_that_is_not_an_opaque_token_is_rejected(
        self, coordinate, value
    ):
        with pytest.raises(ValueError):
            st.HarnessSource(name="mine", **{coordinate: value})


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

    def test_two_entries_in_one_file_load_in_file_order(self, home, tmp_path):
        _write(
            st.user_settings_path(),
            {
                "harness": [
                    {
                        "name": "official",
                        "owner": "molcrafts",
                        "repo": "harness",
                        "ref": "main",
                    },
                    {"name": "team", "owner": "acme", "repo": "harness", "ref": "v2"},
                ]
            },
        )

        loaded = st.load_settings(tmp_path / "repo")

        assert [source.name for source in loaded.harness] == ["official", "team"]

    def test_the_most_specific_layer_replaces_the_list_rather_than_merging(
        self, home, tmp_path
    ):
        _write(st.user_settings_path(), {"harness": [{"name": "user"}]})
        project = tmp_path / "repo"
        _write(st.project_settings_path(project), {"harness": [{"name": "project"}]})
        _write(
            st.project_settings_path(project, local=True),
            {"harness": [{"name": "local"}]},
        )

        loaded = st.load_settings(project)

        assert [source.name for source in loaded.harness] == ["local"]

    def test_two_entries_sharing_a_name_in_one_file_are_refused(self, home, tmp_path):
        _write(
            st.user_settings_path(),
            {
                "harness": [
                    {"name": "twin", "owner": "molcrafts"},
                    {"name": "twin", "owner": "acme"},
                ]
            },
        )

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "twin" in str(excinfo.value)

    def test_a_half_authored_entry_is_stored_as_written(self, home, tmp_path):
        _write(
            st.user_settings_path(), {"harness": [{"name": "mine", "owner": "acme"}]}
        )

        loaded = st.load_settings(tmp_path / "repo")

        assert loaded.harness == (st.HarnessSource(name="mine", owner="acme"),)

    @pytest.mark.parametrize(
        "member", ["dev", "cacheDir", "token", "daily", "telemetry"]
    )
    def test_a_stray_entry_member_is_rejected_by_indexed_name(
        self, home, tmp_path, member
    ):
        _write(st.user_settings_path(), {"harness": [{"name": "mine", member: "x"}]})

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert f"harness[0].{member}" in str(excinfo.value)

    def test_an_entry_without_a_name_is_refused(self, home, tmp_path):
        _write(
            st.user_settings_path(),
            {"harness": [{"owner": "molcrafts", "repo": "harness"}]},
        )

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "harness[0]" in str(excinfo.value)
        assert "name" in str(excinfo.value)

    @pytest.mark.parametrize("table", [{"owner": "molcrafts"}, {}])
    def test_a_harness_table_is_refused_with_the_list_shape(
        self, home, tmp_path, table
    ):
        _write(st.user_settings_path(), {"harness": table})

        with pytest.raises(st.SettingsError) as excinfo:
            st.load_settings(tmp_path / "repo")

        assert "harness" in str(excinfo.value)
        assert "list" in str(excinfo.value)

    def test_to_dict_emits_a_list_of_four_key_objects(self):
        settings = st.Settings(
            harness=(
                st.HarnessSource(
                    name="official", owner="molcrafts", repo="harness", ref="main"
                ),
            )
        )

        assert settings.to_dict()["harness"] == [
            {
                "name": "official",
                "owner": "molcrafts",
                "repo": "harness",
                "ref": "main",
            }
        ]

    def test_an_install_that_names_no_source_has_an_empty_tuple(self):
        assert st.Settings().harness == ()


class TestSettingsHarness:
    """The autonomous harness: an ordered list of named sources.

    Each entry is a ``HarnessSource`` — a ``name``, plus the ``owner`` /
    ``repo`` / ``ref`` coordinates of one repository — and the ``name`` is
    what makes an entry addressable while its coordinates are still being
    filled in. That is why ``name`` is the one field a file cannot leave
    out while the coordinates are the ones it may: completeness is a
    serve-time question, and an entry has to be nameable before it can be
    completed. What the list is *not* is a home for the settings next door.
    A cache location is ``cacheDir`` at the top level, a credential belongs
    in the environment rather than a file that can be committed, and the
    rest were never molmcp settings at all.
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
