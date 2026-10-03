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

Agents: deepseek:deepseek-v4-pro, glm:glm-5, poe:claude-opus-4.7, poe:gemini-3.1-pro, poe:gpt-5.5.

## Highest-dose hold-ratio shift (conservatism under fabricated risk)

| Agent | Kind | Hold-ratio delta | Cohen's d | perm p |
| --- | --- | ---: | ---: | ---: |
| deepseek:deepseek-v4-pro | win_streak | -0.063 | -0.47 | 0.000 |
| glm:glm-5 | win_streak | -0.002 | -0.09 | 0.528 |
| poe:claude-opus-4.7 | win_streak | -0.002 | -0.25 | 0.075 |
| poe:gemini-3.1-pro | win_streak | -0.013 | -0.28 | 0.042 |
| poe:gpt-5.5 | win_streak | +0.000 | 0.00 | 1.000 |

## Significant dose effects (BH-FDR q<0.05)

| Agent | Kind | Risk | Dose | Outcome | Delta | q |
| --- | --- | --- | ---: | --- | ---: | ---: |
| deepseek:deepseek-v4-pro | win_streak | max-position | 3.0 | turnover_events | +5.6444 | 0.0400 |
