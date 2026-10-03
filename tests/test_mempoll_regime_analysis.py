from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts import analyze_mempoll_regimes as analysis
from scripts import render_mempoll_regime_figure as renderer


def _rows(*, agent: str, regime: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    regime_scale = {"bullish": 1.0, "bearish": 0.0, "sideways": 0.5}[regime]
    for dose in analysis.DOSES:
        treatment = dose / 0.75
        for seed in analysis.SEEDS:
            for sample in analysis.SAMPLES:
                rows.append(
                    {
                        "agent": agent,
                        "market_regime": regime,
                        "kind": "fake_violations",
                        "dose": dose,
                        "decay": 0.85,
                        "risk": "none",
                        "seed": seed,
                        "sample": sample,
                        "hold_ratio": 0.2 + 0.1 * regime_scale * treatment,
                        "mean_gross_target_exposure": 1.0 - 0.2 * regime_scale * treatment,
                        "total_return": 0.05 - 0.01 * regime_scale * treatment,
                        "max_drawdown": -0.02,
                    }
                )
    return rows


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_effect_rows_use_one_twelve_test_primary_family():
    all_rows = {
        (regime, agent): _rows(agent=agent, regime=regime)
        for regime in analysis.REGIMES
        for agent in analysis.AGENT_DIRS
    }
    effects = analysis.effect_rows(all_rows)
    primary = [row for row in effects if row["family"] == "primary"]
    exploratory = [row for row in effects if row["family"] == "exploratory"]

    assert len(primary) == 12
    assert all(row["q_value"] is not None for row in primary)
    assert all(row["q_value"] is None for row in exploratory)
    bull_hold = next(
        row
        for row in primary
        if row["regime"] == "bullish"
        and row["agent"] == "deepseek:deepseek-v4-pro"
        and row["outcome"] == "hold_ratio"
    )
    assert float(bull_hold["clean_mean"]) == pytest.approx(0.2)
    assert float(bull_hold["polluted_mean"]) == pytest.approx(0.3)
    assert float(bull_hold["mean_delta"]) == pytest.approx(0.1)


def test_regime_interactions_compare_only_contemporaneous_extension_arms():
    all_rows = {
        (regime, agent): _rows(agent=agent, regime=regime)
        for regime in analysis.REGIMES
        for agent in analysis.AGENT_DIRS
    }
    interactions = analysis.regime_interaction_rows(all_rows)

    assert len(interactions) == 4
    assert all(row["left_regime"] == "sideways" for row in interactions)
    assert all(row["right_regime"] == "bearish" for row in interactions)
    assert all(row["q_value"] is not None for row in interactions)
    hold = next(
        row
        for row in interactions
        if row["agent"] == "deepseek:deepseek-v4-pro"
        and row["outcome"] == "hold_ratio"
    )
    exposure = next(
        row
        for row in interactions
        if row["agent"] == "deepseek:deepseek-v4-pro"
        and row["outcome"] == "mean_gross_target_exposure"
    )
    assert float(hold["mean_delta"]) == pytest.approx(0.05)
    assert float(exposure["mean_delta"]) == pytest.approx(-0.1)


def test_load_arm_rejects_duplicate_frozen_key(tmp_path: Path):
    rows = _rows(agent="glm:glm-5", regime="sideways")
    rows.append(dict(rows[0]))
    path = tmp_path / "memory_pollution_runs.csv"
    _write(path, rows)

    with pytest.raises(SystemExit, match="duplicate target row"):
        analysis.load_arm(path, agent="glm:glm-5", regime="sideways")


def test_renderer_requires_complete_primary_grid(tmp_path: Path):
    path = tmp_path / "effects.csv"
    rows = []
    for regime in renderer.REGIMES:
        for model in renderer.MODELS:
            for outcome, _, _ in renderer.OUTCOMES:
                rows.append(
                    {
                        "regime": regime,
                        "agent": model,
                        "outcome": outcome,
                        "family": "primary",
                        "mean_delta": 0.1,
                        "ci_low": 0.05,
                        "ci_high": 0.15,
                        "q_value": 0.01,
                    }
                )
    _write(path, rows)
    assert len(renderer.load_primary_effects(path)) == 12

    _write(path, rows[:-1])
    with pytest.raises(ValueError, match="expected 12 unique primary effects"):
        renderer.load_primary_effects(path)
