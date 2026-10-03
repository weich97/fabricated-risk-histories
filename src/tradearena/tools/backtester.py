from __future__ import annotations

from dataclasses import dataclass

from tradearena.core.runner import TradeArena
from tradearena.core.trajectory import Trajectory


@dataclass(frozen=True)
class BacktestResult:
    trajectory: Trajectory
    metrics: dict[str, float | int | str]


@dataclass
class Backtester:
    name: str = "backtester"

    def run(self, system: TradeArena) -> BacktestResult:
        trajectory, metrics = system.run()
        return BacktestResult(trajectory=trajectory, metrics=metrics)
