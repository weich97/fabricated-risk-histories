"""Render the memory-pollution paper figures.

Figure 1: rule-based exposure-amplification dose-response, one line per
memory-decay rate, showing the curves coincide (decay does not attenuate
uniform pollution).
Figure 2: per-LLM hold-ratio shift at the highest dose, grouped by pollution
kind, with a zero line; the bars separate susceptible from immune models.

Usage:
  python scripts/render_mempollution_figures.py --output-dir figures
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DET = ROOT / "docs/results/memory_pollution/memory_pollution_dose_response.csv"
LLM = ROOT / "docs/results/memory_pollution_llm/model_difference.csv"
LLM_DOSE = ROOT / "docs/results/memory_pollution_llm/dose_response.csv"

LLM_LABEL = {
    "poe:gpt-5.5": "gpt-5.5",
    "poe:claude-opus-4.7": "claude-opus-4.7",
    "poe:gemini-3.1-pro": "gemini-3.1-pro",
    "deepseek:deepseek-v4-pro": "deepseek-v4-pro$^\\dagger$",
    "glm:glm-5": "glm-5$^\\dagger$",
    "poe:glm-5": "glm-5 (routed)",
}


def render_decay_invariance(output_dir: Path) -> Path:
    rows = list(csv.DictReader(DET.open(encoding="utf-8")))
    metric = "memory_driven_leverage_amplification"
    by_decay: dict[str, dict[float, float]] = defaultdict(dict)
    for r in rows:
        if r["kind"] == "fake_rejections" and r["risk"] == "max-position" and r["outcome"] == metric:
            by_decay[r["decay"]][float(r["dose"])] = float(r["mean_delta_vs_dose0"])
    fig, ax = plt.subplots(figsize=(5.4, 3.0))
    markers = {"0.6": "o", "0.85": "s", "1.0": "^"}
    for decay in sorted(by_decay, key=float):
        pts = sorted(by_decay[decay].items())
        xs = [0.0] + [d for d, _ in pts]
        ys = [0.0] + [v for _, v in pts]
        ax.plot(xs, ys, marker=markers.get(decay, "o"), label=f"decay {decay}", linewidth=1.4)
    ax.set_xlabel("Pollution dose", fontsize=9)
    ax.set_ylabel("Exposure-amplification shift", fontsize=9)
    ax.set_title("Decay does not attenuate uniform pollution", fontsize=9)
    ax.legend(fontsize=8, frameon=False)
    ax.tick_params(labelsize=8)
    ax.grid(linewidth=0.3, alpha=0.5)
    fig.tight_layout()
    path = output_dir / "decay_invariance.pdf"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def render_model_shift(output_dir: Path) -> Path:
    rows = [r for r in csv.DictReader(LLM.open(encoding="utf-8")) if r["outcome"] == "hold_ratio"]
    shift: dict[str, dict[str, float]] = defaultdict(dict)
    err: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    sig: dict[str, dict[str, bool]] = defaultdict(dict)
    for r in rows:
        d = float(r["mean_delta"])
        shift[r["agent"]][r["kind"]] = d
        if r.get("ci_low") not in ("", None):
            err[r["agent"]][r["kind"]] = (max(0.0, d - float(r["ci_low"])), max(0.0, float(r["ci_high"]) - d))
        p = r["permutation_p_value"]
        sig[r["agent"]][r["kind"]] = p not in ("", None) and float(p) < 0.05
    models = [m for m in LLM_LABEL if m in shift]
    models.sort(key=lambda m: -max(shift[m].values()))
    kinds = [("fake_rejections", "phantom rejections", "#4c72b0"), ("fake_violations", "phantom violations", "#c44e52")]
    fig, ax = plt.subplots(figsize=(6.6, 3.0))
    width = 0.38
    for j, (k, label, color) in enumerate(kinds):
        xs = [i + (j - 0.5) * width for i in range(len(models))]
        vals = [shift[m].get(k, 0.0) for m in models]
        lo = [err[m].get(k, (0.0, 0.0))[0] for m in models]
        hi = [err[m].get(k, (0.0, 0.0))[1] for m in models]
        bars = ax.bar(xs, vals, width, label=label, color=color, yerr=[lo, hi], capsize=2, error_kw={"linewidth": 0.7})
        for x, m, b, h in zip(xs, models, bars, hi):
            if sig[m].get(k):
                ax.text(x, b.get_height() + h + 0.006, "*", ha="center", fontsize=11)
    ax.axhline(0.0, color="black", linewidth=0.7)
    ax.set_xticks(range(len(models)), [LLM_LABEL[m] for m in models], rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("Hold-ratio shift at max dose", fontsize=9)
    ax.legend(fontsize=8, frameon=False, loc="upper right")
    ax.tick_params(labelsize=8)
    ax.set_title("* = $p<0.05$ (sign-flip permutation)", fontsize=8)
    fig.tight_layout()
    path = output_dir / "model_shift.pdf"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def render_dose_response_curves(output_dir: Path) -> Path:
    """Hold-ratio shift vs.\\ pollution dose, one line per LLM, pooled over the
    two risk settings, in two panels (one per pollution kind). Shows the effect
    is monotone in dose, not just present at the endpoint."""
    rows = [r for r in csv.DictReader(LLM_DOSE.open(encoding="utf-8")) if r["outcome"] == "hold_ratio"]
    agg: dict[str, dict[str, dict[float, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for r in rows:
        agg[r["kind"]][r["agent"]][float(r["dose"])].append(float(r["mean_delta"]))
    kinds = [("fake_rejections", "Phantom rejections"), ("fake_violations", "Phantom violations")]
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.0), sharey=True)
    for ax, (kind, title) in zip(axes, kinds):
        for agent in LLM_LABEL:
            if agent not in agg.get(kind, {}):
                continue
            doses = sorted(agg[kind][agent])
            xs = [0.0] + doses
            ys = [0.0] + [sum(agg[kind][agent][d]) / len(agg[kind][agent][d]) for d in doses]
            ax.plot(xs, ys, marker="o", linewidth=1.3, markersize=4,
                    label=LLM_LABEL[agent].replace("$^\\dagger$", ""))
        ax.axhline(0.0, color="black", linewidth=0.6)
        ax.set_title(title, fontsize=9)
        ax.set_xlabel("Pollution dose", fontsize=9)
        ax.grid(linewidth=0.3, alpha=0.5)
        ax.tick_params(labelsize=8)
    axes[0].set_ylabel("Hold-ratio shift", fontsize=9)
    axes[1].legend(fontsize=6.5, frameon=False, loc="upper left")
    fig.tight_layout()
    path = output_dir / "dose_response_curves.pdf"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def render_defense(output_dir: Path) -> Path:
    """No-defense vs.\\ journal-verify hold-ratio shift for the cases that
    moved: the additive shifts collapse to zero, the equity-rewrite one does
    not. Values read from the released model-difference tables."""

    def load(rel: str) -> dict[tuple[str, str, str], float]:
        p = ROOT / rel
        return {
            (r["agent"], r["kind"], r["outcome"]): float(r["mean_delta"])
            for r in csv.DictReader(p.open(encoding="utf-8"))
        }

    base_a = load("docs/results/memory_pollution_llm/model_difference.csv")
    base_b = load("docs/results/memory_pollution_llm_B/model_difference.csv")
    deff = load("docs/results/memory_pollution_llm_def/model_difference.csv")
    ds, gem = "deepseek:deepseek-v4-pro", "poe:gemini-3.1-pro"
    cases = [
        ("deepseek\nphantom viol.", base_a[(ds, "fake_violations", "hold_ratio")], deff[(ds, "fake_violations", "hold_ratio")]),
        ("gemini\nphantom viol.", base_a[(gem, "fake_violations", "hold_ratio")], deff[(gem, "fake_violations", "hold_ratio")]),
        ("gemini\nphantom rej.", base_a[(gem, "fake_rejections", "hold_ratio")], deff[(gem, "fake_rejections", "hold_ratio")]),
        ("deepseek\nequity rewrite", base_b[(ds, "win_streak", "hold_ratio")], deff[(ds, "win_streak", "hold_ratio")]),
    ]
    fig, ax = plt.subplots(figsize=(6.2, 3.0))
    xs = list(range(len(cases)))
    w = 0.38
    ax.bar([i - w / 2 for i in xs], [c[1] for c in cases], w, label="no defense", color="#c44e52")
    ax.bar([i + w / 2 for i in xs], [c[2] for c in cases], w, label="journal-verify", color="#4c72b0")
    ax.axhline(0.0, color="black", linewidth=0.7)
    ax.set_xticks(xs, [c[0] for c in cases], fontsize=8)
    ax.set_ylabel("Hold-ratio shift", fontsize=9)
    ax.tick_params(labelsize=8)
    ax.legend(fontsize=8, frameon=False)
    ax.set_title("Quarantine removes additive events, not rewritten values", fontsize=7.5)
    ax.grid(axis="y", linewidth=0.3, alpha=0.5)
    fig.tight_layout()
    path = output_dir / "defense.pdf"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render memory-pollution paper figures.")
    parser.add_argument("--output-dir", default="figures")
    args = parser.parse_args(argv)
    output_dir = ROOT / args.output_dir if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print("wrote", render_decay_invariance(output_dir))
    print("wrote", render_model_shift(output_dir))
    print("wrote", render_dose_response_curves(output_dir))
    print("wrote", render_defense(output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
