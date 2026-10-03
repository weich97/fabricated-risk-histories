from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from tradearena.core.domain import MarketSnapshot


@dataclass
class RollingFeatureStore:
    window: int = 20
    name: str = "rolling-feature-store"
    closes: dict[str, deque[float]] = field(default_factory=dict)

    def update(self, snapshot: MarketSnapshot) -> dict[str, dict[str, float]]:
        features: dict[str, dict[str, float]] = {}
        for symbol, bar in snapshot.bars.items():
            series = self.closes.setdefault(symbol, deque(maxlen=self.window))
            series.append(bar.close)
            values = list(series)
            mean = sum(values) / len(values)
            features[symbol] = {
                "close": bar.close,
                "rolling_mean": mean,
                "distance_to_mean": 0.0 if mean == 0 else (bar.close / mean) - 1.0,
                "observations": float(len(values)),
            }
        return features
