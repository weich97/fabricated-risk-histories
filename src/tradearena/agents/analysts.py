from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from tradearena.core.domain import MarketSnapshot, PortfolioState, Signal


@dataclass
class MomentumAnalyst:
    lookback: int = 10
    name: str = "momentum-analyst"
    _history: dict[str, deque[float]] = field(default_factory=dict)

    def analyze(self, snapshot: MarketSnapshot, portfolio: PortfolioState, memory: object) -> list[Signal]:
        signals: list[Signal] = []
        for symbol, bar in snapshot.bars.items():
            history = self._history.setdefault(symbol, deque(maxlen=self.lookback + 1))
            history.append(bar.close)
            if len(history) <= self.lookback:
                score = 0.0
                confidence = 0.1
                rationale = "insufficient momentum history"
            else:
                start = history[0]
                score = (bar.close / start) - 1.0
                confidence = min(1.0, abs(score) * 8.0 + 0.2)
                rationale = f"{self.lookback}-bar return is {score:.4f}"

            signals.append(
                Signal(
                    symbol=symbol,
                    score=score,
                    horizon=f"{self.lookback}d",
                    confidence=confidence,
                    rationale=rationale,
                    metadata={"analyst": self.name, "feature": "close_to_close_momentum"},
                )
            )
        return signals


@dataclass
class MacroNewsAnalyst:
    name: str = "macro-news-analyst"

    def analyze(self, snapshot: MarketSnapshot, portfolio: PortfolioState, memory: object) -> list[Signal]:
        macro_score = sum(point.value for point in snapshot.macro if "growth" in point.name)
        sentiment_by_symbol = dict.fromkeys(snapshot.bars, 0.0)
        for item in snapshot.news:
            for symbol in item.symbols:
                if symbol in sentiment_by_symbol:
                    sentiment_by_symbol[symbol] += item.sentiment
        filing_by_symbol = dict.fromkeys(snapshot.bars, 0.0)
        for item in getattr(snapshot, "filings", ()):
            for symbol in item.symbols:
                if symbol in filing_by_symbol:
                    filing_by_symbol[symbol] += item.sentiment
        alt_by_symbol = {symbol: _alternative_signal(snapshot.alt_data.get(symbol, {})) for symbol in snapshot.bars}

        signals = []
        for symbol in snapshot.bars:
            score = (
                0.04 * macro_score
                + 0.25 * sentiment_by_symbol[symbol]
                + 0.20 * filing_by_symbol[symbol]
                + 0.05 * alt_by_symbol[symbol]
            )
            signals.append(
                Signal(
                    symbol=symbol,
                    score=score,
                    horizon="event",
                    confidence=min(1.0, 0.25 + abs(score) * 2.0),
                    rationale=(
                        f"macro={macro_score:.3f}, news={sentiment_by_symbol[symbol]:.3f}, "
                        f"filings={filing_by_symbol[symbol]:.3f}, alt={alt_by_symbol[symbol]:.3f}"
                    ),
                    metadata={"analyst": self.name, "feature": "macro_news_filings_alt_blend"},
                )
            )
        return signals


def _alternative_signal(payload: object) -> float:
    values: list[float] = []
    if isinstance(payload, dict):
        for item in payload.values():
            if isinstance(item, dict):
                value = item.get("value")
            else:
                value = item
            try:
                values.append(max(-1.0, min(1.0, float(value))))
            except (TypeError, ValueError):
                continue
    return sum(values)
