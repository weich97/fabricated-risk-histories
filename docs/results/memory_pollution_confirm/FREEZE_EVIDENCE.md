# Specification freeze and replay provenance (instructed batch)

The instructed batch's design, hypotheses, and analysis were frozen in
`CONFIRMATORY_SPEC_2026-07-16.md` and committed to version control before the
fixed grid was replayed. A later per-call reconstruction found that the replay
used the shared response cache; it was therefore not a wholly new prospective
provider sample. The original specification is retained in the development
repository; the public copy removes internal project labels only.

Verification chain:

- **Spec content hash** (sha256 of the original frozen file in the development
  repository):
  `e9181d87685e702251cfa53dcf056fa54e27d25db152b4c79ee1b9ce44c08610`
- **Spec commit timestamp**: 2026-07-16 16:21:42 (UTC+9).
- **Fixed-grid replay launch**: 2026-07-16 17:12:55 (UTC+9), i.e. 51 minutes
  after the freeze commit. The collection log and per-run CSV checkpoints
  postdate the freeze.
- **Shared-cache finding**: 8,316 of the instructed arm's 8,640 logical calls
  resolve to cache entries created before the specification commit. The grid
  and analysis were frozen before replay, but most responses were not freshly
  collected after the freeze. We therefore describe this arm as a frozen-grid
  replay rather than an independent prospective replication.
- **Hash-only reconstruction**: `docs/results/memory_pollution_provenance/`
  records the prompt and response SHA-256 values and original UTC cache time
  for every headline call without releasing prompt or response text. The
  reconstruction ran in cache-only mode and reproduced every checked run
  metric.
- Freeze commits in the development repository: instructed batch
  `f15f1ff03ac8d2cc319afe33eee482d0cd60ec24`, regime extension
  `185300d270a35108eb96a879c2b5b5def6d5d3d1`, low-dose follow-up
  `f564fadcf20f94bca6e6a17520c127a29e2285e6`. That repository is private, so
  the freeze ordering cannot be checked independently from this release.

Analysis implementation: `analyze_mempoll_confirm.py` follows the frozen
analysis section exactly (paired vs internal d=0 cells, provider samples
averaged within seed, sign-flip permutation, BH-FDR per agent across the
12-test dose x risk x metric family).
