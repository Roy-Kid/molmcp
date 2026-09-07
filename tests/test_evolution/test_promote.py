"""Local PR, identity gate, and risk-graded promotion of a snapshot sha.

Mirrors ``src/molmcp/evolution/promote.py``; one class per public behaviour
(``PromotionRequest`` the value object, ``GatePolicy`` the decision table,
``Promoter`` the pointer mover). ``AuthorKind``, ``Risk``, ``GateDecision``,
``ApplyOutcome``, ``ApplyResult`` and ``HistoryEntry`` are exercised through
those three: they are literals and records a caller reads, and a test that
only constructed them would pin no behaviour.

Five disciplines are pinned here that no single assertion makes obvious.

*The activation is a seam, and the fake is the guard.* ``Promoter`` never
imports the real pointer machine; it is handed one, and the only four names
it may call are ``stage`` / ``promote`` / ``bind`` / ``rollback``. The fake's
``promote`` and ``rollback`` are **nullary**, so an implementation reaching
for ``promote(sha)`` raises ``TypeError`` here rather than quietly writing
the pointer twice. ``bind`` exists on the fake only to prove it is never
called: whoever injects the activation has already bound it.

*High risk parks; it does not stage.* The canary branch writes one private
JSON file and makes **zero** calls — ``stage`` included. A ``stage`` with no
``promote`` behind it would leave a staged sha nobody owns, so the
high-risk tests assert the empty call sequence rather than only an unchanged
pointer.

*A ``rolled_back`` entry consumes the ``previous`` slot.* The current
activation is the last ``activated`` entry with **no** ``rolled_back`` after
it anywhere in the log — not the newest ``activated`` left unpaired by
``report_id``. Both binding cases (apply A, apply B, ``rollback(A)``; then
apply A, apply B, ``rollback(B)``, ``rollback(A)``) are written out in full,
because a per-id implementation passes every other test in this module and
then swaps B back in as a second-generation activation.

*The ``rollback()`` return value is not a sha.* The fake returns a string
that is not a sha at all, and the appended history entry has to carry the
sha of the ``activated`` record the Promoter looked up instead.

*The gate is a table.* ``decide`` is hit directly — no promoter, no pointer,
no file — and the source is read only to prove it names no HTTP client, no
credential and no forge.

Nothing here reads a clock, the network, or the environment. The only
directory touched is ``tmp_path``, and the only file read outside it is
``promote.py`` itself.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from molmcp.evolution import promote as promote_module
from molmcp.evolution.promote import (
    PROMOTER_STATE_VERSION,
    ApplyOutcome,
    ApplyResult,
    AuthorKind,
    GateDecision,
    GatePolicy,
    HistoryEntry,
    Promoter,
    PromoterError,
    PromotionRequest,
    Risk,
)

_REPO = Path(__file__).resolve().parents[2]
_PROMOTE = _REPO / "src" / "molmcp" / "evolution" / "promote.py"

#: The dotted package the module under test lives in, used to resolve the
#: relative imports its isolation check has to see through.
_PACKAGE_PARTS: tuple[str, ...] = ("molmcp", "evolution")

#: Enum members by *value*, not by member name: the spec pins the strings
#: that reach the wire and a JSON file, never the Python spelling.
_OWNER = AuthorKind("owner")
_BOT = AuthorKind("bot")
_OTHER = AuthorKind("other")
_LOW = Risk("low")
_HIGH = Risk("high")

#: The three shas of the worked example, and the report ids that carry them.
#: Full 40-hex: an abbreviated sha is not an identity this layer accepts.
_SHA_A = "a" * 40
_SHA_B = "b" * 40
_SHA_C = "c" * 40
_REPORT_A = "report-skill-1"
_REPORT_B = "report-provider-1"
_REPORT_C = "report-reject-1"

#: The two private state files, named by the spec.
_CANARY = "canary.json"
_HISTORY = "history.json"

#: What the fake ``rollback()`` hands back. Deliberately not a sha and not a
#: report id: the Promoter must ignore it and read the history record it
#: already looked up.
_BOGUS_ROLLBACK_RETURN = "whatever-the-pointer-felt-like-returning"

#: ``PromotionRequest`` fields, in the order the spec's value-object table
#: lists them. ``accepted`` sits before the two defaulted flags because it
#: has no default of its own.
_REQUEST_FIELDS: tuple[str, ...] = (
    "sha",
    "report_id",
    "author",
    "risk",
    "accepted",
    "approved",
    "path_allowed",
)

#: ``HistoryEntry`` fields, in the order the stored JSON entry lists them.
_ENTRY_FIELDS: tuple[str, ...] = ("sha", "report_id", "action")

#: Rejected at construction. Uppercase hex, a tag, and an abbreviation are
#: each a *plausible* commit identity, which is why each one is named here
#: rather than left to a single "not 40 hex" case.
_INVALID_SHAS: tuple[str, ...] = (
    "A" * 40,
    "a" * 39,
    "a" * 41,
    "v1.2.3",
    "aaaaaaa",
    "g" * 40,
    "",
    " " + "a" * 39,
)

#: Report ids with no identity in them.
_BLANK_REPORT_IDS: tuple[str, ...] = ("", " ", "\t", "\n", "   ")

#: Names this module may not define. There is no verdict type: the gate
#: consumes ``accepted``, the same bool spec 11 already published, and a
#: second vocabulary for the same fact is a second source of truth.
_ABSENT_MODULE_NAMES: tuple[str, ...] = (
    "Verdict",
    "verdict",
    "PASS",
    "FAIL",
    "Pass",
    "Fail",
)

#: Text the gate's module may not contain at all. The check is the lowercase
#: spelling, so prose may still say "GitHub" while ``import github`` cannot
#: hide — but "credential" is the word to reach for, not the other one. A
#: decision table that names an HTTP client is no longer a table.
_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "requests",
    "urllib",
    "token",
    "github",
)

#: A module that reads the environment cannot be reported by
#: ``molmcp config list``; see ``tests/test_no_env_switches.py``.
_ENV_TOKENS: tuple[str, ...] = ("os.environ", "getenv")

#: Packages this leaf may not reach for. The two ``molmcp.components``
#: entries are the point: the activation arrives through the constructor as
#: a duck type, so importing the module that defines it is the coupling this
#: forbids.
_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "fastmcp",
    "github",
    "mcp",
    "molmcp.cli",
    "molmcp.collection",
    "molmcp.components.activate",
    "molmcp.components.store",
    "molmcp.evolution.wiki",
    "molmcp.providers",
    "molmcp.server",
    "wiki",
)


class ActivationUnboundError(Exception):
    """Stand-in for whatever an unbound activation raises.

    No such type exists in this repository — the real activation is
    unconstructable until it is bound, so this branch is unreachable
    through it. It is reachable through the *seam*: ``Promoter`` takes a
    duck type and matches on ``type(exc).__name__``, so a fake raising a
    class of this name is exactly the case the wrapping guards.
    """


class _FakeActivation:
    """The pointer machine, reduced to the four names a Promoter may call.

    Records ``(name, args, kwargs)`` per call. ``promote`` and ``rollback``
    are nullary on purpose: passing a sha to either is a ``TypeError`` here,
    which is the contract this fake exists to enforce.

    Args:
        current: Initial pointer value, or ``None`` for an activation that
            has promoted nothing yet.
        pointer: Attribute name the pointer is published under — ``current``
            as the real one spells it, ``active`` for the fallback read.
        raises: Method names that raise :class:`ActivationUnboundError`
            after recording the call.
    """

    def __init__(
        self,
        *,
        current: str | None = None,
        pointer: str = "current",
        raises: tuple[str, ...] = (),
    ) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self._pointer_name = pointer
        self._staged: str | None = None
        self._previous: str | None = None
        self._raises = raises
        setattr(self, pointer, current)

    # -- inspection --------------------------------------------------------

    @property
    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    def count(self, name: str) -> int:
        return self.names.count(name)

    def only(self, name: str) -> tuple[tuple[object, ...], dict[str, object]]:
        matched = [
            (args, kwargs) for called, args, kwargs in self.calls if called == name
        ]
        assert len(matched) == 1, f"{name} called {len(matched)} times: {self.names}"
        return matched[0]

    def pointer_value(self) -> str | None:
        value = getattr(self, self._pointer_name)
        return value if isinstance(value, str) else None

    # -- the four names ----------------------------------------------------

    def stage(self, sha: str) -> None:
        self._record("stage", (sha,), {})
        self._staged = sha

    def promote(self) -> None:
        self._record("promote", (), {})
        self._previous = self.pointer_value()
        setattr(self, self._pointer_name, self._staged)
        self._staged = None

    def bind(self) -> None:
        self._record("bind", (), {})

    def rollback(self) -> str:
        self._record("rollback", (), {})
        promoted = self.pointer_value()
        setattr(self, self._pointer_name, self._previous)
        self._previous = promoted
        return _BOGUS_ROLLBACK_RETURN

    def _record(
        self,
        name: str,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> None:
        self.calls.append((name, args, kwargs))
        if name in self._raises:
            raise ActivationUnboundError(name)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """The Promoter's private directory, separate from any lock directory."""
    path = tmp_path / "promoter"
    path.mkdir()
    return path


@pytest.fixture
def fake() -> _FakeActivation:
    return _FakeActivation()


@pytest.fixture
def promoter(fake: _FakeActivation, state_dir: Path) -> Promoter:
    return Promoter(activation=fake, state_dir=state_dir)


def _request(
    *,
    sha: str = _SHA_A,
    report_id: str = _REPORT_A,
    author: AuthorKind = _OWNER,
    risk: Risk = _LOW,
    accepted: bool = True,
    approved: bool = False,
    path_allowed: bool = True,
) -> PromotionRequest:
    """The worked example, with at most one field swapped out."""
    return PromotionRequest(
        sha=sha,
        report_id=report_id,
        author=author,
        risk=risk,
        accepted=accepted,
        approved=approved,
        path_allowed=path_allowed,
    )


def _low_a() -> PromotionRequest:
    return _request(sha=_SHA_A, report_id=_REPORT_A, risk=_LOW)


def _low_b() -> PromotionRequest:
    return _request(sha=_SHA_B, report_id=_REPORT_B, risk=_LOW)


def _high_b() -> PromotionRequest:
    return _request(sha=_SHA_B, report_id=_REPORT_B, risk=_HIGH)


def _rejected_c() -> PromotionRequest:
    return _request(sha=_SHA_C, report_id=_REPORT_C, accepted=False)


def _doc(state_dir: Path, name: str) -> dict[str, object]:
    path = state_dir / name
    assert path.is_file(), f"{path} was not written"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _canary_doc(state_dir: Path) -> dict[str, object]:
    return _doc(state_dir, _CANARY)


def _entries(state_dir: Path) -> list[dict[str, object]]:
    entries = _doc(state_dir, _HISTORY)["entries"]
    assert isinstance(entries, list)
    return entries


def _last_entry(state_dir: Path) -> dict[str, object]:
    entries = _entries(state_dir)
    assert entries, "history has no entries"
    return entries[-1]


def _actions(state_dir: Path) -> list[object]:
    return [entry["action"] for entry in _entries(state_dir)]


def _write_json(state_dir: Path, name: str, payload: dict[str, object]) -> None:
    (state_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def _names(state_dir: Path) -> list[str]:
    return sorted(entry.name for entry in state_dir.iterdir())


def _promote_source() -> str:
    assert _PROMOTE.is_file(), f"{_PROMOTE} does not exist yet"
    return _PROMOTE.read_text(encoding="utf-8")


def _resolved_module(node: ast.ImportFrom) -> str:
    """The dotted module *node* names, with a relative import made absolute."""
    if not node.level:
        return node.module or ""
    kept = len(_PACKAGE_PARTS) - node.level + 1
    base = ".".join(_PACKAGE_PARTS[:kept]) if kept > 0 else ""
    if not node.module:
        return base
    return f"{base}.{node.module}" if base else node.module


def _module_level_imports(tree: ast.Module) -> set[str]:
    """Modules imported at module level — not inside a function or a block."""
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_module(node)
            modules.add(module)
            modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


class TestPromotionRequest:
    def test_carries_the_seven_fields_it_was_given(self) -> None:
        request = _request(
            sha=_SHA_B,
            report_id=_REPORT_B,
            author=_BOT,
            risk=_HIGH,
            accepted=True,
            approved=True,
            path_allowed=False,
        )

        assert request.sha == _SHA_B
        assert request.report_id == _REPORT_B
        assert request.author == "bot"
        assert request.risk == "high"
        assert request.accepted is True
        assert request.approved is True
        assert request.path_allowed is False

    def test_field_names_are_the_value_object_table_in_order(self) -> None:
        names = tuple(field.name for field in dataclasses.fields(PromotionRequest))

        assert names == _REQUEST_FIELDS

    def test_has_exactly_seven_fields(self) -> None:
        assert len(dataclasses.fields(PromotionRequest)) == 7

    def test_accepted_has_no_default(self) -> None:
        """The verdict is carried in, never assumed by whoever forgot it."""
        with pytest.raises(TypeError):
            PromotionRequest(  # type: ignore[call-arg]
                sha=_SHA_A,
                report_id=_REPORT_A,
                author=_OWNER,
                risk=_LOW,
            )

    def test_approved_defaults_to_false_and_path_allowed_to_true(self) -> None:
        request = PromotionRequest(
            sha=_SHA_A,
            report_id=_REPORT_A,
            author=_OWNER,
            risk=_LOW,
            accepted=True,
        )

        assert request.approved is False
        assert request.path_allowed is True

    @pytest.mark.parametrize("field_name", _REQUEST_FIELDS)
    def test_is_frozen(self, field_name: str) -> None:
        request = _request()

        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(request, field_name, "mutated")

    def test_uses_slots(self) -> None:
        assert hasattr(PromotionRequest, "__slots__")
        assert not hasattr(_request(), "__dict__")

    def test_author_kind_is_exactly_owner_bot_and_other(self) -> None:
        values = {member.value for member in AuthorKind}

        assert values == {"owner", "bot", "other"}
        assert len(list(AuthorKind)) == 3

    def test_risk_is_exactly_low_and_high(self) -> None:
        values = {member.value for member in Risk}

        assert values == {"low", "high"}
        assert len(list(Risk)) == 2

    def test_both_enums_are_their_own_strings(self) -> None:
        """``StrEnum``: the stored value is the literal, not ``AuthorKind.OWNER``."""
        assert isinstance(_OWNER, str)
        assert isinstance(_LOW, str)
        assert _OWNER == "owner"
        assert _LOW == "low"

    @pytest.mark.parametrize("sha", _INVALID_SHAS)
    def test_rejects_a_sha_that_is_not_forty_lowercase_hex(self, sha: str) -> None:
        with pytest.raises(PromoterError) as excinfo:
            _request(sha=sha)

        assert excinfo.value.code == "invalid-sha"

    def test_accepts_a_full_lowercase_sha(self) -> None:
        assert _request(sha="0123456789abcdef" + "0" * 24).sha.islower()

    @pytest.mark.parametrize("report_id", _BLANK_REPORT_IDS)
    def test_rejects_a_report_id_with_no_identity_in_it(self, report_id: str) -> None:
        with pytest.raises(PromoterError):
            _request(report_id=report_id)

    def test_the_error_is_a_value_error(self) -> None:
        assert issubclass(PromoterError, ValueError)

    @pytest.mark.parametrize("name", _ABSENT_MODULE_NAMES)
    def test_the_module_defines_no_verdict(self, name: str) -> None:
        """``accepted`` is spec 11's bool; a pass/fail enum would be a second one."""
        assert not hasattr(promote_module, name)

    def test_the_request_carries_no_verdict_attribute(self) -> None:
        assert not hasattr(_request(), "verdict")
        assert "verdict" not in _REQUEST_FIELDS


class TestGatePolicy:
    def test_an_owner_with_an_accepted_report_is_allowed(self) -> None:
        decision = GatePolicy().decide(_request(author=_OWNER))

        assert decision.allow is True

    def test_an_owner_does_not_need_approval(self) -> None:
        """``approved`` is the ``other`` lane; the owner never waits on it."""
        decision = GatePolicy().decide(_request(author=_OWNER, approved=False))

        assert decision.allow is True
        assert decision.reason != "needs-approval"

    def test_an_owner_with_a_failed_report_is_refused(self) -> None:
        """No exemption. A failed report moves no pointer, whoever filed it."""
        decision = GatePolicy().decide(_request(author=_OWNER, accepted=False))

        assert decision.allow is False
        assert decision.reason == "failed-report"

    def test_a_bot_with_a_failed_report_is_refused(self) -> None:
        decision = GatePolicy().decide(_request(author=_BOT, accepted=False))

        assert decision.allow is False
        assert decision.reason == "failed-report"

    def test_an_approved_other_with_a_failed_report_is_refused(self) -> None:
        """``accepted`` is read first: approval cannot buy a failed report in."""
        decision = GatePolicy().decide(
            _request(author=_OTHER, accepted=False, approved=True)
        )

        assert decision.allow is False
        assert decision.reason == "failed-report"

    def test_an_unapproved_other_needs_approval(self) -> None:
        decision = GatePolicy().decide(_request(author=_OTHER, approved=False))

        assert decision.allow is False
        assert decision.reason == "needs-approval"

    def test_an_approved_other_is_allowed(self) -> None:
        decision = GatePolicy().decide(_request(author=_OTHER, approved=True))

        assert decision.allow is True

    def test_a_bot_outside_its_paths_is_refused(self) -> None:
        decision = GatePolicy().decide(_request(author=_BOT, path_allowed=False))

        assert decision.allow is False
        assert decision.reason == "path-not-allowed"

    def test_a_bot_inside_its_paths_is_allowed(self) -> None:
        decision = GatePolicy().decide(_request(author=_BOT, path_allowed=True))

        assert decision.allow is True

    def test_a_bot_inside_its_paths_does_not_need_approval(self) -> None:
        decision = GatePolicy().decide(
            _request(author=_BOT, path_allowed=True, approved=False)
        )

        assert decision.allow is True
        assert decision.reason != "needs-approval"

    def test_path_allowed_does_not_gate_an_other(self) -> None:
        """The path whitelist is the bot's lane; approval is the other's."""
        decision = GatePolicy().decide(
            _request(author=_OTHER, approved=True, path_allowed=False)
        )

        assert decision.allow is True

    def test_approval_does_not_open_a_bots_forbidden_path(self) -> None:
        decision = GatePolicy().decide(
            _request(author=_BOT, approved=True, path_allowed=False)
        )

        assert decision.allow is False
        assert decision.reason == "path-not-allowed"

    def test_the_decision_is_two_fields(self) -> None:
        names = tuple(field.name for field in dataclasses.fields(GateDecision))

        assert names == ("allow", "reason")

    def test_the_decision_is_frozen(self) -> None:
        decision = GatePolicy().decide(_request())

        with pytest.raises(dataclasses.FrozenInstanceError):
            decision.allow = False  # type: ignore[misc]

    def test_the_decision_uses_slots(self) -> None:
        assert hasattr(GateDecision, "__slots__")
        assert not hasattr(GatePolicy().decide(_request()), "__dict__")

    def test_the_reason_is_a_stable_literal(self) -> None:
        assert isinstance(GatePolicy().decide(_request()).reason, str)

    def test_deciding_twice_gives_the_same_answer(self) -> None:
        """A table, not a lookup: the second call asks nobody anything."""
        request = _request(author=_OTHER, approved=False)

        first = GatePolicy().decide(request)
        second = GatePolicy().decide(request)

        assert first == second
        assert first.reason == "needs-approval"

    @pytest.mark.parametrize("token", _FORBIDDEN_TOKENS)
    def test_the_source_names_no_client_credential_or_forge(self, token: str) -> None:
        """The gate is an identity table; asking a forge who someone is is IO."""
        assert token not in _promote_source()


class TestPromoter:
    # -- construction ------------------------------------------------------

    def test_both_seams_are_keyword_only(self, state_dir: Path) -> None:
        with pytest.raises(TypeError):
            Promoter(_FakeActivation(), state_dir)  # type: ignore[misc]

    def test_the_activation_has_no_default(self, state_dir: Path) -> None:
        """No factory: a real pointer machine would drag the store in here."""
        with pytest.raises(TypeError):
            Promoter(state_dir=state_dir)  # type: ignore[call-arg]

    def test_the_state_dir_has_no_default(self, fake: _FakeActivation) -> None:
        with pytest.raises(TypeError):
            Promoter(activation=fake)  # type: ignore[call-arg]

    def test_the_state_version_is_one(self) -> None:
        assert PROMOTER_STATE_VERSION == 1
        assert isinstance(PROMOTER_STATE_VERSION, int)

    def test_the_error_carries_the_code_it_was_given(self) -> None:
        assert PromoterError(code="not-current").code == "not-current"

    # -- the fake is the guard ---------------------------------------------

    def test_the_fake_refuses_a_promote_that_carries_a_sha(self) -> None:
        """Why ``args == ()`` below has teeth: ``promote`` takes nothing."""
        with pytest.raises(TypeError):
            _FakeActivation().promote(_SHA_A)  # type: ignore[call-arg]

    def test_the_fake_refuses_a_rollback_that_carries_a_sha(self) -> None:
        with pytest.raises(TypeError):
            _FakeActivation().rollback(_SHA_A)  # type: ignore[call-arg]

    # -- apply: the rejected branch ----------------------------------------

    def test_a_refused_request_touches_no_pointer(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        result = promoter.apply(_rejected_c())

        assert result.outcome == "rejected"
        assert fake.names == []
        assert fake.current is None

    def test_a_refused_request_writes_no_canary(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        promoter.apply(_rejected_c())

        assert not (state_dir / _CANARY).exists()

    def test_a_refused_request_is_recorded_as_rejected(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        promoter.apply(_rejected_c())

        entry = _last_entry(state_dir)
        assert entry["action"] == "rejected"
        assert entry["sha"] == _SHA_C
        assert entry["report_id"] == _REPORT_C

    def test_the_refusal_reason_stays_out_of_the_history(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        """The narrative belongs to the wiki; the ledger keeps three fields."""
        promoter.apply(_rejected_c())

        entry = _last_entry(state_dir)
        assert "reason" not in entry
        assert set(entry) >= set(_ENTRY_FIELDS)

    def test_an_unapproved_other_never_reaches_the_pointer(
        self, promoter: Promoter, fake: _FakeActivation, state_dir: Path
    ) -> None:
        result = promoter.apply(_request(author=_OTHER, approved=False))

        assert result.outcome == "rejected"
        assert fake.names == []
        assert not (state_dir / _CANARY).exists()

    # -- apply: the low-risk branch ----------------------------------------

    def test_low_risk_stages_then_promotes_in_that_order(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        promoter.apply(_low_a())

        assert fake.names == ["stage", "promote"]

    def test_low_risk_stages_the_requested_sha(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        promoter.apply(_low_a())

        assert fake.only("stage") == ((_SHA_A,), {})

    def test_the_promote_carries_no_sha(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        """Nullary by contract: the staged sha is already the pointer's to read."""
        promoter.apply(_low_a())

        args, kwargs = fake.only("promote")
        assert args == ()
        assert kwargs == {}
        assert _SHA_A not in kwargs.values()

    def test_low_risk_leaves_the_pointer_on_the_requested_sha(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        promoter.apply(_low_a())

        assert fake.current == _SHA_A

    def test_low_risk_is_recorded_as_activated(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        result = promoter.apply(_low_a())

        assert result.outcome == "activated"
        entry = _last_entry(state_dir)
        assert entry["action"] == "activated"
        assert entry["sha"] == _SHA_A
        assert entry["report_id"] == _REPORT_A

    def test_low_risk_writes_no_canary(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        promoter.apply(_low_a())

        assert not (state_dir / _CANARY).exists()

    # -- apply: the high-risk branch ---------------------------------------

    def test_high_risk_parks_the_sha_in_the_canary_file(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        result = promoter.apply(_high_b())

        canary = _canary_doc(state_dir)
        assert result.outcome == "canaried"
        assert canary["sha"] == _SHA_B
        assert canary["report_id"] == _REPORT_B
        assert canary["version"] == PROMOTER_STATE_VERSION
        assert isinstance(canary["version"], int)

    def test_high_risk_makes_no_call_at_all(self, state_dir: Path) -> None:
        """Zero calls, ``stage`` included: a staged sha with no promote behind
        it is leftover state this spec has no compensation for."""
        fake = _FakeActivation(current=_SHA_A)
        promoter = Promoter(activation=fake, state_dir=state_dir)

        promoter.apply(_high_b())

        assert fake.names == []
        assert fake.count("stage") == 0

    def test_high_risk_leaves_the_pointer_where_it_was(self, state_dir: Path) -> None:
        fake = _FakeActivation(current=_SHA_A)
        promoter = Promoter(activation=fake, state_dir=state_dir)

        promoter.apply(_high_b())

        assert fake.current == _SHA_A

    def test_high_risk_is_recorded_as_canaried(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        promoter.apply(_high_b())

        entry = _last_entry(state_dir)
        assert entry["action"] == "canaried"
        assert entry["sha"] == _SHA_B
        assert entry["report_id"] == _REPORT_B

    def test_a_second_sha_cannot_take_an_occupied_canary(
        self, promoter: Promoter, fake: _FakeActivation, state_dir: Path
    ) -> None:
        promoter.apply(_high_b())

        with pytest.raises(PromoterError) as excinfo:
            promoter.apply(_request(sha=_SHA_C, report_id=_REPORT_C, risk=_HIGH))

        assert excinfo.value.code == "canary-occupied"
        assert _canary_doc(state_dir)["sha"] == _SHA_B
        assert fake.names == []

    def test_the_same_sha_may_re_take_its_own_canary(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        promoter.apply(_high_b())
        promoter.apply(_high_b())

        assert _canary_doc(state_dir)["sha"] == _SHA_B

    # -- apply: what it never does -----------------------------------------

    def test_bind_is_never_called_on_any_branch(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        """Whoever injected the activation has already bound it."""
        promoter.apply(_rejected_c())
        promoter.apply(_low_a())
        promoter.apply(_high_b())
        promoter.rollback(_REPORT_A)

        assert "bind" not in fake.names

    def test_the_outcome_vocabulary_is_the_three_apply_actions(self) -> None:
        """``rolled_back`` is absent: ``apply`` never rolls anything back."""
        values = {member.value for member in ApplyOutcome}

        assert values == {"activated", "canaried", "rejected"}

    def test_the_result_is_frozen(self, promoter: Promoter) -> None:
        result = promoter.apply(_low_a())

        with pytest.raises(dataclasses.FrozenInstanceError):
            result.outcome = ApplyOutcome("rejected")  # type: ignore[misc]

    def test_the_result_uses_slots(self, promoter: Promoter) -> None:
        assert hasattr(ApplyResult, "__slots__")
        assert not hasattr(promoter.apply(_low_a()), "__dict__")

    # -- the private state files -------------------------------------------

    def test_the_history_document_carries_an_integer_version(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        promoter.apply(_low_a())

        version = _doc(state_dir, _HISTORY)["version"]
        assert version == PROMOTER_STATE_VERSION
        assert isinstance(version, int)

    def test_a_history_entry_carries_no_version_of_its_own(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        """One integer version per document, not per row."""
        promoter.apply(_low_a())

        assert "version" not in _last_entry(state_dir)

    def test_a_history_entry_is_the_stored_row_in_order(self) -> None:
        names = tuple(field.name for field in dataclasses.fields(HistoryEntry))

        assert names == _ENTRY_FIELDS
        assert "version" not in names

    def test_a_history_entry_is_a_frozen_slotted_record(self) -> None:
        entry = HistoryEntry(sha=_SHA_A, report_id=_REPORT_A, action="activated")

        assert entry.sha == _SHA_A
        assert entry.report_id == _REPORT_A
        assert entry.action == "activated"
        assert hasattr(HistoryEntry, "__slots__")
        assert not hasattr(entry, "__dict__")
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.sha = _SHA_B  # type: ignore[misc]

    def test_a_missing_history_reads_as_no_entries(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        assert _names(state_dir) == []

        promoter.apply(_low_a())

        assert _actions(state_dir) == ["activated"]

    def test_unknown_history_keys_survive_a_rewrite(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        """Read-time ignorance, write-time preservation: another writer's keys
        are not this module's to drop."""
        _write_json(
            state_dir,
            _HISTORY,
            {
                "version": PROMOTER_STATE_VERSION,
                "written_by": "some-other-writer",
                "entries": [
                    {
                        "sha": _SHA_C,
                        "report_id": "report-old-1",
                        "action": "activated",
                        "note": "kept verbatim",
                    }
                ],
            },
        )

        promoter.apply(_low_a())

        document = _doc(state_dir, _HISTORY)
        entries = _entries(state_dir)
        assert document["written_by"] == "some-other-writer"
        assert document["version"] == PROMOTER_STATE_VERSION
        assert entries[0]["note"] == "kept verbatim"
        assert entries[0]["sha"] == _SHA_C
        assert len(entries) == 2
        assert entries[1]["action"] == "activated"

    def test_unknown_canary_keys_survive_a_rewrite(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        _write_json(
            state_dir,
            _CANARY,
            {
                "version": PROMOTER_STATE_VERSION,
                "sha": _SHA_B,
                "report_id": _REPORT_B,
                "parked_by": "some-other-writer",
            },
        )

        promoter.apply(_high_b())

        canary = _canary_doc(state_dir)
        assert canary["parked_by"] == "some-other-writer"
        assert canary["sha"] == _SHA_B
        assert canary["version"] == PROMOTER_STATE_VERSION

    def test_no_partial_file_is_left_behind(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        promoter.apply(_low_a())
        promoter.apply(_high_b())

        assert [name for name in _names(state_dir) if name.endswith(".partial")] == []

    def test_only_the_two_private_files_are_written(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        """No wiki page, no lock, no receipt: two JSON files and nothing else."""
        promoter.apply(_low_a())
        promoter.apply(_high_b())

        assert _names(state_dir) == [_CANARY, _HISTORY]

    # -- the unbound seam --------------------------------------------------

    @pytest.mark.parametrize("failing", ("stage", "promote"))
    def test_an_unbound_activation_is_wrapped(
        self, state_dir: Path, failing: str
    ) -> None:
        """Matched on the class *name*: this leaf imports no pointer type."""
        fake = _FakeActivation(raises=(failing,))
        promoter = Promoter(activation=fake, state_dir=state_dir)

        with pytest.raises(PromoterError) as excinfo:
            promoter.apply(_low_a())

        assert excinfo.value.code == "unbound"

    def test_an_unbound_rollback_is_wrapped(self, state_dir: Path) -> None:
        fake = _FakeActivation(raises=("rollback",))
        promoter = Promoter(activation=fake, state_dir=state_dir)
        promoter.apply(_low_a())

        with pytest.raises(PromoterError) as excinfo:
            promoter.rollback(_REPORT_A)

        assert excinfo.value.code == "unbound"

    # -- rollback: the two binding cases -----------------------------------

    def test_rolling_back_the_older_of_two_activations_is_refused(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        """B is the current activation; A is a generation nobody can reach."""
        promoter.apply(_low_a())
        promoter.apply(_low_b())

        with pytest.raises(PromoterError) as excinfo:
            promoter.rollback(_REPORT_A)

        assert excinfo.value.code == "not-current"
        assert fake.count("rollback") == 0
        assert fake.current == _SHA_B

    def test_rolling_back_the_newer_of_two_activations_pops_one_generation(
        self, promoter: Promoter, fake: _FakeActivation, state_dir: Path
    ) -> None:
        promoter.apply(_low_a())
        promoter.apply(_low_b())

        promoter.rollback(_REPORT_B)

        assert fake.count("rollback") == 1
        assert fake.only("rollback") == ((), {})
        entry = _last_entry(state_dir)
        assert entry["action"] == "rolled_back"
        assert entry["sha"] == _SHA_B
        assert entry["report_id"] == _REPORT_B
        assert fake.current == _SHA_A

    def test_a_second_rollback_does_not_walk_back_a_generation(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        """The binding case. A ``rolled_back`` entry consumes the previous
        slot, so there is no current activation left to roll back — a
        per-report_id pairing would swap B back in as a second generation."""
        promoter.apply(_low_a())
        promoter.apply(_low_b())
        promoter.rollback(_REPORT_B)

        with pytest.raises(PromoterError) as excinfo:
            promoter.rollback(_REPORT_A)

        assert excinfo.value.code == "not-current"
        assert fake.count("rollback") == 1
        assert fake.current == _SHA_A

    def test_the_rolled_back_sha_is_not_the_return_value(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        """The record is read out of the history, not off the call."""
        promoter.apply(_low_a())
        promoter.apply(_low_b())

        promoter.rollback(_REPORT_B)

        entry = _last_entry(state_dir)
        assert entry["sha"] != _BOGUS_ROLLBACK_RETURN
        assert entry["report_id"] != _BOGUS_ROLLBACK_RETURN
        assert _BOGUS_ROLLBACK_RETURN not in (state_dir / _HISTORY).read_text(
            encoding="utf-8"
        )

    # -- rollback: everything else -----------------------------------------

    def test_rolling_back_the_only_activation_is_allowed(
        self, promoter: Promoter, fake: _FakeActivation, state_dir: Path
    ) -> None:
        promoter.apply(_low_a())

        promoter.rollback(_REPORT_A)

        assert fake.count("rollback") == 1
        assert _actions(state_dir) == ["activated", "rolled_back"]

    def test_rolling_back_again_needs_a_new_activation(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        promoter.apply(_low_a())
        promoter.rollback(_REPORT_A)

        with pytest.raises(PromoterError) as excinfo:
            promoter.rollback(_REPORT_A)

        assert excinfo.value.code == "not-current"
        assert fake.count("rollback") == 1

    def test_a_fresh_activation_reopens_rollback(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        promoter.apply(_low_a())
        promoter.rollback(_REPORT_A)
        promoter.apply(_low_b())

        promoter.rollback(_REPORT_B)

        assert fake.count("rollback") == 2

    def test_an_unknown_report_is_refused(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        promoter.apply(_low_a())
        before = list(fake.names)

        with pytest.raises(PromoterError) as excinfo:
            promoter.rollback("report-nobody-filed")

        assert excinfo.value.code == "unknown-report"
        assert fake.names == before

    def test_an_empty_history_refuses_every_report(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        with pytest.raises(PromoterError) as excinfo:
            promoter.rollback(_REPORT_A)

        assert excinfo.value.code == "unknown-report"
        assert fake.names == []

    def test_a_canaried_report_cannot_be_rolled_back(
        self, promoter: Promoter, fake: _FakeActivation, state_dir: Path
    ) -> None:
        promoter.apply(_high_b())

        with pytest.raises(PromoterError) as excinfo:
            promoter.rollback(_REPORT_B)

        assert excinfo.value.code == "canaried"
        assert fake.names == []
        assert _canary_doc(state_dir)["sha"] == _SHA_B

    def test_a_rejected_report_cannot_be_rolled_back(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        promoter.apply(_rejected_c())

        with pytest.raises(PromoterError) as excinfo:
            promoter.rollback(_REPORT_C)

        assert excinfo.value.code == "rejected"
        assert fake.names == []

    def test_the_most_recent_entry_for_the_report_decides(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        """A later refusal of the same report shadows its earlier activation."""
        promoter.apply(_low_a())
        promoter.apply(_request(sha=_SHA_A, report_id=_REPORT_A, accepted=False))
        before = list(fake.names)

        with pytest.raises(PromoterError) as excinfo:
            promoter.rollback(_REPORT_A)

        assert excinfo.value.code == "rejected"
        assert fake.names == before

    def test_a_canary_is_not_cleared_by_an_unrelated_rollback(
        self, promoter: Promoter, state_dir: Path
    ) -> None:
        """Graduating or dropping a canary belongs to a later spec."""
        promoter.apply(_low_a())
        promoter.apply(_high_b())

        promoter.rollback(_REPORT_A)

        assert _canary_doc(state_dir)["sha"] == _SHA_B

    def test_a_pointer_that_moved_underneath_refuses_the_rollback(
        self, promoter: Promoter, fake: _FakeActivation
    ) -> None:
        """Somebody else promoted since; this is not ours to pop."""
        promoter.apply(_low_a())
        fake.current = _SHA_C
        before = list(fake.names)

        with pytest.raises(PromoterError) as excinfo:
            promoter.rollback(_REPORT_A)

        assert excinfo.value.code == "not-current"
        assert fake.names == before

    def test_a_pointer_published_as_active_is_read_too(self, state_dir: Path) -> None:
        """``current`` first, ``active`` as the fallback — and no third name."""
        fake = _FakeActivation(pointer="active")
        promoter = Promoter(activation=fake, state_dir=state_dir)
        promoter.apply(_low_a())
        fake.active = _SHA_C

        with pytest.raises(PromoterError) as excinfo:
            promoter.rollback(_REPORT_A)

        assert excinfo.value.code == "not-current"
        assert fake.count("rollback") == 0

    def test_an_active_named_pointer_still_rolls_back(self, state_dir: Path) -> None:
        fake = _FakeActivation(pointer="active")
        promoter = Promoter(activation=fake, state_dir=state_dir)
        promoter.apply(_low_a())

        promoter.rollback(_REPORT_A)

        assert fake.count("rollback") == 1

    # -- isolation ---------------------------------------------------------

    def test_the_source_imports_no_runtime_pointer_or_forge(self) -> None:
        modules = _module_level_imports(ast.parse(_promote_source()))

        offenders = sorted(
            name
            for name in modules
            if any(
                name == prefix or name.startswith(f"{prefix}.")
                for prefix in _FORBIDDEN_IMPORT_PREFIXES
            )
        )

        assert offenders == []

    @pytest.mark.parametrize("token", _ENV_TOKENS)
    def test_the_source_reads_no_environment_variable(self, token: str) -> None:
        assert token not in _promote_source()

    def test_promotion_is_not_an_mcp_tool(self) -> None:
        """Imported here rather than at module level: the leaf owes it nothing."""
        import molmcp

        for name in ("Promoter", "GatePolicy", "PromotionRequest", "ApplyResult"):
            assert name not in molmcp.__all__
