"""Protocol message helpers matching the Rust SidecarRequest / SidecarResponse types."""
import json


def parse_request(line: str) -> dict:
    data = json.loads(line)
    if "id" not in data or "command" not in data:
        raise ValueError("Invalid request: missing id or command")
    return data


def format_response(request_id: str, payload: dict) -> dict:
    return {"id": request_id or "unknown", "payload": payload}


def format_progress(request_id: str, stage: str, fraction: float, message: str) -> dict:
    return format_response(request_id, {
        "type": "Progress",
        "stage": stage,
        "fraction": fraction,
        "message": message,
    })


def format_stage_complete(request_id: str, stage: str, result: dict, duration: float) -> dict:
    return format_response(request_id, {
        "type": "StageComplete",
        "stage": stage,
        "result": result,
        "duration_secs": duration,
    })


def format_log(request_id: str, level: str, message: str) -> dict:
    return format_response(request_id, {
        "type": "Log",
        "level": level,
        "message": message,
    })


def format_filter_progress(request_id: str, filter_name: str, status: str,
                           bytes_done: int = 0, bytes_total: int = 0) -> dict:
    return format_response(request_id, {
        "type": "FilterProgress",
        "filter": filter_name,
        "status": status,
        "bytes_done": bytes_done,
        "bytes_total": bytes_total,
    })


def format_error(request_id: str | None, message: str, stage: str | None = None, tb: str | None = None) -> dict:
    return format_response(request_id or "unknown", {
        "type": "Error",
        "stage": stage,
        "message": message,
        "traceback": tb,
    })
