from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite

from tradearena.core.domain import Decision, MarketSnapshot, PortfolioState, Side, Signal


@dataclass
class DeterministicRLAllocationStrategy:
    """Tiny deterministic policy wrapper that behaves like an RL allocation head.

    This is a CI-safe integration baseline, not a trained policy. A real
    FinRL/Qlib-style policy can replace `_policy_scores` while keeping the same
    TreLLM strategy interface and downstream risk/execution/evaluation stack.
    """

    max_long_weight: float = 0.35
    total_risk_budget: float = 0.90
    temperature: float = 0.75
    name: str = "deterministic-rl-allocation-strategy"

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        symbols = sorted(snapshot.bars)
        if not symbols:
            return []
        scores = self._policy_scores(snapshot, symbols)
        exp_scores = [exp(score / max(1e-9, self.temperature)) for score in scores]
        total = sum(exp_scores) or 1.0
        raw_weights = [value / total * self.total_risk_budget for value in exp_scores]
        capped = [min(self.max_long_weight, max(0.0, weight)) for weight in raw_weights]
        cap_total = sum(capped)
        if cap_total > self.total_risk_budget:
            capped = [weight * self.total_risk_budget / cap_total for weight in capped]

        decisions: list[Decision] = []
        for symbol, score, target in zip(symbols, scores, capped):
            decisions.append(
                Decision(
                    symbol=symbol,
                    side=Side.BUY if target > 1e-9 else Side.HOLD,
                    target_weight=target,
                    confidence=max(0.1, min(1.0, 0.5 + abs(score))),
                    rationale="deterministic mock RL policy score mapped to target allocation",
                    metadata={
                        "strategy": self.name,
                        "policy_type": "mock_deep_rl_allocation",
                        "policy_score": score,
                        "integration_note": "replace _policy_scores with a trained policy output for FinRL/Qlib-style integration",
                    },
                )
            )
        return decisions

    def _policy_scores(self, snapshot: MarketSnapshot, symbols: list[str]) -> list[float]:
        scores: list[float] = []
        for symbol in symbols:
            bar = snapshot.bars[symbol]
            momentum = (bar.close / bar.open) - 1.0 if bar.open else 0.0
            range_ratio = (bar.high - bar.low) / max(1e-9, bar.close)
            liquidity = min(1.0, bar.volume / 1_000_000.0)
            score = 6.0 * momentum - 0.7 * range_ratio + 0.15 * liquidity
            scores.append(score if isfinite(score) else 0.0)
        return scores
