from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from tradearena.core.domain import Bar, MacroPoint, MarketSnapshot, NewsItem


@dataclass
class SyntheticMarketDataProvider:
    """Deterministic multi-source scenario generator for benchmarks."""

    symbols: tuple[str, ...] = ("SYN",)
    periods: int = 120
    seed: int = 7
    start: datetime = datetime(2024, 1, 1)
    volatility_scale: float = 1.0
    trend_scale: float = 1.0
    seasonal_scale: float = 1.0
    macro_scale: float = 1.0
    tail_df: int | None = None
    jump_probability: float = 0.0
    jump_scale: float = 0.0
    name: str = "synthetic-market"

    def stream(self) -> list[MarketSnapshot]:
        rng = random.Random(self.seed)
        prices = {symbol: 100.0 + idx * 15 for idx, symbol in enumerate(self.symbols)}
        snapshots: list[MarketSnapshot] = []

        for step in range(self.periods):
            timestamp = self.start + timedelta(days=step)
            bars: dict[str, Bar] = {}
            news: list[NewsItem] = []

            macro_cycle = math.sin(step / 18.0)
            macro = (
                MacroPoint(timestamp=timestamp, name="synthetic_growth", value=macro_cycle),
                MacroPoint(timestamp=timestamp, name="synthetic_rates", value=0.03 + 0.01 * math.cos(step / 32.0)),
            )

            for idx, symbol in enumerate(self.symbols):
                trend = self.trend_scale * 0.0008 * (idx + 1)
                seasonal = self.seasonal_scale * 0.012 * math.sin((step + idx * 4) / 9.0)
                shock = self.volatility_scale * _draw_shock(rng, self.tail_df)
                if self.jump_probability > 0.0 and rng.random() < self.jump_probability:
                    shock += rng.choice([-1.0, 1.0]) * abs(rng.gauss(self.jump_scale, self.jump_scale / 2.0))
                news_sentiment = 0.0
                if step % 17 == idx % 5:
                    news_sentiment = rng.choice([-0.7, -0.35, 0.35, 0.7])
                    news.append(
                        NewsItem(
                            timestamp=timestamp,
                            source="synthetic-wire",
                            title=f"{symbol} synthetic event",
                            body=f"Generated event with sentiment {news_sentiment:.2f}.",
                            sentiment=news_sentiment,
                            symbols=(symbol,),
                        )
                    )

                daily_return = trend + seasonal + self.macro_scale * 0.006 * macro_cycle + 0.01 * news_sentiment + shock
                open_price = prices[symbol]
                close = max(1.0, open_price * (1.0 + daily_return))
                high = max(open_price, close) * (1.0 + abs(rng.gauss(0.003, 0.002)))
                low = min(open_price, close) * (1.0 - abs(rng.gauss(0.003, 0.002)))
                volume = 1_000_000 * (1.0 + abs(shock) * 10 + abs(news_sentiment))
                prices[symbol] = close

                bars[symbol] = Bar(
                    symbol=symbol,
                    timestamp=timestamp,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )

            snapshots.append(MarketSnapshot(timestamp=timestamp, bars=bars, news=tuple(news), macro=macro))

        return snapshots


def _draw_shock(rng: random.Random, tail_df: int | None) -> float:
    if tail_df is None:
        return rng.gauss(0.0, 0.01)
    df = max(3, int(tail_df))
    numerator = rng.gauss(0.0, 1.0)
    denominator = math.sqrt(sum(rng.gauss(0.0, 1.0) ** 2 for _ in range(df)) / df)
    return 0.01 * numerator / denominator
