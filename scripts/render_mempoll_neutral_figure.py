"""Render the prompt-mode comparison used in the memory-pollution paper."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
MODE_LABELS = {"instructed": "directive present", "neutral": "directive removed"}
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
        if row["outcome"] in {item[0] for item in OUTCOMES}
        and row["agent"] in MODEL_LABELS
        and row["mode"] in MODE_LABELS
    ]
    expected = len(OUTCOMES) * len(MODEL_LABELS) * len(MODE_LABELS)
    if len(selected) != expected:
        raise ValueError(f"expected {expected} primary mode effects, found {len(selected)}")
    return selected


def render(rows: list[dict[str, Any]], output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.45))
    colors = {"instructed": "#315f8d", "neutral": "#c06c45"}
    markers = {"instructed": "o", "neutral": "s"}
    model_order = list(MODEL_LABELS)
    offsets = {"instructed": -0.11, "neutral": 0.11}

    for ax, (outcome, title, ylabel) in zip(axes, OUTCOMES):
        for mode in MODE_LABELS:
            series = [
                next(
                    row
                    for row in rows
                    if row["outcome"] == outcome
                    and row["agent"] == agent
                    and row["mode"] == mode
                )
                for agent in model_order
            ]
            x_values = [index + offsets[mode] for index in range(len(model_order))]
            means = [float(row["mean_delta"]) for row in series]
            lower = [mean - float(row["ci_low"]) for mean, row in zip(means, series)]
            upper = [float(row["ci_high"]) - mean for mean, row in zip(means, series)]
            ax.errorbar(
                x_values,
                means,
                yerr=[lower, upper],
                fmt=markers[mode],
                color=colors[mode],
                capsize=3,
                elinewidth=1.0,
                markersize=4.8,
                label=MODE_LABELS[mode],
            )
        ax.axhline(0, color="#555555", linewidth=0.7)
        ax.set_xticks(range(len(model_order)), [MODEL_LABELS[item] for item in model_order])
        ax.set_title(title, fontsize=9)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.tick_params(labelsize=7.5)
        ax.grid(axis="y", linewidth=0.35, alpha=0.35)
        ax.spines[["top", "right"]].set_visible(False)

    axes[0].legend(frameon=False, fontsize=7.5, loc="upper right")
    fig.tight_layout(w_pad=2.2)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "docs/results/memory_pollution_neutral/mode_effects.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "figures/prompt_mode_effects.pdf",
    )
    args = parser.parse_args()
    rows = load_primary_effects(args.input.resolve())
    render(rows, args.output.resolve())
    print(f"wrote {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
