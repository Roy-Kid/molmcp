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


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setattr(st.Path, "home", staticmethod(lambda: fake))
    return fake


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


class TestHarnessWriteGuard:
    """The string-valued edit verbs cannot author a list of objects.

    ``harness`` became a ``list``, which unlocked two write paths that were
    safely refused while it was a ``dict``: ``config set harness x`` parses
    to ``["x"]`` and ``config add harness x`` appends the bare string. Both
    reach ``write_settings_file`` *before* anything validates, and the
    per-entry validator then rejects ``"x"`` on the next read — under
    ``load_settings``, hence under ``config list``, ``get``, ``set``,
    ``remove`` and ``serve`` alike. No CLI verb can undo that, so the file
    has to be hand-edited to make the install usable again. The binding
    assertions are therefore that the call raises, that **no file is
    created**, and that a later ``load_settings`` still works.

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

    @pytest.mark.parametrize("member", ["owner", "dev"])
    def test_set_refuses_every_dotted_harness_key_not_only_a_stray_one(
        self, home, member
    ):
        with pytest.raises(st.SettingsError) as excinfo:
            st.set_value(st.user_settings_path(), f"harness.{member}", "x")

        assert f"harness.{member}" in str(excinfo.value)
        assert not st.user_settings_path().exists()

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
