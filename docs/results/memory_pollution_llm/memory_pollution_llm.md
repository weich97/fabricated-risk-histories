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

Agents: deepseek:deepseek-v4-pro, glm:glm-5, poe:claude-opus-4.7, poe:gemini-3.1-pro, poe:glm-5, poe:gpt-5.5.

## Highest-dose hold-ratio shift (conservatism under fabricated risk)

| Agent | Kind | Hold-ratio delta | Cohen's d | perm p |
| --- | --- | ---: | ---: | ---: |
| deepseek:deepseek-v4-pro | fake_rejections | +0.069 | 0.41 | 0.003 |
| deepseek:deepseek-v4-pro | fake_violations | +0.121 | 0.57 | 0.000 |
| glm:glm-5 | fake_rejections | +0.001 | 0.04 | 0.809 |
| glm:glm-5 | fake_violations | +0.014 | 0.61 | 0.000 |
| poe:claude-opus-4.7 | fake_rejections | -0.001 | -0.13 | 0.375 |
| poe:claude-opus-4.7 | fake_violations | +0.003 | 0.25 | 0.068 |
| poe:gemini-3.1-pro | fake_rejections | +0.042 | 0.67 | 0.000 |
| poe:gemini-3.1-pro | fake_violations | +0.085 | 1.29 | 0.000 |
| poe:glm-5 | fake_rejections | -0.006 | -0.18 | 0.672 |
| poe:glm-5 | fake_violations | +0.015 | 0.45 | 0.344 |
| poe:gpt-5.5 | fake_rejections | +0.000 | 0.00 | 1.000 |
| poe:gpt-5.5 | fake_violations | -0.001 | -0.18 | 0.760 |

## Significant dose effects (BH-FDR q<0.05)

| Agent | Kind | Risk | Dose | Outcome | Delta | q |
| --- | --- | --- | ---: | --- | ---: | ---: |
| deepseek:deepseek-v4-pro | fake_rejections | none | 0.75 | hold_ratio | +0.1079 | 0.0192 |
| deepseek:deepseek-v4-pro | fake_violations | none | 0.75 | hold_ratio | +0.2345 | 0.0107 |
| glm:glm-5 | fake_violations | none | 0.75 | hold_ratio | +0.0167 | 0.0240 |
| poe:gemini-3.1-pro | fake_rejections | none | 0.75 | hold_ratio | +0.0764 | 0.0107 |
| poe:gemini-3.1-pro | fake_violations | max-position | 0.75 | hold_ratio | +0.0521 | 0.0107 |
| poe:gemini-3.1-pro | fake_violations | none | 0.75 | hold_ratio | +0.1181 | 0.0107 |
| poe:gemini-3.1-pro | fake_violations | max-position | 0.75 | turnover_events | -1.2778 | 0.0107 |
| deepseek:deepseek-v4-pro | fake_rejections | none | 0.75 | total_return | -0.0433 | 0.0240 |
| deepseek:deepseek-v4-pro | fake_violations | none | 0.75 | total_return | -0.0829 | 0.0107 |
| poe:gemini-3.1-pro | fake_rejections | none | 0.75 | total_return | -0.0211 | 0.0107 |
| poe:gemini-3.1-pro | fake_violations | max-position | 0.75 | total_return | -0.0128 | 0.0107 |
| poe:gemini-3.1-pro | fake_violations | none | 0.75 | total_return | -0.0309 | 0.0107 |
