"""Offline verification of the released files and the paper's derived results.

1. Every file matches SHA256SUMS.
2. Every analysis is rerun in a temporary copy; the regenerated tables and
   reports must match the released ones.
3. The four figures are rendered in a temporary directory.
4. A slice of the rule-based memory-pollution sweep is rerun and compared with
   the released run records.

No network access or API key is used. Released files are never modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMANDS = [
    [
        "scripts/analyze_memory_pollution_llm.py",
        "--input-dirs",
        "outputs/memory_pollution_llm/claude_opus_4_7,outputs/memory_pollution_llm/deepseek_v4_pro,outputs/memory_pollution_llm/gemini_3_1_pro,outputs/memory_pollution_llm/glm_5,outputs/memory_pollution_llm/glm_5_direct,outputs/memory_pollution_llm/gpt_5_5",
        "--output-dir",
        "docs/results/memory_pollution_llm"
    ],
    [
        "scripts/analyze_memory_pollution_llm.py",
        "--input-dirs",
        "outputs/memory_pollution_llm_B/claude_opus_4_7,outputs/memory_pollution_llm_B/claude_opus_4_7_s2,outputs/memory_pollution_llm_B/deepseek_v4_pro,outputs/memory_pollution_llm_B/gemini_3_1_pro,outputs/memory_pollution_llm_B/glm_5_direct,outputs/memory_pollution_llm_B/gpt_5_5",
        "--output-dir",
        "docs/results/memory_pollution_llm_B"
    ],
    [
        "scripts/analyze_memory_pollution_llm.py",
        "--input-dirs",
        "outputs/memory_pollution_llm_abl/claude_opus_4_7,outputs/memory_pollution_llm_abl/deepseek_v4_pro,outputs/memory_pollution_llm_abl/glm_5_direct,outputs/memory_pollution_llm_abl/gpt_5_5",
        "--output-dir",
        "docs/results/memory_pollution_llm_abl"
    ],
    [
        "scripts/analyze_memory_pollution_llm.py",
        "--input-dirs",
        "outputs/memory_pollution_llm_def/deepseek_v4_pro,outputs/memory_pollution_llm_def/gemini_3_1_pro",
        "--output-dir",
        "docs/results/memory_pollution_llm_def"
    ],
    [
        "scripts/analyze_mempoll_confirm.py"
    ],
    [
        "scripts/analyze_mempoll_dose025.py"
    ],
    [
        "scripts/analyze_mempoll_neutral.py"
    ],
    [
        "scripts/analyze_mempoll_regimes.py"
    ]
]
OUTPUTS = [
    "docs/results/memory_pollution_llm/dose_response.csv",
    "docs/results/memory_pollution_llm/model_difference.csv",
    "docs/results/memory_pollution_llm/memory_pollution_llm.md",
    "docs/results/memory_pollution_llm_B/dose_response.csv",
    "docs/results/memory_pollution_llm_B/model_difference.csv",
    "docs/results/memory_pollution_llm_B/memory_pollution_llm.md",
    "docs/results/memory_pollution_llm_abl/dose_response.csv",
    "docs/results/memory_pollution_llm_abl/model_difference.csv",
    "docs/results/memory_pollution_llm_abl/memory_pollution_llm.md",
    "docs/results/memory_pollution_llm_def/dose_response.csv",
    "docs/results/memory_pollution_llm_def/model_difference.csv",
    "docs/results/memory_pollution_llm_def/memory_pollution_llm.md",
    "docs/results/memory_pollution_confirm/confirmatory_analysis.csv",
    "docs/results/memory_pollution_confirm/confirmatory_analysis.md",
    "docs/results/memory_pollution_dose025/dose025_effects.csv",
    "docs/results/memory_pollution_dose025/dose025_curve.csv",
    "docs/results/memory_pollution_dose025/dose025_analysis.md",
    "docs/results/memory_pollution_neutral/mode_effects.csv",
    "docs/results/memory_pollution_neutral/directive_interactions.csv",
    "docs/results/memory_pollution_neutral/robustness_diagnostics.csv",
    "docs/results/memory_pollution_neutral/neutral_analysis.md",
    "docs/results/memory_pollution_regimes/regime_effects.csv",
    "docs/results/memory_pollution_regimes/regime_interactions.csv",
    "docs/results/memory_pollution_regimes/regime_analysis.md"
]
FIGURES = (
    ("scripts/render_mempoll_system_figure.py", "system_isolation.pdf"),
    ("scripts/render_mempoll_neutral_figure.py", "prompt_mode_effects.pdf"),
    ("scripts/render_mempoll_regime_figure.py", "regime_effects.pdf"),
    ("scripts/render_mempoll_dose_curve.py", "dose_curve.pdf"),
)


def run(cwd: Path, *args: str) -> None:
    env = dict(os.environ, PYTHONPATH=str(cwd / "src"), PYTHONDONTWRITEBYTECODE="1", MPLBACKEND="Agg")
    done = subprocess.run([sys.executable, "-B", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"command failed: {' '.join(args)}\n{done.stdout}\n{done.stderr}")


def same_text(a: Path, b: Path) -> bool:
    return a.read_bytes().replace(b"\r\n", b"\n") == b.read_bytes().replace(b"\r\n", b"\n")


def check_manifest() -> None:
    expected = {}
    for line in (ROOT / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        sha, rel = line.split("  ", 1)
        expected[rel] = sha
    actual = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*")
              if p.is_file() and ".git" not in p.relative_to(ROOT).parts
              and "__pycache__" not in p.parts and p.name != "SHA256SUMS"}
    missing = sorted(set(expected) - actual)
    changed = [rel for rel in sorted(set(expected) & actual)
               if hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() != expected[rel]]
    if missing or changed:
        raise SystemExit(f"manifest check failed: missing={missing[:5]} changed={changed[:5]}")
    extra = len(actual - set(expected))
    note = f" ({extra} untracked local files ignored)" if extra else ""
    print(f"PASS {len(expected)} files match SHA256SUMS{note}")


def check_reanalysis(tmp: Path) -> None:
    work = tmp / "work"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    for command in COMMANDS:
        run(work, *command)
    for rel in OUTPUTS:
        if not same_text(work / rel, ROOT / rel):
            raise SystemExit(f"regenerated output differs: {rel}")
    print(f"PASS {len(COMMANDS)} analyses rerun; {len(OUTPUTS)} tables and reports match")


def check_figures(tmp: Path) -> None:
    for script, name in FIGURES:
        run(ROOT, script, "--output", str(tmp / name))
        if not (tmp / name).is_file():
            raise SystemExit(f"figure not produced: {name}")
    print(f"PASS {len(FIGURES)} figures rendered")


def check_rerun(tmp: Path) -> None:
    out = tmp / "rerun"
    run(ROOT, "scripts/run_memory_pollution_sweep.py", "--kinds", "fake_rejections,fake_violations",
        "--doses", "0.0,0.75", "--decays", "0.6,0.85,1.0", "--risks", "none", "--seeds", "1,2",
        "--output-dir", str(out))
    def key(r: dict) -> tuple:
        return r["kind"], float(r["dose"]), float(r["decay"]), r["risk"], r["seed"]

    with (ROOT / "docs/results/memory_pollution/memory_pollution_runs.csv").open(encoding="utf-8") as handle:
        released = {key(r): r for r in csv.DictReader(handle)}
    with (out / "memory_pollution_runs.csv").open(encoding="utf-8") as handle:
        rerun = list(csv.DictReader(handle))
    fields = ("memory_driven_leverage_amplification", "total_return", "max_drawdown")
    for row in rerun:
        ref = released[key(row)]
        if any(abs(float(row[f]) - float(ref[f])) > 1e-12 for f in fields):
            raise SystemExit(f"rule-based rerun differs: {key(row)}")
    print(f"PASS {len(rerun)} rule-based runs reproduce the released records")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-only", action="store_true",
                        help="Recompute saved results and figures without running simulator cases.")
    args = parser.parse_args()
    check_manifest()
    with tempfile.TemporaryDirectory() as tmp:
        check_reanalysis(Path(tmp))
        check_figures(Path(tmp))
        if not args.analysis_only:
            check_rerun(Path(tmp))
    print("Analysis checks passed; simulator rerun skipped (--analysis-only)."
          if args.analysis_only else "All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
