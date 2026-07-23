"""JSON-over-stdin bridge invoked only by the Pi extension."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from . import service
from .contracts import ContractError


def _payload() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("bridge input exceeds 1 MiB")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("bridge input must be one JSON object")
    return value


def _require_text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be non-empty text")
    return value


def dispatch(mode: str, payload: dict[str, Any]) -> dict[str, Any]:
    if mode == "prepare-replay":
        return service.prepare_replay(_require_text(payload, "source_run_id"))
    if mode == "prepare-continuation":
        return service.prepare_continuation(
            _require_text(payload, "source_run_id")
        )
    if mode == "bind":
        return service.bind_request(payload)
    if mode == "inspect":
        return service.inspect_inputs(_require_text(payload, "run_id"))
    if mode == "validate-design":
        response = payload.get("response")
        design = payload.get("design")
        if not isinstance(response, dict):
            raise ValueError("response must be an object")
        if design is not None and not isinstance(design, dict):
            raise ValueError("design must be an object or null")
        return service.validate_and_store_design(
            _require_text(payload, "run_id"),
            response,
            design,
        )
    if mode == "prepare":
        return service.prepare(
            _require_text(payload, "run_id"),
            payload.get("files"),
            payload.get("parent_attempt"),
            _require_text(payload, "change_reason"),
        )
    if mode == "execute":
        return service.execute(
            _require_text(payload, "run_id"),
            _require_text(payload, "attempt_id"),
        )
    if mode == "verify":
        assessment = payload.get("scientific_assessment")
        if assessment is not None and not isinstance(assessment, dict):
            raise ValueError("scientific_assessment must be an object or null")
        return service.verify(
            _require_text(payload, "run_id"),
            _require_text(payload, "attempt_id"),
            assessment,
        )
    if mode == "finalize":
        return service.finalize(_require_text(payload, "run_id"))
    if mode == "finalize-interrupted":
        outcome = payload.get("outcome")
        if outcome is not None and not isinstance(outcome, str):
            raise ValueError("outcome must be text or null")
        return service.finalize_interrupted(
            _require_text(payload, "run_id"),
            _require_text(payload, "reason"),
            outcome,
        )
    if mode == "status":
        run_id = payload.get("run_id")
        if run_id is not None and not isinstance(run_id, str):
            raise ValueError("run_id must be text or null")
        return service.status(run_id)
    if mode == "stop":
        run_id = payload.get("run_id")
        if run_id is not None and not isinstance(run_id, str):
            raise ValueError("run_id must be text or null")
        return service.stop(run_id)
    if mode == "doctor":
        return service.doctor()
    raise ValueError(f"unsupported bridge mode: {mode}")


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "mode",
        choices=[
            "bind",
            "prepare-replay",
            "prepare-continuation",
            "inspect",
            "validate-design",
            "prepare",
            "execute",
            "verify",
            "finalize",
            "finalize-interrupted",
            "status",
            "stop",
            "doctor",
        ],
    )
    args = parser.parse_args()
    try:
        result = dispatch(args.mode, _payload())
        output = {"ok": True, **result}
    except (ContractError, ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        output = {
            "ok": False,
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        if isinstance(exc, ContractError):
            field_path = exc.field_path
            if field_path is None:
                import re

                match = re.search(
                    r"(?:request|response|design|worker_result|scientific_assessment)"
                    r"(?:\.[A-Za-z0-9_]+|\[[0-9]+\])*",
                    str(exc),
                )
                field_path = match.group(0) if match else None
            output.update(
                {
                    "error_code": exc.error_code,
                    "field_path": field_path,
                    "suggestion": exc.suggestion
                    or "按字段路径和 bind/verification preview 返回的写作指南修正后重试。",
                }
            )
    sys.stdout.write(json.dumps(output, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
