"""Execution simulators and calibration surfaces.

The execution package keeps order-fill assumptions explicit:

- simple: idealized paper fills with fees and fixed slippage.
- stress: configurable spread, latency, participation, impact, and quote replay.
- fill_replay: audit replay against realized fills.
- calibration: public calibration helpers for quote/fill or OHLCV diagnostics.
"""

from tradearena.execution.constants import (
    EXECUTION_CALIBRATED,
    EXECUTION_FILL_REPLAY,
    EXECUTION_QUOTE_REPLAY,
    EXECUTION_STRESS,
)
from tradearena.execution.fill_replay import FillReplayOrderSimulator, load_replay_fills_csv
from tradearena.execution.simple import SimpleOrderSimulator
from tradearena.execution.stress import (
    CalibratedOrderSimulator,
    QuoteReplayOrderSimulator,
    RealisticOrderSimulator,
)

__all__ = [
    "EXECUTION_CALIBRATED",
    "EXECUTION_FILL_REPLAY",
    "EXECUTION_QUOTE_REPLAY",
    "EXECUTION_STRESS",
    "CalibratedOrderSimulator",
    "FillReplayOrderSimulator",
    "QuoteReplayOrderSimulator",
    "RealisticOrderSimulator",
    "SimpleOrderSimulator",
    "load_replay_fills_csv",
]
