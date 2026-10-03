from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tradearena.core.runner import TradeArena


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    build_system: Callable[[], TradeArena]
    description: str = ""


@dataclass
class BenchmarkRunner:
    cases: list[BenchmarkCase]

    def run(self) -> dict[str, dict[str, float | int | str]]:
        results: dict[str, dict[str, float | int | str]] = {}
        for case in self.cases:
            _, metrics = case.build_system().run()
            results[case.name] = metrics
        return results
