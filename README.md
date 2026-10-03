# Fabricated Risk Histories and LLM Trading Decisions

Code and data for the paper:

> Weicheng Xue. When Fabricated Risk Histories Alter LLM Trading Decisions. arXiv preprint, 2026.

The study injects fabricated risk violations into the history an LLM trading
agent recalls, while the execution journal, market stream and risk rules stay
authoritative, and estimates the paired effect on later decisions. This
repository contains the run-level outcomes behind every reported table and
figure, the analysis and figure scripts, the simulator, memory and
prompt-construction code, the frozen analysis plans, and an offline verifier.

## Quick start

Python 3.11 or newer (tested with 3.12). No network access or API key is needed.

```text
python -m pip install -r requirements.txt
python verify_artifact.py
python -m pytest
```

`verify_artifact.py` checks every file against `SHA256SUMS`, reruns every
analysis in a temporary copy and compares the regenerated tables and reports
with the released ones, renders the four figures, and reruns a slice of the
rule-based memory-pollution sweep and compares it with the released records.

## Where each result comes from

| Paper item | Released results | Script |
|---|---|---|
| Figure 1 | none (diagram) | `render_mempoll_system_figure.py` |
| Table 2 dates for the upward-path arms, and the shared-cache count | `memory_pollution_provenance/` | `build_mempoll_provenance.py` |
| Figure 2, Table 3 | `memory_pollution_neutral/` | `analyze_mempoll_neutral.py`, `render_mempoll_neutral_figure.py` |
| Figure 3, Table 4, trendless-minus-downward contrast | `memory_pollution_regimes/` | `analyze_mempoll_regimes.py`, `render_mempoll_regime_figure.py` |
| Figure 4 and the dose-0.25 contrasts | `memory_pollution_dose025/` | `analyze_mempoll_dose025.py`, `render_mempoll_dose_curve.py` |
| Instructed fixed grid at doses 0.05 and 0.10 | `memory_pollution_confirm/` | `analyze_mempoll_confirm.py` |
| Section 5, rule-based decay check | `memory_pollution/` | `run_memory_pollution_sweep.py` |
| Section 5, journal reconciliation and field rewrite | `memory_pollution_llm/`, `memory_pollution_llm_B/`, `memory_pollution_llm_def/` | `analyze_memory_pollution_llm.py` |
| Exploratory blackout arm, not reported in the paper | `memory_pollution_llm_abl/` | `analyze_memory_pollution_llm.py` |

Result folders are under `docs/results/`; run-level outcomes are under
`outputs/`; scripts are under `scripts/`. The frozen analysis plans are
`CONFIRMATORY_SPEC_2026-07-16.md`, `REGIME_SPEC_2026-07-29.md` and
`DOSE025_SPEC_2026-07-30.md`; `FREEZE_EVIDENCE.md` records how the
instructed replay relates to its freeze. The provenance script needs the
private response cache and is included for inspection only.

The `memory_pollution_llm*` arms are exploratory instructed runs. Besides the
two direct models, they include routed runs of `claude-opus-4.7`, `gpt-5.5`
and `gemini-3.1-pro`, which the paper does not use for its main estimates.

## What can be reproduced

- **All reported analyses** are recomputed from the released run-level outcomes
  without any API call.
- **The rule-based sweep** can be rerun from the start, for example:

  ```text
  python scripts/run_memory_pollution_sweep.py --kinds fake_violations \
    --doses 0.0,0.75 --decays 0.6,0.85,1.0 --risks none --seeds 1,2 \
    --output-dir outputs/rerun
  ```

- **LLM runs** need provider access (`DEEPSEEK_API_KEY` and `GLM_API_KEY` for
  the direct models; the exploratory arms also used `POE_API_KEY`). Hosted
  models can change behind a fixed identifier, so a fresh collection will not
  reproduce the recorded responses byte for byte.
- **Not included:** prompt and response text, response caches and per-step
  trajectories. The prompt-construction and memory-injection code is included
  (`src/tradearena/agents/llm.py`, `src/tradearena/memory/`), and the call
  ledger records the SHA-256 of every headline prompt and response.

## License

Code is released under the MIT License in `LICENSE`.
