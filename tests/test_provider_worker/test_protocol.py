"""Duplex v1 NDJSON codec and signature facts for the provider worker."""

from __future__ import annotations

import inspect

import pytest

from molmcp.provider_worker.protocol import (
    ANNOTATION_KEYS,
    MESSAGE_TYPES,
    PROTOCOL_VERSION,
    ProtocolError,
    decode,
    encode_error,
    encode_hello,
    encode_invoke,
    encode_result,
    encode_shutdown,
    rebuild_signature,
    signature_facts,
)

# One hello catalog entry, shaped exactly as child.py sends it: signature
# facts, never a JSON Schema object.
ECHO_TOOL: dict[str, object] = {
    "name": "echo",
    "attribute": "echo",
    "doc": "Echo text back.",
    "annotations": {
        "read_only_hint": True,
        "destructive_hint": False,
        "idempotent_hint": True,
        "open_world_hint": False,
    },
    "parameters": [
        {
            "name": "text",
            "kind": "POSITIONAL_OR_KEYWORD",
            "annotation": "str",
            "has_default": False,
        }
    ],
}

# Hard-coded golden: a v0 hello frame the codec must refuse outright.
PROTOCOL_ZERO_HELLO = '{"type": "hello", "protocol": 0, "name": "echo", "tools": []}'

FACT_KEYS = frozenset({"name", "kind", "annotation", "has_default", "default"})
JSON_SCHEMA_KEYS = frozenset({"properties", "type", "required"})


def sample(text: str, count: int = 3, *, flag: bool = False) -> dict:
    """Frozen shape used to pin signature facts and their inverse."""
    return {"text": text, "count": count, "flag": flag}


def _sample_facts() -> list[dict[str, object]]:
    return signature_facts(inspect.signature(sample))


class TestProtocol:
    """Unit tests for ``molmcp.provider_worker.protocol``."""

    # --- Basics: frozen constants -------------------------------------

    def test_protocol_version_is_one(self) -> None:
        assert PROTOCOL_VERSION == 1

    def test_message_types_are_the_five_duplex_v1_frames(self) -> None:
        assert MESSAGE_TYPES == frozenset(
            {"hello", "invoke", "result", "error", "shutdown"}
        )

    def test_annotation_keys_are_the_four_tool_hints(self) -> None:
        assert ANNOTATION_KEYS == (
            "read_only_hint",
            "destructive_hint",
            "idempotent_hint",
            "open_world_hint",
        )

    def test_protocol_error_is_a_runtime_error(self) -> None:
        assert issubclass(ProtocolError, RuntimeError)

    # --- Basics: round trips, one frame per test ----------------------

    def test_encode_hello_round_trips(self) -> None:
        line = encode_hello(name="echo", tools=[ECHO_TOOL])

        assert line.endswith("\n")
        assert line.count("\n") == 1

        frame = decode(line)

        assert frame["type"] == "hello"
        assert frame["protocol"] == 1
        assert frame["name"] == "echo"
        assert frame["tools"] == [ECHO_TOOL]

    def test_encode_invoke_round_trips_call_id_under_wire_key_id(self) -> None:
        line = encode_invoke(call_id="call-1", name="echo", args={"text": "ping"})

        assert line.endswith("\n")
        assert line.count("\n") == 1

        frame = decode(line)

        assert frame["type"] == "invoke"
        assert frame["protocol"] == 1
        assert frame["id"] == "call-1"
        assert "call_id" not in frame
        assert frame["name"] == "echo"
        assert frame["args"] == {"text": "ping"}

    def test_encode_result_round_trips_call_id_under_wire_key_id(self) -> None:
        line = encode_result(call_id="call-2", value={"text": "ping"})

        assert line.endswith("\n")
        assert line.count("\n") == 1

        frame = decode(line)

        assert frame["type"] == "result"
        assert frame["protocol"] == 1
        assert frame["id"] == "call-2"
        assert "call_id" not in frame
        assert frame["value"] == {"text": "ping"}

    def test_encode_error_round_trips_call_id_under_wire_key_id(self) -> None:
        line = encode_error(call_id="call-3", error="ValueError: boom")

        assert line.endswith("\n")
        assert line.count("\n") == 1

        frame = decode(line)

        assert frame["type"] == "error"
        assert frame["protocol"] == 1
        assert frame["id"] == "call-3"
        assert "call_id" not in frame
        assert frame["error"] == "ValueError: boom"

    def test_encode_shutdown_round_trips(self) -> None:
        line = encode_shutdown()

        assert line.endswith("\n")
        assert line.count("\n") == 1

        frame = decode(line)

        assert frame["type"] == "shutdown"
        assert frame["protocol"] == 1

    def test_encode_shutdown_carries_no_payload(self) -> None:
        frame = decode(encode_shutdown())

        assert set(frame) == {"type", "protocol"}

    # --- Edge: decode rejections --------------------------------------

    def test_decode_rejects_protocol_zero_hello(self) -> None:
        with pytest.raises(ProtocolError) as excinfo:
            decode(PROTOCOL_ZERO_HELLO)

        assert "protocol" in str(excinfo.value)

    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("not json at all", id="non-json"),
            pytest.param('["hello", 1]', id="json-array"),
            pytest.param('{"protocol": 1, "name": "echo"}', id="missing-type"),
            pytest.param('{"type": "bogus", "protocol": 1}', id="unknown-type"),
            pytest.param('{"type": "hello", "name": "echo"}', id="missing-protocol"),
        ],
    )
    def test_decode_rejects_malformed_frames(self, line: str) -> None:
        with pytest.raises(ProtocolError):
            decode(line)

    # --- Basics: signature facts --------------------------------------

    def test_signature_facts_returns_a_list_not_a_json_schema(self) -> None:
        facts = _sample_facts()

        assert isinstance(facts, list)
        for fact in facts:
            assert isinstance(fact, dict)
            assert JSON_SCHEMA_KEYS.isdisjoint(fact)
            assert set(fact) <= FACT_KEYS

    def test_signature_facts_pins_every_parameter(self) -> None:
        assert _sample_facts() == [
            {
                "name": "text",
                "kind": "POSITIONAL_OR_KEYWORD",
                "annotation": "str",
                "has_default": False,
            },
            {
                "name": "count",
                "kind": "POSITIONAL_OR_KEYWORD",
                "annotation": "int",
                "has_default": True,
                "default": 3,
            },
            {
                "name": "flag",
                "kind": "KEYWORD_ONLY",
                "annotation": "bool",
                "has_default": True,
                "default": False,
            },
        ]

    def test_signature_facts_omits_default_key_without_a_default(self) -> None:
        text_fact = _sample_facts()[0]

        assert text_fact["has_default"] is False
        assert "default" not in text_fact

    def test_signature_facts_keeps_defaults_typed(self) -> None:
        _, count_fact, flag_fact = _sample_facts()

        assert count_fact["has_default"] is True
        assert count_fact["default"] == 3
        assert flag_fact["has_default"] is True
        assert flag_fact["default"] is False

    # --- Basics: rebuild_signature is the inverse ---------------------

    def test_rebuild_signature_restores_names_kinds_and_defaults(self) -> None:
        signature = rebuild_signature(_sample_facts())
        parameters = signature.parameters

        assert list(parameters) == ["text", "count", "flag"]
        assert parameters["text"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert parameters["count"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert parameters["flag"].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters["text"].default is inspect.Parameter.empty
        assert parameters["count"].default == 3
        assert parameters["flag"].default is False

    def test_rebuild_signature_maps_builtin_annotations_to_type_objects(self) -> None:
        parameters = rebuild_signature(_sample_facts()).parameters

        assert parameters["text"].annotation is str
        assert parameters["count"].annotation is int
        assert parameters["flag"].annotation is bool

    def test_rebuild_signature_keeps_unknown_annotations_as_strings(self) -> None:
        signature = rebuild_signature(
            [
                {
                    "name": "thing",
                    "kind": "POSITIONAL_OR_KEYWORD",
                    "annotation": "MyThing",
                    "has_default": False,
                }
            ]
        )

        assert signature.parameters["thing"].annotation == "MyThing"

    def test_rebuild_signature_maps_empty_annotation_to_parameter_empty(self) -> None:
        signature = rebuild_signature(
            [
                {
                    "name": "raw",
                    "kind": "POSITIONAL_OR_KEYWORD",
                    "annotation": "",
                    "has_default": False,
                }
            ]
        )

        assert signature.parameters["raw"].annotation is inspect.Parameter.empty
