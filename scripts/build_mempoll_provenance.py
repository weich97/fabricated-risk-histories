"""Reconstruct the direct-model call ledger without network access.

The paper sweep stores raw prompts and responses in a private JSONL cache and
only aggregate run metrics in the released CSVs.  This script replays the exact
headline grid in cache-only mode, checks the replayed metrics against those
CSVs, and emits a hash-only provenance ledger suitable for release.  Prompt and
response text never leave the private cache.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.run_memory_pollution_sweep import _run_case_trajectory

AGENTS = {
    "deepseek:deepseek-v4-pro": "deepseek_v4_pro",
    "glm:glm-5": "glm_5_direct",
}
MODES = {
    "instructed": ROOT / "outputs/memory_pollution_confirm",
    "neutral": ROOT / "outputs/memory_pollution_neutral",
}
DOSES = (0.0, 0.75)
SEEDS = tuple(range(1, 31))
SAMPLES = (0, 1, 2)
FREEZE_UTC = datetime.fromisoformat("2026-07-16T07:21:42+00:00")
CHECKED_METRICS = (
    "hold_ratio",
    "mean_gross_target_exposure",
    "turnover_events",
    "total_return",
    "max_drawdown",
)
LEDGER_FIELDS = (
    "mode",
    "agent",
    "provider",
    "model",
    "api_model",
    "dose",
    "seed",
    "sample",
    "step",
    "prompt_sha256",
    "response_sha256",
    "cache_created_at_utc",
    "collected_before_confirmatory_freeze",
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_cache(path: Path, provider: str) -> dict[str, dict[str, Any]]:
    """Load a cache using the same last-write-wins keys as the LLM adapter."""

    entries: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            prompt = str(item.get("prompt", ""))
            response = str(item.get("response_text", ""))
            prompt_hash = str(item.get("prompt_hash", ""))
            if sha256_text(prompt) != prompt_hash:
                raise RuntimeError(f"prompt hash mismatch at {path}:{line_number}")
            item = dict(item)
            item["response_hash"] = sha256_text(response)
            entries[str(item["cache_key"])] = item
            normalized = f"{item.get('provider', provider)}:{item.get('model', '')}:{prompt_hash}"
            tail = str(item.get("cache_key", "")).rsplit(":", 1)[-1]
            if len(tail) > 1 and tail.startswith("s") and tail[1:].isdigit():
                normalized = f"{normalized}:{tail}"
            entries[normalized] = item
    return entries


def load_expected_rows(path: Path, agent: str) -> dict[tuple[float, int, int], dict[str, str]]:
    expected: dict[tuple[float, int, int], dict[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            if str(row.get("agent")) != agent:
                raise RuntimeError(f"unexpected agent at {path}:{line_number}")
            key = (float(row["dose"]), int(row["seed"]), int(row["sample"]))
            is_target = (
                row["kind"] == "fake_violations"
                and key[0] in DOSES
                and math.isclose(float(row["decay"]), 0.85)
                and row["risk"] == "none"
                and key[1] in SEEDS
                and key[2] in SAMPLES
            )
            if not is_target:
                continue
            if key in expected:
                raise RuntimeError(f"duplicate target row in {path}: {key}")
            expected[key] = row
    wanted = {(dose, seed, sample) for dose in DOSES for seed in SEEDS for sample in SAMPLES}
    if set(expected) != wanted:
        raise RuntimeError(f"incomplete target grid in {path}: got {len(expected)}/{len(wanted)}")
    return expected


def verify_metrics(actual: dict[str, Any], expected: dict[str, str], label: str) -> None:
    for metric in CHECKED_METRICS:
        left = float(actual[metric])
        right = float(expected[metric])
        if not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12):
            raise RuntimeError(f"replay mismatch for {label} {metric}: {left} != {right}")


def cache_key(provider: str, model: str, prompt_hash: str, sample: int) -> str:
    base = f"{provider}:{model}:{prompt_hash}"
    return f"{base}:s{sample}" if sample else base


def reconstruct(cache_dir: Path) -> list[dict[str, Any]]:
    ledgers: list[dict[str, Any]] = []
    cache_by_agent: dict[str, dict[str, dict[str, Any]]] = {}
    for agent in AGENTS:
        provider, model = agent.split(":", 1)
        slug = f"{provider}_{model}".replace("-", "_")
        cache_by_agent[agent] = load_cache(cache_dir / f"{slug}.jsonl", provider)

    total_runs = len(MODES) * len(AGENTS) * len(DOSES) * len(SEEDS) * len(SAMPLES)
    completed = 0
    for mode, output_root in MODES.items():
        for agent, directory in AGENTS.items():
            provider, model = agent.split(":", 1)
            expected_rows = load_expected_rows(
                output_root / directory / "memory_pollution_runs.csv", agent
            )
            entries = cache_by_agent[agent]
            for dose in DOSES:
                pollution = {} if dose == 0.0 else {
                    "memory_pollution_kind": "fake_violations",
                    "memory_pollution_dose": dose,
                }
                for seed in SEEDS:
                    for sample in SAMPLES:
                        call_trace: list[dict[str, Any]] = []
                        trajectory, metrics = _run_case_trajectory(
                            agent=agent,
                            kind="fake_violations",
                            pollution_kwargs=pollution,
                            decay=0.85,
                            risk="none",
                            seed=seed,
                            sample=sample,
                            periods=24,
                            symbols=("SYN", "ALT"),
                            cache_dir=cache_dir,
                            risk_feedback_mode="neutral" if mode == "neutral" else "true",
                            market_regime="bullish",
                            cache_only=True,
                            preloaded_llm_cache=entries,
                            llm_call_trace=call_trace,
                        )
                        label = f"{mode}/{agent}/d={dose}/seed={seed}/sample={sample}"
                        verify_metrics(metrics, expected_rows[(dose, seed, sample)], label)
                        if len(trajectory.steps) != 24 or len(call_trace) != 24:
                            raise RuntimeError(
                                f"expected 24 steps and calls for {label}; "
                                f"got {len(trajectory.steps)} steps and {len(call_trace)} calls"
                            )
                        for step_number, call in enumerate(call_trace, start=1):
                            prompt_hash = str(call["prompt_hash"])
                            response_hash = str(call["response_hash"])
                            key = cache_key(provider, model, prompt_hash, sample)
                            if key not in entries:
                                raise RuntimeError(f"missing cache entry for {label}/step={step_number}")
                            entry = entries[key]
                            if entry["response_hash"] != response_hash:
                                raise RuntimeError(f"response hash mismatch for {label}/step={step_number}")
                            created_at = datetime.fromtimestamp(int(entry["created_at"]), tz=timezone.utc)
                            ledgers.append(
                                {
                                    "mode": mode,
                                    "agent": agent,
                                    "provider": provider,
                                    "model": model,
                                    "api_model": str(entry.get("api_model", model)),
                                    "dose": dose,
                                    "seed": seed,
                                    "sample": sample,
                                    "step": step_number,
                                    "prompt_sha256": prompt_hash,
                                    "response_sha256": response_hash,
                                    "cache_created_at_utc": created_at.isoformat().replace("+00:00", "Z"),
                                    "collected_before_confirmatory_freeze": (
                                        mode == "instructed" and created_at < FREEZE_UTC
                                    ),
                                }
                            )
                        completed += 1
                        if completed % 60 == 0:
                            print(f"replayed {completed}/{total_runs} runs", flush=True)
    return ledgers


def summary_rows(ledger: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in ledger:
        groups[(str(row["mode"]), str(row["agent"]), float(row["dose"]))].append(row)
    output: list[dict[str, Any]] = []
    for (mode, agent, dose), rows in sorted(groups.items()):
        unique_pairs = {(row["prompt_sha256"], row["response_sha256"]) for row in rows}
        prefreeze_pairs = {
            (row["prompt_sha256"], row["response_sha256"])
            for row in rows
            if row["collected_before_confirmatory_freeze"]
        }
        output.append(
            {
                "mode": mode,
                "agent": agent,
                "dose": dose,
                "runs": len(rows) // 24,
                "logical_calls": len(rows),
                "unique_prompt_response_pairs": len(unique_pairs),
                "first_cache_timestamp_utc": min(row["cache_created_at_utc"] for row in rows),
                "last_cache_timestamp_utc": max(row["cache_created_at_utc"] for row in rows),
                "logical_calls_collected_before_confirmatory_freeze": sum(
                    bool(row["collected_before_confirmatory_freeze"]) for row in rows
                ),
                "unique_pairs_collected_before_confirmatory_freeze": len(prefreeze_pairs),
            }
        )
    return output


def write_csv(path: Path, rows: list[dict[str, Any]], fields: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_readme(path: Path, summaries: list[dict[str, Any]]) -> None:
    total_calls = sum(int(row["logical_calls"]) for row in summaries)
    prefreeze_calls = sum(
        int(row["logical_calls_collected_before_confirmatory_freeze"])
        for row in summaries
    )
    lines = [
        "# Call provenance",
        "",
        "This directory is a hash-only reconstruction of the direct-model calls used by the",
        "headline instructed and directive-removed comparison. The reconstruction ran with",
        "the provider adapters forced to cache-only mode and reproduced every released run",
        "metric checked by the script. It made no network requests and releases no prompt or",
        "response text.",
        "",
        f"- Logical calls reconstructed: {total_calls:,} (720 runs x 24 steps).",
        "- Each ledger row records SHA-256 hashes for one prompt and response, the model",
        "  identifier, sample index, and the original private-cache timestamp.",
        "- The two symbols in a step share one model call, so the ledger records one row per",
        "  step rather than one row per returned signal.",
        "",
        "## Important provenance finding",
        "",
        f"{prefreeze_calls:,} instructed-arm logical calls resolve to cache entries created",
        "before the confirmatory specification was committed at 2026-07-16 07:21:42 UTC.",
        "The grid and analysis were frozen before the confirmatory replay, but the replay did",
        "not use a fresh isolated response cache. Accordingly, these data must not be described",
        "as wholly prospectively collected after registration. The neutral arm was collected",
        "later and is reported with its own timestamps.",
        "",
        "## Files",
        "",
        "- `call_provenance.csv`: one hash-only row per logical model call.",
        "- `provenance_summary.csv`: counts and timestamp ranges by mode, model, and dose.",
        "- `build_mempoll_provenance.py`: reconstruction and validation code in `scripts/`.",
        "",
        "Regenerate only on a machine holding the private cache:",
        "",
        "```text",
        "python scripts/build_mempoll_provenance.py",
        "```",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", default="outputs/llm_cache/memory_pollution")
    parser.add_argument("--output-dir", default="docs/results/memory_pollution_provenance")
    args = parser.parse_args(argv)
    cache_dir = Path(args.cache_dir)
    output_dir = Path(args.output_dir)
    if not cache_dir.is_absolute():
        cache_dir = ROOT / cache_dir
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir

    ledger = reconstruct(cache_dir)
    summaries = summary_rows(ledger)
    write_csv(output_dir / "call_provenance.csv", ledger, LEDGER_FIELDS)
    write_csv(output_dir / "provenance_summary.csv", summaries, summaries[0].keys())
    write_readme(output_dir / "README.md", summaries)
    print(f"wrote {len(ledger)} call records to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
