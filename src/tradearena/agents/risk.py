from __future__ import annotations

from dataclasses import dataclass, field, replace

from tradearena.core.domain import (
    Decision,
    Fill,
    MarketSnapshot,
    Order,
    PortfolioState,
    RiskAttribution,
    RiskBudget,
    RiskCheck,
    RiskPhase,
    RiskReport,
    RiskViolation,
    Side,
)


@dataclass
class MaxPositionRiskManager:
    max_abs_weight: float = 0.35
    min_confidence: float = 0.05
    max_gross_exposure: float = 1.0
    max_single_step_turnover: float = 0.75
    max_order_participation: float = 0.05
    max_latency_steps: int = 2
    max_slippage_bps: float = 50.0
    max_drawdown: float = 0.20
    drawdown_lookback: int = 5
    drawdown_de_risk_weight: float = 0.0
    name: str = "max-position-risk"
    last_report: RiskReport | None = field(default=None, init=False)

    def budget(self) -> RiskBudget:
        return RiskBudget(
            max_position_weight=self.max_abs_weight,
            max_gross_exposure=self.max_gross_exposure,
            max_single_step_turnover=self.max_single_step_turnover,
            max_drawdown=self.max_drawdown,
            max_order_participation=self.max_order_participation,
            min_confidence=self.min_confidence,
            max_latency_steps=self.max_latency_steps,
            max_slippage_bps=self.max_slippage_bps,
            metadata={
                "drawdown_lookback": self.drawdown_lookback,
                "drawdown_de_risk_weight": self.drawdown_de_risk_weight,
            },
        )

    def approve(self, snapshot: MarketSnapshot, decisions: list[Decision], portfolio: PortfolioState, memory: object) -> list[Decision]:
        approved = []
        checks: list[RiskCheck] = []
        violations: list[RiskViolation] = []
        blocked_count = 0
        clipped_count = 0
        for decision in decisions:
            target = max(-self.max_abs_weight, min(self.max_abs_weight, decision.target_weight))
            side = decision.side
            rationale = decision.rationale
            metadata = dict(decision.metadata)

            if decision.confidence < self.min_confidence:
                target = 0.0
                side = Side.HOLD
                rationale = f"blocked by confidence floor: {rationale}"
                metadata["risk_blocked"] = "low_confidence"
                blocked_count += 1
                checks.append(
                    RiskCheck(
                        name="min_confidence",
                        passed=False,
                        severity="error",
                        message=f"{decision.symbol} confidence {decision.confidence:.3f} below {self.min_confidence:.3f}",
                    )
                )
                violations.append(
                    RiskViolation(
                        phase=RiskPhase.PRE_TRADE,
                        constraint="min_confidence",
                        severity="error",
                        observed=decision.confidence,
                        limit=self.min_confidence,
                        message=f"{decision.symbol} blocked by confidence floor",
                        symbol=decision.symbol,
                    )
                )
            elif target != decision.target_weight:
                metadata["risk_clipped_from"] = decision.target_weight
                clipped_count += 1
                checks.append(
                    RiskCheck(
                        name="max_abs_weight",
                        passed=True,
                        severity="warning",
                        message=f"{decision.symbol} target clipped from {decision.target_weight:.3f} to {target:.3f}",
                    )
                )

            approved.append(
                Decision(
                    symbol=decision.symbol,
                    side=side,
                    target_weight=target,
                    confidence=decision.confidence,
                    rationale=rationale,
                    metadata=metadata,
                )
            )

        drawdown = self._rolling_drawdown(portfolio, memory)
        gross = sum(abs(decision.target_weight) for decision in approved)
        if gross > self.max_gross_exposure:
            scale = self.max_gross_exposure / gross
            clipped_count += len(approved)
            approved = [
                Decision(
                    symbol=decision.symbol,
                    side=decision.side if abs(decision.target_weight * scale) > 1e-12 else Side.HOLD,
                    target_weight=decision.target_weight * scale,
                    confidence=decision.confidence,
                    rationale=decision.rationale,
                    metadata={**decision.metadata, "risk_scaled_by": scale},
                )
                for decision in approved
            ]
            checks.append(
                RiskCheck(
                    name="max_gross_exposure",
                    passed=True,
                    severity="warning",
                    message=f"gross exposure {gross:.3f} scaled to {self.max_gross_exposure:.3f}",
                )
            )

        if drawdown < -abs(self.max_drawdown):
            cap = max(0.0, min(self.max_abs_weight, self.drawdown_de_risk_weight))
            derisked = []
            derisked_count = 0
            for decision in approved:
                target = max(-cap, min(cap, decision.target_weight))
                if target != decision.target_weight:
                    derisked_count += 1
                derisked.append(
                    Decision(
                        symbol=decision.symbol,
                        side=decision.side if abs(target) > 1e-12 else Side.HOLD,
                        target_weight=target,
                        confidence=decision.confidence,
                        rationale=f"drawdown kill switch forced de-risk: {decision.rationale}",
                        metadata={
                            **decision.metadata,
                            "drawdown_kill_switch": True,
                            "risk_clipped_from": decision.target_weight,
                            "rolling_drawdown": drawdown,
                            "drawdown_limit": -abs(self.max_drawdown),
                        },
                    )
                )
            approved = derisked
            clipped_count += derisked_count
            checks.append(
                RiskCheck(
                    name="drawdown_kill_switch",
                    passed=False,
                    severity="error",
                    message=(
                        f"rolling drawdown {drawdown:.3f} breached kill-switch limit "
                        f"{-abs(self.max_drawdown):.3f}; forced target weights to +/-{cap:.3f}"
                    ),
                    metadata={
                        "rolling_drawdown": drawdown,
                        "limit": -abs(self.max_drawdown),
                        "lookback": self.drawdown_lookback,
                        "de_risk_weight": cap,
                    },
                )
            )
            violations.append(
                RiskViolation(
                    phase=RiskPhase.PRE_TRADE,
                    constraint="drawdown_kill_switch",
                    severity="error",
                    observed=drawdown,
                    limit=-abs(self.max_drawdown),
                    message="rolling drawdown breached kill-switch limit; forced de-risk",
                    metadata={"lookback": self.drawdown_lookback, "de_risk_weight": cap},
                )
            )

        projected_turnover = self._projected_turnover(approved, portfolio)
        if projected_turnover > self.max_single_step_turnover:
            checks.append(
                RiskCheck(
                    name="max_single_step_turnover",
                    passed=False,
                    severity="warning",
                    message=f"projected turnover {projected_turnover:.3f} exceeds {self.max_single_step_turnover:.3f}",
                    metadata={"projected_turnover": projected_turnover},
                )
            )
            violations.append(
                RiskViolation(
                    phase=RiskPhase.PRE_TRADE,
                    constraint="max_single_step_turnover",
                    severity="warning",
                    observed=projected_turnover,
                    limit=self.max_single_step_turnover,
                    message="projected turnover exceeds configured risk budget",
                )
            )

        if not checks:
            checks.append(RiskCheck(name="all_constraints", passed=True, severity="info", message="all risk constraints passed"))

        self.last_report = RiskReport(
            timestamp=snapshot.timestamp,
            checks=tuple(checks),
            approved_count=len(approved),
            blocked_count=blocked_count,
            clipped_count=clipped_count,
            phase=RiskPhase.PRE_TRADE,
            budget=self.budget(),
            violations=tuple(violations),
        )
        return approved

    def _projected_turnover(self, decisions: list[Decision], portfolio: PortfolioState) -> float:
        return sum(abs(decision.target_weight - portfolio.weight(decision.symbol)) for decision in decisions)

    def _rolling_drawdown(self, portfolio: PortfolioState, memory: object) -> float:
        equities = self._recent_equities(memory)
        equities.append(portfolio.equity())
        if len(equities) < 2:
            return 0.0
        peak = max(equities)
        if peak <= 0.0:
            return 0.0
        return equities[-1] / peak - 1.0

    def _recent_equities(self, memory: object) -> list[float]:
        events = getattr(memory, "events", []) if memory is not None else []
        lookback = max(0, int(self.drawdown_lookback))
        relevant_events = events[-lookback:] if lookback else events
        equities: list[float] = []
        for event in relevant_events:
            payload = event.get("payload", event) if isinstance(event, dict) else getattr(event, "payload", {})
            if not isinstance(payload, dict):
                continue
            equity = payload.get("equity")
            if isinstance(equity, (int, float)):
                equities.append(float(equity))
        return equities

    def monitor(
        self,
        snapshot: MarketSnapshot,
        orders: list[Order],
        fills: list[Fill],
        portfolio: PortfolioState,
        memory: object,
    ) -> RiskReport:
        checks: list[RiskCheck] = []
        violations: list[RiskViolation] = []
        for fill in fills:
            bar = snapshot.bars.get(fill.symbol)
            if bar is None:
                continue
            participation = fill.quantity / max(1.0, bar.volume)
            slippage_bps = abs(fill.slippage) / max(1e-9, snapshot.price(fill.symbol)) * 10_000
            if participation > self.max_order_participation:
                violations.append(
                    RiskViolation(
                        phase=RiskPhase.IN_TRADE,
                        constraint="max_order_participation",
                        severity="warning",
                        observed=participation,
                        limit=self.max_order_participation,
                        message="fill exceeded participation risk budget",
                        symbol=fill.symbol,
                    )
                )
            if fill.latency_steps > self.max_latency_steps:
                violations.append(
                    RiskViolation(
                        phase=RiskPhase.IN_TRADE,
                        constraint="max_latency_steps",
                        severity="warning",
                        observed=fill.latency_steps,
                        limit=self.max_latency_steps,
                        message="execution latency exceeded risk budget",
                        symbol=fill.symbol,
                    )
                )
            if slippage_bps > self.max_slippage_bps:
                violations.append(
                    RiskViolation(
                        phase=RiskPhase.IN_TRADE,
                        constraint="max_slippage_bps",
                        severity="warning",
                        observed=slippage_bps,
                        limit=self.max_slippage_bps,
                        message="slippage exceeded risk budget",
                        symbol=fill.symbol,
                    )
                )

        if violations:
            checks.extend(
                RiskCheck(
                    name=violation.constraint,
                    passed=False,
                    severity=violation.severity,
                    message=violation.message,
                    metadata={"observed": violation.observed, "limit": violation.limit, "symbol": violation.symbol},
                )
                for violation in violations
            )
        else:
            checks.append(RiskCheck(name="in_trade_monitor", passed=True, severity="info", message="execution within risk budget"))

        report = RiskReport(
            timestamp=snapshot.timestamp,
            checks=tuple(checks),
            approved_count=len(orders),
            blocked_count=0,
            clipped_count=0,
            phase=RiskPhase.IN_TRADE,
            budget=self.budget(),
            violations=tuple(violations),
        )
        self.last_report = report
        return report

    def attribute(
        self,
        snapshot: MarketSnapshot,
        fills: list[Fill],
        portfolio: PortfolioState,
        memory: object,
    ) -> RiskAttribution:
        exposure = {symbol: portfolio.weight(symbol) for symbol in snapshot.bars}
        return RiskAttribution(
            timestamp=snapshot.timestamp,
            realized_pnl=portfolio.realized_pnl,
            commission=sum(fill.commission for fill in fills),
            slippage_cost=sum(abs(fill.slippage) * fill.quantity for fill in fills),
            exposure=exposure,
            notes=("post-trade attribution computed from simulated fills",),
        )


@dataclass
class MaxDrawdownRiskPreset(MaxPositionRiskManager):
    """Small reusable preset for drawdown kill-switch experiments."""

    max_abs_weight: float = 1.0
    min_confidence: float = 0.0
    max_gross_exposure: float = 1.0
    max_single_step_turnover: float = 2.0
    max_drawdown: float = 0.10
    drawdown_lookback: int = 5
    drawdown_de_risk_weight: float = 0.0
    name: str = "max-drawdown-risk"

    def budget(self) -> RiskBudget:
        budget = super().budget()
        return replace(
            budget,
            metadata={
                **budget.metadata,
                "preset": "max_drawdown",
                "risk_manager": self.name,
            },
        )

    def approve(self, snapshot: MarketSnapshot, decisions: list[Decision], portfolio: PortfolioState, memory: object) -> list[Decision]:
        approved = super().approve(snapshot, decisions, portfolio, memory)
        if self.last_report is None:
            return approved
        if not any(violation.constraint == "drawdown_kill_switch" for violation in self.last_report.violations):
            return approved

        blocked_count = 0
        annotated: list[Decision] = []
        for decision in approved:
            metadata = dict(decision.metadata)
            target_weight = 0.0 if abs(decision.target_weight) <= 1e-12 else decision.target_weight
            if metadata.get("drawdown_kill_switch") is True:
                if target_weight == 0.0:
                    metadata["risk_blocked"] = "max_drawdown"
                    blocked_count += 1
                else:
                    metadata["risk_clipped"] = "max_drawdown"
            annotated.append(
                Decision(
                    symbol=decision.symbol,
                    side=Side.HOLD if target_weight == 0.0 else decision.side,
                    target_weight=target_weight,
                    confidence=decision.confidence,
                    rationale=decision.rationale,
                    metadata=metadata,
                )
            )

        self.last_report = replace(
            self.last_report,
            blocked_count=self.last_report.blocked_count + blocked_count,
            clipped_count=max(0, self.last_report.clipped_count - blocked_count),
        )
        return annotated


def max_drawdown_risk_preset(
    *,
    max_drawdown: float = 0.10,
    de_risk_weight: float = 0.0,
    drawdown_lookback: int = 5,
    max_abs_weight: float = 1.0,
    max_gross_exposure: float = 1.0,
) -> MaxDrawdownRiskPreset:
    return MaxDrawdownRiskPreset(
        max_abs_weight=max_abs_weight,
        max_gross_exposure=max_gross_exposure,
        max_drawdown=abs(max_drawdown),
        drawdown_lookback=drawdown_lookback,
        drawdown_de_risk_weight=max(0.0, de_risk_weight),
    )


@dataclass
class NoRiskManager:
    name: str = "no-risk"
    last_report: RiskReport | None = field(default=None, init=False)

    def budget(self) -> RiskBudget:
        return RiskBudget(max_position_weight=1.0, max_gross_exposure=10.0, max_single_step_turnover=10.0)

    def approve(self, snapshot: MarketSnapshot, decisions: list[Decision], portfolio: PortfolioState, memory: object) -> list[Decision]:
        self.last_report = RiskReport(
            timestamp=snapshot.timestamp,
            checks=(RiskCheck(name="disabled", passed=True, severity="info", message="risk gate disabled"),),
            approved_count=len(decisions),
            blocked_count=0,
            clipped_count=0,
            phase=RiskPhase.PRE_TRADE,
            budget=self.budget(),
        )
        return decisions

    def monitor(self, snapshot: MarketSnapshot, orders: list[Order], fills: list[Fill], portfolio: PortfolioState, memory: object) -> RiskReport:
        return RiskReport(
            timestamp=snapshot.timestamp,
            checks=(RiskCheck(name="disabled", passed=True, severity="info", message="in-trade risk monitor disabled"),),
            approved_count=len(orders),
            blocked_count=0,
            clipped_count=0,
            phase=RiskPhase.IN_TRADE,
            budget=self.budget(),
        )

    def attribute(self, snapshot: MarketSnapshot, fills: list[Fill], portfolio: PortfolioState, memory: object) -> RiskAttribution:
        return RiskAttribution(
            timestamp=snapshot.timestamp,
            realized_pnl=portfolio.realized_pnl,
            commission=sum(fill.commission for fill in fills),
            slippage_cost=sum(abs(fill.slippage) * fill.quantity for fill in fills),
            exposure={symbol: portfolio.weight(symbol) for symbol in snapshot.bars},
            notes=("post-trade attribution computed with risk gate disabled",),
        )
