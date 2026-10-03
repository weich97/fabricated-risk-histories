from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from math import isfinite, sqrt

from tradearena.core.domain import Decision, MarketSnapshot, PortfolioState, Side, Signal


@dataclass
class SignalWeightedStrategy:
    max_long_weight: float = 0.8
    max_short_weight: float = 0.0
    deadband: float = 0.01
    name: str = "signal-weighted-strategy"

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        grouped: dict[str, list[Signal]] = {}
        for signal in signals:
            grouped.setdefault(signal.symbol, []).append(signal)

        decisions: list[Decision] = []
        for symbol in snapshot.bars:
            symbol_signals = grouped.get(symbol, [])
            if not symbol_signals:
                combined = 0.0
                confidence = 0.0
                rationale = "no signals"
            else:
                weight_sum = sum(max(0.01, signal.confidence) for signal in symbol_signals)
                combined = sum(signal.score * max(0.01, signal.confidence) for signal in symbol_signals) / weight_sum
                confidence = min(1.0, weight_sum / max(1, len(symbol_signals)))
                rationale = " | ".join(signal.rationale for signal in symbol_signals)

            if combined > self.deadband:
                target = min(self.max_long_weight, combined * 5.0)
                side = Side.BUY
            elif combined < -self.deadband and self.max_short_weight > 0:
                target = max(-self.max_short_weight, combined * 5.0)
                side = Side.SELL
            else:
                target = 0.0
                side = Side.HOLD

            decisions.append(
                Decision(
                    symbol=symbol,
                    side=side,
                    target_weight=target,
                    confidence=confidence,
                    rationale=rationale,
                    metadata={"combined_score": combined, "strategy": self.name},
                )
            )
        return decisions


@dataclass
class BuyAndHoldStrategy:
    target_weight: float = 1.0
    name: str = "buy-and-hold"

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        if not snapshot.bars:
            return []
        target = self.target_weight / len(snapshot.bars)
        return [
            Decision(
                symbol=symbol,
                side=Side.BUY,
                target_weight=target,
                confidence=1.0,
                rationale="baseline equal buy-and-hold allocation",
                metadata={"strategy": self.name},
            )
            for symbol in snapshot.bars
        ]


@dataclass
class EqualWeightStrategy:
    """Equal-weight rebalancing baseline.

    This is intentionally distinct from `BuyAndHoldStrategy`: it represents a
    simple periodic rebalance policy and is useful as a classical anchor when
    LLM rows are evaluated on target-weight quality.
    """

    target_gross: float = 1.0
    max_long_weight: float = 0.35
    name: str = "equal-weight-strategy"

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        symbols = sorted(snapshot.bars)
        if not symbols:
            return []
        target = min(self.max_long_weight, self.target_gross / len(symbols))
        return [
            _target_weight_decision(
                symbol=symbol,
                target=target,
                confidence=1.0,
                rationale="equal-weight periodic rebalance baseline",
                metadata={
                    "strategy": self.name,
                    "target_gross": self.target_gross,
                    "max_long_weight": self.max_long_weight,
                },
            )
            for symbol in symbols
        ]


@dataclass
class AlwaysHoldStrategy:
    name: str = "always-hold"

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        return [
            Decision(
                symbol=symbol,
                side=Side.HOLD,
                target_weight=0.0,
                confidence=1.0,
                rationale="always-hold lower-anchor baseline",
                metadata={"strategy": self.name},
            )
            for symbol in snapshot.bars
        ]


@dataclass
class RandomAllocationStrategy:
    seed: int = 7
    max_long_weight: float = 0.35
    cash_probability: float = 0.20
    name: str = "random-allocation"
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        symbols = sorted(snapshot.bars)
        if not symbols or self._rng.random() < self.cash_probability:
            weights = dict.fromkeys(symbols, 0.0)
        else:
            scores = {symbol: self._rng.random() for symbol in symbols}
            weights = _normalize_capped(scores, self.max_long_weight)
        return [
            _target_weight_decision(
                symbol=symbol,
                target=weights.get(symbol, 0.0),
                confidence=0.25 if weights.get(symbol, 0.0) > 0 else 0.0,
                rationale="random allocation lower-anchor baseline",
                metadata={
                    "strategy": self.name,
                    "seed": self.seed,
                    "max_long_weight": self.max_long_weight,
                    "cash_probability": self.cash_probability,
                },
            )
            for symbol in symbols
        ]


@dataclass
class NaiveMomentumStrategy:
    """Long-only rolling return baseline with no LLM or text inputs."""

    lookback: int = 5
    max_long_weight: float = 0.35
    name: str = "naive-momentum-strategy"
    _history: dict[str, deque[float]] = field(default_factory=dict)

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        symbols = sorted(snapshot.bars)
        for symbol in symbols:
            self._history.setdefault(symbol, deque(maxlen=self.lookback + 1)).append(float(snapshot.bars[symbol].close))

        scores = {
            symbol: max(0.0, self._rolling_return(symbol))
            for symbol in symbols
        }
        weights = _normalize_capped(scores, self.max_long_weight)
        return [
            _target_weight_decision(
                symbol=symbol,
                target=weights.get(symbol, 0.0),
                confidence=min(1.0, len(self._history.get(symbol, ())) / max(1, self.lookback + 1)),
                rationale="naive rolling-return momentum baseline",
                metadata={
                    "strategy": self.name,
                    "lookback": self.lookback,
                    "rolling_return": self._rolling_return(symbol),
                    "max_long_weight": self.max_long_weight,
                },
            )
            for symbol in symbols
        ]

    def _rolling_return(self, symbol: str) -> float:
        history = list(self._history.get(symbol, ()))
        if len(history) < 2 or history[0] <= 0:
            return 0.0
        return (history[-1] / history[0]) - 1.0


@dataclass
class MeanReversionStrategy:
    """Long-only contrarian baseline that buys recent underperformers."""

    lookback: int = 5
    max_long_weight: float = 0.35
    name: str = "mean-reversion-strategy"
    _history: dict[str, deque[float]] = field(default_factory=dict)

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        symbols = sorted(snapshot.bars)
        for symbol in symbols:
            self._history.setdefault(symbol, deque(maxlen=self.lookback + 1)).append(float(snapshot.bars[symbol].close))

        returns = {symbol: self._rolling_return(symbol) for symbol in symbols}
        scores = {symbol: max(0.0, -value) for symbol, value in returns.items()}
        weights = _normalize_capped(scores, self.max_long_weight)
        return [
            _target_weight_decision(
                symbol=symbol,
                target=weights.get(symbol, 0.0),
                confidence=min(1.0, len(self._history.get(symbol, ())) / max(1, self.lookback + 1)),
                rationale="naive mean-reversion baseline using recent underperformance",
                metadata={
                    "strategy": self.name,
                    "lookback": self.lookback,
                    "rolling_return": returns.get(symbol, 0.0),
                    "max_long_weight": self.max_long_weight,
                },
            )
            for symbol in symbols
        ]

    def _rolling_return(self, symbol: str) -> float:
        history = list(self._history.get(symbol, ()))
        if len(history) < 2 or history[0] <= 0:
            return 0.0
        return (history[-1] / history[0]) - 1.0


@dataclass
class SMACrossoverStrategy:
    """Deterministic long-only simple moving average crossover baseline."""

    fast_window: int = 3
    slow_window: int = 8
    max_long_weight: float = 0.35
    name: str = "sma-crossover-strategy"
    _history: dict[str, deque[float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.fast_window <= 0:
            raise ValueError("fast_window must be positive")
        if self.slow_window <= 0:
            raise ValueError("slow_window must be positive")
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be smaller than slow_window")

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        symbols = sorted(snapshot.bars)
        decisions: list[Decision] = []
        for symbol in symbols:
            history = self._history.setdefault(symbol, deque(maxlen=self.slow_window))
            history.append(float(snapshot.bars[symbol].close))
            prices = list(history)
            fast_sma = self._average(prices[-self.fast_window :]) if len(prices) >= self.fast_window else 0.0
            slow_sma = self._average(prices) if len(prices) >= self.slow_window else 0.0
            has_signal = len(prices) >= self.slow_window and fast_sma > slow_sma
            target = self.max_long_weight if has_signal else 0.0
            decisions.append(
                _target_weight_decision(
                    symbol=symbol,
                    target=target,
                    confidence=1.0 if has_signal else 0.0,
                    rationale=(
                        "fast SMA crossed above slow SMA"
                        if has_signal
                        else "SMA crossover baseline is warming up or below slow average"
                    ),
                    metadata={
                        "strategy": self.name,
                        "fast_window": self.fast_window,
                        "slow_window": self.slow_window,
                        "fast_sma": fast_sma,
                        "slow_sma": slow_sma,
                        "price_history_length": len(prices),
                        "max_long_weight": self.max_long_weight,
                    },
                )
            )
        return decisions

    def _average(self, values: list[float]) -> float:
        if not values:
            return 0.0
        return sum(values) / len(values)


@dataclass
class RiskParityStrategy:
    """Rolling inverse-volatility allocation baseline."""

    lookback: int = 12
    max_long_weight: float = 0.35
    volatility_floor: float = 1e-4
    name: str = "risk-parity-strategy"
    _history: dict[str, deque[float]] = field(default_factory=dict)

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        symbols = sorted(snapshot.bars)
        for symbol in symbols:
            self._history.setdefault(symbol, deque(maxlen=self.lookback + 1)).append(float(snapshot.bars[symbol].close))

        vols = {symbol: self._realized_volatility(symbol) for symbol in symbols}
        if sum(vols.values()) <= 0:
            weights = _equal_capped_weights(symbols, self.max_long_weight)
        else:
            scores = {symbol: 1.0 / max(self.volatility_floor, vol) for symbol, vol in vols.items()}
            weights = _normalize_capped(scores, self.max_long_weight)
        return [
            _target_weight_decision(
                symbol=symbol,
                target=weights.get(symbol, 0.0),
                confidence=min(1.0, len(self._history.get(symbol, ())) / max(1, self.lookback + 1)),
                rationale="rolling inverse-volatility risk-parity baseline",
                metadata={
                    "strategy": self.name,
                    "lookback": self.lookback,
                    "realized_volatility": vols.get(symbol, 0.0),
                    "max_long_weight": self.max_long_weight,
                },
            )
            for symbol in symbols
        ]

    def _realized_volatility(self, symbol: str) -> float:
        history = list(self._history.get(symbol, ()))
        returns = [
            (curr / prev) - 1.0
            for prev, curr in zip(history, history[1:])
            if prev > 0
        ]
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        return sqrt(sum((value - mean) ** 2 for value in returns) / max(1, len(returns) - 1))


@dataclass
class NoTradeBandStrategy:
    """Cost-aware wrapper around a base allocation strategy.

    Implements the standard transaction-cost heuristic: only rebalance toward the
    base target when it deviates from the currently held weight by more than
    ``band``; otherwise hold the current position (no trade). This suppresses the
    small, frequent, friction-expensive rebalances that an unconstrained
    optimizer or risk-parity allocator would make every bar, without changing the
    target it aims for. Defaults wrap inverse-volatility risk parity.
    """

    base: object = field(default_factory=RiskParityStrategy)
    band: float = 0.05
    name: str = "no-trade-band-strategy"

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        base_decisions = self.base.decide(snapshot, signals, portfolio, memory)
        decisions: list[Decision] = []
        for decision in base_decisions:
            current = portfolio.weight(decision.symbol)
            metadata = dict(decision.metadata)
            metadata.update(
                {
                    "strategy": self.name,
                    "no_trade_band": self.band,
                    "base_strategy": getattr(self.base, "name", "base"),
                    "base_target_weight": decision.target_weight,
                }
            )
            if abs(decision.target_weight - current) <= self.band:
                decisions.append(
                    Decision(
                        symbol=decision.symbol,
                        side=Side.HOLD,
                        target_weight=current,
                        confidence=decision.confidence,
                        rationale=f"within no-trade band ({self.band:.2f}); hold to save turnover",
                        metadata=metadata,
                    )
                )
            else:
                decisions.append(
                    Decision(
                        symbol=decision.symbol,
                        side=decision.side,
                        target_weight=decision.target_weight,
                        confidence=decision.confidence,
                        rationale=f"rebalance beyond no-trade band ({self.band:.2f}): {decision.rationale}",
                        metadata=metadata,
                    )
                )
        return decisions


@dataclass
class MeanVarianceStrategy:
    """Classical rolling Markowitz minimum-variance baseline.

    The strategy deliberately uses only realized prices from the current
    trajectory. It is included as a non-LLM quant baseline for experiments that
    compare language-agent behavior against covariance-driven allocation.
    """

    lookback: int = 24
    max_long_weight: float = 0.08
    ridge: float = 1e-4
    name: str = "mean-variance-strategy"
    _history: dict[str, deque[float]] = field(default_factory=dict)

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        symbols = sorted(snapshot.bars)
        for symbol in symbols:
            history = self._history.setdefault(symbol, deque(maxlen=self.lookback + 1))
            history.append(float(snapshot.bars[symbol].close))

        weights = self._weights(symbols)
        decisions: list[Decision] = []
        for symbol in symbols:
            target = weights.get(symbol, 0.0)
            side = Side.BUY if target > 1e-6 else Side.HOLD
            decisions.append(
                Decision(
                    symbol=symbol,
                    side=side,
                    target_weight=target,
                    confidence=1.0 if target > 0 else 0.0,
                    rationale="rolling Markowitz minimum-variance allocation using realized covariance only",
                    metadata={
                        "strategy": self.name,
                        "lookback": self.lookback,
                        "ridge": self.ridge,
                        "max_long_weight": self.max_long_weight,
                    },
                )
            )
        return decisions

    def _weights(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        usable = [symbol for symbol in symbols if len(self._history.get(symbol, ())) >= min(self.lookback + 1, 3)]
        if len(usable) < 2:
            return self._equal_weight(symbols)

        returns_by_symbol: dict[str, list[float]] = {}
        min_len = self.lookback
        for symbol in usable:
            prices = list(self._history[symbol])
            returns = []
            for prev, curr in zip(prices, prices[1:]):
                if prev > 0:
                    returns.append((curr / prev) - 1.0)
            if returns:
                returns_by_symbol[symbol] = returns[-self.lookback :]
                min_len = min(min_len, len(returns_by_symbol[symbol]))
        if len(returns_by_symbol) < 2 or min_len < 2:
            return self._equal_weight(symbols)

        ordered = sorted(returns_by_symbol)
        matrix = [returns_by_symbol[symbol][-min_len:] for symbol in ordered]
        covariance = self._covariance(matrix)
        for idx in range(len(covariance)):
            covariance[idx][idx] += self.ridge

        solution = self._solve(covariance, [1.0] * len(ordered))
        raw = [max(0.0, value) if isfinite(value) else 0.0 for value in solution]
        if sum(raw) <= 1e-12:
            return self._equal_weight(symbols)
        normalized = [value / sum(raw) for value in raw]
        capped = [min(self.max_long_weight, value) for value in normalized]
        total = sum(capped)
        if total <= 1e-12:
            return self._equal_weight(symbols)
        weights = {symbol: value / total for symbol, value in zip(ordered, capped)}
        return {symbol: min(self.max_long_weight, weights.get(symbol, 0.0)) for symbol in symbols}

    def _equal_weight(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        target = min(self.max_long_weight, 1.0 / len(symbols))
        return dict.fromkeys(symbols, target)

    def _covariance(self, series: list[list[float]]) -> list[list[float]]:
        means = [sum(values) / len(values) for values in series]
        covariance: list[list[float]] = []
        denominator = max(1, len(series[0]) - 1)
        for left, left_mean in zip(series, means):
            row = []
            for right, right_mean in zip(series, means):
                row.append(sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right)) / denominator)
            covariance.append(row)
        return covariance

    def _solve(self, matrix: list[list[float]], vector: list[float]) -> list[float]:
        n = len(vector)
        augmented = [row[:] + [rhs] for row, rhs in zip(matrix, vector)]
        for col in range(n):
            pivot = max(range(col, n), key=lambda row: abs(augmented[row][col]))
            if abs(augmented[pivot][col]) < 1e-12:
                return [1.0 / n] * n
            augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
            scale = augmented[col][col]
            augmented[col] = [value / scale for value in augmented[col]]
            for row in range(n):
                if row == col:
                    continue
                factor = augmented[row][col]
                augmented[row] = [value - factor * pivot_value for value, pivot_value in zip(augmented[row], augmented[col])]
        return [augmented[row][-1] for row in range(n)]


@dataclass
class MarkowitzMVOStrategy(MeanVarianceStrategy):
    """Rolling long-only Markowitz mean-variance optimization baseline."""

    risk_aversion: float = 10.0
    min_expected_return: float = 0.0
    name: str = "markowitz-mvo-strategy"

    def _weights(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        usable = [symbol for symbol in symbols if len(self._history.get(symbol, ())) >= min(self.lookback + 1, 3)]
        if len(usable) < 2:
            return self._equal_weight(symbols)

        returns_by_symbol: dict[str, list[float]] = {}
        min_len = self.lookback
        for symbol in usable:
            prices = list(self._history[symbol])
            returns = [(curr / prev) - 1.0 for prev, curr in zip(prices, prices[1:]) if prev > 0]
            if returns:
                returns_by_symbol[symbol] = returns[-self.lookback :]
                min_len = min(min_len, len(returns_by_symbol[symbol]))
        if len(returns_by_symbol) < 2 or min_len < 2:
            return self._equal_weight(symbols)

        ordered = sorted(returns_by_symbol)
        matrix = [returns_by_symbol[symbol][-min_len:] for symbol in ordered]
        expected = [sum(values) / len(values) for values in matrix]
        signal = [max(self.min_expected_return, value) for value in expected]
        if sum(signal) <= 1e-12:
            return self._equal_weight(symbols)

        covariance = self._covariance(matrix)
        risk_scale = max(1e-9, float(self.risk_aversion))
        for idx in range(len(covariance)):
            covariance[idx][idx] = covariance[idx][idx] * risk_scale + self.ridge
            for col in range(len(covariance)):
                if col != idx:
                    covariance[idx][col] *= risk_scale

        solution = self._solve(covariance, signal)
        scores = {symbol: max(0.0, value) if isfinite(value) else 0.0 for symbol, value in zip(ordered, solution)}
        if sum(scores.values()) <= 1e-12:
            return self._equal_weight(symbols)
        weights = _normalize_capped(scores, self.max_long_weight)
        return {symbol: weights.get(symbol, 0.0) for symbol in symbols}

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        decisions = super().decide(snapshot, signals, portfolio, memory)
        adjusted = []
        for decision in decisions:
            metadata = dict(decision.metadata)
            metadata.update(
                {
                    "strategy": self.name,
                    "risk_aversion": self.risk_aversion,
                    "min_expected_return": self.min_expected_return,
                }
            )
            adjusted.append(
                Decision(
                    symbol=decision.symbol,
                    side=decision.side,
                    target_weight=decision.target_weight,
                    confidence=decision.confidence,
                    rationale="rolling Markowitz mean-variance allocation using realized returns and covariance",
                    metadata=metadata,
                )
            )
        return adjusted


@dataclass
class MemoryAwareSignalWeightedStrategy:
    """Signal-weighted strategy with an audit-memory exposure overlay."""

    base: SignalWeightedStrategy = field(default_factory=SignalWeightedStrategy)
    lookback_events: int = 5
    memory_decay_rate: float = 0.85
    drawdown_threshold: float = -0.015
    risk_off_scale: float = 0.65
    recovery_boost: float = 1.08
    name: str = "memory-aware-signal-weighted-strategy"

    def decide(self, snapshot: MarketSnapshot, signals: list[Signal], portfolio: PortfolioState, memory: object) -> list[Decision]:
        decisions = self.base.decide(snapshot, signals, portfolio, memory)
        memory_state = self._memory_state(memory)
        scale = float(memory_state["scale"])
        reason = str(memory_state["reason"])
        adjusted: list[Decision] = []
        for decision in decisions:
            base_target = decision.target_weight
            target = base_target
            if decision.side != Side.HOLD:
                target *= scale
            final_target = target if abs(target) > self.base.deadband else 0.0
            amplification = abs(final_target) / abs(base_target) if abs(base_target) > 1e-12 else 0.0
            metadata = dict(decision.metadata)
            metadata.update(
                {
                    "strategy": self.name,
                    "memory_scale": scale,
                    "memory_reason": reason,
                    "memory_decay_rate": memory_state["memory_decay_rate"],
                    "memory_pollution_ratio": memory_state["memory_pollution_ratio"],
                    "memory_base_target_weight": base_target,
                    "memory_adjusted_target_weight": final_target,
                    "memory_driven_leverage_amplification": amplification,
                }
            )
            adjusted.append(
                Decision(
                    symbol=decision.symbol,
                    side=decision.side if abs(final_target) > self.base.deadband else Side.HOLD,
                    target_weight=final_target,
                    confidence=decision.confidence,
                    rationale=f"{decision.rationale} | memory overlay: {reason}",
                    metadata=metadata,
                )
            )
        return adjusted

    def _memory_scale(self, memory: object) -> tuple[float, str]:
        state = self._memory_state(memory)
        return float(state["scale"]), str(state["reason"])

    def _memory_state(self, memory: object) -> dict[str, float | str]:
        decay_rate = self._decay_rate()
        recent_fn = getattr(memory, "recent", None)
        if not callable(recent_fn):
            return self._empty_memory_state("memory unavailable", decay_rate)
        recent = recent_fn("step", self.lookback_events)
        if len(recent) < 2:
            return self._empty_memory_state("insufficient memory", decay_rate)

        equities: list[float] = []
        total_weight = 0.0
        polluted_weight = 0.0
        weighted_rejections = 0.0
        weighted_violations = 0.0
        for index, event in enumerate(recent):
            weight = decay_rate ** (len(recent) - index - 1)
            total_weight += weight
            payload = event.get("payload", {}) if isinstance(event, dict) else {}
            if not isinstance(payload, dict):
                payload = {}
            equity = payload.get("equity")
            has_equity = isinstance(equity, (int, float)) and isfinite(float(equity))
            if has_equity:
                equities.append(float(equity))
            execution_report = payload.get("execution_report", {})
            rejected_orders = 0
            if isinstance(execution_report, dict):
                rejected_orders = int(execution_report.get("rejected_orders", 0) or 0)
            raw_violations = payload.get("risk_violations", []) or []
            risk_violations = len(raw_violations) if isinstance(raw_violations, (list, tuple)) else int(bool(raw_violations))
            explicit_pollution = bool(payload.get("memory_pollution") or payload.get("polluted"))
            polluted = explicit_pollution or not has_equity or rejected_orders > 0 or risk_violations > 0
            if polluted:
                polluted_weight += weight
            weighted_rejections += weight * rejected_orders
            weighted_violations += weight * risk_violations

        if len(equities) >= 2 and equities[0]:
            equity_change = (equities[-1] / equities[0]) - 1.0
        else:
            equity_change = 0.0

        denominator = max(total_weight, 1e-12)
        pollution_ratio = min(1.0, max(0.0, polluted_weight / denominator))
        rejection_pressure = weighted_rejections / denominator
        violation_pressure = weighted_violations / denominator

        if equity_change <= self.drawdown_threshold or rejection_pressure > 1.0 or violation_pressure > 0:
            scale = self.risk_off_scale
            reason = f"risk-off after recent drawdown/rejections ({equity_change:.3f})"
        elif equity_change > abs(self.drawdown_threshold) and rejection_pressure == 0 and violation_pressure == 0:
            scale = self.recovery_boost
            reason = f"modest recovery boost ({equity_change:.3f})"
        else:
            scale = 1.0
            reason = f"neutral memory state ({equity_change:.3f})"

        if pollution_ratio > 0.0:
            if scale > 1.0:
                scale = 1.0 + (scale - 1.0) * (1.0 - pollution_ratio)
            else:
                scale *= 1.0 - (0.5 * pollution_ratio)
            reason = f"{reason}; memory pollution {pollution_ratio:.3f}"

        return {
            "scale": scale,
            "reason": reason,
            "memory_decay_rate": decay_rate,
            "memory_pollution_ratio": pollution_ratio,
            "memory_rejection_pressure": rejection_pressure,
            "memory_violation_pressure": violation_pressure,
        }

    def _empty_memory_state(self, reason: str, decay_rate: float) -> dict[str, float | str]:
        return {
            "scale": 1.0,
            "reason": reason,
            "memory_decay_rate": decay_rate,
            "memory_pollution_ratio": 0.0,
            "memory_rejection_pressure": 0.0,
            "memory_violation_pressure": 0.0,
        }

    def _decay_rate(self) -> float:
        try:
            parsed = float(self.memory_decay_rate)
        except (TypeError, ValueError):
            return 0.85
        if not isfinite(parsed):
            return 0.85
        return min(1.0, max(0.0, parsed))


def _target_weight_decision(
    *,
    symbol: str,
    target: float,
    confidence: float,
    rationale: str,
    metadata: dict[str, float | int | str],
) -> Decision:
    return Decision(
        symbol=symbol,
        side=Side.BUY if target > 1e-6 else Side.HOLD,
        target_weight=target if target > 1e-6 else 0.0,
        confidence=confidence if target > 1e-6 else 0.0,
        rationale=rationale,
        metadata=metadata,
    )


def _normalize_capped(scores: dict[str, float], max_long_weight: float) -> dict[str, float]:
    positive = {symbol: max(0.0, value) for symbol, value in scores.items()}
    total = sum(positive.values())
    if total <= 1e-12:
        return dict.fromkeys(scores, 0.0)
    weights = {symbol: value / total for symbol, value in positive.items()}
    return {symbol: min(max_long_weight, value) for symbol, value in weights.items()}


def _equal_capped_weights(symbols: list[str], max_long_weight: float) -> dict[str, float]:
    if not symbols:
        return {}
    target = min(max_long_weight, 1.0 / len(symbols))
    return dict.fromkeys(symbols, target)
