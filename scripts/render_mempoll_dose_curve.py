"""Render the directive-removed bullish three-point dose curve."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("deepseek:deepseek-v4-pro", "glm:glm-5")
MODEL_LABELS = {
    "deepseek:deepseek-v4-pro": "DeepSeek-v4-pro",
    "glm:glm-5": "GLM-5",
}
DOSES = (0.0, 0.25, 0.75)
OUTCOMES = (
    ("hold_ratio", "(a) Hold ratio", "mean hold ratio"),
    (
        "mean_gross_target_exposure",
        "(b) Gross target exposure",
        "mean gross target exposure",
    ),
)


def load_curve(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    selected = [
        row
        for row in rows
        if row.get("agent") in MODELS
        and row.get("outcome") in {item[0] for item in OUTCOMES}
        and float(row["dose"]) in DOSES
    ]
    keys = {(row["agent"], row["outcome"], float(row["dose"])) for row in selected}
    expected = {
        (model, outcome, dose)
        for model in MODELS
        for outcome, _, _ in OUTCOMES
        for dose in DOSES
    }
    if keys != expected or len(selected) != len(expected):
        raise ValueError(f"expected {len(expected)} unique curve points, found {len(selected)}")
    for row in selected:
        for field in ("mean", "ci_low", "ci_high"):
            float(row[field])
    return selected


def render(rows: list[dict[str, Any]], output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.45))
    colors = {MODELS[0]: "#315f8d", MODELS[1]: "#b45f3c"}
    markers = {MODELS[0]: "o", MODELS[1]: "s"}
    offsets = {MODELS[0]: -0.012, MODELS[1]: 0.012}
    for ax, (outcome, title, ylabel) in zip(axes, OUTCOMES):
        for model in MODELS:
            series = [
                next(
                    row
                    for row in rows
                    if row["agent"] == model
                    and row["outcome"] == outcome
                    and float(row["dose"]) == dose
                )
                for dose in DOSES
            ]
            means = [float(row["mean"]) for row in series]
            lower = [mean - float(row["ci_low"]) for mean, row in zip(means, series)]
            upper = [float(row["ci_high"]) - mean for mean, row in zip(means, series)]
            x_values = [dose + offsets[model] for dose in DOSES]
            ax.errorbar(
                x_values,
                means,
                yerr=[lower, upper],
                color=colors[model],
                marker=markers[model],
                markersize=4.8,
                linestyle="none",
                capsize=3,
                label=MODEL_LABELS[model],
            )
        ax.set_xticks(DOSES, ["0", ".25", ".75"])
        ax.set_xlabel("fabricated-violation dose", fontsize=8)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.set_title(title, fontsize=9)
        ax.tick_params(labelsize=7.5)
        ax.grid(axis="y", linewidth=0.35, alpha=0.35)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].legend(frameon=False, fontsize=7.3, loc="best")
    fig.tight_layout(w_pad=2.0)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "docs/results/memory_pollution_dose025/dose025_curve.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "figures/dose_curve.pdf",
    )
    args = parser.parse_args()
    render(load_curve(args.input.resolve()), args.output.resolve())
    print(f"wrote {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
