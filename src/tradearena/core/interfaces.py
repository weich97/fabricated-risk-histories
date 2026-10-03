from __future__ import annotations

from typing import Any, Protocol

from tradearena.core.domain import (
    Decision,
    Fill,
    MarketSnapshot,
    Order,
    PortfolioState,
    RiskAttribution,
    RiskReport,
    Signal,
)
from tradearena.core.trajectory import Trajectory


class MarketDataProvider(Protocol):
    name: str

    def stream(self) -> list[MarketSnapshot]:
        """Return deterministic market snapshots for one experiment."""


class AnalystAgent(Protocol):
    name: str

    def analyze(self, snapshot: MarketSnapshot, portfolio: PortfolioState, memory: Any) -> list[Signal]:
        """Transform observations into scored investment signals."""


class StrategyAgent(Protocol):
    name: str

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: Any) -> list[Decision]:
        """Transform analyst signals into target portfolio decisions."""


class RiskManagerAgent(Protocol):
    name: str

    def approve(self, snapshot: MarketSnapshot, decisions: list[Decision], portfolio: PortfolioState, memory: Any) -> list[Decision]:
        """Constrain decisions according to risk policy."""

    def monitor(self, snapshot: MarketSnapshot, orders: list[Order], fills: list[Fill], portfolio: PortfolioState, memory: Any) -> RiskReport:
        """Monitor in-trade execution and liquidity risks."""

    def attribute(self, snapshot: MarketSnapshot, fills: list[Fill], portfolio: PortfolioState, memory: Any) -> RiskAttribution:
        """Attribute post-trade outcome to PnL, costs, slippage, and exposure."""


class TradingAgent(Protocol):
    name: str

    def observe(self, market_state: MarketSnapshot, portfolio_state: PortfolioState, memory: Any) -> dict[str, Any]:
        """Create an agent-native observation object."""

    def plan(self, tools: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
        """Create a plan under tool and risk constraints."""

    def propose_order(self) -> list[Order]:
        """Propose orders before risk review."""

    def risk_report(self) -> RiskReport:
        """Expose current risk report."""

    def revise(self, feedback: RiskReport) -> dict[str, Any]:
        """Revise the plan after risk feedback."""

    def act(self, execution_env: Any) -> list[Fill]:
        """Act in an execution environment."""

    def reflect(self, outcome: dict[str, Any]) -> dict[str, Any]:
        """Update internal state after outcomes are known."""


class ExecutionAgent(Protocol):
    name: str

    def create_orders(self, snapshot: MarketSnapshot, decisions: list[Decision], portfolio: PortfolioState) -> list[Order]:
        """Convert approved target decisions into executable orders."""


class OrderSimulator(Protocol):
    name: str

    def execute(self, snapshot: MarketSnapshot, orders: list[Order], portfolio: PortfolioState) -> list[Fill]:
        """Simulate fills and mutate portfolio state."""


class MemoryStore(Protocol):
    name: str

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        """Persist an event in agent memory."""


class Evaluator(Protocol):
    name: str

    def evaluate(self, trajectory: Trajectory) -> dict[str, float | int | str]:
        """Produce metrics from a trajectory."""
