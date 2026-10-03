from __future__ import annotations

from dataclasses import dataclass

from tradearena.core.domain import Decision, MarketSnapshot, Order, PortfolioState, Side


@dataclass
class TargetWeightExecutionAgent:
    min_trade_value: float = 25.0
    name: str = "target-weight-execution"

    def create_orders(self, snapshot: MarketSnapshot, decisions: list[Decision], portfolio: PortfolioState) -> list[Order]:
        equity = portfolio.equity()
        orders: list[Order] = []
        for decision in decisions:
            if decision.symbol not in snapshot.bars:
                continue
            price = snapshot.price(decision.symbol)
            current_value = portfolio.positions.get(decision.symbol, 0.0) * price
            target_value = decision.target_weight * equity
            diff_value = target_value - current_value
            if abs(diff_value) < self.min_trade_value or price <= 0:
                continue
            quantity = abs(diff_value) / price
            side = Side.BUY if diff_value > 0 else Side.SELL
            orders.append(Order(symbol=decision.symbol, side=side, quantity=quantity, reason=decision.rationale))
        return orders
