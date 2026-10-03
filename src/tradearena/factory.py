from __future__ import annotations

import os

from tradearena.agents import (
    AlwaysHoldStrategy,
    BuyAndHoldStrategy,
    DeepSeekLLMAnalyst,
    DeterministicRLAllocationStrategy,
    EqualWeightStrategy,
    MacroNewsAnalyst,
    MarkowitzMVOStrategy,
    MaxPositionRiskManager,
    MeanReversionStrategy,
    MeanVarianceStrategy,
    MemoryAwareSignalWeightedStrategy,
    MomentumAnalyst,
    NaiveMomentumStrategy,
    NoRiskManager,
    NoTradeBandStrategy,
    RandomAllocationStrategy,
    RiskParityStrategy,
    SignalWeightedStrategy,
    SMACrossoverStrategy,
    TargetWeightExecutionAgent,
)
from tradearena.core.domain import ExperimentConfig
from tradearena.core.registry import PluginRegistry
from tradearena.core.runner import TradeArena
from tradearena.data import CsvMarketDataProvider, SyntheticMarketDataProvider
from tradearena.evaluation import (
    BehavioralEvaluator,
    DecisionQualityEvaluator,
    ExecutionRealismEvaluator,
    IntentExecutionGapEvaluator,
    PerformanceEvaluator,
    ReasoningConsistencyEvaluator,
    RiskAuditEvaluator,
)
from tradearena.memory import (
    InMemoryResearchMemory,
    JournalVerifiedMemory,
    PollutedResearchMemory,
    PollutionConfig,
)
from tradearena.tools import (
    CalibratedOrderSimulator,
    FillReplayOrderSimulator,
    QuoteReplayOrderSimulator,
    RealisticOrderSimulator,
    SimpleOrderSimulator,
)

POE_MODEL_API_ALIASES = {
    "gpt-5.5": "gpt-5.5",
    "gemini-3.1-pro": "gemini-3.1-pro",
    "kimi-k2.5": "kimi-k2.5",
    "glm-5": "glm-5",
    "claude-opus-4.7": "claude-opus-4.7",
}


def default_registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.register("data", "synthetic-market", SyntheticMarketDataProvider)
    registry.register("data", "csv-market", CsvMarketDataProvider)
    registry.register("analyst", "momentum", MomentumAnalyst)
    registry.register("analyst", "macro-news", MacroNewsAnalyst)
    registry.register("strategy", "signal-weighted", SignalWeightedStrategy)
    registry.register("strategy", "memory-aware", MemoryAwareSignalWeightedStrategy)
    registry.register("strategy", "buy-and-hold", BuyAndHoldStrategy)
    registry.register("strategy", "equal-weight", EqualWeightStrategy)
    registry.register("strategy", "always-hold", AlwaysHoldStrategy)
    registry.register("strategy", "random-allocation", RandomAllocationStrategy)
    registry.register("strategy", "naive-momentum", NaiveMomentumStrategy)
    registry.register("strategy", "mean-reversion", MeanReversionStrategy)
    registry.register("strategy", "risk-parity", RiskParityStrategy)
    registry.register("strategy", "sma-crossover", SMACrossoverStrategy)
    registry.register("strategy", "mean-variance", MeanVarianceStrategy)
    registry.register("strategy", "markowitz-mvo", MarkowitzMVOStrategy)
    registry.register("strategy", "no-trade-band", NoTradeBandStrategy)
    registry.register("strategy", "mock-rl-policy", DeterministicRLAllocationStrategy)
    registry.register("risk", "max-position", MaxPositionRiskManager)
    registry.register("risk", "none", NoRiskManager)
    registry.register("execution", "target-weight", TargetWeightExecutionAgent)
    registry.register("simulator", "calibrated", CalibratedOrderSimulator)
    registry.register("simulator", "fill-replay", FillReplayOrderSimulator)
    registry.register("simulator", "quote-replay", QuoteReplayOrderSimulator)
    registry.register("simulator", "simple", SimpleOrderSimulator)
    registry.register("simulator", "realistic", RealisticOrderSimulator)
    registry.register("memory", "in-memory", InMemoryResearchMemory)
    registry.register("evaluator", "performance", PerformanceEvaluator)
    registry.register("evaluator", "behavioral", BehavioralEvaluator)
    registry.register("evaluator", "decision-quality", DecisionQualityEvaluator)
    registry.register("evaluator", "reasoning", ReasoningConsistencyEvaluator)
    registry.register("evaluator", "execution-realism", ExecutionRealismEvaluator)
    registry.register("evaluator", "risk-audit", RiskAuditEvaluator)
    return registry


def _build_memory(
    pollution_kind: str,
    pollution_dose: float,
    pollution_seed: int,
    loss_streak_length: int,
    pollution_defense: str = "",
) -> InMemoryResearchMemory | PollutedResearchMemory | JournalVerifiedMemory:
    memory = InMemoryResearchMemory()
    if not pollution_kind:
        return memory
    polluted = PollutedResearchMemory(
        base=memory,
        config=PollutionConfig(
            kind=pollution_kind,
            dose=pollution_dose,
            seed=pollution_seed,
            loss_streak_length=loss_streak_length,
        ),
    )
    if pollution_defense == "journal-verify":
        return JournalVerifiedMemory(inner=polluted)
    return polluted


def build_default_system(
    *,
    name: str = "momentum-macro-baseline",
    symbols: tuple[str, ...] = ("SYN",),
    periods: int = 120,
    seed: int = 7,
    initial_cash: float = 100_000.0,
    strategy_name: str = "signal-weighted",
    risk_name: str = "max-position",
    execution_mode: str = "realistic",
    commission_bps: float = 1.0,
    slippage_bps: float = 2.0,
    spread_bps: float = 0.0,
    participation_rate: float = 0.05,
    latency_steps: int = 1,
    market_impact: float = 0.15,
    execution_calibration_profile_id: str | None = None,
    execution_replay_fills_path: str | None = None,
    max_position_weight: float = 0.35,
    max_gross_exposure: float = 1.0,
    max_turnover: float = 0.75,
    max_drawdown: float = 0.20,
    drawdown_lookback: int = 5,
    drawdown_de_risk_weight: float = 0.0,
    memory_lookback_events: int = 5,
    memory_decay_rate: float = 0.85,
    memory_pollution_kind: str = "",
    memory_pollution_dose: float = 0.0,
    memory_pollution_seed: int = 0,
    memory_pollution_loss_streak_length: int = 3,
    memory_pollution_defense: str = "",
    analyst_names: tuple[str, ...] = ("momentum", "macro-news"),
    data_source: str = "synthetic",
    real_data_dir: str = "data/real/yahoo_daily_2021_2026",
    real_data_frequency: str = "daily",
    real_data_start: str | None = None,
    real_data_end: str | None = None,
    real_data_max_periods: int | None = None,
    real_data_window_offset: int = 0,
    real_news_path: str | None = None,
    real_macro_path: str | None = None,
    real_filings_path: str | None = None,
    real_alternative_data_path: str | None = None,
    llm_model: str = "deepseek-v4-flash",
    llm_cache_path: str = "data/llm_cache/deepseek_analyst.jsonl",
    llm_use_risk_feedback: bool = True,
    llm_risk_feedback_mode: str = "true",
    llm_output_mode: str = "rationale",
    llm_mask_timestamps: bool = False,
    llm_anonymize_symbols: bool = False,
    llm_sample_index: int = 0,
    synthetic_volatility_scale: float = 1.0,
    synthetic_trend_scale: float = 1.0,
    synthetic_seasonal_scale: float = 1.0,
    synthetic_macro_scale: float = 1.0,
    synthetic_tail_df: int | None = None,
    synthetic_jump_probability: float = 0.0,
    synthetic_jump_scale: float = 0.0,
    initial_holdings_weights: dict[str, float] | None = None,
    strategy_override: object | None = None,
) -> TradeArena:
    if strategy_override is not None:
        strategy = strategy_override  # type: ignore[assignment]
    elif strategy_name == "buy-and-hold":
        strategy = BuyAndHoldStrategy()
    elif strategy_name in {"equal-weight", "equal-weight-rebalance", "equal_weight"}:
        strategy = EqualWeightStrategy(max_long_weight=max_position_weight)
    elif strategy_name in {"always-hold", "hold", "cash"}:
        strategy = AlwaysHoldStrategy()
    elif strategy_name in {"random-allocation", "random", "noise"}:
        strategy = RandomAllocationStrategy(seed=seed, max_long_weight=max_position_weight)
    elif strategy_name in {"naive-momentum", "momentum-baseline"}:
        strategy = NaiveMomentumStrategy(max_long_weight=max_position_weight)
    elif strategy_name in {"mean-reversion", "contrarian"}:
        strategy = MeanReversionStrategy(max_long_weight=max_position_weight)
    elif strategy_name in {"risk-parity", "inverse-volatility"}:
        strategy = RiskParityStrategy(max_long_weight=max_position_weight)
    elif strategy_name in {"sma-crossover", "sma", "moving-average-crossover"}:
        strategy = SMACrossoverStrategy(max_long_weight=max_position_weight)
    elif strategy_name in {"mean-variance", "min-var", "minimum-variance"}:
        strategy = MeanVarianceStrategy(max_long_weight=max_position_weight)
    elif strategy_name in {"markowitz-mvo", "markowitz", "mvo", "mean-variance-optimization"}:
        strategy = MarkowitzMVOStrategy(max_long_weight=max_position_weight)
    elif strategy_name in {"no-trade-band", "no-trade-band-risk-parity", "cost-aware"}:
        strategy = NoTradeBandStrategy(base=RiskParityStrategy(max_long_weight=max_position_weight), band=0.05)
    elif strategy_name in {"mock-rl-policy", "rl-policy", "deep-rl"}:
        strategy = DeterministicRLAllocationStrategy(max_long_weight=max_position_weight)
    elif strategy_name == "memory-aware":
        strategy = MemoryAwareSignalWeightedStrategy(
            lookback_events=memory_lookback_events,
            memory_decay_rate=memory_decay_rate,
        )
    else:
        strategy = SignalWeightedStrategy()

    analysts = []
    for analyst_name in analyst_names:
        if analyst_name == "momentum":
            analysts.append(MomentumAnalyst())
        elif analyst_name == "macro-news":
            analysts.append(MacroNewsAnalyst())
        elif analyst_name in {"deepseek-llm", "chat-completions-llm"}:
            analysts.append(
                DeepSeekLLMAnalyst(
                    model=llm_model,
                    cache_path=llm_cache_path,
                    use_risk_feedback=llm_use_risk_feedback,
                    risk_feedback_mode=llm_risk_feedback_mode,
                    output_mode=llm_output_mode,
                    mask_timestamps=llm_mask_timestamps,
                    anonymize_symbols=llm_anonymize_symbols,
                    sample_index=llm_sample_index,
                )
            )
        elif analyst_name == "poe-llm":
            analysts.append(
                DeepSeekLLMAnalyst(
                    model=llm_model,
                    api_model=POE_MODEL_API_ALIASES.get(llm_model, llm_model),
                    cache_path=llm_cache_path,
                    api_key_env="POE_API_KEY",
                    fallback_api_key_env="",
                    api_base_url="https://api.poe.com/v1",
                    provider="poe",
                    api_protocol="openai_chat_completions",
                    thinking="",
                    use_response_format=False,
                    timeout_seconds=120,
                    use_risk_feedback=llm_use_risk_feedback,
                    risk_feedback_mode=llm_risk_feedback_mode,
                    output_mode=llm_output_mode,
                    mask_timestamps=llm_mask_timestamps,
                    anonymize_symbols=llm_anonymize_symbols,
                    sample_index=llm_sample_index,
                    name="poe-llm-analyst",
                )
            )
        elif analyst_name == "glm-llm":
            analysts.append(
                DeepSeekLLMAnalyst(
                    model=llm_model,
                    cache_path=llm_cache_path,
                    api_key_env="GLM_API_KEY",
                    fallback_api_key_env="",
                    api_base_url="https://open.bigmodel.cn/api/paas/v4",
                    provider="glm",
                    api_protocol="openai_chat_completions",
                    thinking="disabled",
                    use_response_format=False,
                    timeout_seconds=120,
                    use_risk_feedback=llm_use_risk_feedback,
                    risk_feedback_mode=llm_risk_feedback_mode,
                    output_mode=llm_output_mode,
                    mask_timestamps=llm_mask_timestamps,
                    anonymize_symbols=llm_anonymize_symbols,
                    sample_index=llm_sample_index,
                    name="glm-llm-analyst",
                )
            )
        elif analyst_name == "ollama-llm":
            analysts.append(
                DeepSeekLLMAnalyst(
                    model=llm_model,
                    cache_path=llm_cache_path,
                    api_key_env="TRADEARENA_OLLAMA_API_KEY",
                    fallback_api_key_env="",
                    api_base_url=os.environ.get("TRADEARENA_OLLAMA_BASE_URL", "http://localhost:11434/v1"),
                    provider="ollama",
                    api_protocol="openai_chat_completions",
                    thinking="",
                    use_response_format=False,
                    require_api_key=False,
                    use_risk_feedback=llm_use_risk_feedback,
                    risk_feedback_mode=llm_risk_feedback_mode,
                    output_mode=llm_output_mode,
                    mask_timestamps=llm_mask_timestamps,
                    anonymize_symbols=llm_anonymize_symbols,
                    sample_index=llm_sample_index,
                    name="ollama-llm-analyst",
                )
            )
        else:
            raise ValueError(f"Unknown analyst: {analyst_name}")
    risk_manager = (
        NoRiskManager()
        if risk_name == "none"
        else MaxPositionRiskManager(
            max_abs_weight=max_position_weight,
            max_gross_exposure=max_gross_exposure,
            max_single_step_turnover=max_turnover,
            max_drawdown=max_drawdown,
            drawdown_lookback=drawdown_lookback,
            drawdown_de_risk_weight=drawdown_de_risk_weight,
            max_order_participation=participation_rate,
            max_latency_steps=max(2, latency_steps + 1),
            max_slippage_bps=max(50.0, (slippage_bps + spread_bps / 2.0) * 10.0),
        )
    )
    if execution_mode == "ideal":
        simulator = SimpleOrderSimulator(commission_bps=commission_bps, slippage_bps=slippage_bps)
    elif execution_mode in {"calibrated", "calibrated-realistic"}:
        simulator = CalibratedOrderSimulator(
            commission_bps=commission_bps,
            base_slippage_bps=slippage_bps,
            spread_bps=spread_bps,
            participation_rate=participation_rate,
            latency_steps=latency_steps,
            market_impact=market_impact,
            calibration_profile_id=execution_calibration_profile_id or "external-calibration",
        )
    elif execution_mode in {"quote-replay", "level2-replay"}:
        simulator = QuoteReplayOrderSimulator(
            commission_bps=commission_bps,
            base_slippage_bps=slippage_bps,
            spread_bps=spread_bps,
            participation_rate=participation_rate,
            latency_steps=latency_steps,
            market_impact=market_impact,
            calibration_profile_id=execution_calibration_profile_id,
        )
    elif execution_mode in {"fill-replay", "real-fill-replay"}:
        simulator = FillReplayOrderSimulator(csv_path=execution_replay_fills_path)
    else:
        simulator = RealisticOrderSimulator(
            commission_bps=commission_bps,
            base_slippage_bps=slippage_bps,
            spread_bps=spread_bps,
            participation_rate=participation_rate,
            latency_steps=latency_steps,
            market_impact=market_impact,
        )
    config = ExperimentConfig(
        name=name,
        symbols=symbols,
        seed=seed,
        initial_cash=initial_cash,
        initial_holdings_weights=dict(initial_holdings_weights or {}),
    )
    data_provider = (
        CsvMarketDataProvider(
            data_dir=real_data_dir,
            symbols=symbols,
            start=real_data_start,
            end=real_data_end,
            frequency=real_data_frequency,
            max_periods=real_data_max_periods,
            window_offset=real_data_window_offset,
            name="yahoo-finance-csv",
            news_path=real_news_path,
            macro_path=real_macro_path,
            filings_path=real_filings_path,
            alternative_data_path=real_alternative_data_path,
        )
        if data_source == "csv"
        else SyntheticMarketDataProvider(
            symbols=symbols,
            periods=periods,
            seed=seed,
            volatility_scale=synthetic_volatility_scale,
            trend_scale=synthetic_trend_scale,
            seasonal_scale=synthetic_seasonal_scale,
            macro_scale=synthetic_macro_scale,
            tail_df=synthetic_tail_df,
            jump_probability=synthetic_jump_probability,
            jump_scale=synthetic_jump_scale,
        )
    )
    return TradeArena(
        config=config,
        data_provider=data_provider,
        analysts=analysts,
        strategy=strategy,
        risk_manager=risk_manager,
        execution_agent=TargetWeightExecutionAgent(),
        order_simulator=simulator,
        memory=_build_memory(
            memory_pollution_kind,
            memory_pollution_dose,
            memory_pollution_seed,
            memory_pollution_loss_streak_length,
            memory_pollution_defense,
        ),
        evaluators=[
            PerformanceEvaluator(),
            BehavioralEvaluator(),
            IntentExecutionGapEvaluator(),
            DecisionQualityEvaluator(),
            ReasoningConsistencyEvaluator(),
            ExecutionRealismEvaluator(),
            RiskAuditEvaluator(),
        ],
    )
