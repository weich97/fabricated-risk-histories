# Market-regime extension — frozen before provider calls (2026-07-29)

> Public copy of a specification frozen in the author's development repository.
> Internal project labels were removed; the design, hypotheses and analysis
> text are unchanged.

This document fixes the only new experiment proposed for the current paper.
No provider call for this extension had been made when the specification was
written. The purpose is to test whether the directive-removed memory effect is
peculiar to the original upward path.

## Fixed grid

- Models: `deepseek:deepseek-v4-pro` and `glm:glm-5`, direct APIs.
- Prompt: directive removed (`--risk-feedback-mode neutral`).
- Corruption: `fake_violations`, doses 0 and 0.75, decay 0.85.
- Risk gate for the comparison: `none`, matching the paper's headline arm.
- Market length and universe: 24 steps, `SYN,ALT`.
- Sampling: seeds 1–30 and three provider samples per seed.
- Execution: $100,000 initial cash, 1 bp commission, 2 bp base slippage,
  5% participation, one-step latency, and market-impact coefficient 0.15.

The original `bullish` arm is retained without recollection. Two new regimes
change only the deterministic systematic components of the market generator:

| Regime | Trend scale | Seasonal scale | Macro scale |
|---|---:|---:|---:|
| bullish (existing) | 1 | 1 | 1 |
| bearish (new) | -1 | -1 | -1 |
| sideways (new) | 0 | 0 | 0 |

Across the 30 fixed seeds, the equal-weight mean buy-and-hold change over 24
steps is 0.368 for bullish, -0.268 for bearish, and 0.003 for sideways. All 30
bullish paths are positive, all 30 bearish paths are negative, and the sideways
set contains 16 positive and 14 negative paths. These diagnostics are computed
from the market generator before any model call and are not outcome filters.

## Estimands and decisions

Within each regime, model, and outcome, provider samples are averaged within
seed. The estimand is the paired seed-level difference between dose 0.75 and
dose 0.

Primary outcomes:

1. hold ratio;
2. mean gross approved target exposure.

The 12 primary tests (3 regimes x 2 models x 2 outcomes) form one
Benjamini–Hochberg family. We will report paired-bootstrap 95% intervals,
paired sign-flip permutation p-values, corrected q-values, and standardized
paired effects. No regime or model subgroup will be added after results are
seen.

`total_return` and `max_drawdown` are exploratory. We will report them as
paired differences with intervals and uncorrected p-values. We will not report
Sharpe ratios: 24 steps are too short for a defensible annualized estimate.

## Interpretation gate

- **Supports cross-regime susceptibility:** both new regimes show the same
  directional primary exposure effect for both models, with the 12-test family
  reported regardless of significance.
- **Regime-dependent result:** effects change direction or materially collapse
  in either new regime. The paper will present that boundary rather than pool
  regimes into a single headline number.
- **No post-hoc rescue:** no extra seeds, alternative regime definitions,
  prompt variants, or additional primary outcomes will be introduced after the
  first extension result is read.

## Cost and launch gate

The two new regimes require:

`2 regimes x 2 models x 2 doses x 30 seeds x 3 samples = 720 runs`.

At 24 decisions per run, this is **17,280 logical provider calls**. This file
and the runner/tests may be committed without launching those calls. Collection
requires an explicit budget decision after this count is reviewed.

Planned output layout:

```text
outputs/memory_pollution_regimes/
  bearish/{deepseek_v4_pro,glm_5_direct}/
  sideways/{deepseek_v4_pro,glm_5_direct}/
```
