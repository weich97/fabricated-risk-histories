from __future__ import annotations

from collections import Counter
from typing import Any

FAILURE_MODES = (
    "overtrading",
    "pre_risk_leverage",
    "low_confidence_bet",
    "slippage_insensitive",
    "liquidity_insensitive",
    "memory_pollution",
    "rationale_decision_mismatch",
    "position_limit_noncompliance",
)


def autopsy_trajectory(trajectory: dict[str, Any]) -> dict[str, Any]:
    """Summarize failure modes from a serialized TreLLM trajectory."""

    steps = []
    counts: Counter[str] = Counter()
    for index, step in enumerate(trajectory.get("steps", []) or []):
        modes = classify_step_failure_modes(step)
        counts.update(modes)
        if modes:
            steps.append(
                {
                    "step": index + 1,
                    "timestamp": step.get("timestamp", ""),
                    "failure_modes": modes,
                    "evidence": _step_evidence(step),
                }
            )
    return {
        "schema": "tradearena_failure_autopsy_v1",
        "experiment_name": trajectory.get("experiment_name", ""),
        "step_count": len(trajectory.get("steps", []) or []),
        "failure_mode_counts": {mode: counts.get(mode, 0) for mode in FAILURE_MODES},
        "top_failure_modes": [
            {"mode": mode, "count": count}
            for mode, count in counts.most_common()
        ],
        "flagged_step_count": len(steps),
        "steps": steps,
    }


def classify_step_failure_modes(step: dict[str, Any]) -> list[str]:
    modes: list[str] = []
    decisions = list(step.get("decisions", []) or [])
    approved = list(step.get("approved_decisions", []) or [])
    risk = step.get("risk_report", {}) or {}
    execution = step.get("execution_report", {}) or {}

    intended_gross = _gross_weight(decisions)
    approved_gross = _gross_weight(approved)
    order_count = len(step.get("orders", []) or [])
    fill_count = int(execution.get("filled_orders", 0) or 0)
    rejected = int(execution.get("rejected_orders", 0) or 0)
    pending = int(execution.get("pending_orders", 0) or 0)
    partial = int(execution.get("partial_fills", 0) or 0)
    clipped = int(risk.get("clipped_count", 0) or 0)
    blocked = int(risk.get("blocked_count", 0) or 0)
    slippage = _float(execution.get("total_slippage"))

    if order_count >= 5 or intended_gross > 1.25:
        modes.append("overtrading")
    if intended_gross > max(1.0, approved_gross + 0.20) or clipped or blocked:
        modes.append("pre_risk_leverage")
    if any(abs(_float(decision.get("target_weight"))) >= 0.10 and _float(decision.get("confidence")) < 0.30 for decision in decisions):
        modes.append("low_confidence_bet")
    if slippage > 100.0 and not _mentions_any(decisions, ("slippage", "spread", "cost", "execution")):
        modes.append("slippage_insensitive")
    if (pending or rejected or partial) and not _mentions_any(decisions, ("liquidity", "volume", "partial", "fill")):
        modes.append("liquidity_insensitive")
    if _max_memory_pollution(decisions + approved) >= 0.25:
        modes.append("memory_pollution")
    if _has_rationale_decision_mismatch(decisions):
        modes.append("rationale_decision_mismatch")
    if _has_position_limit_violation(risk) or blocked or clipped:
        modes.append("position_limit_noncompliance")
    if fill_count == 0 and intended_gross > 0.5 and not rejected and not pending:
        modes.append("rationale_decision_mismatch")

    return [mode for mode in FAILURE_MODES if mode in set(modes)]


def _step_evidence(step: dict[str, Any]) -> dict[str, Any]:
    risk = step.get("risk_report", {}) or {}
    execution = step.get("execution_report", {}) or {}
    decisions = list(step.get("decisions", []) or [])
    return {
        "intended_gross": _gross_weight(decisions),
        "approved_gross": _gross_weight(step.get("approved_decisions", []) or []),
        "order_count": len(step.get("orders", []) or []),
        "max_decision_confidence": max((_float(decision.get("confidence")) for decision in decisions), default=0.0),
        "min_decision_confidence": min((_float(decision.get("confidence")) for decision in decisions), default=0.0),
        "risk_clipped": int(risk.get("clipped_count", 0) or 0),
        "risk_blocked": int(risk.get("blocked_count", 0) or 0),
        "pending_orders": int(execution.get("pending_orders", 0) or 0),
        "rejected_orders": int(execution.get("rejected_orders", 0) or 0),
        "partial_fills": int(execution.get("partial_fills", 0) or 0),
        "total_slippage": _float(execution.get("total_slippage")),
        "max_memory_pollution": _max_memory_pollution(decisions),
    }


def _gross_weight(decisions: list[dict[str, Any]]) -> float:
    return sum(abs(_float(decision.get("target_weight"))) for decision in decisions)


def _mentions_any(decisions: list[dict[str, Any]], terms: tuple[str, ...]) -> bool:
    text = " ".join(str(decision.get("rationale", "")) for decision in decisions).lower()
    return any(term in text for term in terms)


def _max_memory_pollution(decisions: list[dict[str, Any]]) -> float:
    values = []
    for decision in decisions:
        metadata = decision.get("metadata", {}) or {}
        if isinstance(metadata, dict):
            values.append(_float(metadata.get("memory_pollution_ratio")))
    return max(values, default=0.0)


def _has_rationale_decision_mismatch(decisions: list[dict[str, Any]]) -> bool:
    risk_off_terms = ("risk-off", "avoid", "reduce", "de-risk", "hold", "exit")
    bullish_terms = ("buy", "increase", "add", "bullish", "momentum")
    for decision in decisions:
        target = _float(decision.get("target_weight"))
        side = str(decision.get("side", "")).lower()
        rationale = str(decision.get("rationale", "")).lower()
        if target >= 0.20 and any(term in rationale for term in risk_off_terms):
            return True
        if side == "hold" and any(term in rationale for term in bullish_terms):
            return True
    return False


def _has_position_limit_violation(risk_report: dict[str, Any]) -> bool:
    raw = risk_report.get("violations", []) or []
    for violation in raw:
        if not isinstance(violation, dict):
            continue
        constraint = str(violation.get("constraint", "")).lower()
        if "position" in constraint or "gross" in constraint or "turnover" in constraint:
            return True
    return False


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
