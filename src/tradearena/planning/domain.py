from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class InvestorProfile:
    """Investor facts used for suitability-aware planning.

    The planning layer is intended for research, education, paper trading, and
    human-reviewed workflows. It does not place live orders.
    """

    investor_id: str
    age: int
    annual_income: float
    net_worth: float
    emergency_fund_months: float
    time_horizon_years: float
    risk_tolerance: str
    liquidity_need_ratio: float
    max_drawdown_tolerance: float
    investment_objective: str
    account_type: str = "taxable"
    allowed_asset_classes: tuple[str, ...] = ("cash_equivalent", "bond_etf", "equity_etf", "equity")
    restricted_symbols: tuple[str, ...] = ()
    allow_margin: bool = False
    allow_futures: bool = False
    tax_sensitive: bool = True
    max_single_stock_weight: float = 0.10
    max_single_asset_weight: float = 0.45
    max_derivatives_weight: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FinancialGoal:
    name: str
    target_amount: float
    horizon_years: float
    priority: int = 1
    required: bool = True


@dataclass(frozen=True)
class AssetCandidate:
    symbol: str
    name: str
    asset_class: str
    expected_return: float
    volatility: float
    liquidity_score: float
    expense_ratio: float = 0.0
    diversified: bool = False
    is_derivative: bool = False
    contract_multiplier: float | None = None
    initial_margin_rate: float | None = None
    maintenance_margin_rate: float | None = None
    description: str = ""


@dataclass(frozen=True)
class Holding:
    symbol: str
    market_value: float
    quantity: float = 0.0
    cost_basis: float | None = None


@dataclass(frozen=True)
class AllocationTarget:
    symbol: str
    target_weight: float
    dollar_amount: float
    asset_class: str
    rationale: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SuitabilityCheck:
    name: str
    passed: bool
    severity: str
    message: str
    symbol: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanningRiskBudget:
    risk_score: float
    max_equity_weight: float
    max_single_stock_weight: float
    max_single_asset_weight: float
    max_derivatives_weight: float
    liquidity_reserve_weight: float
    max_drawdown_tolerance: float
    allow_margin: bool
    allow_futures: bool


@dataclass(frozen=True)
class SuitabilityReport:
    timestamp: datetime
    passed: bool
    checks: tuple[SuitabilityCheck, ...]
    blocked_symbols: tuple[str, ...]
    clipped_symbols: tuple[str, ...]
    budget: PlanningRiskBudget


@dataclass(frozen=True)
class PlanningOrder:
    symbol: str
    side: str
    dollar_amount: float
    current_weight: float
    target_weight: float
    status: str
    reason: str
    manual_approval_required: bool = True


@dataclass(frozen=True)
class FuturesMarginEstimate:
    symbol: str
    target_notional: float
    estimated_contracts: float
    initial_margin: float
    maintenance_margin: float
    leverage_ratio: float
    warning: str


@dataclass(frozen=True)
class PlanningReport:
    timestamp: datetime
    profile: InvestorProfile
    goals: tuple[FinancialGoal, ...]
    total_portfolio_value: float
    proposed_allocations: tuple[AllocationTarget, ...]
    approved_allocations: tuple[AllocationTarget, ...]
    suitability_report: SuitabilityReport
    rebalance_orders: tuple[PlanningOrder, ...]
    futures_margin: tuple[FuturesMarginEstimate, ...]
    planning_notes: tuple[str, ...]
    disclaimers: tuple[str, ...] = (
        "Educational and research workflow only; not investment, tax, legal, or futures trading advice.",
        "Default execution mode is paper trading with human approval required.",
        "Live brokerage and futures execution require separate regulatory, suitability, and supervision controls.",
    )
