from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts import analyze_mempoll_dose025 as analysis
from scripts import render_mempoll_dose_curve as renderer


def _rows(*, agent: str, doses: tuple[float, ...], include_regime: bool) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for dose in doses:
        for seed in analysis.SEEDS:
            for sample in analysis.SAMPLES:
                row: dict[str, object] = {
                    "agent": agent,
                    "kind": "fake_violations",
                    "dose": dose,
                    "decay": 0.85,
                    "risk": "none",
                    "seed": seed,
                    "sample": sample,
                    "hold_ratio": 0.2 + 0.1 * dose,
                    "mean_gross_target_exposure": 1.0 - 0.2 * dose,
                    "total_return": 0.05 - 0.01 * dose,
                    "max_drawdown": -0.02,
                }
                if include_regime:
                    row["market_regime"] = "bullish"
                rows.append(row)
    return rows


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_analysis_uses_separate_four_test_family_and_three_curve_points():
    existing = {
        agent: _rows(agent=agent, doses=analysis.EXISTING_DOSES, include_regime=False)
        for agent in analysis.AGENT_DIRS
    }
    new = {
        agent: _rows(agent=agent, doses=(analysis.NEW_DOSE,), include_regime=True)
        for agent in analysis.AGENT_DIRS
    }
    effects, curves = analysis.analyze(existing, new)
    primary = [row for row in effects if row["family"] == "primary"]
    exploratory = [row for row in effects if row["family"] == "exploratory"]

    assert len(primary) == 4
    assert all(row["q_value"] is not None for row in primary)
    assert all(row["q_value"] is None for row in exploratory)
    assert len(curves) == 24
    assert {float(row["dose"]) for row in curves} == {0.0, 0.25, 0.75}


def test_new_arm_requires_exact_unique_grid_and_regime(tmp_path: Path):
    agent = "glm:glm-5"
    rows = _rows(agent=agent, doses=(analysis.NEW_DOSE,), include_regime=True)
    rows.append(dict(rows[0]))
    path = tmp_path / "memory_pollution_runs.csv"
    _write(path, rows)
    with pytest.raises(SystemExit, match="duplicate target row"):
        analysis.load_arm(
            path,
            agent=agent,
            doses=(analysis.NEW_DOSE,),
            require_regime=True,
        )


def test_renderer_requires_complete_primary_curve(tmp_path: Path):
    rows = []
    for model in renderer.MODELS:
        for outcome, _, _ in renderer.OUTCOMES:
            for dose in renderer.DOSES:
                rows.append(
                    {
                        "agent": model,
                        "outcome": outcome,
                        "dose": dose,
                        "mean": 0.2,
                        "ci_low": 0.1,
                        "ci_high": 0.3,
                    }
                )
    path = tmp_path / "curve.csv"
    _write(path, rows)
    assert len(renderer.load_curve(path)) == 12
    _write(path, rows[:-1])
    with pytest.raises(ValueError, match="expected 12 unique curve points"):
        renderer.load_curve(path)
