"""Analyze the frozen market-regime extension."""

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

from tradearena.evaluation.statistics import benjamini_hochberg, mean, paired_bootstrap_difference

AGENT_DIRS = {
    "deepseek:deepseek-v4-pro": "deepseek_v4_pro",
    "glm:glm-5": "glm_5_direct",
}
REGIMES = ("bullish", "bearish", "sideways")
DOSES = (0.0, 0.75)
SEEDS = tuple(range(1, 31))
SAMPLES = (0, 1, 2)
PRIMARY = ("hold_ratio", "mean_gross_target_exposure")
EXPLORATORY = ("total_return", "max_drawdown")
INTERACTION_REGIMES = ("sideways", "bearish")


def expected_keys() -> set[tuple[float, int, int]]:
    return {(dose, seed, sample) for dose in DOSES for seed in SEEDS for sample in SAMPLES}


def load_arm(path: Path, *, agent: str, regime: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SystemExit(f"missing regime arm: {path}")
    rows: list[dict[str, Any]] = []
    got: set[tuple[float, int, int]] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for line_number, raw in enumerate(csv.DictReader(handle), start=2):
            row_regime = str(raw.get("market_regime") or "bullish")
            key = (float(raw["dose"]), int(raw["seed"]), int(raw["sample"]))
            target = (
                raw["agent"] == agent
                and row_regime == regime
                and raw["kind"] == "fake_violations"
                and key[0] in DOSES
                and float(raw["decay"]) == 0.85
                and raw["risk"] == "none"
                and key[1] in SEEDS
                and key[2] in SAMPLES
            )
            if not target:
                if regime != "bullish":
                    raise SystemExit(f"unexpected row at {path}:{line_number}")
                continue
            if key in got:
                raise SystemExit(f"duplicate target row in {path}: {key}")
            for outcome in PRIMARY + EXPLORATORY:
                try:
                    float(raw[outcome])
                except (KeyError, TypeError, ValueError) as exc:
                    raise SystemExit(f"invalid {outcome} at {path}:{line_number}") from exc
            got.add(key)
            rows.append(dict(raw))
    if got != expected_keys():
        raise SystemExit(f"incomplete target grid in {path}: got {len(got)}/{len(expected_keys())}")
    return rows


def seed_effects(
    rows: list[dict[str, Any]],
    *,
    outcome: str,
) -> dict[int, float]:
    grouped: dict[tuple[float, int], list[float]] = defaultdict(list)
    for row in rows:
        grouped[(float(row["dose"]), int(row["seed"]))].append(float(row[outcome]))
    return {
        seed: mean(grouped[(0.75, seed)]) - mean(grouped[(0.0, seed)])
        for seed in SEEDS
    }


def effect_rows(all_rows: dict[tuple[str, str], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for regime in REGIMES:
        for agent in AGENT_DIRS:
            rows = all_rows[(regime, agent)]
            for outcome in PRIMARY + EXPLORATORY:
                grouped: dict[tuple[float, int], list[float]] = defaultdict(list)
                for row in rows:
                    grouped[(float(row["dose"]), int(row["seed"]))].append(float(row[outcome]))
                base = {seed: mean(grouped[(0.0, seed)]) for seed in SEEDS}
                polluted = {seed: mean(grouped[(0.75, seed)]) for seed in SEEDS}
                fit = paired_bootstrap_difference(polluted, base)
                output.append(
                    {
                        "regime": regime,
                        "agent": agent,
                        "outcome": outcome,
                        "family": "primary" if outcome in PRIMARY else "exploratory",
                        "paired_n": fit["paired_n"],
                        "clean_mean": mean(base.values()),
                        "polluted_mean": mean(polluted.values()),
                        "mean_delta": fit["mean_delta"],
                        "ci_low": fit["delta_ci_low"],
                        "ci_high": fit["delta_ci_high"],
                        "permutation_p_value": fit["permutation_p_value"],
                        "q_value": None,
                        "cohens_d": fit["cohens_d"],
                    }
                )
    primary_p = {
        index: row["permutation_p_value"]
        for index, row in enumerate(output)
        if row["family"] == "primary"
    }
    for index, q_value in benjamini_hochberg(primary_p).items():
        output[index]["q_value"] = q_value
    return output


def regime_interaction_rows(
    all_rows: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Post-hoc dose-by-regime contrasts within the contemporaneous extension.

    The upward arm predates the two extension regimes. Comparing only trendless
    with downward avoids treating that earlier provider batch as if market
    regime had been randomized across all three collection windows.
    """

    left_regime, right_regime = INTERACTION_REGIMES
    output: list[dict[str, Any]] = []
    for agent in AGENT_DIRS:
        for outcome in PRIMARY:
            left = seed_effects(all_rows[(left_regime, agent)], outcome=outcome)
            right = seed_effects(all_rows[(right_regime, agent)], outcome=outcome)
            fit = paired_bootstrap_difference(left, right)
            output.append(
                {
                    "left_regime": left_regime,
                    "right_regime": right_regime,
                    "agent": agent,
                    "outcome": outcome,
                    "family": "posthoc_primary_interaction",
                    "paired_n": fit["paired_n"],
                    "mean_delta": fit["mean_delta"],
                    "ci_low": fit["delta_ci_low"],
                    "ci_high": fit["delta_ci_high"],
                    "permutation_p_value": fit["permutation_p_value"],
                    "q_value": None,
                    "cohens_d": fit["cohens_d"],
                }
            )
    p_values = {
        index: row["permutation_p_value"] for index, row in enumerate(output)
    }
    for index, q_value in benjamini_hochberg(p_values).items():
        output[index]["q_value"] = q_value
    return output


def write_outputs(
    output_dir: Path,
    rows: list[dict[str, Any]],
    interactions: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with (output_dir / "regime_effects.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    interaction_fields = list(interactions[0])
    with (output_dir / "regime_interactions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=interaction_fields, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(interactions)

    lines = [
        "# Directive-removed effects across market regimes",
        "",
        "Provider samples are averaged within seed. `q` applies only to the single",
        "12-test primary family fixed in `REGIME_SPEC_2026-07-29.md`; financial",
        "outcomes are exploratory and retain uncorrected p-values.",
        "",
        "| Regime | Model | Outcome | Clean | Polluted | Delta [95% CI] | p | q |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        model = str(row["agent"]).split(":", 1)[1]
        interval = f"{float(row['mean_delta']):+.4f} [{float(row['ci_low']):+.4f}, {float(row['ci_high']):+.4f}]"
        q_value = "--" if row["q_value"] is None else f"{float(row['q_value']):.4f}"
        lines.append(
            f"| {row['regime']} | {model} | {row['outcome']} | "
            f"{float(row['clean_mean']):+.4f} | {float(row['polluted_mean']):+.4f} | "
            f"{interval} | "
            f"{float(row['permutation_p_value']):.4f} | {q_value} |"
        )
    lines.extend(
        [
            "",
            "## Post-hoc trendless-minus-downward interaction",
            "",
            "This difference-in-differences was added after inspection of the",
            "within-regime results. It compares only the two extension regimes,",
            "which were collected in the same July 29--30 provider window. `q`",
            "adjusts the four primary interactions below; the analysis is",
            "exploratory rather than part of the frozen 12-test family.",
            "",
            "| Model | Outcome | Interaction [95% CI] | p | q |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in interactions:
        model = str(row["agent"]).split(":", 1)[1]
        interval = (
            f"{float(row['mean_delta']):+.4f} "
            f"[{float(row['ci_low']):+.4f}, {float(row['ci_high']):+.4f}]"
        )
        lines.append(
            f"| {model} | {row['outcome']} | {interval} | "
            f"{float(row['permutation_p_value']):.4f} | "
            f"{float(row['q_value']):.4f} |"
        )
    (output_dir / "regime_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bull-root", default="outputs/memory_pollution_neutral")
    parser.add_argument("--extension-root", default="outputs/memory_pollution_regimes")
    parser.add_argument("--output-dir", default="docs/results/memory_pollution_regimes")
    args = parser.parse_args(argv)
    bull_root = ROOT / args.bull_root if not Path(args.bull_root).is_absolute() else Path(args.bull_root)
    extension_root = (
        ROOT / args.extension_root
        if not Path(args.extension_root).is_absolute()
        else Path(args.extension_root)
    )
    output_dir = ROOT / args.output_dir if not Path(args.output_dir).is_absolute() else Path(args.output_dir)

    all_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for regime in REGIMES:
        for agent, directory in AGENT_DIRS.items():
            root = bull_root if regime == "bullish" else extension_root / regime
            all_rows[(regime, agent)] = load_arm(
                root / directory / "memory_pollution_runs.csv",
                agent=agent,
                regime=regime,
            )
    rows = effect_rows(all_rows)
    interactions = regime_interaction_rows(all_rows)
    write_outputs(output_dir, rows, interactions)
    print(
        f"wrote {len(rows)} regime-effect rows and {len(interactions)} "
        f"post-hoc interaction rows to {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
