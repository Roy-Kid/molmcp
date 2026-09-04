"""Activation.bind / stage / promote / rollback — fake GitTransport."""

from __future__ import annotations

import dataclasses
import inspect
import io
import json
import tarfile
from pathlib import Path

import pytest

import molmcp
import molmcp.components
from molmcp.components import activate as activate_module
from molmcp.components.activate import (
    Activation,
    ActivationError,
    ActivationVersionError,
    IneligibleShaError,
    NothingStagedError,
    NothingToRollbackError,
)
from molmcp.components.catalog import CatalogError
from molmcp.components.store import ImmutableGitStore

CAPABILITIES = frozenset({"provider-sdk", "harness-catalog"})
SHA_A = "a" * 40
SHA_B = "b" * 40
_OWNER = "acme"
_REPO = "widgets"
CANONICAL_TOML = """\
requires = ["provider-sdk", "harness-catalog"]

[[component]]
kind = "skill"
name = "daily"
path = "skills/daily/SKILL.md"

[[component]]
kind = "rule"
name = "safety"
path = "rules/safety.md"

[[component]]
kind = "provider"
name = "molvis"
path = "providers/molvis/provider.py"
entrypoint = "molmcp.providers.molvis:MolvisProvider"

[[component]]
kind = "overlay"
name = "molpy"
path = "overlays/molpy/overlay.py"
entrypoint = "molpy.overlay:MolpyOverlay"

[[component]]
kind = "agent"
name = "reviewer"
path = "agents/reviewer/AGENT.md"

[[component]]
kind = "bundle"
name = "daily"
members = ["skill.daily", "rule.safety", "provider.molvis", "overlay.molpy"]

[[component]]
kind = "bundle"
name = "dev"
members = ["skill.daily", "agent.reviewer", "rule.safety", "provider.molvis"]
"""
_ASSIGN_ERRORS = (AttributeError, dataclasses.FrozenInstanceError)


def _github_tarball(repo: str, sha: str) -> bytes:
    """GitHub-style tar.gz whose inner directory is ``{repo}-{sha}/``."""
    prefix = f"{repo}-{sha}"
    members = {f"{prefix}/harness.toml": CANONICAL_TOML}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _FakeGitTransport:
    """``fetch_archive`` only; ``resolve_commit`` raises if called."""

    def __init__(self, archives: dict[str, bytes]) -> None:
        self._archives = archives

    def fetch_archive(self, owner: str, repo: str, sha: str) -> bytes:
        return self._archives[sha]

    def resolve_commit(self, owner: str, repo: str, ref: str | None) -> str:
        raise AssertionError("Activation tests must not call resolve_commit")


def _pointer(tmp_path: Path) -> Path:
    return tmp_path / "activation.json"


def _new_store(tmp_path: Path, *shas: str) -> ImmutableGitStore:
    keys = shas or (SHA_A,)
    archives = {sha: _github_tarball(_REPO, sha) for sha in keys}
    return ImmutableGitStore(tmp_path / "store", _FakeGitTransport(archives))


def _published(tmp_path: Path, *shas: str) -> ImmutableGitStore:
    keys = shas or (SHA_A,)
    store = _new_store(tmp_path, *keys)
    for sha in keys:
        store.publish(sha, owner=_OWNER, repo=_REPO)
    return store


def _bind(
    tmp_path: Path,
    store: ImmutableGitStore | None = None,
    *,
    path: Path | None = None,
) -> Activation:
    if store is None:
        store = _new_store(tmp_path)
    if path is None:
        path = _pointer(tmp_path)
    return Activation.bind(path, store=store, supported_capabilities=CAPABILITIES)


def _write_pointer(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_pointer(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _stub_load_harness_catalog(monkeypatch: pytest.MonkeyPatch, stub: object) -> None:
    monkeypatch.setattr(
        "molmcp.components.activate.load_harness_catalog",
        stub,
    )


def _public_names(obj: object) -> set[str]:
    return {name for name in dir(obj) if not name.startswith("_")}


class TestActivation:
    def test_bind_signature_path_positional_collaborators_keyword_only(self):
        params = inspect.signature(Activation.bind).parameters
        assert list(params) == ["path", "store", "supported_capabilities"]
        assert params["path"].kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        assert params["store"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["supported_capabilities"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["path"].default is inspect.Parameter.empty
        assert params["store"].default is inspect.Parameter.empty
        assert params["supported_capabilities"].default is inspect.Parameter.empty

    def test_bind_store_none_raises_type_error(self, tmp_path):
        with pytest.raises(TypeError):
            Activation.bind(
                _pointer(tmp_path),
                store=None,  # type: ignore[arg-type]
                supported_capabilities=CAPABILITIES,
            )

    def test_bind_supported_capabilities_none_raises_type_error(self, tmp_path):
        store = _new_store(tmp_path)
        with pytest.raises(TypeError):
            Activation.bind(
                _pointer(tmp_path),
                store=store,
                supported_capabilities=None,  # type: ignore[arg-type]
            )

    def test_constructs_without_arguments_raises_type_error(self):
        with pytest.raises(TypeError):
            Activation()  # type: ignore[call-arg]

    def test_constructs_with_path_only_raises_type_error(self, tmp_path):
        with pytest.raises(TypeError):
            Activation(_pointer(tmp_path))  # type: ignore[call-arg]

    def test_bind_is_classmethod(self):
        assert hasattr(Activation, "bind")
        assert isinstance(inspect.getattr_static(Activation, "bind"), classmethod)

    def test_from_record_is_private(self):
        assert hasattr(Activation, "_from_record")
        assert Activation._from_record.__name__.startswith("_")

    def test_bind_missing_pointer_sets_current_previous_staged_none(self, tmp_path):
        activation = _bind(tmp_path)
        assert activation.current is None
        assert activation.previous is None
        assert activation.staged is None

    def test_bind_missing_pointer_does_not_create_path(self, tmp_path):
        path = _pointer(tmp_path)
        _bind(tmp_path, path=path)
        assert not path.exists()

    def test_instance_has_no_public_active_attribute(self, tmp_path):
        activation = _bind(tmp_path)
        assert "active" not in _public_names(activation)
        assert hasattr(activation, "current")

    def test_instance_has_no_public_staging_attribute(self, tmp_path):
        activation = _bind(tmp_path)
        assert "staging" not in _public_names(activation)
        assert hasattr(activation, "staged")

    def test_assigning_current_raises(self, tmp_path):
        activation = _bind(tmp_path)
        with pytest.raises(_ASSIGN_ERRORS):
            activation.current = SHA_A  # type: ignore[misc]

    def test_assigning_previous_raises(self, tmp_path):
        activation = _bind(tmp_path)
        with pytest.raises(_ASSIGN_ERRORS):
            activation.previous = SHA_A  # type: ignore[misc]

    def test_assigning_staged_raises(self, tmp_path):
        activation = _bind(tmp_path)
        with pytest.raises(_ASSIGN_ERRORS):
            activation.staged = SHA_A  # type: ignore[misc]

    def test_bind_unknown_json_version_raises_activation_version_error(self, tmp_path):
        path = _pointer(tmp_path)
        _write_pointer(
            path,
            {"version": 2, "active": None, "staging": None, "previous": None},
        )
        with pytest.raises(ActivationVersionError):
            _bind(tmp_path, path=path)

    def test_bind_unknown_json_field_raises_activation_version_error(self, tmp_path):
        path = _pointer(tmp_path)
        _write_pointer(
            path,
            {
                "version": 1,
                "active": None,
                "staging": None,
                "previous": None,
                "extra": True,
            },
        )
        with pytest.raises(ActivationVersionError):
            _bind(tmp_path, path=path)

    def test_bind_invalid_json_raises_activation_version_error(self, tmp_path):
        path = _pointer(tmp_path)
        path.write_text("{not-json", encoding="utf-8")
        with pytest.raises(ActivationVersionError):
            _bind(tmp_path, path=path)

    def test_activate_module_has_no_activation_unbound_error(self):
        assert not hasattr(activate_module, "ActivationUnboundError")
        assert "ActivationUnboundError" not in dir(activate_module)

    def test_stage_calls_load_harness_catalog_with_three_positional_args(
        self, tmp_path, monkeypatch
    ):
        store = _published(tmp_path, SHA_A)
        recorded: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def fake_load(*args: object, **kwargs: object) -> object:
            recorded.append((args, kwargs))
            return object()

        _stub_load_harness_catalog(monkeypatch, fake_load)
        activation = _bind(tmp_path, store)
        activation.stage(SHA_A)
        assert recorded == [
            ((store.tree_path(SHA_A), SHA_A, CAPABILITIES), {}),
        ]

    def test_stage_sets_staged_without_changing_current_or_previous(self, tmp_path):
        store = _published(tmp_path, SHA_A)
        activation = _bind(tmp_path, store)
        activation.stage(SHA_A)
        assert activation.staged == SHA_A
        assert activation.current is None
        assert activation.previous is None

    def test_stage_writes_pointer_json_matching_properties(self, tmp_path):
        store = _published(tmp_path, SHA_A)
        path = _pointer(tmp_path)
        activation = _bind(tmp_path, store, path=path)
        activation.stage(SHA_A)
        payload = _read_pointer(path)
        assert payload == {
            "version": 1,
            "active": None,
            "staging": SHA_A,
            "previous": None,
        }
        assert activation.current == payload["active"]
        assert activation.staged == payload["staging"]
        assert activation.previous == payload["previous"]

    def test_stage_unpublished_sha_raises_ineligible_sha_error(self, tmp_path):
        activation = _bind(tmp_path)
        with pytest.raises(IneligibleShaError):
            activation.stage(SHA_A)

    def test_stage_unpublished_sha_does_not_create_pointer_file(self, tmp_path):
        path = _pointer(tmp_path)
        activation = _bind(tmp_path, path=path)
        with pytest.raises(IneligibleShaError):
            activation.stage(SHA_A)
        assert not path.exists()

    def test_stage_unpublished_sha_does_not_mutate_existing_pointer(self, tmp_path):
        path = _pointer(tmp_path)
        original = {
            "version": 1,
            "active": None,
            "staging": None,
            "previous": None,
        }
        _write_pointer(path, original)
        activation = _bind(tmp_path, path=path)
        with pytest.raises(IneligibleShaError):
            activation.stage(SHA_A)
        assert _read_pointer(path) == original

    def test_stage_catalog_error_raises_ineligible_sha_error(
        self, tmp_path, monkeypatch
    ):
        store = _published(tmp_path, SHA_A)

        def boom(*_args: object, **_kwargs: object) -> object:
            raise CatalogError("ineligible")

        _stub_load_harness_catalog(monkeypatch, boom)
        activation = _bind(tmp_path, store)
        with pytest.raises(IneligibleShaError):
            activation.stage(SHA_A)

    def test_ineligible_sha_error_subclasses_activation_error(self):
        assert issubclass(IneligibleShaError, ActivationError)

    def test_promote_signature_has_only_self(self):
        params = inspect.signature(Activation.promote).parameters
        assert list(params) == ["self"]
        assert "sha" not in params

    def test_rollback_signature_has_only_self(self):
        params = inspect.signature(Activation.rollback).parameters
        assert list(params) == ["self"]
        assert "sha" not in params

    def test_promote_with_no_staged_raises_nothing_staged_error(self, tmp_path):
        activation = _bind(tmp_path)
        with pytest.raises(NothingStagedError):
            activation.promote()

    def test_rollback_with_no_previous_raises_nothing_to_rollback_error(self, tmp_path):
        activation = _bind(tmp_path)
        with pytest.raises(NothingToRollbackError):
            activation.rollback()

    def test_promote_moves_staged_to_current_and_clears_staged(self, tmp_path):
        store = _published(tmp_path, SHA_A)
        path = _pointer(tmp_path)
        activation = _bind(tmp_path, store, path=path)
        activation.stage(SHA_A)
        activation.promote()
        assert activation.current == SHA_A
        assert activation.staged is None
        assert activation.previous is None
        payload = _read_pointer(path)
        assert payload == {
            "version": 1,
            "active": SHA_A,
            "staging": None,
            "previous": None,
        }
        assert activation.current == payload["active"]
        assert activation.staged == payload["staging"]
        assert activation.previous == payload["previous"]

    def test_second_promote_moves_current_to_previous(self, tmp_path):
        store = _published(tmp_path, SHA_A, SHA_B)
        path = _pointer(tmp_path)
        activation = _bind(tmp_path, store, path=path)
        activation.stage(SHA_A)
        activation.promote()
        activation.stage(SHA_B)
        activation.promote()
        assert activation.current == SHA_B
        assert activation.staged is None
        assert activation.previous == SHA_A
        payload = _read_pointer(path)
        assert payload == {
            "version": 1,
            "active": SHA_B,
            "staging": None,
            "previous": SHA_A,
        }
        assert activation.current == payload["active"]
        assert activation.staged == payload["staging"]
        assert activation.previous == payload["previous"]

    def test_rollback_restores_previous_and_clears_previous(self, tmp_path):
        store = _published(tmp_path, SHA_A, SHA_B)
        path = _pointer(tmp_path)
        activation = _bind(tmp_path, store, path=path)
        activation.stage(SHA_A)
        activation.promote()
        activation.stage(SHA_B)
        activation.promote()
        activation.rollback()
        assert activation.current == SHA_A
        assert activation.staged is None
        assert activation.previous is None
        payload = _read_pointer(path)
        assert payload == {
            "version": 1,
            "active": SHA_A,
            "staging": None,
            "previous": None,
        }
        assert activation.current == payload["active"]
        assert activation.staged == payload["staging"]
        assert activation.previous == payload["previous"]

    def test_second_instance_promote_does_not_wipe_original_current(self, tmp_path):
        store = _published(tmp_path, SHA_A, SHA_B)
        path = _pointer(tmp_path)
        first = _bind(tmp_path, store, path=path)
        first.stage(SHA_A)
        first.promote()
        second = _bind(tmp_path, store, path=path)
        assert second.current == SHA_A
        second.stage(SHA_B)
        second.promote()
        assert second.current == SHA_B
        assert second.previous == SHA_A
        assert second.staged is None

    def test_activation_is_in_components_all(self):
        assert "Activation" in molmcp.components.__all__

    def test_immutable_git_store_is_in_components_all(self):
        assert "ImmutableGitStore" in molmcp.components.__all__

    def test_activation_is_not_in_molmcp_all(self):
        assert "Activation" not in molmcp.__all__

    def test_immutable_git_store_is_not_in_molmcp_all(self):
        assert "ImmutableGitStore" not in molmcp.__all__
