# Confirmatory batch — frozen before any run (2026-07-16)

> Public copy of a specification frozen in the author's development repository.
> Internal project labels were removed; the design, hypotheses and analysis
> text are unchanged.

Committed before the first row of this batch executes. Purpose: a single
pre-specified confirmatory replication that simultaneously addresses the three
sharpest concerns about the memory-pollution results:

1. **Sequential-sampling hygiene.** The published 30-seed cells grew out of
   5→10→30 seed escalations decided after looking at estimates. This batch
   re-runs the headline comparison as a *confirmatory* protocol: cells, doses,
   seeds, metrics, and tests fixed here in advance; whatever it shows is
   reported.
2. **Dose realism.** The published LLM arms test d ∈ {0.25, 0.75}; the threat
   model's motivating faults (a stale tool batch, one hallucinated reflection)
   are small. This batch adds d ∈ {0.05, 0.10}.
3. **Thresholded primary metric.** Hold ratio is deadband-thresholded. The
   harness now also records `mean_gross_target_exposure` (continuous, per-step
   gross approved target weight), so behavioral shifts are measured on a
   smooth axis alongside hold ratio.

## Frozen design

- Agents: `deepseek:deepseek-v4-pro`, `glm:glm-5` (both direct, version-pinned).
- Kind: `fake_violations` (the published headline channel).
- Doses: {0, 0.05, 0.10, 0.75}; decay 0.85; risks {max-position, none};
  seeds 1–30; 3 provider samples per seed; 24 periods; SYN,ALT.
- 720 runs per agent; output `outputs/memory_pollution_confirm/<agent_slug>`;
  paired within seed (samples averaged within seed before pairing).

## Frozen hypotheses

- H-C1 (replication): deepseek's no-gate hold-ratio shift at d=0.75 is
  positive and significant (published: +0.234, p<0.001).
- H-C2 (gate masking): the same shift under max-position is near zero
  (published: +0.007).
- H-C3 (continuous axis): the d=0.75 no-gate shift is also visible as a
  *decrease* in mean_gross_target_exposure (conservatism on the smooth axis).
- H-C4 (small doses, genuinely open): direction expected negative-exposure at
  d ∈ {0.05, 0.10} but magnitude unknown; a null at small doses is an honest,
  reportable bound on the threat model's realistic regime.

## Frozen analysis

Paired deltas vs the batch's own d=0 cells; exact sign-flip permutation tests;
BH-FDR across the (dose × risk × metric) family per agent; effect sizes with
95% paired-bootstrap intervals. Note: the batch runs under the current pinned
harness; its d=0 baselines are internal, so no cross-era path comparability is
assumed. Published-vs-confirmatory numerical drift (provider-side model
updates since June are possible even on pinned APIs) is reported as observed.
