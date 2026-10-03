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

Agents: deepseek:deepseek-v4-pro, glm:glm-5, poe:claude-opus-4.7, poe:gpt-5.5.

## Highest-dose hold-ratio shift (conservatism under fabricated risk)

| Agent | Kind | Hold-ratio delta | Cohen's d | perm p |
| --- | --- | ---: | ---: | ---: |
| deepseek:deepseek-v4-pro | blackout | -0.067 | -0.38 | 0.122 |
| glm:glm-5 | blackout | +0.045 | 1.24 | 0.000 |
| poe:claude-opus-4.7 | blackout | -0.002 | -0.20 | 0.528 |

## Significant dose effects (BH-FDR q<0.05)

| Agent | Kind | Risk | Dose | Outcome | Delta | q |
| --- | --- | --- | ---: | --- | ---: | ---: |
| glm:glm-5 | blackout | max-position | 1.0 | hold_ratio | +0.0542 | 0.0469 |
