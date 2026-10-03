from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from tradearena.core.domain import Side


@dataclass(frozen=True)
class AlmgrenChrissImpactStress:
    """Paper-only market-impact stress proxy inspired by Almgren-Chriss terms."""

    eta: float = 0.20
    gamma: float = 0.02
    exponent: float = 0.5
    name: str = "almgren-chriss-impact-stress"
    paper_only: bool = True
    calibration_boundary: str = "stress_proxy_not_broker_calibrated"

    def estimate(
        self,
        *,
        symbol: str,
        side: Side | str,
        quantity: float,
        price: float,
        volume: float,
        model: str = "linear",
    ) -> dict[str, float | str | bool]:
        parsed_side = Side(side)
        safe_quantity = _non_negative(quantity)
        safe_price = max(1e-9, _non_negative(price))
        safe_volume = max(1.0, _non_negative(volume))
        participation = safe_quantity / safe_volume
        notional = safe_quantity * safe_price
        if model == "concave":
            temporary_rate = self.eta * (participation**self.exponent)
        elif model == "linear":
            temporary_rate = self.eta * participation
        else:
            raise ValueError("model must be 'linear' or 'concave'")
        permanent_rate = self.gamma * participation
        total_rate = temporary_rate + permanent_rate
        signed_shortfall_bps = total_rate * 10_000.0
        return {
            "plugin": self.name,
            "assumption_class": "paper_impact_stress",
            "paper_only": self.paper_only,
            "calibration_boundary": self.calibration_boundary,
            "symbol": symbol,
            "side": parsed_side.value,
            "model": model,
            "quantity": safe_quantity,
            "price": safe_price,
            "volume": safe_volume,
            "participation": participation,
            "notional": notional,
            "temporary_impact_rate": temporary_rate,
            "permanent_impact_rate": permanent_rate,
            "modeled_shortfall_bps": signed_shortfall_bps,
            "temporary_impact_cost": notional * temporary_rate,
            "permanent_impact_cost": notional * permanent_rate,
            "modeled_shortfall_cost": notional * total_rate,
        }


def _non_negative(value: float) -> float:
    numeric = float(value)
    if not isfinite(numeric):
        return 0.0
    return max(0.0, numeric)
