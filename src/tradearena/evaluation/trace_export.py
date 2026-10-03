from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_RAW_PROVIDER_FIELDS = ("prompt", "messages", "response_text", "raw_response", "rationale")
_REDACTION_FIELD_CATEGORIES = (
    "prompt_payloads",
    "message_payloads",
    "provider_outputs",
    "rationales",
)


def export_trajectory_to_trace_json(
    trajectory_path: str | Path,
    output_path: str | Path,
    *,
    case_name: str = "",
) -> dict[str, Any]:
    """Export a TreLLM trajectory to an OpenTelemetry-style local trace JSON.

    The exporter reads an existing artifact and never reruns an experiment. It
    intentionally emits counts, hashes, scores, and structured risk/execution
    fields rather than raw provider prompts, responses, rationales, or account
    data.
    """

    payload = _load_trajectory_payload(Path(trajectory_path), case_name)
    trace = trajectory_to_trace(payload, source_path=Path(trajectory_path))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return trace


def trajectory_to_trace(trajectory: dict[str, Any], *, source_path: str | Path = "") -> dict[str, Any]:
    experiment = str(trajectory.get("experiment_name", "tradearena"))
    seed = trajectory.get("seed", "")
    steps = trajectory.get("steps", [])
    if not isinstance(steps, list):
        steps = []
    trace_id = _trace_id(experiment, seed, len(steps))
    root_id = _span_id(trace_id, "root")
    spans = [
        _span(
            trace_id=trace_id,
            span_id=root_id,
            parent_span_id="",
            name="tradearena.run",
            timestamp=_first_timestamp(steps),
            attributes={
                "tradearena.experiment": experiment,
                "tradearena.seed": seed,
                "tradearena.step_count": len(steps),
                "tradearena.schema_version": trajectory.get("schema_version", ""),
                "tradearena.source_path": Path(source_path).as_posix() if source_path else "",
            },
        )
    ]
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        step_id = _span_id(trace_id, f"step:{index}")
        timestamp = str(step.get("timestamp", ""))
        spans.append(
            _span(
                trace_id=trace_id,
                span_id=step_id,
                parent_span_id=root_id,
                name="tradearena.step",
                timestamp=timestamp,
                attributes={
                    "tradearena.step": index,
                    "tradearena.timestamp": timestamp,
                    "tradearena.memory_event_count": _list_len(step.get("memory_events")),
                },
            )
        )
        spans.extend(_step_child_spans(trace_id, step_id, index, step, timestamp))
    return {
        "schema": "tradearena_opentelemetry_trace_v0.1",
        "redaction": {
            "prompt_payloads_exported": False,
            "provider_outputs_exported": False,
            "rationale_payloads_exported": False,
            "raw_provider_text_policy": "excluded_by_default",
            "excluded_field_categories": list(_REDACTION_FIELD_CATEGORIES),
        },
        "resource": {
            "service.name": "tradearena",
            "telemetry.sdk.language": "python",
            "tradearena.exporter": "opentelemetry-json-local",
        },
        "trace_id": trace_id,
        "spans": spans,
    }


def _step_child_spans(
    trace_id: str,
    step_id: str,
    index: int,
    step: dict[str, Any],
    timestamp: str,
) -> list[dict[str, Any]]:
    observation = step.get("observation", {}) if isinstance(step.get("observation"), dict) else {}
    risk = step.get("risk_report", {}) if isinstance(step.get("risk_report"), dict) else {}
    execution = step.get("execution_report", {}) if isinstance(step.get("execution_report"), dict) else {}
    portfolio = step.get("portfolio", {}) if isinstance(step.get("portfolio"), dict) else {}
    reproducibility = (
        step.get("reproducibility_state", {})
        if isinstance(step.get("reproducibility_state"), dict)
        else {}
    )
    return [
        _span(
            trace_id=trace_id,
            span_id=_span_id(trace_id, f"{index}:observe"),
            parent_span_id=step_id,
            name="market.observe",
            timestamp=timestamp,
            attributes={
                "market.symbol_count": _dict_len(observation.get("prices")),
                "market.news_count": observation.get("news_count", 0),
                "market.macro_count": observation.get("macro_count", 0),
                "market.filing_count": observation.get("filings_count", 0),
                "market.alt_data_count": observation.get("alt_data_count", 0),
            },
        ),
        _span(
            trace_id=trace_id,
            span_id=_span_id(trace_id, f"{index}:analyze"),
            parent_span_id=step_id,
            name="agent.analyze",
            timestamp=timestamp,
            attributes={
                "agent.signal_count": _list_len(step.get("signals")),
                "agent.prompt_version": reproducibility.get("prompt_version", ""),
                "agent.model_version_hash": _short_hash(reproducibility.get("model_version", "")),
            },
        ),
        _span(
            trace_id=trace_id,
            span_id=_span_id(trace_id, f"{index}:decide"),
            parent_span_id=step_id,
            name="agent.decide",
            timestamp=timestamp,
            attributes={
                "agent.raw_decision_count": _list_len(step.get("decisions")),
                "agent.approved_decision_count": _list_len(step.get("approved_decisions")),
                "agent.memory_digest_hash": _short_hash(reproducibility.get("memory_digest", "")),
            },
        ),
        _span(
            trace_id=trace_id,
            span_id=_span_id(trace_id, f"{index}:risk"),
            parent_span_id=step_id,
            name="risk.approve",
            timestamp=timestamp,
            attributes={
                "risk.phase": risk.get("phase", ""),
                "risk.approved_count": risk.get("approved_count", 0),
                "risk.blocked_count": risk.get("blocked_count", 0),
                "risk.clipped_count": risk.get("clipped_count", 0),
                "risk.check_count": _list_len(risk.get("checks")),
                "risk.violation_count": _list_len(step.get("risk_violations")),
            },
        ),
        _span(
            trace_id=trace_id,
            span_id=_span_id(trace_id, f"{index}:execution"),
            parent_span_id=step_id,
            name="execution.simulate",
            timestamp=timestamp,
            attributes={
                "execution.order_count": _list_len(step.get("orders")),
                "execution.fill_count": _list_len(step.get("fills")),
                "execution.submitted_orders": execution.get("submitted_orders", 0),
                "execution.filled_orders": execution.get("filled_orders", 0),
                "execution.partial_fills": execution.get("partial_fills", 0),
                "execution.pending_orders": execution.get("pending_orders", 0),
                "execution.rejected_orders": execution.get("rejected_orders", 0),
                "execution.total_commission": execution.get("total_commission", 0.0),
                "execution.total_slippage": execution.get("total_slippage", 0.0),
                "execution.average_latency_steps": execution.get("average_latency_steps", 0.0),
                "portfolio.equity": portfolio.get("equity", 0.0),
            },
        ),
    ]


def _span(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: str,
    name: str,
    timestamp: str,
    attributes: dict[str, Any],
) -> dict[str, Any]:
    start = _to_unix_nano(timestamp)
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "start_time_unix_nano": start,
        "end_time_unix_nano": start,
        "attributes": _redacted_attributes(attributes),
    }


def _redacted_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    clean = {}
    for key, value in attributes.items():
        lowered = key.lower()
        if any(token in lowered for token in _RAW_PROVIDER_FIELDS):
            clean[f"{key}_hash"] = _short_hash(value)
        else:
            clean[key] = value
    return clean


def _load_trajectory_payload(path: Path, case_name: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("steps"), list):
        return payload
    if not isinstance(payload, dict):
        raise ValueError(f"Unsupported trajectory payload: {path}")
    cases = {
        name: value
        for name, value in payload.items()
        if isinstance(value, dict) and isinstance(value.get("trajectory"), dict)
    }
    if not cases:
        raise ValueError(f"Unsupported trajectory payload: {path}")
    if case_name:
        if case_name not in cases:
            raise ValueError(f"Case not found: {case_name}")
        return cases[case_name]["trajectory"]
    if len(cases) == 1:
        return next(iter(cases.values()))["trajectory"]
    raise ValueError(f"Multiple cases found; pass case_name. Available: {', '.join(sorted(cases))}")


def _trace_id(experiment: str, seed: Any, step_count: int) -> str:
    return hashlib.sha256(f"{experiment}:{seed}:{step_count}".encode()).hexdigest()[:32]


def _span_id(trace_id: str, label: str) -> str:
    return hashlib.sha256(f"{trace_id}:{label}".encode()).hexdigest()[:16]


def _short_hash(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _first_timestamp(steps: list[Any]) -> str:
    for step in steps:
        if isinstance(step, dict) and step.get("timestamp"):
            return str(step["timestamp"])
    return datetime.now(timezone.utc).isoformat()


def _to_unix_nano(value: str) -> int:
    text = str(value or "")
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        timestamp = datetime.fromisoformat(text)
    except ValueError:
        timestamp = datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return int(timestamp.timestamp() * 1_000_000_000)


def _list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _dict_len(value: Any) -> int:
    return len(value) if isinstance(value, dict) else 0


__all__ = ["export_trajectory_to_trace_json", "trajectory_to_trace"]
