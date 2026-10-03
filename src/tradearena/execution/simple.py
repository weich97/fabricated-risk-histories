from __future__ import annotations

from dataclasses import dataclass, field

from tradearena.core.domain import ExecutionReport, Fill, MarketSnapshot, Order, PortfolioState, Side


@dataclass
class SimpleOrderSimulator:
    commission_bps: float = 1.0
    slippage_bps: float = 2.0
    allow_short: bool = False
    name: str = "simple-order-simulator"
    last_report: ExecutionReport | None = field(default=None, init=False)

    def execute(self, snapshot: MarketSnapshot, orders: list[Order], portfolio: PortfolioState) -> list[Fill]:
        portfolio.last_prices.update({symbol: bar.close for symbol, bar in snapshot.bars.items()})
        fills: list[Fill] = []
        rejected = 0
        for order in orders:
            if order.side == Side.HOLD or order.symbol not in snapshot.bars:
                rejected += 1
                continue
            mid = snapshot.price(order.symbol)
            slip = self.slippage_bps / 10_000.0
            price = mid * (1.0 + slip if order.side == Side.BUY else 1.0 - slip)
            quantity = max(0.0, order.quantity)
            trade_value = quantity * price
            commission = trade_value * self.commission_bps / 10_000.0

            if order.side == Side.BUY:
                commission_rate = self.commission_bps / 10_000.0
                affordable = max(0.0, portfolio.cash) / (price * (1.0 + commission_rate))
                quantity = min(quantity, affordable)
                if quantity <= 0:
                    continue
                trade_value = quantity * price
                commission = trade_value * self.commission_bps / 10_000.0
                portfolio.cash -= trade_value + commission
                portfolio.positions[order.symbol] = portfolio.positions.get(order.symbol, 0.0) + quantity
            elif order.side == Side.SELL:
                available = portfolio.positions.get(order.symbol, 0.0)
                if not self.allow_short:
                    quantity = min(quantity, max(0.0, available))
                if quantity <= 0:
                    continue
                trade_value = quantity * price
                commission = trade_value * self.commission_bps / 10_000.0
                portfolio.cash += trade_value - commission
                portfolio.positions[order.symbol] = available - quantity

            fills.append(
                Fill(
                    symbol=order.symbol,
                    side=order.side,
                    quantity=quantity,
                    price=price,
                    commission=commission,
                    timestamp=snapshot.timestamp,
                    requested_quantity=order.quantity,
                    liquidity_available=order.quantity,
                    fill_ratio=1.0,
                    slippage=price - mid,
                )
            )
        self.last_report = ExecutionReport(
            timestamp=snapshot.timestamp,
            submitted_orders=len(orders),
            eligible_orders=len(orders) - rejected,
            filled_orders=len(fills),
            partial_fills=0,
            pending_orders=0,
            rejected_orders=rejected,
            total_commission=sum(fill.commission for fill in fills),
            total_slippage=sum(abs(fill.slippage) * fill.quantity for fill in fills),
            average_latency_steps=0.0,
            metadata={"mode": "idealized", "assumption_class": "idealized"},
        )
        return fills


__all__ = ["SimpleOrderSimulator"]
