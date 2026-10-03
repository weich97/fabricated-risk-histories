from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from tradearena.core.domain import ExecutionReport, Fill, MarketSnapshot, Order, PortfolioState, Side
from tradearena.execution.constants import EXECUTION_FILL_REPLAY
from tradearena.execution.utils import float_or_none, parse_datetime


@dataclass
class FillReplayOrderSimulator:
    """Apply realized fills from a private or licensed fill log.

    This simulator is intentionally conservative: if a submitted order has no
    matching replay fill at the current timestamp, the order is counted as
    rejected. It is meant for audit replay, not for predicting future costs.
    """

    replay_fills: list[Fill] | None = None
    csv_path: str | Path | None = None
    allow_short: bool = False
    enforce_cash: bool = True
    name: str = "fill-replay-order-simulator"
    _unused_fills: list[Fill] = field(default_factory=list, init=False)
    last_report: ExecutionReport | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        fills = list(self.replay_fills or [])
        if self.csv_path:
            fills.extend(load_replay_fills_csv(self.csv_path))
        self._unused_fills = fills

    def execute(self, snapshot: MarketSnapshot, orders: list[Order], portfolio: PortfolioState) -> list[Fill]:
        portfolio.last_prices.update({symbol: bar.close for symbol, bar in snapshot.bars.items()})
        fills: list[Fill] = []
        rejected = 0
        partial = 0
        for order in orders:
            if order.side == Side.HOLD:
                rejected += 1
                continue
            fill = self._pop_replay_fill(snapshot, order)
            if fill is None:
                rejected += 1
                continue
            quantity = min(fill.quantity, max(0.0, order.quantity))
            if order.side == Side.SELL and not self.allow_short:
                quantity = min(quantity, max(0.0, portfolio.positions.get(order.symbol, 0.0)))
            if order.side == Side.BUY and self.enforce_cash:
                affordable = max(0.0, portfolio.cash - fill.commission) / max(fill.price, 1e-9)
                quantity = min(quantity, affordable)
            if quantity <= 0:
                rejected += 1
                continue
            fill_ratio = quantity / max(order.quantity, 1e-9)
            partial += 1 if fill_ratio < 0.999999 else 0
            applied = Fill(
                symbol=fill.symbol,
                side=fill.side,
                quantity=quantity,
                price=fill.price,
                commission=fill.commission,
                timestamp=fill.timestamp,
                requested_quantity=order.quantity,
                latency_steps=fill.latency_steps,
                liquidity_available=fill.liquidity_available,
                fill_ratio=fill_ratio,
                slippage=fill.slippage,
                status="partial" if fill_ratio < 0.999999 else "filled",
            )
            if applied.side == Side.BUY:
                portfolio.cash -= applied.quantity * applied.price + applied.commission
                portfolio.positions[applied.symbol] = portfolio.positions.get(applied.symbol, 0.0) + applied.quantity
            else:
                portfolio.cash += applied.quantity * applied.price - applied.commission
                portfolio.positions[applied.symbol] = portfolio.positions.get(applied.symbol, 0.0) - applied.quantity
            fills.append(applied)
        self.last_report = ExecutionReport(
            timestamp=snapshot.timestamp,
            submitted_orders=len(orders),
            eligible_orders=len(orders),
            filled_orders=len(fills),
            partial_fills=partial,
            pending_orders=0,
            rejected_orders=rejected,
            total_commission=sum(fill.commission for fill in fills),
            total_slippage=sum(abs(fill.slippage) * fill.quantity for fill in fills),
            average_latency_steps=sum(fill.latency_steps for fill in fills) / len(fills) if fills else 0.0,
            metadata={
                "mode": "real_fill_replay",
                "assumption_class": EXECUTION_FILL_REPLAY,
                "fill_source": str(self.csv_path) if self.csv_path else "in_memory",
                "unmatched_replay_fills": len(self._unused_fills),
            },
        )
        return fills

    def _pop_replay_fill(self, snapshot: MarketSnapshot, order: Order) -> Fill | None:
        for index, fill in enumerate(self._unused_fills):
            if fill.symbol != order.symbol or fill.side != order.side:
                continue
            if fill.timestamp != snapshot.timestamp:
                continue
            return self._unused_fills.pop(index)
        return None


def load_replay_fills_csv(path: str | Path) -> list[Fill]:
    fills: list[Fill] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            timestamp = parse_datetime(row.get("timestamp") or row.get("filled_at") or row.get("fill_time"))
            side = Side(str(row.get("side", "")).strip().lower())
            reference_price = float_or_none(row.get("reference_price"))
            fill_price = float(row.get("fill_price") or row.get("price") or 0.0)
            slippage = 0.0 if reference_price is None else fill_price - reference_price
            fills.append(
                Fill(
                    symbol=str(row.get("symbol", "")).strip(),
                    side=side,
                    quantity=float(row.get("quantity") or 0.0),
                    price=fill_price,
                    commission=float(row.get("commission") or 0.0),
                    timestamp=timestamp,
                    requested_quantity=float_or_none(row.get("requested_quantity")),
                    latency_steps=int(float(row.get("latency_steps") or 0.0)),
                    liquidity_available=float_or_none(row.get("liquidity_available")),
                    fill_ratio=float(row.get("fill_ratio") or 1.0),
                    slippage=slippage,
                    status=str(row.get("status") or "filled"),
                )
            )
    return fills


__all__ = ["FillReplayOrderSimulator", "load_replay_fills_csv"]
