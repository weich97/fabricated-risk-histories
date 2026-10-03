# Directive-removed effects across market regimes

Provider samples are averaged within seed. `q` applies only to the single
12-test primary family fixed in `REGIME_SPEC_2026-07-29.md`; financial
outcomes are exploratory and retain uncorrected p-values.

| Regime | Model | Outcome | Clean | Polluted | Delta [95% CI] | p | q |
|---|---|---|---:|---:|---:|---:|---:|
| bullish | deepseek-v4-pro | hold_ratio | +0.3021 | +0.3861 | +0.0840 [+0.0491, +0.1220] | 0.0005 | 0.0020 |
| bullish | deepseek-v4-pro | mean_gross_target_exposure | +1.0306 | +0.8889 | -0.1417 [-0.1989, -0.0901] | 0.0005 | 0.0020 |
| bullish | deepseek-v4-pro | total_return | +0.2430 | +0.2145 | -0.0286 [-0.0495, -0.0105] | 0.0040 | -- |
| bullish | deepseek-v4-pro | max_drawdown | -0.0052 | -0.0053 | -0.0001 [-0.0008, +0.0004] | 0.9695 | -- |
| bullish | glm-5 | hold_ratio | +0.1405 | +0.1505 | +0.0100 [+0.0042, +0.0157] | 0.0065 | 0.0111 |
| bullish | glm-5 | mean_gross_target_exposure | +1.3186 | +1.2961 | -0.0225 [-0.0324, -0.0125] | 0.0005 | 0.0020 |
| bullish | glm-5 | total_return | +0.3031 | +0.2997 | -0.0034 [-0.0075, +0.0006] | 0.1289 | -- |
| bullish | glm-5 | max_drawdown | -0.0055 | -0.0054 | +0.0001 [-0.0002, +0.0004] | 0.4403 | -- |
| bearish | deepseek-v4-pro | hold_ratio | +0.8653 | +0.8669 | +0.0016 [-0.0116, +0.0150] | 0.8461 | 0.8461 |
| bearish | deepseek-v4-pro | mean_gross_target_exposure | +0.1853 | +0.1747 | -0.0106 [-0.0275, +0.0064] | 0.2404 | 0.3205 |
| bearish | deepseek-v4-pro | total_return | -0.0693 | -0.0669 | +0.0024 [-0.0068, +0.0114] | 0.6067 | -- |
| bearish | deepseek-v4-pro | max_drawdown | -0.0705 | -0.0681 | +0.0024 [-0.0068, +0.0113] | 0.6127 | -- |
| bearish | glm-5 | hold_ratio | +0.8315 | +0.8299 | -0.0016 [-0.0093, +0.0056] | 0.7186 | 0.7840 |
| bearish | glm-5 | mean_gross_target_exposure | +0.2493 | +0.2519 | +0.0025 [-0.0073, +0.0129] | 0.6352 | 0.7622 |
| bearish | glm-5 | total_return | -0.0961 | -0.0995 | -0.0035 [-0.0078, +0.0006] | 0.1369 | -- |
| bearish | glm-5 | max_drawdown | -0.0974 | -0.1010 | -0.0036 [-0.0079, +0.0005] | 0.1204 | -- |
| sideways | deepseek-v4-pro | hold_ratio | +0.5958 | +0.6301 | +0.0343 [+0.0137, +0.0553] | 0.0045 | 0.0090 |
| sideways | deepseek-v4-pro | mean_gross_target_exposure | +0.5759 | +0.5191 | -0.0568 [-0.0853, -0.0290] | 0.0010 | 0.0030 |
| sideways | deepseek-v4-pro | total_return | -0.0173 | -0.0157 | +0.0016 [-0.0018, +0.0051] | 0.3873 | -- |
| sideways | deepseek-v4-pro | max_drawdown | -0.0339 | -0.0329 | +0.0011 [-0.0015, +0.0035] | 0.4198 | -- |
| sideways | glm-5 | hold_ratio | +0.4926 | +0.5039 | +0.0113 [+0.0032, +0.0192] | 0.0085 | 0.0127 |
| sideways | glm-5 | mean_gross_target_exposure | +0.7630 | +0.7416 | -0.0214 [-0.0323, -0.0110] | 0.0025 | 0.0060 |
| sideways | glm-5 | total_return | -0.0166 | -0.0165 | +0.0000 [-0.0036, +0.0035] | 0.9830 | -- |
| sideways | glm-5 | max_drawdown | -0.0395 | -0.0392 | +0.0003 [-0.0021, +0.0027] | 0.8156 | -- |

## Post-hoc trendless-minus-downward interaction

This difference-in-differences was added after inspection of the
within-regime results. It compares only the two extension regimes,
which were collected in the same July 29--30 provider window. `q`
adjusts the four primary interactions below; the analysis is
exploratory rather than part of the frozen 12-test family.

| Model | Outcome | Interaction [95% CI] | p | q |
|---|---|---:|---:|---:|
| deepseek-v4-pro | hold_ratio | +0.0326 [+0.0079, +0.0593] | 0.0250 | 0.0333 |
| deepseek-v4-pro | mean_gross_target_exposure | -0.0462 [-0.0803, -0.0132] | 0.0180 | 0.0333 |
| glm-5 | hold_ratio | +0.0130 [+0.0009, +0.0257] | 0.0490 | 0.0490 |
| glm-5 | mean_gross_target_exposure | -0.0239 [-0.0411, -0.0078] | 0.0090 | 0.0333 |
