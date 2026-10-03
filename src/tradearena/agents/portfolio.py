from __future__ import annotations

from dataclasses import dataclass

from tradearena.core.domain import Decision, MarketSnapshot, PortfolioState, Side


@dataclass
class EqualWeightPortfolioManager:
    name: str = "equal-weight-portfolio-manager"

    def rebalance(self, snapshot: MarketSnapshot, portfolio: PortfolioState) -> list[Decision]:
        if not snapshot.bars:
            return []
        target = 1.0 / len(snapshot.bars)
        return [
            Decision(
                symbol=symbol,
                side=Side.BUY,
                target_weight=target,
                confidence=1.0,
                rationale="equal-weight rebalance",
                metadata={"agent": self.name},
            )
            for symbol in snapshot.bars
        ]
