# Confirmatory analysis (frozen spec 2026-07-16)

Runs: 1440 (both agents complete). Tests: paired vs internal d=0,
samples averaged within seed, sign-flip permutation, BH-FDR per agent
across the 12-test (dose x risk x metric) family.

## Frozen hypotheses

- **H-C1** deepseek no-gate d=0.75 hold_ratio (published +0.234): delta=+0.2308 [+0.1787, +0.2840], p=0.0005, q=0.0030, d=+1.46, n=30
- **H-C2** same under max-position (published +0.007): delta=+0.0116 [-0.0549, +0.0773], p=0.7306, q=0.7306, d=+0.06, n=30
- **H-C3** deepseek no-gate d=0.75 exposure decrease: delta=-0.3526 [-0.4260, -0.2801], p=0.0005, q=0.0030, d=-1.61, n=30
- **H-C4** small doses (genuinely open):
  - deepseek no-gate d=0.05 hold_ratio: delta=+0.0130 [-0.0102, +0.0384], p=0.3078, q=0.3694, d=+0.19, n=30
  - deepseek no-gate d=0.05 mean_gross_target_exposure: delta=-0.0245 [-0.0616, +0.0106], p=0.1869, q=0.2492, d=-0.24, n=30
  - deepseek no-gate d=0.1 hold_ratio: delta=+0.0363 [+0.0125, +0.0590], p=0.0075, q=0.0225, d=+0.54, n=30
  - deepseek no-gate d=0.1 mean_gross_target_exposure: delta=-0.0623 [-0.0961, -0.0270], p=0.0020, q=0.0080, d=-0.62, n=30
  - glm no-gate d=0.05 hold_ratio: delta=-0.0021 [-0.0067, +0.0021], p=0.4118, q=0.5211, d=-0.17, n=30
  - glm no-gate d=0.05 mean_gross_target_exposure: delta=-0.0191 [-0.0263, -0.0112], p=0.0005, q=0.0015, d=-0.86, n=30
  - glm no-gate d=0.1 hold_ratio: delta=-0.0002 [-0.0072, +0.0067], p=1.0000, q=1.0000, d=-0.01, n=30
  - glm no-gate d=0.1 mean_gross_target_exposure: delta=-0.0353 [-0.0481, -0.0222], p=0.0005, q=0.0015, d=-0.97, n=30

## All cells

Note: the deepseek max-position d=0.05/0.1 cells carry opposite-signed
estimates that do not survive FDR (q=0.24/0.077); we do not interpret
their sign.

| Agent | Risk | Dose | Outcome | delta | 95% CI | p | q | d |
| --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: |
| deepseek:deepseek-v4-pro | max-position | 0.05 | hold_ratio | -0.0194 | [-0.0461, +0.0062] | 0.1604 | 0.2406 | -0.26 |
| deepseek:deepseek-v4-pro | max-position | 0.1 | hold_ratio | -0.0576 | [-0.1065, -0.0100] | 0.0390 | 0.0780 | -0.40 |
| deepseek:deepseek-v4-pro | max-position | 0.75 | hold_ratio | +0.0116 | [-0.0549, +0.0773] | 0.7306 | 0.7306 | +0.06 |
| deepseek:deepseek-v4-pro | none | 0.05 | hold_ratio | +0.0130 | [-0.0102, +0.0384] | 0.3078 | 0.3694 | +0.19 |
| deepseek:deepseek-v4-pro | none | 0.1 | hold_ratio | +0.0363 | [+0.0125, +0.0590] | 0.0075 | 0.0225 | +0.54 |
| deepseek:deepseek-v4-pro | none | 0.75 | hold_ratio | +0.2308 | [+0.1787, +0.2840] | 0.0005 | 0.0030 | +1.46 |
| deepseek:deepseek-v4-pro | max-position | 0.05 | mean_gross_target_exposure | +0.0136 | [-0.0044, +0.0322] | 0.1604 | 0.2406 | +0.26 |
| deepseek:deepseek-v4-pro | max-position | 0.1 | mean_gross_target_exposure | +0.0403 | [+0.0070, +0.0745] | 0.0390 | 0.0780 | +0.40 |
| deepseek:deepseek-v4-pro | max-position | 0.75 | mean_gross_target_exposure | -0.0081 | [-0.0541, +0.0384] | 0.7306 | 0.7306 | -0.06 |
| deepseek:deepseek-v4-pro | none | 0.05 | mean_gross_target_exposure | -0.0245 | [-0.0616, +0.0106] | 0.1869 | 0.2492 | -0.24 |
| deepseek:deepseek-v4-pro | none | 0.1 | mean_gross_target_exposure | -0.0623 | [-0.0961, -0.0270] | 0.0020 | 0.0080 | -0.62 |
| deepseek:deepseek-v4-pro | none | 0.75 | mean_gross_target_exposure | -0.3526 | [-0.4260, -0.2801] | 0.0005 | 0.0030 | -1.61 |
| glm:glm-5 | max-position | 0.05 | hold_ratio | +0.0019 | [-0.0028, +0.0067] | 0.5227 | 0.5703 | +0.13 |
| glm:glm-5 | max-position | 0.1 | hold_ratio | +0.0028 | [-0.0032, +0.0093] | 0.4343 | 0.5211 | +0.16 |
| glm:glm-5 | max-position | 0.75 | hold_ratio | +0.0113 | [+0.0044, +0.0183] | 0.0060 | 0.0144 | +0.57 |
| glm:glm-5 | none | 0.05 | hold_ratio | -0.0021 | [-0.0067, +0.0021] | 0.4118 | 0.5211 | -0.17 |
| glm:glm-5 | none | 0.1 | hold_ratio | -0.0002 | [-0.0072, +0.0067] | 1.0000 | 1.0000 | -0.01 |
| glm:glm-5 | none | 0.75 | hold_ratio | +0.0197 | [+0.0123, +0.0275] | 0.0005 | 0.0015 | +0.87 |
| glm:glm-5 | max-position | 0.05 | mean_gross_target_exposure | -0.0023 | [-0.0060, +0.0012] | 0.2204 | 0.3306 | -0.22 |
| glm:glm-5 | max-position | 0.1 | mean_gross_target_exposure | -0.0034 | [-0.0079, +0.0008] | 0.1264 | 0.2167 | -0.29 |
| glm:glm-5 | max-position | 0.75 | mean_gross_target_exposure | -0.0081 | [-0.0134, -0.0030] | 0.0080 | 0.0160 | -0.54 |
| glm:glm-5 | none | 0.05 | mean_gross_target_exposure | -0.0191 | [-0.0263, -0.0112] | 0.0005 | 0.0015 | -0.86 |
| glm:glm-5 | none | 0.1 | mean_gross_target_exposure | -0.0353 | [-0.0481, -0.0222] | 0.0005 | 0.0015 | -0.97 |
| glm:glm-5 | none | 0.75 | mean_gross_target_exposure | -0.0661 | [-0.0789, -0.0547] | 0.0005 | 0.0015 | -1.95 |
