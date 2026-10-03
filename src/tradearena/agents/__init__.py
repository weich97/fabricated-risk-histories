"""Baseline trading agents."""

from tradearena.agents.analysts import MacroNewsAnalyst, MomentumAnalyst
from tradearena.agents.execution import TargetWeightExecutionAgent
from tradearena.agents.llm import ChatCompletionsLLMAnalyst, DeepSeekLLMAnalyst
from tradearena.agents.portfolio import EqualWeightPortfolioManager
from tradearena.agents.risk import (
    MaxDrawdownRiskPreset,
    MaxPositionRiskManager,
    NoRiskManager,
    max_drawdown_risk_preset,
)
from tradearena.agents.rl import DeterministicRLAllocationStrategy
from tradearena.agents.strategy import (
    AlwaysHoldStrategy,
    BuyAndHoldStrategy,
    EqualWeightStrategy,
    MarkowitzMVOStrategy,
    MeanReversionStrategy,
    MeanVarianceStrategy,
    MemoryAwareSignalWeightedStrategy,
    NaiveMomentumStrategy,
    NoTradeBandStrategy,
    RandomAllocationStrategy,
    RiskParityStrategy,
    SignalWeightedStrategy,
    SMACrossoverStrategy,
)

__all__ = [
    "AlwaysHoldStrategy",
    "BuyAndHoldStrategy",
    "ChatCompletionsLLMAnalyst",
    "DeepSeekLLMAnalyst",
    "DeterministicRLAllocationStrategy",
    "EqualWeightPortfolioManager",
    "EqualWeightStrategy",
    "MacroNewsAnalyst",
    "MaxDrawdownRiskPreset",
    "MaxPositionRiskManager",
    "MarkowitzMVOStrategy",
    "MeanVarianceStrategy",
    "MeanReversionStrategy",
    "MemoryAwareSignalWeightedStrategy",
    "MomentumAnalyst",
    "NaiveMomentumStrategy",
    "NoRiskManager",
    "NoTradeBandStrategy",
    "RandomAllocationStrategy",
    "RiskParityStrategy",
    "SMACrossoverStrategy",
    "SignalWeightedStrategy",
    "TargetWeightExecutionAgent",
    "max_drawdown_risk_preset",
]
