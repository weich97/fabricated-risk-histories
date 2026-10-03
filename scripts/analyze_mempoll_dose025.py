"""Analyze the frozen bullish directive-removed dose-0.25 follow-up."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tradearena.evaluation.statistics import (
    benjamini_hochberg,
    bootstrap_ci,
    mean,
    paired_bootstrap_difference,
)

AGENT_DIRS = {
    "deepseek:deepseek-v4-pro": "deepseek_v4_pro",
    "glm:glm-5": "glm_5_direct",
}
SEEDS = tuple(range(1, 31))
SAMPLES = (0, 1, 2)
EXISTING_DOSES = (0.0, 0.75)
NEW_DOSE = 0.25
PRIMARY = ("hold_ratio", "mean_gross_target_exposure")
EXPLORATORY = ("total_return", "max_drawdown")


def expected_keys(doses: tuple[float, ...]) -> set[tuple[float, int, int]]:
    return {(dose, seed, sample) for dose in doses for seed in SEEDS for sample in SAMPLES}


def load_arm(
    path: Path,
    *,
    agent: str,
    doses: tuple[float, ...],
    require_regime: bool,
) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SystemExit(f"missing dose arm: {path}")
    rows: list[dict[str, Any]] = []
    got: set[tuple[float, int, int]] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for line_number, raw in enumerate(csv.DictReader(handle), start=2):
            key = (float(raw["dose"]), int(raw["seed"]), int(raw["sample"]))
            target = (
                raw.get("agent") == agent
                and str(raw.get("market_regime") or "bullish") == "bullish"
                and raw.get("kind") == "fake_violations"
                and key[0] in doses
                and float(raw["decay"]) == 0.85
                and raw.get("risk") == "none"
                and key[1] in SEEDS
                and key[2] in SAMPLES
            )
            if not target:
                raise SystemExit(f"unexpected row at {path}:{line_number}")
            if require_regime and raw.get("market_regime") != "bullish":
                raise SystemExit(f"missing bullish regime label at {path}:{line_number}")
            if key in got:
                raise SystemExit(f"duplicate target row in {path}: {key}")
            for outcome in PRIMARY + EXPLORATORY:
                try:
                    float(raw[outcome])
                except (KeyError, TypeError, ValueError) as exc:
                    raise SystemExit(f"invalid {outcome} at {path}:{line_number}") from exc
            got.add(key)
            rows.append(dict(raw))
    expected = expected_keys(doses)
    if got != expected:
        raise SystemExit(f"incomplete target grid in {path}: got {len(got)}/{len(expected)}")
    return rows


def seed_means(rows: list[dict[str, Any]], outcome: str) -> dict[tuple[float, int], float]:
    grouped: dict[tuple[float, int], list[float]] = defaultdict(list)
    for row in rows:
        grouped[(float(row["dose"]), int(row["seed"]))].append(float(row[outcome]))
    return {key: mean(values) for key, values in grouped.items()}


def analyze(
    existing: dict[str, list[dict[str, Any]]],
    new: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    effects: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    for agent in AGENT_DIRS:
        rows = existing[agent] + new[agent]
        for outcome in PRIMARY + EXPLORATORY:
            values = seed_means(rows, outcome)
            base = {seed: values[(0.0, seed)] for seed in SEEDS}
            low = {seed: values[(NEW_DOSE, seed)] for seed in SEEDS}
            high = {seed: values[(0.75, seed)] for seed in SEEDS}
            fit = paired_bootstrap_difference(low, base)
            effects.append(
                {
                    "agent": agent,
                    "outcome": outcome,
                    "family": "primary" if outcome in PRIMARY else "exploratory",
                    "paired_n": fit["paired_n"],
                    "dose0_mean": mean(base.values()),
                    "dose025_mean": mean(low.values()),
                    "dose075_mean": mean(high.values()),
                    "mean_delta": fit["mean_delta"],
                    "ci_low": fit["delta_ci_low"],
                    "ci_high": fit["delta_ci_high"],
                    "permutation_p_value": fit["permutation_p_value"],
                    "q_value": None,
                    "cohens_d": fit["cohens_d"],
                }
            )
            for dose, arm in ((0.0, base), (NEW_DOSE, low), (0.75, high)):
                ci_low, ci_high = bootstrap_ci(arm.values())
                curves.append(
                    {
                        "agent": agent,
                        "outcome": outcome,
                        "dose": dose,
                        "seed_n": len(arm),
                        "mean": mean(arm.values()),
                        "ci_low": ci_low,
                        "ci_high": ci_high,
                    }
                )

    primary_p = {
        index: row["permutation_p_value"]
        for index, row in enumerate(effects)
        if row["family"] == "primary"
    }
    for index, q_value in benjamini_hochberg(primary_p).items():
        effects[index]["q_value"] = q_value
    return effects, curves


def write_outputs(
    output_dir: Path,
    effects: list[dict[str, Any]],
    curves: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "dose025_effects.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(effects[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(effects)
    with (output_dir / "dose025_curve.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(curves[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(curves)

    lines = [
        "# Bullish directive-removed dose-0.25 follow-up",
        "",
        "The dose-0.25 rows were frozen and collected after the regime results.",
        "Dose-zero controls are reused from the earlier collection; contrasts",
        "therefore assume provider-service stability across batches. `q` applies",
        "only to the new four-test primary family. Financial outcomes are exploratory.",
        "",
        "| Model | Outcome | Dose 0 | Dose .25 | Dose .75 | Delta .25-0 [95% CI] | p | q |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in effects:
        model = str(row["agent"]).split(":", 1)[1]
        interval = (
            f"{float(row['mean_delta']):+.4f} "
            f"[{float(row['ci_low']):+.4f}, {float(row['ci_high']):+.4f}]"
        )
        q_value = "--" if row["q_value"] is None else f"{float(row['q_value']):.4f}"
        lines.append(
            f"| {model} | {row['outcome']} | {float(row['dose0_mean']):+.4f} | "
            f"{float(row['dose025_mean']):+.4f} | {float(row['dose075_mean']):+.4f} | "
            f"{interval} | {float(row['permutation_p_value']):.4f} | {q_value} |"
        )
    (output_dir / "dose025_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing-root", default="outputs/memory_pollution_neutral")
    parser.add_argument("--new-root", default="outputs/memory_pollution_dose025")
    parser.add_argument("--output-dir", default="docs/results/memory_pollution_dose025")
    args = parser.parse_args(argv)

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else ROOT / path

    existing_root = resolve(args.existing_root)
    new_root = resolve(args.new_root)
    output_dir = resolve(args.output_dir)
    existing: dict[str, list[dict[str, Any]]] = {}
    new: dict[str, list[dict[str, Any]]] = {}
    for agent, directory in AGENT_DIRS.items():
        existing[agent] = load_arm(
            existing_root / directory / "memory_pollution_runs.csv",
            agent=agent,
            doses=EXISTING_DOSES,
            require_regime=False,
        )
        new[agent] = load_arm(
            new_root / directory / "memory_pollution_runs.csv",
            agent=agent,
            doses=(NEW_DOSE,),
            require_regime=True,
        )
    effects, curves = analyze(existing, new)
    write_outputs(output_dir, effects, curves)
    print(f"wrote {len(effects)} effects and {len(curves)} curve points to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
