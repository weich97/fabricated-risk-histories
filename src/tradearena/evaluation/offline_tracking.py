from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradearena.evaluation.trace_export import _load_trajectory_payload


def export_trajectory_to_offline_tracking(
    trajectory_path: str | Path,
    output_dir: str | Path,
    *,
    case_name: str = "",
) -> dict[str, Any]:
    """Write a dependency-free MLflow-style local tracking directory.

    This exporter consumes an existing trajectory JSON and writes plain files.
    It does not import W&B, MLflow, or contact any tracking service.
    """

    source = Path(trajectory_path)
    payload = _load_trajectory_payload(source, case_name)
    export = trajectory_to_offline_tracking(payload, trajectory_path=source)
    root = Path(output_dir)
    artifacts_dir = root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (root / "meta.yaml").write_text(_render_meta_yaml(export), encoding="utf-8")
    (root / "metrics.json").write_text(json.dumps(export["metrics"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (artifacts_dir / "trajectory_manifest.json").write_text(
        json.dumps(export["trajectory_manifest"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (artifacts_dir / "redaction.json").write_text(
        json.dumps(export["redaction"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "export_summary.json").write_text(json.dumps(export, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return export


def trajectory_to_offline_tracking(trajectory: dict[str, Any], *, trajectory_path: str | Path = "") -> dict[str, Any]:
    steps = trajectory.get("steps", [])
    if not isinstance(steps, list):
        steps = []
    metrics = _metrics(steps)
    manifest = {
        "experiment_name": str(trajectory.get("experiment_name", "trellm")),
        "seed": trajectory.get("seed", ""),
        "schema_version": trajectory.get("schema_version", ""),
        "step_count": len(steps),
        "trajectory_hash": _hash_payload(trajectory),
    }
    source_path = Path(trajectory_path).as_posix() if trajectory_path else ""
    return {
        "schema": "trellm_offline_tracking_export_v0.1",
        "mode": "plain_file_mlflow_style",
        "dependencies_required": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "trajectory_path": source_path,
            "case_name_required": False,
        },
        "metrics": metrics,
        "trajectory_manifest": manifest,
        "redaction": {
            "raw_provider_text_policy": "excluded_by_default",
            "prompt_payloads_exported": False,
            "provider_outputs_exported": False,
            "rationale_payloads_exported": False,
        },
    }


def _metrics(steps: list[Any]) -> dict[str, int | float]:
    final_equity = 0.0
    fill_count = 0
    rejected_orders = 0
    pending_orders = 0
    partial_fills = 0
    total_commission = 0.0
    total_slippage = 0.0
    risk_blocked = 0
    risk_clipped = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        fills = step.get("fills")
        fill_count += len(fills) if isinstance(fills, list) else 0
        execution = step.get("execution_report", {}) if isinstance(step.get("execution_report"), dict) else {}
        rejected_orders += int(execution.get("rejected_orders", 0) or 0)
        pending_orders += int(execution.get("pending_orders", 0) or 0)
        partial_fills += int(execution.get("partial_fills", 0) or 0)
        total_commission += float(execution.get("total_commission", 0.0) or 0.0)
        total_slippage += float(execution.get("total_slippage", 0.0) or 0.0)
        risk = step.get("risk_report", {}) if isinstance(step.get("risk_report"), dict) else {}
        risk_blocked += int(risk.get("blocked_count", 0) or 0)
        risk_clipped += int(risk.get("clipped_count", 0) or 0)
        portfolio = step.get("portfolio", {}) if isinstance(step.get("portfolio"), dict) else {}
        if "equity" in portfolio:
            final_equity = float(portfolio["equity"] or 0.0)
    return {
        "step_count": len([step for step in steps if isinstance(step, dict)]),
        "fill_count": fill_count,
        "rejected_order_count": rejected_orders,
        "pending_order_count": pending_orders,
        "partial_fill_count": partial_fills,
        "risk_blocked_count": risk_blocked,
        "risk_clipped_count": risk_clipped,
        "total_commission": total_commission,
        "total_slippage": total_slippage,
        "final_equity": final_equity,
    }


def _render_meta_yaml(export: dict[str, Any]) -> str:
    manifest = export["trajectory_manifest"]
    lines = [
        "artifact_type: trellm_offline_tracking_export",
        f"schema: {export['schema']}",
        f"mode: {export['mode']}",
        "tracking_backend: plain-file",
        f"experiment_name: {manifest['experiment_name']}",
        f"run_id: {manifest['trajectory_hash'][:24]}",
        "dependencies_required: []",
        f"source_trajectory: {export['source']['trajectory_path']}",
        "",
    ]
    return "\n".join(lines)


def _hash_payload(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["export_trajectory_to_offline_tracking", "trajectory_to_offline_tracking"]
