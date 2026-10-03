from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DataLeakagePolicy:
    """Benchmark metadata for time split and contamination control."""

    train_end: str | None = None
    validation_end: str | None = None
    test_start: str | None = None
    forbid_future_news: bool = True
    freeze_prompts: bool = True
    freeze_memory_at_test_start: bool = True
    notes: str = ""


@dataclass(frozen=True)
class BenchmarkTask:
    name: str
    universe: tuple[str, ...]
    task_type: str
    horizon: str
    objective: str
    leakage_policy: DataLeakagePolicy = field(default_factory=DataLeakagePolicy)
    metrics: tuple[str, ...] = (
        "total_return",
        "sharpe",
        "max_drawdown",
        "execution_fill_rate",
        "total_commission",
        "total_slippage_cost",
        "risk_audit_coverage",
        "reasoning_consistency",
    )
    description: str = ""


TRADEARENA_CORE_TASKS: tuple[BenchmarkTask, ...] = (
    BenchmarkTask(
        name="single_asset_tactical_trading",
        universe=("SYN",),
        task_type="single-stock trading",
        horizon="daily",
        objective="maximize risk-adjusted return under realistic execution",
        description="Single instrument scenario for basic action and risk-gate evaluation.",
    ),
    BenchmarkTask(
        name="multi_asset_portfolio_allocation",
        universe=("SYN", "ALT", "DEF"),
        task_type="portfolio allocation",
        horizon="daily",
        objective="allocate capital across assets while controlling gross exposure and trading costs",
        description="Portfolio task for turnover, concentration, and execution-cost analysis.",
    ),
    BenchmarkTask(
        name="event_driven_news_reaction",
        universe=("SYN", "ALT"),
        task_type="event-driven trading",
        horizon="daily",
        objective="react to timestamped news without using future information",
        description="News reaction task focused on leakage control and reasoning auditability.",
    ),
)
