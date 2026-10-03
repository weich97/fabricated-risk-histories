"""Confirmatory analysis for the pre-registered batch (frozen spec).

Follows docs/results/memory_pollution_confirm/CONFIRMATORY_SPEC_2026-07-16.md
exactly: paired deltas vs the batch's own d=0 cells (provider samples averaged
within seed before pairing), sign-flip permutation tests, BH-FDR across the
(dose x risk x metric) family *per agent*, paired-bootstrap 95% intervals.
Metrics are the two frozen axes: hold_ratio (published, thresholded) and
mean_gross_target_exposure (continuous). The published analyzer is left
untouched so its outputs stay byte-stable.

Writes docs/results/memory_pollution_confirm/{confirmatory_analysis.csv,
confirmatory_analysis.md} with explicit H-C1..C4 verdict lines.
"""
from __future__ import annotations

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

INPUT_DIRS = (
    ROOT / "outputs/memory_pollution_confirm/deepseek_v4_pro",
    ROOT / "outputs/memory_pollution_confirm/glm_5_direct",
)
OUT_DIR = ROOT / "docs/results/memory_pollution_confirm"
OUTCOMES = ("hold_ratio", "mean_gross_target_exposure")
EXPECTED_PER_AGENT = 720


def load_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for directory in INPUT_DIRS:
        path = directory / "memory_pollution_runs.csv"
        with path.open(encoding="utf-8") as handle:
            got = list(csv.DictReader(handle))
        if len(got) < EXPECTED_PER_AGENT:
            raise SystemExit(f"{path}: {len(got)} rows < {EXPECTED_PER_AGENT} (batch incomplete)")
        rows.extend(got)
    return rows


def seed_means(rows: list[dict[str, Any]], outcome: str) -> dict[tuple[str, float, str, int], float]:
    grouped: dict[tuple[str, float, str, int], list[float]] = defaultdict(list)
    for row in rows:
        if row.get(outcome) in ("", None):
            continue
        grouped[(str(row["agent"]), float(row["dose"]), str(row["risk"]), int(row["seed"]))].append(
            float(row[outcome])
        )
    return {key: mean(values) for key, values in grouped.items()}


def main() -> int:
    rows = load_rows()
    agents = sorted({str(r["agent"]) for r in rows})
    risks = sorted({str(r["risk"]) for r in rows})
    doses = sorted({float(r["dose"]) for r in rows if float(r["dose"]) > 0})

    results: list[dict[str, Any]] = []
    for agent in agents:
        agent_rows: list[dict[str, Any]] = []
        for outcome in OUTCOMES:
            means = seed_means(rows, outcome)
            for risk in risks:
                base = {s: v for (a, d, rk, s), v in means.items()
                        if a == agent and rk == risk and d == 0.0}
                for dose in doses:
                    cand = {s: v for (a, d, rk, s), v in means.items()
                            if a == agent and rk == risk and d == dose}
                    fit = paired_bootstrap_difference(cand, base)
                    agent_rows.append({
                        "agent": agent, "risk": risk, "dose": dose, "outcome": outcome,
                        "paired_n": fit["paired_n"], "mean_delta": fit["mean_delta"],
                        "ci_low": fit["delta_ci_low"], "ci_high": fit["delta_ci_high"],
                        "permutation_p_value": fit["permutation_p_value"],
                        "q_value": None, "cohens_d": fit["cohens_d"],
                    })
        q = benjamini_hochberg({i: r["permutation_p_value"] for i, r in enumerate(agent_rows)})
        for i, r in enumerate(agent_rows):
            r["q_value"] = q[i]
        results.extend(agent_rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["agent", "risk", "dose", "outcome", "paired_n", "mean_delta",
              "ci_low", "ci_high", "permutation_p_value", "q_value", "cohens_d"]
    with (OUT_DIR / "confirmatory_analysis.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    def cell(agent_sub: str, risk: str, dose: float, outcome: str) -> dict[str, Any]:
        for r in results:
            if (agent_sub in r["agent"] and r["risk"] == risk
                    and r["dose"] == dose and r["outcome"] == outcome):
                return r
        return {}

    def fmt(r: dict[str, Any]) -> str:
        if not r or r["mean_delta"] is None:
            return "cell missing"
        return (f"delta={r['mean_delta']:+.4f} [{r['ci_low']:+.4f}, {r['ci_high']:+.4f}], "
                f"p={r['permutation_p_value']:.4f}, q={r['q_value']:.4f}, d={r['cohens_d']:+.2f}, "
                f"n={r['paired_n']}")

    hc1 = cell("deepseek", "none", 0.75, "hold_ratio")
    hc2 = cell("deepseek", "max-position", 0.75, "hold_ratio")
    hc3 = cell("deepseek", "none", 0.75, "mean_gross_target_exposure")
    lines = [
        "# Confirmatory analysis (frozen spec 2026-07-16)",
        "",
        f"Runs: {len(rows)} (both agents complete). Tests: paired vs internal d=0,",
        "samples averaged within seed, sign-flip permutation, BH-FDR per agent",
        "across the 12-test (dose x risk x metric) family.",
        "",
        "## Frozen hypotheses",
        "",
        f"- **H-C1** deepseek no-gate d=0.75 hold_ratio (published +0.234): {fmt(hc1)}",
        f"- **H-C2** same under max-position (published +0.007): {fmt(hc2)}",
        f"- **H-C3** deepseek no-gate d=0.75 exposure decrease: {fmt(hc3)}",
        "- **H-C4** small doses (genuinely open):",
    ]
    for agent_sub in ("deepseek", "glm"):
        for dose in (0.05, 0.10):
            for outcome in OUTCOMES:
                r = cell(agent_sub, "none", dose, outcome)
                lines.append(f"  - {agent_sub} no-gate d={dose} {outcome}: {fmt(r)}")
    lines += ["", "## All cells", "",
              "Note: the deepseek max-position d=0.05/0.1 cells carry opposite-signed",
              "estimates that do not survive FDR (q=0.24/0.077); we do not interpret",
              "their sign.",
              "",
              "| Agent | Risk | Dose | Outcome | delta | 95% CI | p | q | d |",
              "| --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: |"]
    for r in results:
        lines.append(
            f"| {r['agent']} | {r['risk']} | {r['dose']} | {r['outcome']} "
            f"| {r['mean_delta']:+.4f} | [{r['ci_low']:+.4f}, {r['ci_high']:+.4f}] "
            f"| {r['permutation_p_value']:.4f} | {r['q_value']:.4f} | {r['cohens_d']:+.2f} |"
        )
    (OUT_DIR / "confirmatory_analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(results)} test rows -> {OUT_DIR / 'confirmatory_analysis.md'}")
    for line in lines[8:12]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
