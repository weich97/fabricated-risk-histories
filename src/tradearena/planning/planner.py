from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from tradearena.planning.domain import (
    AllocationTarget,
    AssetCandidate,
    FinancialGoal,
    FuturesMarginEstimate,
    Holding,
    InvestorProfile,
    PlanningOrder,
    PlanningReport,
    PlanningRiskBudget,
    SuitabilityCheck,
    SuitabilityReport,
)

SAFE_ASSET_CLASSES = ("cash_equivalent", "bond_etf", "treasury")
EQUITY_ASSET_CLASSES = ("equity_etf", "equity", "sector_etf")
FUTURES_ASSET_CLASSES = ("index_future", "commodity_future", "rate_future", "currency_future")


class RetailPlanningAgent:
    """Deterministic planning agent for auditable paper-trading workflows."""

    name = "retail-planning-agent"

    def create_report(
        self,
        *,
        profile: InvestorProfile,
        goals: tuple[FinancialGoal, ...],
        holdings: tuple[Holding, ...],
        universe: tuple[AssetCandidate, ...],
        prices: dict[str, float],
        timestamp: datetime | None = None,
    ) -> PlanningReport:
        timestamp = timestamp or datetime.utcnow()
        portfolio_value = sum(holding.market_value for holding in holdings)
        if portfolio_value <= 0:
            portfolio_value = max(1.0, profile.net_worth)

        budget = self.risk_budget(profile, goals)
        proposed = StrategicAllocationEngine().propose(profile, goals, universe, portfolio_value, budget)
        approved, suitability = SuitabilityGate().review(profile, proposed, universe, budget, timestamp)
        orders = PaperRebalanceBroker().plan_orders(holdings, approved, portfolio_value)
        futures_margin = FuturesMarginModel().estimate(approved, universe, prices, portfolio_value)
        notes = self._notes(profile, goals, budget, suitability)
        return PlanningReport(
            timestamp=timestamp,
            profile=profile,
            goals=goals,
            total_portfolio_value=portfolio_value,
            proposed_allocations=tuple(proposed),
            approved_allocations=tuple(approved),
            suitability_report=suitability,
            rebalance_orders=tuple(orders),
            futures_margin=tuple(futures_margin),
            planning_notes=tuple(notes),
        )

    def risk_budget(self, profile: InvestorProfile, goals: tuple[FinancialGoal, ...]) -> PlanningRiskBudget:
        risk_score = _risk_score(profile, goals)
        max_equity_weight = min(0.90, max(0.10, profile.max_drawdown_tolerance / 0.35))
        if profile.age >= 60:
            max_equity_weight = min(max_equity_weight, 0.55)
        if profile.time_horizon_years < 3:
            max_equity_weight = min(max_equity_weight, 0.35)
        derivatives_cap = profile.max_derivatives_weight if profile.allow_futures else 0.0
        if profile.risk_tolerance not in ("aggressive", "speculative"):
            derivatives_cap = 0.0
        return PlanningRiskBudget(
            risk_score=risk_score,
            max_equity_weight=max_equity_weight,
            max_single_stock_weight=profile.max_single_stock_weight,
            max_single_asset_weight=profile.max_single_asset_weight,
            max_derivatives_weight=min(0.08, max(0.0, derivatives_cap)),
            liquidity_reserve_weight=min(0.40, max(0.03, profile.liquidity_need_ratio)),
            max_drawdown_tolerance=profile.max_drawdown_tolerance,
            allow_margin=profile.allow_margin,
            allow_futures=profile.allow_futures,
        )

    def _notes(
        self,
        profile: InvestorProfile,
        goals: tuple[FinancialGoal, ...],
        budget: PlanningRiskBudget,
        suitability: SuitabilityReport,
    ) -> list[str]:
        required_goals = [goal for goal in goals if goal.required]
        horizon = min((goal.horizon_years for goal in required_goals), default=profile.time_horizon_years)
        notes = [
            f"Risk score {budget.risk_score:.2f} derived from tolerance, horizon, liquidity need, emergency fund, and drawdown tolerance.",
            f"Nearest required goal horizon is {horizon:.1f} years; liquidity reserve target is {budget.liquidity_reserve_weight:.1%}.",
            "Recommendations are target allocations and paper rebalance instructions, not live brokerage orders.",
        ]
        if not suitability.passed:
            notes.append("Suitability gate produced warnings or blocks; inspect the audit checks before using the plan.")
        if not profile.allow_futures:
            notes.append("Futures and margin overlays are disabled for this profile.")
        return notes


class StrategicAllocationEngine:
    """Converts profile risk capacity into diversified target weights."""

    def propose(
        self,
        profile: InvestorProfile,
        goals: tuple[FinancialGoal, ...],
        universe: tuple[AssetCandidate, ...],
        portfolio_value: float,
        budget: PlanningRiskBudget,
    ) -> list[AllocationTarget]:
        cash_weight = budget.liquidity_reserve_weight
        derivative_weight = budget.max_derivatives_weight
        equity_weight = min(budget.max_equity_weight, 0.18 + 0.70 * budget.risk_score)
        bond_weight = max(0.0, 1.0 - cash_weight - equity_weight - derivative_weight)

        allocations: list[AllocationTarget] = []
        allocations.extend(_allocate_bucket(universe, SAFE_ASSET_CLASSES[:1], cash_weight, portfolio_value, "liquidity reserve"))
        allocations.extend(_allocate_bucket(universe, SAFE_ASSET_CLASSES[1:], bond_weight, portfolio_value, "stability sleeve"))
        allocations.extend(_allocate_bucket(universe, EQUITY_ASSET_CLASSES, equity_weight, portfolio_value, "growth sleeve"))
        if derivative_weight > 0:
            allocations.extend(_allocate_bucket(universe, FUTURES_ASSET_CLASSES, derivative_weight, portfolio_value, "derivatives overlay"))

        if not allocations:
            return []
        total = sum(item.target_weight for item in allocations)
        if total <= 0:
            return []
        return [
            replace(item, target_weight=item.target_weight / total, dollar_amount=portfolio_value * item.target_weight / total)
            for item in allocations
        ]


class SuitabilityGate:
    """Pre-trade suitability and planning-risk gate."""

    name = "retail-suitability-gate"

    def review(
        self,
        profile: InvestorProfile,
        allocations: list[AllocationTarget],
        universe: tuple[AssetCandidate, ...],
        budget: PlanningRiskBudget,
        timestamp: datetime,
    ) -> tuple[list[AllocationTarget], SuitabilityReport]:
        asset_by_symbol = {asset.symbol: asset for asset in universe}
        checks: list[SuitabilityCheck] = []
        blocked_symbols: list[str] = []
        clipped_symbols: list[str] = []
        approved: list[AllocationTarget] = []

        for allocation in allocations:
            asset = asset_by_symbol[allocation.symbol]
            target = allocation.target_weight
            metadata = dict(allocation.metadata)
            if allocation.symbol in profile.restricted_symbols:
                blocked_symbols.append(allocation.symbol)
                checks.append(_check("restricted_symbol", False, "error", f"{allocation.symbol} is restricted by profile", allocation.symbol))
                continue
            if asset.asset_class not in profile.allowed_asset_classes:
                blocked_symbols.append(allocation.symbol)
                checks.append(
                    _check(
                        "asset_class_allowed",
                        False,
                        "error",
                        f"{allocation.symbol} asset class {asset.asset_class} is not allowed",
                        allocation.symbol,
                    )
                )
                continue
            if asset.is_derivative or asset.asset_class in FUTURES_ASSET_CLASSES:
                if not profile.allow_futures:
                    blocked_symbols.append(allocation.symbol)
                    checks.append(_check("futures_permission", False, "error", "futures disabled for profile", allocation.symbol))
                    continue
                if not profile.allow_margin:
                    blocked_symbols.append(allocation.symbol)
                    checks.append(_check("margin_permission", False, "error", "futures require margin permission", allocation.symbol))
                    continue
                if target > budget.max_derivatives_weight:
                    clipped_symbols.append(allocation.symbol)
                    metadata["suitability_clipped_from"] = target
                    target = budget.max_derivatives_weight
                    checks.append(
                        _check(
                            "derivatives_budget",
                            True,
                            "warning",
                            f"{allocation.symbol} clipped to derivative budget {budget.max_derivatives_weight:.1%}",
                            allocation.symbol,
                        )
                    )
            elif asset.asset_class == "equity" and not asset.diversified and target > budget.max_single_stock_weight:
                clipped_symbols.append(allocation.symbol)
                metadata["suitability_clipped_from"] = target
                target = budget.max_single_stock_weight
                checks.append(
                    _check(
                        "single_stock_budget",
                        True,
                        "warning",
                        f"{allocation.symbol} clipped to single-stock budget {budget.max_single_stock_weight:.1%}",
                        allocation.symbol,
                    )
                )
            elif target > budget.max_single_asset_weight and not asset.diversified:
                clipped_symbols.append(allocation.symbol)
                metadata["suitability_clipped_from"] = target
                target = budget.max_single_asset_weight
                checks.append(
                    _check(
                        "single_asset_budget",
                        True,
                        "warning",
                        f"{allocation.symbol} clipped to asset budget {budget.max_single_asset_weight:.1%}",
                        allocation.symbol,
                    )
                )

            approved.append(
                replace(
                    allocation,
                    target_weight=target,
                    metadata={**metadata, "suitability_gate": self.name},
                )
            )

        approved = _renormalize_with_safe_asset(approved, universe)
        approved = [replace(item, dollar_amount=sum(a.dollar_amount for a in allocations) * item.target_weight) for item in approved]

        if profile.emergency_fund_months < 3:
            checks.append(
                _check(
                    "emergency_fund",
                    False,
                    "warning",
                    "emergency fund below three months; consider cash reserve before taking market risk",
                )
            )
        if profile.max_drawdown_tolerance < 0.10 and any(item.asset_class in EQUITY_ASSET_CLASSES for item in approved):
            checks.append(_check("drawdown_tolerance", False, "warning", "low drawdown tolerance conflicts with equity exposure"))
        if not checks:
            checks.append(_check("all_constraints", True, "info", "all suitability checks passed"))

        report = SuitabilityReport(
            timestamp=timestamp,
            passed=not any(check.severity == "error" and not check.passed for check in checks),
            checks=tuple(checks),
            blocked_symbols=tuple(blocked_symbols),
            clipped_symbols=tuple(clipped_symbols),
            budget=budget,
        )
        return approved, report


class PaperRebalanceBroker:
    """Paper-only rebalance planner with human approval required."""

    name = "paper-rebalance-broker"

    def __init__(self, min_trade_value: float = 50.0) -> None:
        self.min_trade_value = min_trade_value

    def plan_orders(
        self,
        holdings: tuple[Holding, ...],
        allocations: tuple[AllocationTarget, ...] | list[AllocationTarget],
        portfolio_value: float,
    ) -> list[PlanningOrder]:
        current_values = {holding.symbol: holding.market_value for holding in holdings}
        current_weights = {symbol: value / portfolio_value for symbol, value in current_values.items()} if portfolio_value else {}
        orders: list[PlanningOrder] = []
        for allocation in allocations:
            current_value = current_values.get(allocation.symbol, 0.0)
            current_weight = current_weights.get(allocation.symbol, 0.0)
            target_value = allocation.target_weight * portfolio_value
            delta = target_value - current_value
            if abs(delta) < self.min_trade_value:
                continue
            side = "buy" if delta > 0 else "sell"
            orders.append(
                PlanningOrder(
                    symbol=allocation.symbol,
                    side=side,
                    dollar_amount=abs(delta),
                    current_weight=current_weight,
                    target_weight=allocation.target_weight,
                    status="paper_pending_human_approval",
                    reason=allocation.rationale,
                    manual_approval_required=True,
                )
            )
        return orders


class FuturesMarginModel:
    """Simple futures notional and margin estimator for audit reports."""

    def estimate(
        self,
        allocations: tuple[AllocationTarget, ...] | list[AllocationTarget],
        universe: tuple[AssetCandidate, ...],
        prices: dict[str, float],
        portfolio_value: float,
    ) -> list[FuturesMarginEstimate]:
        asset_by_symbol = {asset.symbol: asset for asset in universe}
        estimates: list[FuturesMarginEstimate] = []
        for allocation in allocations:
            asset = asset_by_symbol.get(allocation.symbol)
            if asset is None or not (asset.is_derivative or asset.asset_class in FUTURES_ASSET_CLASSES):
                continue
            price = prices.get(asset.symbol, 0.0)
            multiplier = asset.contract_multiplier or 1.0
            margin_rate = asset.initial_margin_rate or 0.10
            maintenance_rate = asset.maintenance_margin_rate or margin_rate * 0.80
            contract_notional = price * multiplier
            target_notional = allocation.target_weight * portfolio_value
            estimated_contracts = target_notional / contract_notional if contract_notional > 0 else 0.0
            initial_margin = abs(estimated_contracts) * contract_notional * margin_rate
            maintenance_margin = abs(estimated_contracts) * contract_notional * maintenance_rate
            leverage_ratio = target_notional / max(1.0, initial_margin)
            estimates.append(
                FuturesMarginEstimate(
                    symbol=asset.symbol,
                    target_notional=target_notional,
                    estimated_contracts=estimated_contracts,
                    initial_margin=initial_margin,
                    maintenance_margin=maintenance_margin,
                    leverage_ratio=leverage_ratio,
                    warning="paper estimate only; confirm exchange margin, expiry, liquidity, and broker rules before live trading",
                )
            )
        return estimates


def default_retail_universe() -> tuple[AssetCandidate, ...]:
    return (
        AssetCandidate("SGOV", "0-3 Month Treasury ETF", "cash_equivalent", 0.043, 0.004, 0.99, 0.0007, True),
        AssetCandidate("BND", "Total Bond Market ETF", "bond_etf", 0.042, 0.060, 0.96, 0.0003, True),
        AssetCandidate("VTI", "Total U.S. Stock Market ETF", "equity_etf", 0.075, 0.170, 0.98, 0.0003, True),
        AssetCandidate("VXUS", "Total International Stock ETF", "equity_etf", 0.068, 0.185, 0.96, 0.0008, True),
        AssetCandidate("XLK", "Technology Sector ETF", "sector_etf", 0.088, 0.230, 0.95, 0.0009, True),
        AssetCandidate("AAPL", "Apple Inc.", "equity", 0.080, 0.280, 0.99, 0.0, False),
        AssetCandidate("MSFT", "Microsoft Corp.", "equity", 0.078, 0.250, 0.99, 0.0, False),
        AssetCandidate(
            "MES",
            "Micro E-mini S&P 500 Future",
            "index_future",
            0.070,
            0.250,
            0.94,
            0.0,
            True,
            True,
            5.0,
            0.080,
            0.065,
            "Illustrative futures overlay, paper only.",
        ),
        AssetCandidate(
            "MCL",
            "Micro WTI Crude Oil Future",
            "commodity_future",
            0.040,
            0.360,
            0.88,
            0.0,
            True,
            True,
            100.0,
            0.120,
            0.100,
            "Illustrative commodity futures exposure, paper only.",
        ),
    )


def _allocate_bucket(
    universe: tuple[AssetCandidate, ...],
    asset_classes: tuple[str, ...],
    bucket_weight: float,
    portfolio_value: float,
    rationale: str,
) -> list[AllocationTarget]:
    if bucket_weight <= 0:
        return []
    candidates = [asset for asset in universe if asset.asset_class in asset_classes]
    if not candidates:
        return []
    scores = [_asset_score(asset) for asset in candidates]
    total_score = sum(scores)
    allocations = []
    for asset, score in zip(candidates, scores):
        weight = bucket_weight * score / total_score if total_score > 0 else bucket_weight / len(candidates)
        allocations.append(
            AllocationTarget(
                symbol=asset.symbol,
                target_weight=weight,
                dollar_amount=portfolio_value * weight,
                asset_class=asset.asset_class,
                rationale=f"{rationale}: {asset.name}",
                metadata={
                    "expected_return": asset.expected_return,
                    "volatility": asset.volatility,
                    "liquidity_score": asset.liquidity_score,
                    "expense_ratio": asset.expense_ratio,
                    "is_derivative": asset.is_derivative,
                },
            )
        )
    return allocations


def _asset_score(asset: AssetCandidate) -> float:
    risk_adjusted = max(0.001, asset.expected_return) / max(0.01, asset.volatility)
    cost_penalty = max(0.25, 1.0 - asset.expense_ratio * 25.0)
    liquidity = max(0.10, asset.liquidity_score)
    return max(0.01, risk_adjusted * cost_penalty * liquidity)


def _risk_score(profile: InvestorProfile, goals: tuple[FinancialGoal, ...]) -> float:
    tolerance = {
        "capital_preservation": 0.15,
        "conservative": 0.25,
        "moderate": 0.50,
        "growth": 0.68,
        "aggressive": 0.84,
        "speculative": 0.94,
    }.get(profile.risk_tolerance, 0.45)
    horizon = min(1.0, max(0.0, profile.time_horizon_years / 20.0))
    goal_horizon = min((goal.horizon_years for goal in goals if goal.required), default=profile.time_horizon_years)
    horizon_adjustment = 0.18 * horizon - (0.10 if goal_horizon < 3 else 0.0)
    drawdown_adjustment = min(0.18, max(-0.20, (profile.max_drawdown_tolerance - 0.15) * 0.90))
    liquidity_penalty = min(0.22, max(0.0, profile.liquidity_need_ratio - 0.05) * 0.80)
    emergency_penalty = 0.12 if profile.emergency_fund_months < 3 else 0.04 if profile.emergency_fund_months < 6 else 0.0
    age_penalty = max(0.0, profile.age - 55) * 0.006
    return _clamp(tolerance + horizon_adjustment + drawdown_adjustment - liquidity_penalty - emergency_penalty - age_penalty, 0.05, 0.95)


def _renormalize_with_safe_asset(allocations: list[AllocationTarget], universe: tuple[AssetCandidate, ...]) -> list[AllocationTarget]:
    if not allocations:
        return []
    total = sum(max(0.0, item.target_weight) for item in allocations)
    if total <= 1e-12:
        return []
    normalized = [replace(item, target_weight=max(0.0, item.target_weight) / total) for item in allocations]
    safe_symbol = next((asset.symbol for asset in universe if asset.asset_class == "cash_equivalent"), None)
    if safe_symbol and sum(item.target_weight for item in normalized) < 0.999:
        gap = 1.0 - sum(item.target_weight for item in normalized)
        normalized.append(
            AllocationTarget(
                symbol=safe_symbol,
                target_weight=gap,
                dollar_amount=0.0,
                asset_class="cash_equivalent",
                rationale="residual allocation moved to cash equivalent",
            )
        )
    return normalized


def _check(name: str, passed: bool, severity: str, message: str, symbol: str | None = None) -> SuitabilityCheck:
    return SuitabilityCheck(name=name, passed=passed, severity=severity, message=message, symbol=symbol)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
