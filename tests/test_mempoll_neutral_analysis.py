from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIELDS = [
    "agent",
    "kind",
    "dose",
    "decay",
    "risk",
    "seed",
    "sample",
    "hold_ratio",
    "mean_gross_target_exposure",
    "turnover_events",
    "total_return",
]


def _load():
    path = ROOT / "scripts" / "analyze_mempoll_neutral.py"
    spec = importlib.util.spec_from_file_location("analyze_mempoll_neutral", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(module, *, agent: str, mode: str) -> list[dict[str, object]]:
    rows = []
    for dose in module.TARGET_DOSES:
        for seed in module.TARGET_SEEDS:
            for sample in module.TARGET_SAMPLES:
                instructed = mode == "instructed"
                pollution_shift = 0.30 if instructed else 0.05
                rows.append(
                    {
                        "agent": agent,
                        "kind": module.TARGET_KIND,
                        "dose": dose,
                        "decay": module.TARGET_DECAY,
                        "risk": module.TARGET_RISK,
                        "seed": seed,
                        "sample": sample,
                        "hold_ratio": 0.20 + pollution_shift * (dose / 0.75),
                        "mean_gross_target_exposure": 1.0 - pollution_shift * (dose / 0.75),
                        "turnover_events": 10 + pollution_shift * (dose / 0.75),
                        "total_return": 0.1 - pollution_shift * (dose / 0.75),
                        "mode": mode,
                    }
                )
    return rows


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def test_directive_interaction_is_difference_of_within_mode_effects():
    module = _load()
    rows = []
    for agent in module.AGENT_DIRS:
        rows.extend(_rows(module, agent=agent, mode="instructed"))
        rows.extend(_rows(module, agent=agent, mode="neutral"))

    interactions = module.directive_interaction_rows(rows)
    hold = [row for row in interactions if row["outcome"] == "hold_ratio"]
    exposure = [row for row in interactions if row["outcome"] == "mean_gross_target_exposure"]

    assert len(hold) == 2
    assert all(float(row["mean_interaction"]) == pytest.approx(0.25) for row in hold)
    assert all(float(row["mean_interaction"]) == pytest.approx(-0.25) for row in exposure)
    assert all(row["q_value"] is not None for row in hold + exposure)


def test_neutral_primary_effects_share_their_own_bh_family():
    module = _load()
    rows = []
    for agent in module.AGENT_DIRS:
        rows.extend(_rows(module, agent=agent, mode="instructed"))
        rows.extend(_rows(module, agent=agent, mode="neutral"))

    effects = module.mode_effect_rows(rows)
    neutral_primary = [
        row for row in effects if row["family"] == "neutral_primary"
    ]
    descriptive = [row for row in effects if row["family"] == "descriptive"]

    assert len(neutral_primary) == 4
    assert all(row["q_value"] is not None for row in neutral_primary)
    assert all(row["q_value"] is None for row in descriptive)


def test_robustness_diagnostics_preserve_effect_direction():
    module = _load()
    rows = []
    for agent in module.AGENT_DIRS:
        rows.extend(_rows(module, agent=agent, mode="instructed"))
        rows.extend(_rows(module, agent=agent, mode="neutral"))

    diagnostics = module.robustness_diagnostic_rows(rows)
    assert len(diagnostics) == 8
    for row in diagnostics:
        values = [
            float(row["sample_0_delta"]),
            float(row["sample_1_delta"]),
            float(row["sample_2_delta"]),
            float(row["leave_one_seed_out_min"]),
            float(row["leave_one_seed_out_max"]),
        ]
        if row["outcome"] == "hold_ratio":
            assert all(value > 0 for value in values)
        else:
            assert all(value < 0 for value in values)


def test_exact_grid_validator_rejects_one_missing_key(tmp_path):
    module = _load()
    agent, directory = next(iter(module.AGENT_DIRS.items()))
    rows = _rows(module, agent=agent, mode="neutral")[:-1]
    _write(tmp_path / directory / "memory_pollution_runs.csv", rows)

    with pytest.raises(SystemExit, match="incomplete target grid"):
        module.load_validated_arm(
            tmp_path,
            agent=agent,
            directory=directory,
            mode="neutral",
            allow_non_target_rows=False,
        )


def test_exact_grid_validator_rejects_duplicate_key(tmp_path):
    module = _load()
    agent, directory = next(iter(module.AGENT_DIRS.items()))
    rows = _rows(module, agent=agent, mode="neutral")
    rows.append(dict(rows[0]))
    _write(tmp_path / directory / "memory_pollution_runs.csv", rows)

    with pytest.raises(SystemExit, match="duplicate target key"):
        module.load_validated_arm(
            tmp_path,
            agent=agent,
            directory=directory,
            mode="neutral",
            allow_non_target_rows=False,
        )
