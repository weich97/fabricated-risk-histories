from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tradearena.evaluation.trace_export import _load_trajectory_payload

_REDACTION_FIELD_CATEGORIES = (
    "prompt_payloads",
    "message_payloads",
    "provider_outputs",
    "rationales",
)


def export_trajectory_to_trace_schema_json(
    trajectory_path: str | Path,
    output_path: str | Path,
    *,
    case_name: str = "",
) -> dict[str, Any]:
    """Export a TreLLM trajectory to an Evals/LangSmith-style local schema.

    The artifact is a compatibility mapping for adjacent agent tooling. It does
    not call OpenAI Evals, LangSmith, or any live service.
    """

    payload = _load_trajectory_payload(Path(trajectory_path), case_name)
    artifact = trajectory_to_eval_trace_schema(payload, source_path=Path(trajectory_path))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def trajectory_to_eval_trace_schema(trajectory: dict[str, Any], *, source_path: str | Path = "") -> dict[str, Any]:
    experiment = str(trajectory.get("experiment_name", "trellm"))
    seed = trajectory.get("seed", "")
    steps = trajectory.get("steps", [])
    if not isinstance(steps, list):
        steps = []
    run_id = _stable_id("run", experiment, seed, len(steps))
    return {
        "schema": "trellm_eval_trace_schema_v0.1",
        "compatibility": {
            "openai_evals": "style_mapping_only",
            "langsmith": "style_mapping_only",
            "limits": [
                "local_json_not_cloud_api_payload",
                "raw_provider_text_redacted_by_default",
                "risk_reports_mapped_to_guardrail_events",
                "simulated_fills_mapped_to_tool_result_events",
            ],
        },
        "redaction": {
            "prompt_payloads_exported": False,
            "provider_outputs_exported": False,
            "rationale_payloads_exported": False,
            "raw_provider_text_policy": "excluded_by_default",
            "excluded_field_categories": list(_REDACTION_FIELD_CATEGORIES),
        },
        "source": {
            "experiment_name": experiment,
            "seed": seed,
            "schema_version": trajectory.get("schema_version", ""),
            "source_path": Path(source_path).as_posix() if source_path else "",
        },
        "run_id": run_id,
        "records": [_record(run_id, index, step) for index, step in enumerate(steps, start=1) if isinstance(step, dict)],
    }


def _record(run_id: str, index: int, step: dict[str, Any]) -> dict[str, Any]:
    observation = step.get("observation", {}) if isinstance(step.get("observation"), dict) else {}
    risk = step.get("risk_report", {}) if isinstance(step.get("risk_report"), dict) else {}
    execution = step.get("execution_report", {}) if isinstance(step.get("execution_report"), dict) else {}
    return {
        "id": _stable_id(run_id, "record", index),
        "run_id": run_id,
        "step_index": index,
        "timestamp": str(step.get("timestamp", "")),
        "inputs": {
            "symbol_count": _dict_len(observation.get("prices")),
            "news_count": observation.get("news_count", 0),
            "macro_count": observation.get("macro_count", 0),
        },
        "outputs": {
            "approved_decision_count": _list_len(step.get("approved_decisions")),
            "order_count": _list_len(step.get("orders")),
            "fill_count": _list_len(step.get("fills")),
        },
        "events": [
            _model_candidate_event(step),
            _guardrail_event(risk, step),
            _tool_result_event(execution, step),
        ],
        "scores": {
            "risk_passed": bool(risk.get("passed", risk.get("blocked_count", 0) == 0)),
            "risk_violation_count": _list_len(step.get("risk_violations")),
            "fill_count": _list_len(step.get("fills")),
        },
    }


def _model_candidate_event(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "model_candidate",
        "name": "agent.decide",
        "payload": {
            "signal_count": _list_len(step.get("signals")),
            "raw_decision_count": _list_len(step.get("decisions")),
            "approved_decision_count": _list_len(step.get("approved_decisions")),
        },
    }


def _guardrail_event(risk: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    blocked_count = int(risk.get("blocked_count", 0) or 0)
    clipped_count = int(risk.get("clipped_count", 0) or 0)
    status = "blocked" if blocked_count else "clipped" if clipped_count else "passed"
    return {
        "type": "guardrail",
        "name": "risk.approve",
        "status": status,
        "payload": {
            "phase": risk.get("phase", ""),
            "approved_count": risk.get("approved_count", 0),
            "blocked_count": blocked_count,
            "clipped_count": clipped_count,
            "check_count": _list_len(risk.get("checks")),
            "violation_count": _list_len(step.get("risk_violations")),
        },
    }


def _tool_result_event(execution: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "name": "execution.simulate",
        "payload": {
            "order_count": _list_len(step.get("orders")),
            "fill_count": _list_len(step.get("fills")),
            "submitted_orders": execution.get("submitted_orders", 0),
            "filled_orders": execution.get("filled_orders", 0),
            "partial_fills": execution.get("partial_fills", 0),
            "pending_orders": execution.get("pending_orders", 0),
            "rejected_orders": execution.get("rejected_orders", 0),
            "total_commission": execution.get("total_commission", 0.0),
            "total_slippage": execution.get("total_slippage", 0.0),
        },
    }


def _stable_id(*parts: object) -> str:
    return hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]


def _list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _dict_len(value: Any) -> int:
    return len(value) if isinstance(value, dict) else 0


__all__ = ["export_trajectory_to_trace_schema_json", "trajectory_to_eval_trace_schema"]
