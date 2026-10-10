"""NDJSON protocol helpers: the contract between the Rust host and the sidecar."""
import json

import pytest

from jwst_pipeline import protocol


def test_parse_request_requires_id_and_command():
    assert protocol.parse_request('{"id": "1", "command": "Status"}') == {"id": "1", "command": "Status"}
    with pytest.raises(ValueError):
        protocol.parse_request('{"command": "Status"}')


@pytest.mark.parametrize(
    "fn, args, type_name",
    [
        (protocol.format_progress, ("r1", "detect", 0.5, "half"), "Progress"),
        (protocol.format_stage_complete, ("r1", "detect", {"n": 3}, 1.5), "StageComplete"),
        (protocol.format_log, ("r1", "info", "hello"), "Log"),
        (protocol.format_filter_progress, ("r1", "F200W", "downloading", 1, 2), "FilterProgress"),
        (protocol.format_error, ("r1", "boom", "detect", "tb"), "Error"),
    ],
)
def test_messages_are_json_serialisable_and_typed(fn, args, type_name):
    msg = fn(*args)
    assert msg["id"] == "r1"
    assert msg["payload"]["type"] == type_name
    assert json.loads(json.dumps(msg)) == msg


def test_error_without_request_id_uses_unknown():
    assert protocol.format_error(None, "x")["id"] == "unknown"
