from __future__ import annotations

import hashlib
import json
from typing import Any

from tradearena.core.domain import (
    AgentProtocolTrace,
    ExperimentConfig,
    PortfolioState,
    ReproducibilityState,
    RiskCheck,
    RiskPhase,
    RiskReport,
    ToolCallRecord,
)
from tradearena.core.interfaces import (
    AnalystAgent,
    Evaluator,
    ExecutionAgent,
    MarketDataProvider,
    MemoryStore,
    OrderSimulator,
    RiskManagerAgent,
    StrategyAgent,
)
from tradearena.core.serialization import to_jsonable
from tradearena.core.trajectory import StepRecord, Trajectory


class TradeArena:
    """Composable experiment runner for one financial-agent stack."""

    def __init__(
        self,
        config: ExperimentConfig,
        data_provider: MarketDataProvider,
        analysts: list[AnalystAgent],
        strategy: StrategyAgent,
        risk_manager: RiskManagerAgent,
        execution_agent: ExecutionAgent,
        order_simulator: OrderSimulator,
        memory: MemoryStore,
        evaluators: list[Evaluator],
    ) -> None:
        self.config = config
        self.data_provider = data_provider
        self.analysts = analysts
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.execution_agent = execution_agent
        self.order_simulator = order_simulator
        self.memory = memory
        self.evaluators = evaluators

    def run(self) -> tuple[Trajectory, dict[str, float | int | str]]:
        portfolio = PortfolioState(cash=self.config.initial_cash)
        trajectory = Trajectory(
            experiment_name=self.config.name,
            seed=self.config.seed,
            metadata={
                "data_provider": self.data_provider.name,
                "analysts": [agent.name for agent in self.analysts],
                "strategy": self.strategy.name,
                "risk_manager": self.risk_manager.name,
                "execution_agent": self.execution_agent.name,
                "order_simulator": self.order_simulator.name,
            },
        )

        warm_start_pending = dict(getattr(self.config, "initial_holdings_weights", {}) or {})

        for snapshot in self.data_provider.stream():
            portfolio.last_prices.update({symbol: bar.close for symbol, bar in snapshot.bars.items()})
            if warm_start_pending:
                self._seed_warm_start(portfolio, snapshot, warm_start_pending)
                warm_start_pending = {}
            before_memory = len(getattr(self.memory, "events", []))
            memory_digest_before = self._memory_digest()

            signals = []
            for analyst in self.analysts:
                signals.extend(analyst.analyze(snapshot, portfolio.copy(), self.memory))

            tool_outputs = (
                ToolCallRecord(
                    tool_name="analyst_stack",
                    inputs={"analysts": [agent.name for agent in self.analysts]},
                    outputs={"signals": [to_jsonable(signal) for signal in signals]},
                    timestamp=snapshot.timestamp,
                ),
            )
            decisions = self.strategy.decide(snapshot, signals, portfolio.copy(), self.memory)
            approved = self.risk_manager.approve(snapshot, decisions, portfolio.copy(), self.memory)
            risk_report = getattr(self.risk_manager, "last_report", {})
            orders = self.execution_agent.create_orders(snapshot, approved, portfolio.copy())
            fills = self.order_simulator.execute(snapshot, orders, portfolio)
            execution_report = getattr(self.order_simulator, "last_report", {})
            in_trade_report = self.risk_manager.monitor(snapshot, orders, fills, portfolio.copy(), self.memory)
            attribution = self.risk_manager.attribute(snapshot, fills, portfolio.copy(), self.memory)
            post_trade_report = RiskReport(
                timestamp=snapshot.timestamp,
                checks=(RiskCheck(name="post_trade_attribution", passed=True, severity="info", message="post-trade attribution recorded"),),
                approved_count=len(approved),
                blocked_count=0,
                clipped_count=0,
                phase=RiskPhase.POST_TRADE,
                budget=getattr(self.risk_manager, "budget", lambda: None)(),
                attribution=attribution,
            )
            reproducibility_state = self._reproducibility_state(
                snapshot=snapshot,
                portfolio=portfolio.copy(),
                memory_digest=memory_digest_before,
                tool_outputs=tool_outputs,
            )
            agent_trace = self._agent_trace(
                snapshot=snapshot,
                signals=signals,
                decisions=decisions,
                approved=approved,
                orders=orders,
                fills=fills,
                risk_report=risk_report,
                in_trade_report=in_trade_report,
                post_trade_report=post_trade_report,
                execution_report=execution_report,
                reproducibility_state=reproducibility_state,
            )
            risk_violations: list[Any] = []
            for report in (risk_report, in_trade_report, post_trade_report):
                risk_violations.extend(getattr(report, "violations", []) or [])

            self.memory.record(
                "step",
                {
                    "timestamp": snapshot.timestamp,
                    "reproducibility_state": reproducibility_state,
                    "agent_trace": agent_trace,
                    "signals": signals,
                    "decisions": decisions,
                    "approved_decisions": approved,
                    "risk_report": risk_report,
                    "in_trade_report": in_trade_report,
                    "post_trade_report": post_trade_report,
                    "risk_violations": risk_violations,
                    "orders": orders,
                    "fills": fills,
                    "execution_report": execution_report,
                    "equity": portfolio.equity(),
                },
            )
            memory_events = getattr(self.memory, "events", [])[before_memory:]

            trajectory.append(
                StepRecord(
                    timestamp=snapshot.timestamp,
                    observation={
                        "prices": {symbol: bar.close for symbol, bar in snapshot.bars.items()},
                        "news_count": len(snapshot.news),
                        "macro_count": len(snapshot.macro),
                        "filings_count": len(getattr(snapshot, "filings", ())),
                        "alt_data_count": len(snapshot.alt_data),
                    },
                    signals=[to_jsonable(signal) for signal in signals],
                    decisions=[to_jsonable(decision) for decision in decisions],
                    approved_decisions=[to_jsonable(decision) for decision in approved],
                    orders=[to_jsonable(order) for order in orders],
                    fills=[to_jsonable(fill) for fill in fills],
                    portfolio={
                        "cash": portfolio.cash,
                        "positions": dict(portfolio.positions),
                        "last_prices": dict(portfolio.last_prices),
                        "equity": portfolio.equity(),
                    },
                    reproducibility_state=to_jsonable(reproducibility_state),
                    agent_trace=to_jsonable(agent_trace),
                    risk_report=to_jsonable(risk_report),
                    in_trade_report=to_jsonable(in_trade_report),
                    post_trade_report=to_jsonable(post_trade_report),
                    execution_report=to_jsonable(execution_report),
                    risk_violations=[to_jsonable(violation) for violation in risk_violations],
                    memory_events=[to_jsonable(event) for event in memory_events],
                )
            )

        metrics: dict[str, float | int | str] = {}
        for evaluator in self.evaluators:
            metrics.update(evaluator.evaluate(trajectory))
        return trajectory, metrics

    def _seed_warm_start(self, portfolio, snapshot, weights: dict[str, float]) -> None:
        """Seed the portfolio with target-weight holdings at first-bar prices,
        free of execution cost, so initial-construction friction is not charged
        to the agent (anchor robustness check)."""

        equity = portfolio.cash
        for symbol, weight in weights.items():
            price = snapshot.bars[symbol].close if symbol in snapshot.bars else 0.0
            if price <= 0.0:
                continue
            notional = equity * float(weight)
            qty = notional / price
            portfolio.positions[symbol] = portfolio.positions.get(symbol, 0.0) + qty
            portfolio.cash -= notional

    def _memory_digest(self) -> str:
        events = getattr(self.memory, "events", [])
        payload = to_jsonable(events[-10:])
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _risk_budget(self):
        budget_fn = getattr(self.risk_manager, "budget", None)
        return budget_fn() if callable(budget_fn) else None

    def _simulator_state(self) -> dict[str, object]:
        return {
            "name": self.order_simulator.name,
            "pending_orders": len(getattr(self.order_simulator, "_pending", [])),
            "step": getattr(self.order_simulator, "_step", None),
            "sequence": getattr(self.order_simulator, "_sequence", None),
        }

    def _reproducibility_state(self, snapshot, portfolio, memory_digest: str, tool_outputs: tuple[ToolCallRecord, ...]) -> ReproducibilityState:
        return ReproducibilityState(
            prompt_version=str(self.config.metadata.get("prompt_version", "baseline-v0")),
            model_version=str(self.config.metadata.get("model_version", "deterministic-baseline")),
            retrieved_documents=tuple(self.config.metadata.get("retrieved_documents", ())),
            market_data_timestamp=snapshot.timestamp,
            tool_outputs=tool_outputs,
            memory_digest=memory_digest,
            risk_constraints=self._risk_budget(),
            portfolio_state={
                "cash": portfolio.cash,
                "positions": dict(portfolio.positions),
                "last_prices": dict(portfolio.last_prices),
                "equity": portfolio.equity(),
            },
            agent_discussion_history=tuple(self.config.metadata.get("agent_discussion_history", ())),
            execution_simulator_state=self._simulator_state(),
            random_seed=self.config.seed,
        )

    def _agent_trace(
        self,
        *,
        snapshot,
        signals,
        decisions,
        approved,
        orders,
        fills,
        risk_report,
        in_trade_report,
        post_trade_report,
        execution_report,
        reproducibility_state,
    ) -> AgentProtocolTrace:
        schemas = {
            "observation": {
                "timestamp": "datetime",
                "bars": "dict[str, Bar]",
                "news": "tuple[NewsItem]",
                "macro": "tuple[MacroPoint]",
                "filings": "tuple[FilingItem]",
                "alt_data": "dict[str, Any]",
            },
            "memory": {"digest": "sha256", "events": "append-only journal"},
            "tool": {"tool_name": "str", "inputs": "dict", "outputs": "dict", "status": "str"},
            "action": {"decision": "target_weight", "order": "side/quantity/type/limit"},
            "risk": {"budget": "RiskBudget", "checks": "RiskCheck[]", "violations": "RiskViolation[]"},
            "trajectory": {"observe": "dict", "plan": "dict", "act": "fills", "reflect": "post-trade attribution"},
            "evaluation": {"performance": "returns/risk", "execution": "costs/fills", "audit": "coverage/violations"},
        }
        return AgentProtocolTrace(
            observation_schema=schemas["observation"],
            memory_schema=schemas["memory"],
            tool_schema=schemas["tool"],
            action_schema=schemas["action"],
            risk_schema=schemas["risk"],
            trajectory_schema=schemas["trajectory"],
            evaluation_schema=schemas["evaluation"],
            observe={
                "timestamp": snapshot.timestamp,
                "symbols": tuple(snapshot.bars),
                "news_count": len(snapshot.news),
                "macro_count": len(snapshot.macro),
                "filings_count": len(getattr(snapshot, "filings", ())),
                "alt_data_count": len(snapshot.alt_data),
                "memory_digest": reproducibility_state.memory_digest,
            },
            plan={
                "analysts": [agent.name for agent in self.analysts],
                "strategy": self.strategy.name,
                "signals": signals,
                "decisions": decisions,
            },
            propose_order={"execution_agent": self.execution_agent.name, "orders": orders},
            risk_report={"pre_trade": risk_report, "in_trade": in_trade_report, "post_trade": post_trade_report},
            revise={"approved_decisions": approved, "revisions": [decision.metadata for decision in approved]},
            act={"simulator": self.order_simulator.name, "fills": fills, "execution_report": execution_report},
            reflect={"post_trade_attribution": post_trade_report.attribution, "portfolio_equity": reproducibility_state.portfolio_state["equity"]},
        )
