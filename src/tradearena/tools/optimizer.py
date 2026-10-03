from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EqualRiskBudgetOptimizer:
    max_weight: float = 0.35
    name: str = "equal-risk-budget-optimizer"

    def optimize(self, expected_scores: dict[str, float], risk_estimates: dict[str, float]) -> dict[str, float]:
        positive = {
            symbol: max(0.0, score) / max(1e-9, risk_estimates.get(symbol, 1.0))
            for symbol, score in expected_scores.items()
        }
        total = sum(positive.values())
        if total <= 0:
            return dict.fromkeys(expected_scores, 0.0)
        raw = {symbol: value / total for symbol, value in positive.items()}
        return {symbol: min(self.max_weight, weight) for symbol, weight in raw.items()}
