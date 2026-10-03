"""Render directive-removed memory effects across the three market regimes."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]
REGIMES = ("bullish", "sideways", "bearish")
REGIME_LABELS = {"bullish": "Upward", "sideways": "Trendless", "bearish": "Downward"}
MODELS = ("deepseek:deepseek-v4-pro", "glm:glm-5")
MODEL_LABELS = {
    "deepseek:deepseek-v4-pro": "DeepSeek-v4-pro",
    "glm:glm-5": "GLM-5",
}
OUTCOMES = (
    ("hold_ratio", "(a) Hold ratio", "change in hold ratio"),
    (
        "mean_gross_target_exposure",
        "(b) Gross target exposure",
        "change in mean gross exposure",
    ),
)


def load_primary_effects(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    selected = [
        row
        for row in rows
        if row.get("family") == "primary"
        and row.get("regime") in REGIMES
        and row.get("agent") in MODELS
        and row.get("outcome") in {item[0] for item in OUTCOMES}
    ]
    keys = {(row["regime"], row["agent"], row["outcome"]) for row in selected}
    expected = {
        (regime, model, outcome)
        for regime in REGIMES
        for model in MODELS
        for outcome, _, _ in OUTCOMES
    }
    if keys != expected or len(selected) != len(expected):
        raise ValueError(f"expected {len(expected)} unique primary effects, found {len(selected)}")
    for row in selected:
        for field in ("mean_delta", "ci_low", "ci_high", "q_value"):
            float(row[field])
    return selected


def render(rows: list[dict[str, Any]], output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.5))
    colors = {MODELS[0]: "#315f8d", MODELS[1]: "#b45f3c"}
    markers = {MODELS[0]: "o", MODELS[1]: "s"}
    offsets = {MODELS[0]: -0.10, MODELS[1]: 0.10}

    for ax, (outcome, title, ylabel) in zip(axes, OUTCOMES):
        for model in MODELS:
            series = [
                next(
                    row
                    for row in rows
                    if row["regime"] == regime
                    and row["agent"] == model
                    and row["outcome"] == outcome
                )
                for regime in REGIMES
            ]
            x_values = [index + offsets[model] for index in range(len(REGIMES))]
            means = [float(row["mean_delta"]) for row in series]
            lower = [mean - float(row["ci_low"]) for mean, row in zip(means, series)]
            upper = [float(row["ci_high"]) - mean for mean, row in zip(means, series)]
            for x_value, mean_value, low, high, row in zip(
                x_values, means, lower, upper, series
            ):
                significant = float(row["q_value"]) < 0.05
                ax.errorbar(
                    x_value,
                    mean_value,
                    yerr=[[low], [high]],
                    fmt=markers[model],
                    color=colors[model],
                    markerfacecolor=colors[model] if significant else "white",
                    markeredgewidth=1.0,
                    capsize=3,
                    elinewidth=1.0,
                    markersize=5.0,
                )
        ax.axhline(0, color="#555555", linewidth=0.7)
        ax.set_xticks(range(len(REGIMES)), [REGIME_LABELS[item] for item in REGIMES])
        ax.set_title(title, fontsize=9)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.tick_params(labelsize=7.5)
        ax.grid(axis="y", linewidth=0.35, alpha=0.35)
        ax.spines[["top", "right"]].set_visible(False)

    handles = [
        Line2D(
            [0],
            [0],
            color=colors[model],
            marker=markers[model],
            linestyle="none",
            markersize=5,
            label=MODEL_LABELS[model],
        )
        for model in MODELS
    ]
    handles.append(
        Line2D(
            [0],
            [0],
            color="#555555",
            marker="o",
            markerfacecolor="white",
            linestyle="none",
            markersize=5,
            label=r"open: $q\geq .05$",
        )
    )
    axes[0].legend(handles=handles, frameon=False, fontsize=7.2, loc="best")
    fig.tight_layout(w_pad=2.0)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "docs/results/memory_pollution_regimes/regime_effects.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "figures/regime_effects.pdf",
    )
    args = parser.parse_args()
    rows = load_primary_effects(args.input.resolve())
    render(rows, args.output.resolve())
    print(f"wrote {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
