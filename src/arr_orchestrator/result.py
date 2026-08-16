from __future__ import annotations

import json
from typing import Any

from .exit_codes import ExitCode


RESULT_SCHEMA = "arr-orchestrator.cli-result.v1"


def success(command: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "command": command,
        "status": "ok",
        "exit_code": int(ExitCode.SUCCESS),
        "data": data,
    }


def failure(
    command: str,
    exit_code: ExitCode,
    *,
    code: str,
    category: str,
    message: str,
    reason: str,
    data: dict[str, Any] | None = None,
    status: str = "error",
) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "command": command,
        "status": status,
        "exit_code": int(exit_code),
        "data": data or {"remote_side_effects": False},
        "error": {
            "contract_version": "1.0.0",
            "artifact_kind": "error",
            "error_id": f"cli.{command}.{reason}",
            "code": code,
            "category": category,
            "severity": "blocker",
            "message": message,
            "retryable": False,
            "owner": "arrctl",
            "details": {"reason": reason},
            "redacted": True,
        },
    }


def cli_failure(
    command: str,
    exit_code: ExitCode,
    *,
    code: str,
    message: str,
) -> dict[str, Any]:
    """Return a CLI-layer error without misusing the domain error taxonomy."""
    return {
        "schema": RESULT_SCHEMA,
        "command": command,
        "status": "error",
        "exit_code": int(exit_code),
        "data": {"remote_side_effects": False},
        "cli_error": {
            "code": code,
            "message": message,
            "redacted": True,
        },
    }


def render(payload: dict[str, Any], json_output: bool) -> str:
    if json_output:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
    command = payload["command"]
    status = payload["status"]
    if status == "ok":
        version = payload.get("data", {}).get("version")
        return f"arrctl {version}" if command == "version" else f"arrctl {command}: {status}"
    error = payload.get("error") or payload["cli_error"]
    return f"arrctl {command}: {status} ({error['code']})"
