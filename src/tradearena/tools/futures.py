from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from tradearena.core.domain import RiskCheck, RiskPhase, RiskReport, RiskViolation


@dataclass(frozen=True)
class FuturesContractMetadata:
    symbol: str
    root_symbol: str
    expiry: date
    roll_start: date
    roll_end: date
    contract_multiplier: float
    initial_margin_rate: float
    description: str = ""


class FuturesRollRiskEngine:
    """Paper-only expiry and roll-window risk checker for futures examples."""

    name = "futures-roll-risk-engine"

    def __init__(self, *, expiry_warning_days: int = 5) -> None:
        self.expiry_warning_days = expiry_warning_days

    def review(
        self,
        *,
        timestamp: datetime,
        contracts: tuple[FuturesContractMetadata, ...],
        positions: dict[str, float],
    ) -> RiskReport:
        today = timestamp.date()
        checks: list[RiskCheck] = []
        violations: list[RiskViolation] = []

        for contract in contracts:
            quantity = float(positions.get(contract.symbol, 0.0))
            if abs(quantity) <= 1e-12:
                continue
            days_to_expiry = (contract.expiry - today).days
            metadata = {
                "symbol": contract.symbol,
                "root_symbol": contract.root_symbol,
                "expiry": contract.expiry.isoformat(),
                "roll_start": contract.roll_start.isoformat(),
                "roll_end": contract.roll_end.isoformat(),
                "quantity": quantity,
            }
            if days_to_expiry < 0:
                checks.append(
                    RiskCheck(
                        name="futures_expired",
                        passed=False,
                        severity="error",
                        message=f"{contract.symbol} expired {abs(days_to_expiry)} days ago",
                        metadata=metadata,
                    )
                )
                violations.append(_violation("futures_expired", "error", days_to_expiry, 0, contract.symbol, metadata))
            elif contract.roll_start <= today <= contract.roll_end:
                checks.append(
                    RiskCheck(
                        name="futures_roll_window",
                        passed=False,
                        severity="warning",
                        message=f"{contract.symbol} is inside the paper roll window",
                        metadata=metadata,
                    )
                )
                violations.append(
                    _violation("futures_roll_window", "warning", today.isoformat(), contract.roll_end.isoformat(), contract.symbol, metadata)
                )
            elif days_to_expiry <= self.expiry_warning_days:
                checks.append(
                    RiskCheck(
                        name="futures_expiry_proximity",
                        passed=False,
                        severity="warning",
                        message=f"{contract.symbol} expires in {days_to_expiry} days",
                        metadata=metadata,
                    )
                )
                violations.append(_violation("futures_expiry_proximity", "warning", days_to_expiry, self.expiry_warning_days, contract.symbol, metadata))
            else:
                checks.append(
                    RiskCheck(
                        name="futures_expiry_clear",
                        passed=True,
                        severity="info",
                        message=f"{contract.symbol} has {days_to_expiry} days to expiry",
                        metadata=metadata,
                    )
                )

        if not checks:
            checks.append(RiskCheck(name="no_futures_positions", passed=True, severity="info", message="no futures positions to review"))

        return RiskReport(
            timestamp=timestamp,
            checks=tuple(checks),
            approved_count=max(0, len([symbol for symbol, qty in positions.items() if abs(qty) > 1e-12])),
            blocked_count=sum(1 for item in checks if item.severity == "error" and not item.passed),
            clipped_count=0,
            phase=RiskPhase.PRE_TRADE,
            violations=tuple(violations),
        )


def _violation(
    constraint: str,
    severity: str,
    observed: float | str,
    limit: float | str,
    symbol: str,
    metadata: dict[str, object],
) -> RiskViolation:
    return RiskViolation(
        phase=RiskPhase.PRE_TRADE,
        constraint=constraint,
        severity=severity,
        observed=observed,
        limit=limit,
        message=f"{symbol} triggered {constraint}",
        symbol=symbol,
        metadata=metadata,
    )
