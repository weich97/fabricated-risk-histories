"""Analyze the explicit risk-feedback directive ablation.

The instructed arm comes from the frozen confirmatory batch. The neutral arm
removes the explicit risk-feedback directive from the user JSON while retaining
the cautious system role. The primary estimand is the paired difference in
pollution effects:

    (instructed[d=.75] - instructed[d=0])
      - (neutral[d=.75] - neutral[d=0]).

Each file is validated against the exact expected seed/sample key set before
analysis. Provider samples are averaged within seed. The four neutral-mode
main effects (two direct models x hold ratio / mean gross exposure) share one
BH family, and the four directive interactions share a separate BH family.
"""

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
TARGET_KIND = "fake_violations"
TARGET_RISK = "none"
TARGET_DECAY = 0.85
TARGET_DOSES = (0.0, 0.75)
TARGET_SEEDS = tuple(range(1, 31))
TARGET_SAMPLES = (0, 1, 2)
PRIMARY_OUTCOMES = ("hold_ratio", "mean_gross_target_exposure")
EXPLORATORY_OUTCOMES = ("turnover_events", "total_return")
ALL_OUTCOMES = PRIMARY_OUTCOMES + EXPLORATORY_OUTCOMES


def _expected_keys() -> set[tuple[float, int, int]]:
    return {
        (dose, seed, sample)
        for dose in TARGET_DOSES
        for seed in TARGET_SEEDS
        for sample in TARGET_SAMPLES
    }


def load_validated_arm(
    root: Path,
    *,
    agent: str,
    directory: str,
    mode: str,
    allow_non_target_rows: bool,
) -> list[dict[str, Any]]:
    """Load one model arm and require every target key exactly once."""

    path = root / directory / "memory_pollution_runs.csv"
    if not path.is_file():
        raise SystemExit(f"missing arm CSV: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))

    selected: list[dict[str, Any]] = []
    got: set[tuple[float, int, int]] = set()
    for line_number, row in enumerate(raw_rows, start=2):
        try:
            row_agent = str(row["agent"])
            kind = str(row["kind"])
            dose = float(row["dose"])
            decay = float(row["decay"])
            risk = str(row["risk"])
            seed = int(row["seed"])
            sample = int(row["sample"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"malformed row {path}:{line_number}: {exc}") from exc
        if row_agent != agent:
            raise SystemExit(f"unexpected agent at {path}:{line_number}: {row_agent!r}")

        is_target = (
            kind == TARGET_KIND
            and dose in TARGET_DOSES
            and decay == TARGET_DECAY
            and risk == TARGET_RISK
            and seed in TARGET_SEEDS
            and sample in TARGET_SAMPLES
        )
        if not is_target:
            if not allow_non_target_rows:
                raise SystemExit(f"extra non-target row at {path}:{line_number}")
            continue

        key = (dose, seed, sample)
        if key in got:
            raise SystemExit(f"duplicate target key in {path}: {key}")
        for outcome in ALL_OUTCOMES:
            if row.get(outcome) in (None, ""):
                raise SystemExit(f"missing {outcome} at {path}:{line_number}")
            try:
                float(row[outcome])
            except ValueError as exc:
                raise SystemExit(f"invalid {outcome} at {path}:{line_number}") from exc
        got.add(key)
        tagged = dict(row)
        tagged["mode"] = mode
        selected.append(tagged)

    expected = _expected_keys()
    if got != expected:
        missing = sorted(expected - got)[:8]
        extra = sorted(got - expected)[:8]
        raise SystemExit(
            f"incomplete target grid in {path}: got={len(got)}/{len(expected)}, "
            f"missing={missing}, extra={extra}"
        )
    return selected


def seed_means(
    rows: list[dict[str, Any]], outcome: str
) -> dict[tuple[str, str, float, int], float]:
    grouped: dict[tuple[str, str, float, int], list[float]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row["mode"]),
                str(row["agent"]),
                float(row["dose"]),
                int(row["seed"]),
            )
        ].append(float(row[outcome]))
    return {key: mean(values) for key, values in grouped.items()}


def mode_effect_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for outcome in ALL_OUTCOMES:
        values = seed_means(rows, outcome)
        for mode in ("instructed", "neutral"):
            for agent in AGENT_DIRS:
                base = {
                    seed: value
                    for (row_mode, row_agent, dose, seed), value in values.items()
                    if row_mode == mode and row_agent == agent and dose == 0.0
                }
                polluted = {
                    seed: value
                    for (row_mode, row_agent, dose, seed), value in values.items()
                    if row_mode == mode and row_agent == agent and dose == 0.75
                }
                fit = paired_bootstrap_difference(polluted, base)
                output.append(
                    {
                        "mode": mode,
                        "agent": agent,
                        "outcome": outcome,
                        "family": (
                            "neutral_primary"
                            if mode == "neutral" and outcome in PRIMARY_OUTCOMES
                            else "descriptive"
                        ),
                        "paired_n": fit["paired_n"],
                        "mean_delta": fit["mean_delta"],
                        "ci_low": fit["delta_ci_low"],
                        "ci_high": fit["delta_ci_high"],
                        "permutation_p_value": fit["permutation_p_value"],
                        "q_value": None,
                        "cohens_d": fit["cohens_d"],
                    }
                )

    primary = {
        index: row["permutation_p_value"]
        for index, row in enumerate(output)
        if row["family"] == "neutral_primary"
    }
    q_values = benjamini_hochberg(primary)
    for index, q_value in q_values.items():
        output[index]["q_value"] = q_value
    return output


def directive_interaction_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for outcome in ALL_OUTCOMES:
        values = seed_means(rows, outcome)
        for agent in AGENT_DIRS:
            instructed = {
                seed: values[("instructed", agent, 0.75, seed)]
                - values[("instructed", agent, 0.0, seed)]
                for seed in TARGET_SEEDS
            }
            neutral = {
                seed: values[("neutral", agent, 0.75, seed)]
                - values[("neutral", agent, 0.0, seed)]
                for seed in TARGET_SEEDS
            }
            fit = paired_bootstrap_difference(instructed, neutral)
            output.append(
                {
                    "agent": agent,
                    "outcome": outcome,
                    "family": "primary" if outcome in PRIMARY_OUTCOMES else "exploratory",
                    "paired_n": fit["paired_n"],
                    "mean_interaction": fit["mean_delta"],
                    "ci_low": fit["delta_ci_low"],
                    "ci_high": fit["delta_ci_high"],
                    "permutation_p_value": fit["permutation_p_value"],
                    "q_value": None,
                    "cohens_d": fit["cohens_d"],
                }
            )

    primary = {
        index: row["permutation_p_value"]
        for index, row in enumerate(output)
        if row["family"] == "primary"
    }
    q_values = benjamini_hochberg(primary)
    for index, q_value in q_values.items():
        output[index]["q_value"] = q_value
    return output


def robustness_diagnostic_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return descriptive sample-wise and leave-one-seed-out checks."""

    output: list[dict[str, Any]] = []
    for outcome in PRIMARY_OUTCOMES:
        values = seed_means(rows, outcome)
        for mode in ("instructed", "neutral"):
            for agent in AGENT_DIRS:
                sample_effects: list[float] = []
                for sample in TARGET_SAMPLES:
                    per_seed: list[float] = []
                    for seed in TARGET_SEEDS:
                        treated = [
                            float(row[outcome])
                            for row in rows
                            if row["mode"] == mode
                            and row["agent"] == agent
                            and float(row["dose"]) == 0.75
                            and int(row["seed"]) == seed
                            and int(row["sample"]) == sample
                        ]
                        base = [
                            float(row[outcome])
                            for row in rows
                            if row["mode"] == mode
                            and row["agent"] == agent
                            and float(row["dose"]) == 0.0
                            and int(row["seed"]) == seed
                            and int(row["sample"]) == sample
                        ]
                        if len(treated) != 1 or len(base) != 1:
                            raise ValueError(
                                f"unexpected sample cell for {mode}/{agent}/{outcome}/"
                                f"seed={seed}/sample={sample}"
                            )
                        per_seed.append(treated[0] - base[0])
                    sample_effects.append(mean(per_seed))

                seed_effects = {
                    seed: values[(mode, agent, 0.75, seed)]
                    - values[(mode, agent, 0.0, seed)]
                    for seed in TARGET_SEEDS
                }
                leave_one_out = [
                    mean([value for seed, value in seed_effects.items() if seed != omitted])
                    for omitted in TARGET_SEEDS
                ]
                output.append(
                    {
                        "mode": mode,
                        "agent": agent,
                        "outcome": outcome,
                        "sample_0_delta": sample_effects[0],
                        "sample_1_delta": sample_effects[1],
                        "sample_2_delta": sample_effects[2],
                        "leave_one_seed_out_min": min(leave_one_out),
                        "leave_one_seed_out_max": max(leave_one_out),
                    }
                )
    return output


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(
    path: Path,
    mode_effects: list[dict[str, Any]],
    interactions: list[dict[str, Any]],
) -> None:
    lines = [
        "# Explicit risk-feedback directive ablation",
        "",
        "The neutral arm removes the explicit risk-feedback directive from the",
        "user JSON but retains the cautious system role. The interaction is",
        "the earlier instructed batch's d=.75 minus d=0 contrast minus the",
        "later directive-removed batch's contrast. Because prompt modes were",
        "collected sequentially, it is descriptive and does not identify a",
        "causal effect of the directive. Within-mode contrasts also assume",
        "provider stability across arm collection dates.",
        "",
        "## Directive interactions",
        "",
        "| Agent | Outcome | Family | Interaction | 95% CI | p | q |",
        "| --- | --- | --- | ---: | --- | ---: | ---: |",
    ]
    for row in interactions:
        q_text = f"{float(row['q_value']):.4f}" if row["q_value"] is not None else ""
        lines.append(
            f"| {row['agent']} | {row['outcome']} | {row['family']} "
            f"| {float(row['mean_interaction']):+.4f} "
            f"| [{float(row['ci_low']):+.4f}, {float(row['ci_high']):+.4f}] "
            f"| {float(row['permutation_p_value']):.4f} | {q_text} |"
        )
    lines += [
        "",
        "## Within-mode effects",
        "",
        "This prompt-mode analysis uses a four-test BH family for the neutral",
        "primary effects. The final paper's q_R values instead use all twelve",
        "directive-removed regime effects and are in",
        "docs/results/memory_pollution_regimes/regime_effects.csv.",
        "The four directive interactions have their own separate BH family.",
        "",
        "| Mode | Agent | Outcome | Family | d=.75 - d=0 | 95% CI | p | q |",
        "| --- | --- | --- | --- | ---: | --- | ---: | ---: |",
    ]
    for row in mode_effects:
        q_text = f"{float(row['q_value']):.4f}" if row["q_value"] is not None else ""
        lines.append(
            f"| {row['mode']} | {row['agent']} | {row['outcome']} | {row['family']} "
            f"| {float(row['mean_delta']):+.4f} "
            f"| [{float(row['ci_low']):+.4f}, {float(row['ci_high']):+.4f}] "
            f"| {float(row['permutation_p_value']):.4f} | {q_text} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instructed-root", default="outputs/memory_pollution_confirm")
    parser.add_argument("--neutral-root", default="outputs/memory_pollution_neutral")
    parser.add_argument("--output-dir", default="docs/results/memory_pollution_neutral")
    args = parser.parse_args(argv)

    instructed_root = Path(args.instructed_root)
    if not instructed_root.is_absolute():
        instructed_root = ROOT / instructed_root
    neutral_root = Path(args.neutral_root)
    if not neutral_root.is_absolute():
        neutral_root = ROOT / neutral_root
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir

    rows: list[dict[str, Any]] = []
    for agent, directory in AGENT_DIRS.items():
        rows.extend(
            load_validated_arm(
                instructed_root,
                agent=agent,
                directory=directory,
                mode="instructed",
                allow_non_target_rows=True,
            )
        )
        rows.extend(
            load_validated_arm(
                neutral_root,
                agent=agent,
                directory=directory,
                mode="neutral",
                allow_non_target_rows=False,
            )
        )

    effects = mode_effect_rows(rows)
    interactions = directive_interaction_rows(rows)
    diagnostics = robustness_diagnostic_rows(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "mode_effects.csv",
        effects,
        [
            "mode",
            "agent",
            "outcome",
            "family",
            "paired_n",
            "mean_delta",
            "ci_low",
            "ci_high",
            "permutation_p_value",
            "q_value",
            "cohens_d",
        ],
    )
    _write_csv(
        output_dir / "directive_interactions.csv",
        interactions,
        [
            "agent",
            "outcome",
            "family",
            "paired_n",
            "mean_interaction",
            "ci_low",
            "ci_high",
            "permutation_p_value",
            "q_value",
            "cohens_d",
        ],
    )
    _write_csv(
        output_dir / "robustness_diagnostics.csv",
        diagnostics,
        [
            "mode",
            "agent",
            "outcome",
            "sample_0_delta",
            "sample_1_delta",
            "sample_2_delta",
            "leave_one_seed_out_min",
            "leave_one_seed_out_max",
        ],
    )
    _write_markdown(output_dir / "neutral_analysis.md", effects, interactions)
    print(f"Analyzed {len(rows)} rows -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
