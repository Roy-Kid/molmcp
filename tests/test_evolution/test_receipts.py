"""EpisodeReceipt V1 — redaction, the TTL log, and default-off consent.

Mirrors ``src/molmcp/evolution/receipts.py``; one class per public symbol
(``redact_text``, ``EpisodeReceipt``, ``ReceiptLog``, ``upload_payload``).

Two disciplines are read off the source instead of exercised. The module may
never reach into the environment to *find* secrets — the same rule
``tests/test_no_env_switches.py`` enforces, for the same reason: a redactor
that scans ``os.environ`` learns secrets it was never given. And
``fence_untrusted`` must be imported inside ``upload_payload`` alone: the
fence is for the LLM-facing payload, never for the bytes on disk, so the
package must not re-export it.

Nothing here reads a clock or the real home. ``created_at`` values are
literals, ``now`` is injected into ``append`` / ``prune``, and ``Path.home``
is monkeypatched to a distinctively named directory so the username under
test is unambiguous.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from molmcp import evolution
from molmcp.evolution.receipts import (
    RECEIPT_FIELDS,
    RECEIPT_TTL_DAYS,
    RECEIPT_VERSION,
    SHARE_RECEIPTS_KEY,
    Consent,
    EpisodeReceipt,
    ReceiptError,
    ReceiptLog,
    redact_text,
    upload_payload,
)

_REPO = Path(__file__).resolve().parents[2]
_EVOLUTION = _REPO / "src" / "molmcp" / "evolution"
_RECEIPTS = _EVOLUTION / "receipts.py"

#: The instant ``append`` is told about: early enough that nothing a test
#: writes is expired on the way in, so ``prune`` stays the thing under test.
_WRITE_NOW = datetime(2026, 8, 22, tzinfo=UTC)
#: The instant ``prune`` is told about. 14-day threshold: 2026-08-21.
_PRUNE_NOW = datetime(2026, 9, 4, tzinfo=UTC)

#: 15 days before ``_PRUNE_NOW`` — past the TTL.
_OLD_CREATED_AT = "2026-08-20T00:00:00+00:00"
#: 13 days before ``_PRUNE_NOW`` — inside the TTL.
_YOUNG_CREATED_AT = "2026-08-22T00:00:00+00:00"
#: ``_PRUNE_NOW`` itself.
_NEW_CREATED_AT = "2026-09-04T00:00:00+00:00"

#: (prefix, secret body) pairs the redactor must swallow whole, prefix
#: included. ``Bearer`` is tested apart: its secret sits after a space.
_TOKENS: tuple[tuple[str, str], ...] = (
    ("ghp_", "abcdefghijklmnopqrstuvwxyz012345"),
    ("gho_", "abcdefghijklmnopqrstuvwxyz012345"),
    ("github_pat_", "11ABCDEFG0abcdefghijklmnopqrst"),
    ("sk-", "abcdefghijklmnopqrstuvwxyz012345"),
    ("xoxb-", "0123456789abcdefghijABCDEFGHIJ"),
)

#: Every ``RECEIPT_FIELDS`` entry that is text rather than a number — the
#: fields ``__post_init__`` guards with ``isinstance(..., str)``. Spelled out
#: here rather than imported: the private tuple in the source is the thing
#: under test, so a test deriving it from the source would agree with itself.
_TEXT_FIELDS: tuple[str, ...] = (
    "episode_id",
    "created_at",
    "outcome",
    "task",
    "error_detail",
)

#: Identifiers that would mean the module went looking for secrets itself.
_ENVIRONMENT_IDENTIFIERS = frozenset({"environ", "getenv", "getpass"})


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home directory with an unmistakable name, injected everywhere.

    Autouse: no test may depend on the machine's real home or username, and
    a redaction assertion that happens to pass on one laptop is not a test.
    """
    home = tmp_path / "zzhomeuser"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _payload(**overrides: object) -> dict[str, object]:
    """A well-formed V1 payload, plus whatever the caller wants changed."""
    base: dict[str, object] = {
        "version": RECEIPT_VERSION,
        "episode_id": "ep-001",
        "created_at": _YOUNG_CREATED_AT,
        "outcome": "failed",
        "task": "rebuild the catalog index",
        "error_detail": "boom",
    }
    return {**base, **overrides}


def _receipt(
    *,
    episode_id: str = "ep-001",
    created_at: str = _YOUNG_CREATED_AT,
    outcome: str = "failed",
    task: str = "rebuild the catalog index",
    error_detail: str = "boom",
) -> EpisodeReceipt:
    """Build a receipt by keyword only — field order is the module's business."""
    return EpisodeReceipt(
        episode_id=episode_id,
        created_at=created_at,
        outcome=outcome,
        task=task,
        error_detail=error_detail,
    )


def _receipts_source() -> str:
    assert _RECEIPTS.is_file(), f"{_RECEIPTS} does not exist yet"
    return _RECEIPTS.read_text(encoding="utf-8")


def _receipts_tree() -> ast.Module:
    return ast.parse(_receipts_source())


def _identifiers(tree: ast.AST) -> set[str]:
    """Every name, attribute, and import alias mentioned anywhere in *tree*."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name.split(".")[0])
            if node.asname:
                found.add(node.asname)
    return found


def _imported_modules(tree: ast.AST) -> set[str]:
    """Module names imported at any depth; relative imports excluded."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules.add(node.module)
    return modules


def _module_level_imports(tree: ast.Module) -> set[str]:
    """Modules and symbols imported at module level — not inside a function."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined at module level in {_RECEIPTS}")


class TestRedactText:
    def test_home_prefix_becomes_a_tilde(self, fake_home: Path) -> None:
        assert redact_text(f"{fake_home}/work/notes.md") == "~/work/notes.md"

    def test_leftover_username_becomes_a_placeholder(self, fake_home: Path) -> None:
        redacted = redact_text(f"user={fake_home.name}")

        assert redacted == "user=[USER]"
        assert fake_home.name not in redacted

    def test_home_is_replaced_before_the_bare_username(self, fake_home: Path) -> None:
        """Order is fixed: username-first would leave the home path unmatched."""
        text = f"{fake_home}/work and user={fake_home.name}"

        assert redact_text(text) == "~/work and user=[USER]"

    @pytest.mark.parametrize(
        ("prefix", "secret"),
        _TOKENS,
        ids=[prefix for prefix, _ in _TOKENS],
    )
    def test_token_is_replaced_whole(self, prefix: str, secret: str) -> None:
        assert redact_text(f"token={prefix}{secret}") == "token=[REDACTED]"

    def test_bearer_scheme_and_its_secret_are_replaced(self) -> None:
        redacted = redact_text("Authorization: Bearer abcdefghij0123456789")

        assert "[REDACTED]" in redacted
        assert "abcdefghij0123456789" not in redacted
        assert "Bearer" not in redacted

    def test_home_and_github_token_golden(self, fake_home: Path) -> None:
        """The pinned golden — also the regression script's first assertion."""
        text = f"{fake_home}/secret ghp_abcdefghijklmnopqrstuvwxyz012345"

        assert redact_text(text) == "~/secret [REDACTED]"

    def test_source_never_reads_the_environment_or_getpass(self) -> None:
        source = _receipts_source()

        assert "os.environ" not in source
        assert "getenv" not in source
        assert "getpass" not in source
        assert _identifiers(_receipts_tree()) & _ENVIRONMENT_IDENTIFIERS == set()


class TestEpisodeReceipt:
    def test_round_trips_through_to_dict_and_from_dict(self) -> None:
        receipt = _receipt()

        assert EpisodeReceipt.from_dict(receipt.to_dict()) == receipt

    def test_to_dict_emits_exactly_the_receipt_fields(self) -> None:
        assert set(_receipt().to_dict()) == set(RECEIPT_FIELDS)

    def test_receipt_version_is_one(self) -> None:
        assert RECEIPT_VERSION == 1

    def test_version_defaults_to_the_constant(self) -> None:
        assert _receipt().version == RECEIPT_VERSION

    def test_is_frozen(self) -> None:
        receipt = _receipt()

        with pytest.raises(dataclasses.FrozenInstanceError):
            receipt.outcome = "ok"  # type: ignore[misc]

    def test_uses_slots(self) -> None:
        assert hasattr(EpisodeReceipt, "__slots__")
        assert not hasattr(_receipt(), "__dict__")

    def test_field_names_are_receipt_fields_in_order(self) -> None:
        names = tuple(field.name for field in dataclasses.fields(EpisodeReceipt))

        assert names == RECEIPT_FIELDS
        assert RECEIPT_FIELDS == (
            "version",
            "episode_id",
            "created_at",
            "outcome",
            "task",
            "error_detail",
        )

    def test_construction_redacts_the_task(self, fake_home: Path) -> None:
        assert _receipt(task=f"index {fake_home}/work").task == "index ~/work"

    def test_construction_redacts_the_error_detail(self) -> None:
        receipt = _receipt(error_detail="push denied: ghp_abcdefghij0123456789")

        assert receipt.error_detail == "push denied: [REDACTED]"

    def test_from_dict_drops_unknown_keys(self) -> None:
        payload = _payload(
            cot="first I thought",
            reasoning="then I reasoned",
            thought="and thought again",
            chain_of_thought="the whole chain",
            pattern_key="retry-on-timeout",
            extras={"nested": "junk"},
        )

        restored = EpisodeReceipt.from_dict(payload)

        assert set(restored.to_dict()) == set(RECEIPT_FIELDS)

    def test_from_dict_keeps_no_trace_of_the_reasoning(self) -> None:
        payload = _payload(cot="first I thought", chain_of_thought="the whole chain")

        restored = EpisodeReceipt.from_dict(payload)

        assert "thought" not in json.dumps(restored.to_dict())

    def test_has_no_extras_or_pattern_key_attribute(self) -> None:
        receipt = _receipt()

        assert not hasattr(receipt, "extras")
        assert not hasattr(receipt, "pattern_key")

    def test_from_dict_requires_an_episode_id(self) -> None:
        payload = _payload()
        del payload["episode_id"]

        with pytest.raises(ReceiptError):
            EpisodeReceipt.from_dict(payload)

    def test_from_dict_requires_a_created_at(self) -> None:
        payload = _payload()
        del payload["created_at"]

        with pytest.raises(ReceiptError):
            EpisodeReceipt.from_dict(payload)

    @pytest.mark.parametrize("outcome", ["ok", "failed", "skipped"])
    def test_from_dict_accepts_every_legal_outcome(self, outcome: str) -> None:
        assert EpisodeReceipt.from_dict(_payload(outcome=outcome)).outcome == outcome

    @pytest.mark.parametrize("outcome", ["cancelled", "OK", "", "success"])
    def test_from_dict_rejects_an_illegal_outcome(self, outcome: str) -> None:
        with pytest.raises(ReceiptError):
            EpisodeReceipt.from_dict(_payload(outcome=outcome))

    @pytest.mark.parametrize("payload", [None, [], "receipt", 7])
    def test_from_dict_rejects_a_non_mapping_payload(self, payload: object) -> None:
        with pytest.raises(ReceiptError):
            EpisodeReceipt.from_dict(payload)  # type: ignore[arg-type]

    @pytest.mark.parametrize("version", ["1", 1.5, None])
    def test_rejects_a_version_that_is_not_an_integer(self, version: object) -> None:
        """The stamp is a number: ``"1"`` is a different contract, not this one."""
        with pytest.raises(ReceiptError):
            EpisodeReceipt(
                version=version,  # type: ignore[arg-type]
                episode_id="ep-001",
                created_at=_YOUNG_CREATED_AT,
                outcome="failed",
            )

    @pytest.mark.parametrize("field", _TEXT_FIELDS)
    def test_rejects_a_text_field_that_is_not_a_string(self, field: str) -> None:
        """Every text field is type-guarded, not just the two that get redacted."""
        fields: dict[str, object] = {
            "episode_id": "ep-001",
            "created_at": _YOUNG_CREATED_AT,
            "outcome": "failed",
            "task": "rebuild the catalog index",
            "error_detail": "boom",
            field: 5,
        }

        with pytest.raises(ReceiptError):
            EpisodeReceipt(**fields)  # type: ignore[arg-type]

    def test_from_dict_rejects_a_non_string_outcome(self) -> None:
        """A document may say anything; the constructor's guards still run."""
        with pytest.raises(ReceiptError):
            EpisodeReceipt.from_dict(_payload(outcome=5))


class TestReceiptLog:
    def test_root_has_no_default(self) -> None:
        """No cwd, no cacheDir: the caller names the directory or there is none."""
        with pytest.raises(TypeError):
            ReceiptLog()  # type: ignore[call-arg]

    def test_ttl_is_fourteen_days(self) -> None:
        assert RECEIPT_TTL_DAYS == 14

    def test_append_writes_episode_id_json(self, tmp_path: Path) -> None:
        root = tmp_path / "receipts"

        path = ReceiptLog(root).append(_receipt(episode_id="ep-001"), now=_WRITE_NOW)

        assert path == (root / "ep-001.json").resolve()
        assert path.is_file()

    def test_append_leaves_no_partial_behind(self, tmp_path: Path) -> None:
        root = tmp_path / "receipts"

        ReceiptLog(root).append(_receipt(episode_id="ep-001"), now=_WRITE_NOW)

        assert list(root.glob("*.partial")) == []
        assert sorted(entry.name for entry in root.iterdir()) == ["ep-001.json"]

    def test_on_disk_error_detail_is_redacted_plaintext(
        self, tmp_path: Path, fake_home: Path
    ) -> None:
        root = tmp_path / "receipts"
        detail = f"401 from {fake_home}/.netrc ghp_abcdefghij0123456789"

        path = ReceiptLog(root).append(
            _receipt(episode_id="ep-001", error_detail=detail), now=_WRITE_NOW
        )

        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored["error_detail"] == "401 from ~/.netrc [REDACTED]"
        assert "<!-- BEGIN" not in path.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "episode_id", ["../escape", "a/b", "", "ep 001", "/abs", "ep\tid"]
    )
    def test_append_rejects_an_episode_id_that_is_not_one_segment(
        self, tmp_path: Path, episode_id: str
    ) -> None:
        log = ReceiptLog(tmp_path / "receipts")

        with pytest.raises(ReceiptError):
            log.append(_receipt(episode_id=episode_id), now=_WRITE_NOW)

    def test_second_append_with_the_same_id_overwrites(self, tmp_path: Path) -> None:
        root = tmp_path / "receipts"
        log = ReceiptLog(root)
        log.append(_receipt(episode_id="ep-001", outcome="failed"), now=_WRITE_NOW)

        path = log.append(
            _receipt(episode_id="ep-001", outcome="ok", error_detail=""),
            now=_WRITE_NOW,
        )

        assert json.loads(path.read_text(encoding="utf-8"))["outcome"] == "ok"
        assert sorted(entry.name for entry in root.iterdir()) == ["ep-001.json"]

    def test_prune_deletes_a_receipt_older_than_the_ttl(self, tmp_path: Path) -> None:
        root = tmp_path / "receipts"
        log = ReceiptLog(root)
        log.append(
            _receipt(episode_id="ep-old", created_at=_OLD_CREATED_AT), now=_WRITE_NOW
        )

        removed = log.prune(now=_PRUNE_NOW)

        assert removed == 1
        assert not (root / "ep-old.json").exists()

    def test_prune_keeps_a_receipt_inside_the_ttl(self, tmp_path: Path) -> None:
        root = tmp_path / "receipts"
        log = ReceiptLog(root)
        log.append(
            _receipt(episode_id="ep-young", created_at=_YOUNG_CREATED_AT),
            now=_WRITE_NOW,
        )

        removed = log.prune(now=_PRUNE_NOW)

        assert removed == 0
        assert (root / "ep-young.json").is_file()

    def test_prune_leaves_a_malformed_file_alone(self, tmp_path: Path) -> None:
        """A file nobody can date is not a file anybody may delete."""
        root = tmp_path / "receipts"
        log = ReceiptLog(root)
        log.append(
            _receipt(episode_id="ep-young", created_at=_YOUNG_CREATED_AT),
            now=_WRITE_NOW,
        )
        broken = root / "broken.json"
        broken.write_text("{not json", encoding="utf-8")

        removed = log.prune(now=_PRUNE_NOW)

        assert removed == 0
        assert broken.is_file()

    def test_append_prunes_expired_receipts(self, tmp_path: Path) -> None:
        """TTL is not a second step a caller can forget."""
        root = tmp_path / "receipts"
        log = ReceiptLog(root)
        log.append(
            _receipt(episode_id="ep-old", created_at=_OLD_CREATED_AT), now=_WRITE_NOW
        )

        log.append(
            _receipt(episode_id="ep-new", created_at=_NEW_CREATED_AT), now=_PRUNE_NOW
        )

        assert not (root / "ep-old.json").exists()
        assert (root / "ep-new.json").is_file()

    def test_list_returns_a_tuple_of_receipts(self, tmp_path: Path) -> None:
        log = ReceiptLog(tmp_path / "receipts")
        log.append(_receipt(episode_id="ep-001"), now=_WRITE_NOW)

        listed = log.list()

        assert isinstance(listed, tuple)
        assert [type(item) for item in listed] == [EpisodeReceipt]

    def test_list_sorts_by_created_at(self, tmp_path: Path) -> None:
        log = ReceiptLog(tmp_path / "receipts")
        log.append(
            _receipt(episode_id="ep-young", created_at=_YOUNG_CREATED_AT),
            now=_WRITE_NOW,
        )
        log.append(
            _receipt(episode_id="ep-old", created_at=_OLD_CREATED_AT), now=_WRITE_NOW
        )

        listed = log.list()

        assert [item.episode_id for item in listed] == ["ep-old", "ep-young"]

    def test_list_skips_files_it_cannot_read_as_receipts(self, tmp_path: Path) -> None:
        """One corrupt document must not make the whole log unreadable forever.

        ``prune`` deliberately never deletes an undatable file, so ``list`` is
        the method that has to live with it: both a file that is not JSON and a
        file that is JSON but not a receipt are skipped, not raised on.
        """
        root = tmp_path / "receipts"
        log = ReceiptLog(root)
        log.append(
            _receipt(episode_id="ep-young", created_at=_YOUNG_CREATED_AT),
            now=_WRITE_NOW,
        )
        broken = root / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        headless = _payload(episode_id="ep-headless")
        del headless["episode_id"]
        (root / "headless.json").write_text(json.dumps(headless), encoding="utf-8")

        listed = log.list()

        assert [item.episode_id for item in listed] == ["ep-young"]
        assert broken.is_file()

    def test_list_drops_unknown_keys(self, tmp_path: Path) -> None:
        root = tmp_path / "receipts"
        root.mkdir(parents=True)
        payload = _payload(episode_id="ep-raw", cot="...", pattern_key="retry")
        (root / "ep-raw.json").write_text(json.dumps(payload), encoding="utf-8")

        listed = ReceiptLog(root).list()

        assert set(listed[0].to_dict()) == set(RECEIPT_FIELDS)


class TestUploadPayload:
    def test_omitted_consent_returns_none(self) -> None:
        assert upload_payload(_receipt()) is None

    def test_default_consent_returns_none(self) -> None:
        assert upload_payload(_receipt(), Consent()) is None

    def test_explicit_refusal_returns_none(self) -> None:
        assert upload_payload(_receipt(), Consent(share_receipts=False)) is None

    def test_granted_consent_returns_exactly_the_receipt_fields(self) -> None:
        payload = upload_payload(_receipt(), Consent(share_receipts=True))

        assert payload is not None
        assert set(payload) == set(RECEIPT_FIELDS)

    def test_granted_consent_fences_the_error_detail(self) -> None:
        payload = upload_payload(
            _receipt(error_detail="boom"), Consent(share_receipts=True)
        )

        assert payload is not None
        assert "<!-- BEGIN receipt error_detail -->" in payload["error_detail"]

    def test_other_fields_stay_redacted_plaintext(self, fake_home: Path) -> None:
        receipt = _receipt(task=f"index {fake_home}/work")

        payload = upload_payload(receipt, Consent(share_receipts=True))

        assert payload is not None
        assert payload["task"] == "index ~/work"
        assert "<!-- BEGIN" not in payload["task"]

    def test_share_receipts_key_is_a_top_level_name(self) -> None:
        assert SHARE_RECEIPTS_KEY == "shareReceipts"
        assert "." not in SHARE_RECEIPTS_KEY

    def test_evolution_does_not_re_export_the_fence(self) -> None:
        assert "fence_untrusted" not in evolution.__all__
        assert not hasattr(evolution, "fence_untrusted")

    def test_the_fence_is_imported_inside_upload_payload(self) -> None:
        function = _function(_receipts_tree(), "upload_payload")

        imported = {
            alias.name
            for node in ast.walk(function)
            if isinstance(node, ast.ImportFrom) and node.module == "molmcp.helpers.text"
            for alias in node.names
        }

        assert "fence_untrusted" in imported

    def test_the_fence_is_not_a_module_level_import(self) -> None:
        names = _module_level_imports(_receipts_tree())

        assert "fence_untrusted" not in names
        assert "molmcp.helpers.text" not in names

    def test_the_package_imports_no_server_ledger_or_settings(self) -> None:
        assert _EVOLUTION.is_dir(), f"{_EVOLUTION} does not exist yet"

        offenders: dict[str, set[str]] = {}
        for path in sorted(_EVOLUTION.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            found = {
                name
                for name in _imported_modules(ast.parse(source))
                if name == "fastmcp"
                or name.startswith("fastmcp.")
                or name.endswith("adopt.ledger")
            }
            found |= {
                name for name in ("load_settings", "write_ledger") if name in source
            }
            if found:
                offenders[path.name] = found

        assert offenders == {}
