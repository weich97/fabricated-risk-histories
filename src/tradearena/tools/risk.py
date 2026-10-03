from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RiskCalculator:
    name: str = "risk-calculator"

    def returns(self, equity_curve: list[float]) -> list[float]:
        values = [value for value in equity_curve if value > 0]
        return [(values[idx] / values[idx - 1]) - 1.0 for idx in range(1, len(values))]

    def volatility(self, returns: list[float], annualization: float = 252.0) -> float:
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
        return math.sqrt(variance) * math.sqrt(annualization)

    def sharpe(self, returns: list[float], annualization: float = 252.0) -> float:
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        vol_daily = self.volatility(returns, annualization=1.0)
        if vol_daily == 0:
            return 0.0
        return mean / vol_daily * math.sqrt(annualization)

    def max_drawdown(self, equity_curve: list[float]) -> float:
        peak = None
        max_dd = 0.0
        for value in equity_curve:
            peak = value if peak is None else max(peak, value)
            if peak and peak > 0:
                max_dd = min(max_dd, (value / peak) - 1.0)
        return max_dd
