from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tradearena.core.serialization import write_json
from tradearena.core.trajectory import Trajectory


@dataclass(frozen=True)
class AuditManifest:
    framework: str = "TreLLM"
    leaderboard_module: str = "TradeArena"
    claim: str = (
        "Financial AI agents should be studied as auditable decision-making systems "
        "whose intent must survive risk and execution constraints."
    )
    design_goals: tuple[str, ...] = (
        "modularity",
        "reproducibility",
        "execution_realism",
        "risk_awareness",
        "auditability",
    )
    challenges: tuple[str, ...] = (
        "reproducibility",
        "evaluation",
        "execution_realism",
        "risk_control",
        "extensibility",
        "auditability",
        "data_leakage",
        "agent_organization",
    )
    metadata: dict[str, Any] = field(default_factory=dict)


def export_audit_bundle(
    output_dir: str | Path,
    trajectory: Trajectory,
    metrics: dict[str, float | int | str],
    manifest: AuditManifest | None = None,
) -> None:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    write_json(path / "manifest.json", manifest or AuditManifest())
    write_json(path / "trajectory.json", trajectory.to_dict())
    write_json(path / "metrics.json", metrics)
