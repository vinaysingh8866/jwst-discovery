"""Drive ``python -m jwst_pipeline`` as the Rust host does: NDJSON over stdin/stdout.

stdout must carry nothing but one JSON object per line; logging goes to stderr.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SIDECAR_DIR = Path(__file__).resolve().parents[1]


def _run_sidecar(stdin_text: str, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "jwst_pipeline", *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=SIDECAR_DIR,
        timeout=timeout,
    )


def _responses(stdout: str) -> list[dict]:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError as exc:  # pragma: no cover - the assertion message is the point
            pytest.fail(f"non-JSON line on stdout: {ln!r} ({exc})")
    return out


def test_status_list_fields_shutdown():
    requests = [
        {"id": "1", "command": {"type": "Status"}},
        {"id": "2", "command": {"type": "ListKnownFields"}},
        {"id": "3", "command": {"type": "Shutdown"}},
    ]
    proc = _run_sidecar("".join(json.dumps(r) + "\n" for r in requests))
    assert proc.returncode == 0, proc.stderr[-2000:]

    responses = _responses(proc.stdout)
    assert [r["id"] for r in responses] == ["1", "2", "3"]
    for r in responses:
        assert set(r) == {"id", "payload"} and "type" in r["payload"]

    assert responses[0]["payload"] == {"type": "Ack", "command": "Status"}

    fields = responses[1]["payload"]
    assert fields["type"] == "KnownFieldsList"
    assert isinstance(fields["fields"], list) and fields["fields"]

    assert responses[2]["payload"] == {"type": "Ack", "command": "Shutdown"}


def test_bad_json_and_unknown_command_produce_error_payloads():
    stdin = "\n".join([
        "this is not json",
        json.dumps({"id": "9", "command": {"type": "NoSuchCommand"}}),
        json.dumps({"id": "10", "command": {"type": "Shutdown"}}),
    ]) + "\n"
    proc = _run_sidecar(stdin)
    assert proc.returncode == 0
    responses = _responses(proc.stdout)
    assert len(responses) == 3
    assert responses[0]["id"] == "unknown" and responses[0]["payload"]["type"] == "Error"
    assert responses[1]["id"] == "9" and responses[1]["payload"]["type"] == "Error"
    assert "NoSuchCommand" in responses[1]["payload"]["message"]


def test_stdin_eof_exits_cleanly():
    proc = _run_sidecar("")
    assert proc.returncode == 0
    assert proc.stdout == ""


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_help_and_version_do_not_enter_the_loop(flag):
    # No stdin content; if the flag fell through to the loop the process would still exit at EOF,
    # so the real assertion is on the output text.
    proc = _run_sidecar("", flag, timeout=60)
    assert proc.returncode == 0
    if flag == "--help":
        assert "NDJSON" in proc.stdout and "ListKnownFields" in proc.stdout and "Shutdown" in proc.stdout
    else:
        assert proc.stdout.strip().startswith("jwst-pipeline ")
