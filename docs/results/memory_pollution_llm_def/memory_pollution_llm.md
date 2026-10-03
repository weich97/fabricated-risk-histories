# Memory Pollution Dose-Response (LLM Agents)

Controlled fabricated-memory evidence (fake risk violations, fake
rejections) is injected into the agent's recalled risk feedback; the
risk gate keeps reading the raw journal. Outcomes are behavioral
(hold ratio, turnover) since the deterministic overlay's amplification
metric does not apply to LLM decisions. Paired within seed, samples
averaged, BH-FDR over the combined model x kind x risk x dose x
outcome family.
For `win_streak`, the prompt exposes at most three equity records;
configured streak five is therefore labeled effective streak three
and used as the 30-seed replication rather than as a stronger dose.

Agents: deepseek:deepseek-v4-pro, poe:gemini-3.1-pro.

## Highest-dose hold-ratio shift (conservatism under fabricated risk)

| Agent | Kind | Hold-ratio delta | Cohen's d | perm p |
| --- | --- | ---: | ---: | ---: |
| deepseek:deepseek-v4-pro | fake_rejections | -0.006 | -0.04 | 0.786 |
| deepseek:deepseek-v4-pro | fake_violations | -0.006 | -0.04 | 0.786 |
| deepseek:deepseek-v4-pro | win_streak | -0.060 | -0.32 | 0.018 |
| poe:gemini-3.1-pro | fake_rejections | -0.031 | -0.39 | 0.105 |
| poe:gemini-3.1-pro | fake_violations | -0.031 | -0.39 | 0.105 |

## Significant dose effects (BH-FDR q<0.05)

| Agent | Kind | Risk | Dose | Outcome | Delta | q |
| --- | --- | --- | ---: | --- | ---: | ---: |
| deepseek:deepseek-v4-pro | win_streak | max-position | 3.0 | hold_ratio | -0.1595 | 0.0133 |
| deepseek:deepseek-v4-pro | win_streak | max-position | 3.0 | turnover_events | +8.6556 | 0.0133 |
| deepseek:deepseek-v4-pro | win_streak | max-position | 3.0 | total_return | +0.0392 | 0.0133 |
| deepseek:deepseek-v4-pro | win_streak | none | 3.0 | total_return | -0.0208 | 0.0500 |
