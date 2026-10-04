# Explicit risk-feedback directive ablation

The neutral arm removes the explicit risk-feedback directive from the
user JSON but retains the cautious system role. The interaction is
the earlier instructed batch's d=.75 minus d=0 contrast minus the
later directive-removed batch's contrast. Because prompt modes were
collected sequentially, it is descriptive and does not identify a
causal effect of the directive. Within-mode contrasts also assume
provider stability across arm collection dates.

## Directive interactions

| Agent | Outcome | Family | Interaction | 95% CI | p | q |
| --- | --- | --- | ---: | --- | ---: | ---: |
| deepseek:deepseek-v4-pro | hold_ratio | primary | +0.1468 | [+0.0898, +0.2079] | 0.0005 | 0.0007 |
| glm:glm-5 | hold_ratio | primary | +0.0097 | [+0.0009, +0.0190] | 0.0425 | 0.0425 |
| deepseek:deepseek-v4-pro | mean_gross_target_exposure | primary | -0.2109 | [-0.2956, -0.1331] | 0.0005 | 0.0007 |
| glm:glm-5 | mean_gross_target_exposure | primary | -0.0435 | [-0.0560, -0.0313] | 0.0005 | 0.0007 |
| deepseek:deepseek-v4-pro | turnover_events | exploratory | -3.0222 | [-5.3889, -0.5667] | 0.0220 |  |
| glm:glm-5 | turnover_events | exploratory | +1.4111 | [-0.2778, +3.0000] | 0.1234 |  |
| deepseek:deepseek-v4-pro | total_return | exploratory | -0.0598 | [-0.0906, -0.0294] | 0.0005 |  |
| glm:glm-5 | total_return | exploratory | -0.0028 | [-0.0083, +0.0033] | 0.3433 |  |

## Within-mode effects

This prompt-mode analysis uses a four-test BH family for the neutral
primary effects. The final paper's q_R values instead use all twelve
directive-removed regime effects and are in
docs/results/memory_pollution_regimes/regime_effects.csv.
The four directive interactions have their own separate BH family.

| Mode | Agent | Outcome | Family | d=.75 - d=0 | 95% CI | p | q |
| --- | --- | --- | --- | ---: | --- | ---: | ---: |
| instructed | deepseek:deepseek-v4-pro | hold_ratio | descriptive | +0.2308 | [+0.1787, +0.2840] | 0.0005 |  |
| instructed | glm:glm-5 | hold_ratio | descriptive | +0.0197 | [+0.0123, +0.0275] | 0.0005 |  |
| neutral | deepseek:deepseek-v4-pro | hold_ratio | neutral_primary | +0.0840 | [+0.0491, +0.1220] | 0.0005 | 0.0007 |
| neutral | glm:glm-5 | hold_ratio | neutral_primary | +0.0100 | [+0.0042, +0.0157] | 0.0065 | 0.0065 |
| instructed | deepseek:deepseek-v4-pro | mean_gross_target_exposure | descriptive | -0.3526 | [-0.4260, -0.2801] | 0.0005 |  |
| instructed | glm:glm-5 | mean_gross_target_exposure | descriptive | -0.0661 | [-0.0789, -0.0547] | 0.0005 |  |
| neutral | deepseek:deepseek-v4-pro | mean_gross_target_exposure | neutral_primary | -0.1417 | [-0.1989, -0.0901] | 0.0005 | 0.0007 |
| neutral | glm:glm-5 | mean_gross_target_exposure | neutral_primary | -0.0225 | [-0.0324, -0.0125] | 0.0005 | 0.0007 |
| instructed | deepseek:deepseek-v4-pro | turnover_events | descriptive | -2.8889 | [-5.0333, -0.6556] | 0.0210 |  |
| instructed | glm:glm-5 | turnover_events | descriptive | +2.1222 | [+0.8111, +3.5889] | 0.0065 |  |
| neutral | deepseek:deepseek-v4-pro | turnover_events | descriptive | +0.1333 | [-1.2889, +1.4556] | 0.8661 |  |
| neutral | glm:glm-5 | turnover_events | descriptive | +0.7111 | [-0.5111, +2.0222] | 0.3158 |  |
| instructed | deepseek:deepseek-v4-pro | total_return | descriptive | -0.0884 | [-0.1120, -0.0662] | 0.0005 |  |
| instructed | glm:glm-5 | total_return | descriptive | -0.0062 | [-0.0125, -0.0002] | 0.0700 |  |
| neutral | deepseek:deepseek-v4-pro | total_return | descriptive | -0.0286 | [-0.0495, -0.0105] | 0.0040 |  |
| neutral | glm:glm-5 | total_return | descriptive | -0.0034 | [-0.0075, +0.0006] | 0.1289 |  |
