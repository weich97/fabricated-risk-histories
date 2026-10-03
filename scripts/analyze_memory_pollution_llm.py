"""Merge per-model memory-pollution sweeps and run the cross-model analysis.

Each model's sweep lives in its own directory (parallel runs, independent
checkpoints). This script merges them and produces the study's headline
tables for research plan 02's LLM arm:

1. dose-response per (model, kind): paired (same seed) deltas of behavioral
   outcomes vs dose 0, sample-averaged within seed, with permutation tests
   and BH-FDR across the combined model x kind x risk x dose x outcome
   family;
2. model-difference summary (RQ4): how each model's exposure / turnover /
   hold-ratio responds to the highest pollution dose;
3. routing comparison: poe:glm-5 vs glm:glm-5 (same model name, different
   transport) on identical pollution conditions.

Usage:

  python scripts/analyze_memory_pollution_llm.py \
    --input-dirs outputs/memory_pollution_llm/gpt_5_5,... \
    --output-dir docs/results/memory_pollution_llm
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

BEHAVIORAL_OUTCOMES = ("hold_ratio", "turnover_events", "total_return", "max_drawdown")
VISIBLE_WIN_STREAK = 3.0
STREAK_KINDS = {"win_streak", "loss_streak"}


def effective_dose(kind: str, configured_dose: float) -> float:
    """Map a configured dose to the treatment visible in the LLM prompt.

    The current LLM prompt exposes only the three most recent equity-bearing
    feedback records. A configured win streak of five therefore presents the
    same evidence as a streak of three; treating them as distinct dose levels
    would manufacture a dose-response from unequal seed coverage.
    """

    if kind in STREAK_KINDS:
        return min(configured_dose, VISIBLE_WIN_STREAK)
    return configured_dose


def validate_run_design(rows: list[dict[str, Any]]) -> None:
    """Require unique raw cells and one decay setting per analysis."""

    seen: set[tuple[str, str, float, float, str, int, int]] = set()
    decays: set[float] = set()
    for row in rows:
        decay = float(row["decay"])
        key = (
            str(row["agent"]),
            str(row["kind"]),
            float(row["dose"]),
            decay,
            str(row["risk"]),
            int(row["seed"]),
            int(row.get("sample", 0)),
        )
        if key in seen:
            raise ValueError(f"duplicate raw run cell: {key}")
        seen.add(key)
        decays.add(decay)
    if len(decays) > 1:
        raise ValueError(f"analysis mixes decay settings: {sorted(decays)}")


def _selected_configured_doses(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, float, str], float]:
    """Choose one configured arm per effective dose, preferring coverage.

    For the aliased win-streak arms this selects configured streak five (the
    30-seed extension) over streak three (10 seeds), while labeling the
    treatment as effective visible streak three.
    """

    coverage: dict[tuple[str, str, float, str, float], set[tuple[int, int]]] = defaultdict(set)
    for row in rows:
        agent = str(row["agent"])
        kind = str(row["kind"])
        configured = float(row["dose"])
        effective = effective_dose(kind, configured)
        risk = str(row["risk"])
        coverage[(agent, kind, effective, risk, configured)].add(
            (int(row["seed"]), int(row.get("sample", 0)))
        )

    candidates: dict[tuple[str, str, float, str], list[tuple[int, float]]] = defaultdict(list)
    for (agent, kind, effective, risk, configured), cells in coverage.items():
        candidates[(agent, kind, effective, risk)].append((len(cells), configured))
    return {key: max(values)[1] for key, values in candidates.items()}


def validate_effective_dose_aliases(rows: list[dict[str, Any]]) -> None:
    """Reject contradictory duplicate cells for prompt-equivalent doses."""

    grouped: dict[tuple[str, str, float, str, int, int, str], list[float]] = defaultdict(list)
    for row in rows:
        kind = str(row["kind"])
        configured = float(row["dose"])
        effective = effective_dose(kind, configured)
        for outcome in BEHAVIORAL_OUTCOMES:
            if row.get(outcome) in ("", None):
                continue
            key = (
                str(row["agent"]),
                kind,
                effective,
                str(row["risk"]),
                int(row["seed"]),
                int(row.get("sample", 0)),
                outcome,
            )
            grouped[key].append(float(row[outcome]))
    for key, values in grouped.items():
        if len(values) > 1 and max(values) - min(values) > 1e-12:
            raise ValueError(f"prompt-equivalent dose cells disagree for {key}: {values}")


def load_runs(input_dirs: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for directory in input_dirs:
        path = directory / "memory_pollution_runs.csv"
        if not path.exists():
            raise SystemExit(f"Missing runs CSV: {path}")
        with path.open(encoding="utf-8") as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def _seed_means(rows: list[dict[str, Any]], outcome: str) -> dict[tuple[str, str, float, str, int], float]:
    """Average samples within each effective-dose seed cell."""

    grouped: dict[tuple[str, str, float, str, int], list[float]] = defaultdict(list)
    selected = _selected_configured_doses(rows)
    for row in rows:
        if row.get(outcome) in ("", None):
            continue
        kind = str(row["kind"])
        configured = float(row["dose"])
        effective = effective_dose(kind, configured)
        selection_key = (str(row["agent"]), kind, effective, str(row["risk"]))
        if configured != selected[selection_key]:
            continue
        key = (
            str(row["agent"]),
            kind,
            effective,
            str(row["risk"]),
            int(row["seed"]),
        )
        grouped[key].append(float(row[outcome]))
    return {key: mean(values) for key, values in grouped.items()}


def dose_response_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    agents = sorted({str(r["agent"]) for r in rows})
    kinds = sorted({str(r["kind"]) for r in rows})
    risks = sorted({str(r["risk"]) for r in rows})
    doses = sorted({effective_dose(str(r["kind"]), float(r["dose"])) for r in rows})
    selected = _selected_configured_doses(rows)
    output: list[dict[str, Any]] = []
    for outcome in BEHAVIORAL_OUTCOMES:
        seed_means = _seed_means(rows, outcome)
        for agent in agents:
            for kind in kinds:
                for risk in risks:
                    base = {
                        seed: value
                        for (a, k, d, rk, seed), value in seed_means.items()
                        if a == agent and k == kind and rk == risk and d == 0.0
                    }
                    if not base:
                        continue
                    for dose in doses:
                        if dose == 0.0:
                            continue
                        cand = {
                            seed: value
                            for (a, k, d, rk, seed), value in seed_means.items()
                            if a == agent and k == kind and rk == risk and d == dose
                        }
                        if not cand:
                            continue
                        result = paired_bootstrap_difference(cand, base)
                        output.append(
                            {
                                "agent": agent,
                                "kind": kind,
                                "risk": risk,
                                "dose": dose,
                                "configured_dose": selected[(agent, kind, dose, risk)],
                                "outcome": outcome,
                                "paired_n": result["paired_n"],
                                "mean_delta": result["mean_delta"],
                                "ci_low": result["delta_ci_low"],
                                "ci_high": result["delta_ci_high"],
                                "permutation_p_value": result["permutation_p_value"],
                                "q_value": None,
                                "cohens_d": result["cohens_d"],
                            }
                        )
    q = benjamini_hochberg({i: r["permutation_p_value"] for i, r in enumerate(output)})
    for i, r in enumerate(output):
        r["q_value"] = q[i]
    return output


def model_difference_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Highest-dose hold-ratio and turnover shift per (agent, kind), pooled over risk."""

    output = []
    selected = _selected_configured_doses(rows)
    for outcome in ("hold_ratio", "turnover_events", "total_return", "max_drawdown"):
        seed_means = _seed_means(rows, outcome)
        agents = sorted({a for (a, _k, _d, _r, _s) in seed_means})
        kinds = sorted({k for (_a, k, _d, _r, _s) in seed_means})
        for agent in agents:
            for kind in kinds:
                # Highest non-zero dose *for this kind* -- fake_* doses (<=0.75)
                # and win_streak streak lengths (up to 5) share the dose column,
                # so a global max would only ever match the streak arm.
                doses = [d for (a, k, d, _r, _s) in seed_means if a == agent and k == kind and d != 0.0]
                if not doses:
                    continue
                max_dose = max(doses)
                cand = {
                    (rk, seed): value
                    for (a, k, d, rk, seed), value in seed_means.items()
                    if a == agent and k == kind and d == max_dose
                }
                base = {
                    (rk, seed): value
                    for (a, k, d, rk, seed), value in seed_means.items()
                    if a == agent and k == kind and d == 0.0
                }
                if not cand or not base:
                    continue
                result = paired_bootstrap_difference(cand, base)
                configured = sorted(
                    {
                        chosen
                        for (chosen_agent, chosen_kind, effective, _risk), chosen in selected.items()
                        if chosen_agent == agent and chosen_kind == kind and effective == max_dose
                    }
                )
                output.append(
                    {
                        "agent": agent,
                        "kind": kind,
                        "outcome": outcome,
                        "max_dose": max_dose,
                        "configured_dose": ";".join(f"{value:g}" for value in configured),
                        "mean_delta": result["mean_delta"],
                        "ci_low": result["delta_ci_low"],
                        "ci_high": result["delta_ci_high"],
                        "permutation_p_value": result["permutation_p_value"],
                        "cohens_d": result["cohens_d"],
                    }
                )
    return output


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-model memory-pollution analysis.")
    parser.add_argument("--input-dirs", required=True)
    parser.add_argument("--output-dir", default="docs/results/memory_pollution_llm")
    args = parser.parse_args(argv)

    input_dirs = [
        Path(item) if Path(item).is_absolute() else ROOT / item
        for item in args.input_dirs.split(",")
        if item.strip()
    ]
    output_dir = ROOT / args.output_dir if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = load_runs(input_dirs)
    validate_run_design(rows)
    validate_effective_dose_aliases(rows)
    dose_response = dose_response_rows(rows)
    model_diff = model_difference_rows(rows)

    _write_csv(
        output_dir / "dose_response.csv",
        dose_response,
        ["agent", "kind", "risk", "dose", "configured_dose", "outcome", "paired_n", "mean_delta", "ci_low", "ci_high", "permutation_p_value", "q_value", "cohens_d"],
    )
    _write_csv(
        output_dir / "model_difference.csv",
        model_diff,
        ["agent", "kind", "outcome", "max_dose", "configured_dose", "mean_delta", "ci_low", "ci_high", "permutation_p_value", "cohens_d"],
    )
    _write_markdown(output_dir / "memory_pollution_llm.md", dose_response, model_diff, rows)
    sig = sum(1 for r in dose_response if r["q_value"] is not None and float(r["q_value"]) < 0.05)
    print(f"Merged {len(rows)} runs -> {len(dose_response)} dose-response rows ({sig} sig q<0.05), {len(model_diff)} model-diff rows in {output_dir}")
    return 0


def _write_markdown(path: Path, dose_response: list[dict[str, Any]], model_diff: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    agents = sorted({str(r["agent"]) for r in rows})
    lines = [
        "# Memory Pollution Dose-Response (LLM Agents)",
        "",
        "Controlled fabricated-memory evidence (fake risk violations, fake",
        "rejections) is injected into the agent's recalled risk feedback; the",
        "risk gate keeps reading the raw journal. Outcomes are behavioral",
        "(hold ratio, turnover) since the deterministic overlay's amplification",
        "metric does not apply to LLM decisions. Paired within seed, samples",
        "averaged, BH-FDR over the combined model x kind x risk x dose x",
        "outcome family.",
        "For `win_streak`, the prompt exposes at most three equity records;",
        "configured streak five is therefore labeled effective streak three",
        "and used as the 30-seed replication rather than as a stronger dose.",
        "",
        f"Agents: {', '.join(agents)}.",
        "",
        "## Highest-dose hold-ratio shift (conservatism under fabricated risk)",
        "",
        "| Agent | Kind | Hold-ratio delta | Cohen's d | perm p |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for r in sorted(model_diff, key=lambda x: (x["outcome"], x["agent"], x["kind"])):
        if r["outcome"] != "hold_ratio":
            continue
        d = f"{float(r['cohens_d']):.2f}" if r["cohens_d"] is not None else ""
        p = f"{float(r['permutation_p_value']):.3f}" if r["permutation_p_value"] is not None else ""
        lines.append(f"| {r['agent']} | {r['kind']} | {float(r['mean_delta']):+.3f} | {d} | {p} |")
    lines += [
        "",
        "## Significant dose effects (BH-FDR q<0.05)",
        "",
        "| Agent | Kind | Risk | Dose | Outcome | Delta | q |",
        "| --- | --- | --- | ---: | --- | ---: | ---: |",
    ]
    for r in dose_response:
        if r["q_value"] is not None and float(r["q_value"]) < 0.05:
            lines.append(
                f"| {r['agent']} | {r['kind']} | {r['risk']} | {r['dose']} | {r['outcome']} "
                f"| {float(r['mean_delta']):+.4f} | {float(r['q_value']):.4f} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
