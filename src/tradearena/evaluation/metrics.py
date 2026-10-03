from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from tradearena.core.domain import Side
from tradearena.core.trajectory import Trajectory
from tradearena.tools.risk import RiskCalculator


@dataclass
class PerformanceEvaluator:
    name: str = "performance-evaluator"
    risk: RiskCalculator = field(default_factory=RiskCalculator)

    def evaluate(self, trajectory: Trajectory) -> dict[str, float | int | str]:
        curve = [equity for _, equity in trajectory.equity_curve()]
        if not curve:
            return {"total_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "steps": 0}
        returns = self.risk.returns(curve)
        return {
            "total_return": (curve[-1] / curve[0]) - 1.0 if curve[0] else 0.0,
            "sharpe": self.risk.sharpe(returns),
            "volatility": self.risk.volatility(returns),
            "max_drawdown": self.risk.max_drawdown(curve),
            "steps": len(curve),
            "final_equity": curve[-1],
        }


@dataclass
class BehavioralEvaluator:
    name: str = "behavioral-evaluator"

    def evaluate(self, trajectory: Trajectory) -> dict[str, float | int | str]:
        order_count = sum(len(step.orders) for step in trajectory.steps)
        fill_count = sum(len(step.fills) for step in trajectory.steps)
        hold_decisions = 0
        decisions = 0
        memory_amplifications: list[float] = []
        memory_pollution_ratios: list[float] = []
        step_gross_targets: list[float] = []
        for step in trajectory.steps:
            step_gross = 0.0
            for decision in step.approved_decisions:
                decisions += 1
                if decision.get("side") == Side.HOLD.value:
                    hold_decisions += 1
                weight = _finite_float(decision.get("target_weight"))
                if weight is not None:
                    step_gross += abs(weight)
                metadata = decision.get("metadata", {})
                if not isinstance(metadata, dict):
                    continue
                amplification = _finite_float(metadata.get("memory_driven_leverage_amplification"))
                if amplification is not None:
                    memory_amplifications.append(amplification)
                pollution_ratio = _finite_float(metadata.get("memory_pollution_ratio"))
                if pollution_ratio is not None:
                    memory_pollution_ratios.append(pollution_ratio)
            if step.approved_decisions:
                step_gross_targets.append(step_gross)
        return {
            "order_count": order_count,
            "fill_count": fill_count,
            "turnover_events": fill_count,
            "hold_ratio": hold_decisions / decisions if decisions else 0.0,
            # Continuous behavioral axis: mean per-step gross approved target
            # exposure (sum of |target_weight| across symbols). Unlike the
            # deadband-thresholded hold ratio, this moves smoothly with the
            # agent's stance, so behavioral shifts are not artifacts of
            # decisions crossing the HOLD threshold.
            "mean_gross_target_exposure": (
                sum(step_gross_targets) / len(step_gross_targets) if step_gross_targets else 0.0
            ),
            "memory_decision_count": len(memory_amplifications),
            "memory_driven_leverage_amplification": sum(memory_amplifications) / len(memory_amplifications)
            if memory_amplifications
            else 0.0,
            "max_memory_driven_leverage_amplification": max(memory_amplifications) if memory_amplifications else 0.0,
            "memory_pollution_ratio": sum(memory_pollution_ratios) / len(memory_pollution_ratios)
            if memory_pollution_ratios
            else 0.0,
            "max_memory_pollution_ratio": max(memory_pollution_ratios) if memory_pollution_ratios else 0.0,
        }


@dataclass
class IntentExecutionGapEvaluator:
    """Distance between what the model wanted, what risk approved, and what executed.

    The audit trail preserves model intent (pre-risk decisions), risk-approved
    targets, and realized end-of-step portfolio weights. The per-step L1
    distances between those allocations quantify how much of the final
    portfolio came from the model versus the risk gate versus execution
    frictions - the gap most leaderboards cannot report because they only
    keep realized results.
    """

    name: str = "intent-execution-gap-evaluator"

    def evaluate(self, trajectory: Trajectory) -> dict[str, float | int | str]:
        intent_risk: list[float] = []
        risk_execution: list[float] = []
        intent_execution: list[float] = []
        for step in trajectory.steps:
            intent = _target_weights(step.decisions)
            approved = _target_weights(step.approved_decisions)
            if not intent and not approved:
                continue
            realized = _realized_weights(step.portfolio)
            intent_risk.append(_l1_distance(intent, approved))
            risk_execution.append(_l1_distance(approved, realized))
            intent_execution.append(_l1_distance(intent, realized))
        steps = len(intent_execution)
        return {
            "intent_gap_steps": steps,
            "intent_risk_gap_l1": sum(intent_risk) / steps if steps else 0.0,
            "risk_execution_gap_l1": sum(risk_execution) / steps if steps else 0.0,
            "intent_execution_gap_l1": sum(intent_execution) / steps if steps else 0.0,
            "max_intent_execution_gap_l1": max(intent_execution) if steps else 0.0,
        }


def _target_weights(decisions: list[dict]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for decision in decisions or []:
        symbol = str(decision.get("symbol", ""))
        weight = _finite_float(decision.get("target_weight"))
        if symbol and weight is not None:
            weights[symbol] = weights.get(symbol, 0.0) + weight
    return weights


def _realized_weights(portfolio: dict) -> dict[str, float]:
    if not isinstance(portfolio, dict):
        return {}
    equity = _finite_float(portfolio.get("equity"))
    if not equity:
        return {}
    prices = portfolio.get("last_prices", {}) or {}
    weights: dict[str, float] = {}
    for symbol, quantity in (portfolio.get("positions", {}) or {}).items():
        quantity_value = _finite_float(quantity)
        price = _finite_float(prices.get(symbol))
        if quantity_value is None or price is None:
            continue
        weights[str(symbol)] = quantity_value * price / equity
    return weights


def _l1_distance(left: dict[str, float], right: dict[str, float]) -> float:
    symbols = set(left) | set(right)
    return sum(abs(left.get(symbol, 0.0) - right.get(symbol, 0.0)) for symbol in symbols)


@dataclass
class ExecutionRealismEvaluator:
    name: str = "execution-realism-evaluator"

    def evaluate(self, trajectory: Trajectory) -> dict[str, float | int | str]:
        reports = [step.execution_report for step in trajectory.steps if step.execution_report]
        fills = [fill for step in trajectory.steps for fill in step.fills]
        submitted = sum(int(report.get("submitted_orders", 0)) for report in reports)
        filled = sum(int(report.get("filled_orders", 0)) for report in reports)
        partial = sum(int(report.get("partial_fills", 0)) for report in reports)
        rejected = sum(int(report.get("rejected_orders", 0)) for report in reports)
        pending = reports[-1].get("pending_orders", 0) if reports else 0
        commission = sum(float(report.get("total_commission", 0.0)) for report in reports)
        slippage_cost = sum(float(report.get("total_slippage", 0.0)) for report in reports)
        latency = [float(fill.get("latency_steps", 0.0)) for fill in fills]
        fill_ratios = [float(fill.get("fill_ratio", 1.0)) for fill in fills]
        return {
            "submitted_orders": submitted,
            "execution_fill_rate": filled / submitted if submitted else 0.0,
            "partial_fill_count": partial,
            "partial_fill_rate": partial / filled if filled else 0.0,
            "rejected_order_count": rejected,
            "pending_order_count": int(pending),
            "total_commission": commission,
            "total_slippage_cost": slippage_cost,
            "avg_latency_steps": sum(latency) / len(latency) if latency else 0.0,
            "avg_fill_ratio": sum(fill_ratios) / len(fill_ratios) if fill_ratios else 0.0,
        }


@dataclass
class DecisionQualityEvaluator:
    """Separate intended alpha, risk discipline, and execution robustness."""

    name: str = "decision-quality-evaluator"
    risk: RiskCalculator = field(default_factory=RiskCalculator)

    def evaluate(self, trajectory: Trajectory) -> dict[str, float | int | str]:
        intended_returns = self._intended_returns(trajectory)
        if intended_returns:
            alpha_curve = [1.0]
            for value in intended_returns:
                alpha_curve.append(alpha_curve[-1] * (1.0 + value))
            alpha_total_return = alpha_curve[-1] - 1.0
            alpha_sharpe = self.risk.sharpe(intended_returns)
            alpha_hit_rate = sum(1 for value in intended_returns if value > 0) / len(intended_returns)
        else:
            alpha_total_return = 0.0
            alpha_sharpe = 0.0
            alpha_hit_rate = 0.0

        proposed = sum(len(step.decisions) for step in trajectory.steps)
        blocked = sum(int(step.risk_report.get("blocked_count", 0) or 0) for step in trajectory.steps)
        clipped = sum(int(step.risk_report.get("clipped_count", 0) or 0) for step in trajectory.steps)
        violations = sum(len(step.risk_violations) for step in trajectory.steps)
        severe = sum(
            1
            for step in trajectory.steps
            for violation in step.risk_violations
            if violation.get("severity") == "error"
        )
        risk_penalty = blocked + 0.5 * clipped + violations + severe
        risk_discipline = 1.0 - min(1.0, risk_penalty / max(1, proposed))

        reports = [step.execution_report for step in trajectory.steps if step.execution_report]
        fills = [fill for step in trajectory.steps for fill in step.fills]
        submitted = sum(int(report.get("submitted_orders", 0) or 0) for report in reports)
        filled = sum(int(report.get("filled_orders", 0) or 0) for report in reports)
        partial = sum(int(report.get("partial_fills", 0) or 0) for report in reports)
        rejected = sum(int(report.get("rejected_orders", 0) or 0) for report in reports)
        pending = int(reports[-1].get("pending_orders", 0) or 0) if reports else 0
        commission = sum(float(report.get("total_commission", 0.0) or 0.0) for report in reports)
        slippage = sum(float(report.get("total_slippage", 0.0) or 0.0) for report in reports)
        fill_rate = filled / submitted if submitted else 1.0
        rejection_rate = rejected / submitted if submitted else 0.0
        partial_rate = partial / filled if filled else 0.0
        pending_rate = pending / submitted if submitted else 0.0
        fill_ratios = [value for value in (_finite_float(fill.get("fill_ratio", 1.0)) for fill in fills) if value is not None]
        avg_fill_ratio = sum(fill_ratios) / len(fill_ratios) if fill_ratios else 1.0
        latencies = [value for value in (_finite_float(fill.get("latency_steps", 0.0)) for fill in fills) if value is not None]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        first_equity = float(trajectory.steps[0].portfolio.get("equity", 0.0)) if trajectory.steps else 0.0
        cost_rate = (commission + slippage) / first_equity if first_equity else 0.0
        execution_robustness = _clamp01(
            0.40 * fill_rate
            + 0.20 * avg_fill_ratio
            + 0.15 * (1.0 - rejection_rate)
            + 0.10 * (1.0 - partial_rate)
            + 0.10 * (1.0 - pending_rate)
            + 0.05 * (1.0 - min(1.0, avg_latency / 5.0))
            - min(0.25, cost_rate * 10.0)
        )

        alpha_quality = _clamp01(
            0.45 * _clamp01(0.5 + alpha_total_return / 0.20)
            + 0.35 * _clamp01((alpha_sharpe + 2.0) / 6.0)
            + 0.20 * alpha_hit_rate
        )
        return {
            "alpha_pre_risk_total_return": alpha_total_return,
            "alpha_pre_risk_sharpe": alpha_sharpe,
            "alpha_pre_risk_hit_rate": alpha_hit_rate,
            "alpha_pre_risk_steps": len(intended_returns),
            "alpha_quality_score": alpha_quality,
            "risk_discipline_score": _clamp01(risk_discipline),
            "execution_robustness_score": execution_robustness,
        }

    def _intended_returns(self, trajectory: Trajectory) -> list[float]:
        returns: list[float] = []
        for current, next_step in zip(trajectory.steps, trajectory.steps[1:]):
            current_prices = current.observation.get("prices", {})
            next_prices = next_step.observation.get("prices", {})
            if not isinstance(current_prices, dict) or not isinstance(next_prices, dict):
                continue
            step_return = 0.0
            gross_intent = 0.0
            for decision in current.decisions:
                symbol = decision.get("symbol")
                target = _finite_float(decision.get("target_weight", 0.0))
                current_price = _finite_float(current_prices.get(symbol)) if symbol else None
                next_price = _finite_float(next_prices.get(symbol)) if symbol else None
                if target is None or current_price is None or next_price is None or current_price <= 0:
                    continue
                step_return += target * ((next_price / current_price) - 1.0)
                gross_intent += abs(target)
            if gross_intent > 0:
                returns.append(step_return)
        return returns


@dataclass
class RiskAuditEvaluator:
    name: str = "risk-audit-evaluator"

    def evaluate(self, trajectory: Trajectory) -> dict[str, float | int | str]:
        reports = [step.risk_report for step in trajectory.steps if step.risk_report]
        in_trade_reports = [step.in_trade_report for step in trajectory.steps if step.in_trade_report]
        post_trade_reports = [step.post_trade_report for step in trajectory.steps if step.post_trade_report]
        blocked = sum(int(report.get("blocked_count", 0)) for report in reports)
        clipped = sum(int(report.get("clipped_count", 0)) for report in reports)
        checks = 0
        failed = 0
        warnings = 0
        violation_count = sum(len(step.risk_violations) for step in trajectory.steps)
        severe_violations = 0
        for step in trajectory.steps:
            for violation in step.risk_violations:
                if violation.get("severity") == "error":
                    severe_violations += 1
        for report in reports + in_trade_reports + post_trade_reports:
            for check in report.get("checks", []):
                checks += 1
                if not check.get("passed", True):
                    failed += 1
                if check.get("severity") == "warning":
                    warnings += 1
        reproducibility_fields = (
            "prompt_version",
            "model_version",
            "market_data_timestamp",
            "memory_digest",
            "risk_constraints",
            "portfolio_state",
            "execution_simulator_state",
            "random_seed",
        )
        reproducible_steps = 0
        trace_complete_steps = 0
        for step in trajectory.steps:
            state = step.reproducibility_state
            if state and all(field in state and state[field] not in (None, "") for field in reproducibility_fields):
                reproducible_steps += 1
            trace = step.agent_trace
            if trace and all(key in trace for key in ("observe", "plan", "propose_order", "risk_report", "revise", "act", "reflect")):
                trace_complete_steps += 1
        return {
            "risk_reports": len(reports),
            "in_trade_reports": len(in_trade_reports),
            "post_trade_reports": len(post_trade_reports),
            "risk_blocked_decisions": blocked,
            "risk_clipped_decisions": clipped,
            "risk_check_count": checks,
            "risk_failed_checks": failed,
            "risk_warning_checks": warnings,
            "risk_violation_count": violation_count,
            "severe_risk_violation_count": severe_violations,
            "risk_audit_coverage": len(reports) / len(trajectory.steps) if trajectory.steps else 0.0,
            "risk_lifecycle_coverage": (len(reports) + len(in_trade_reports) + len(post_trade_reports)) / (3 * len(trajectory.steps)) if trajectory.steps else 0.0,
            "trajectory_reproducibility_coverage": reproducible_steps / len(trajectory.steps) if trajectory.steps else 0.0,
            "agent_trace_coverage": trace_complete_steps / len(trajectory.steps) if trajectory.steps else 0.0,
        }


@dataclass
class ReasoningConsistencyEvaluator:
    name: str = "reasoning-consistency-evaluator"

    def evaluate(self, trajectory: Trajectory) -> dict[str, float | int | str]:
        checked = 0
        consistent = 0
        for step in trajectory.steps:
            for decision in step.approved_decisions:
                side = decision.get("side")
                target = _finite_float(decision.get("target_weight", 0.0)) or 0.0
                checked += 1
                if side == Side.BUY.value and target > 0:
                    consistent += 1
                elif side == Side.SELL.value and target < 0:
                    consistent += 1
                elif side == Side.HOLD.value and abs(target) < 1e-12:
                    consistent += 1
        return {
            "reasoning_consistency": consistent / checked if checked else 1.0,
            "reasoning_checks": checked,
        }


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(parsed):
        return None
    return parsed


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))
