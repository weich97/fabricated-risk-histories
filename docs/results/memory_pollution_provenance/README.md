# Call provenance

This directory is a hash-only reconstruction of the direct-model calls used by the
headline instructed and directive-removed comparison. The reconstruction ran with
the provider adapters forced to cache-only mode and reproduced every released run
metric checked by the script. It made no network requests and releases no prompt or
response text.

- Logical calls reconstructed: 17,280 (720 runs x 24 steps).
- Each ledger row records SHA-256 hashes for one prompt and response, the model
  identifier, sample index, and the original private-cache timestamp.
- The two symbols in a step share one model call, so the ledger records one row per
  step rather than one row per returned signal.

## Important provenance finding

8,316 instructed-arm logical calls resolve to cache entries created
before the confirmatory specification was committed at 2026-07-16 07:21:42 UTC.
The grid and analysis were frozen before the confirmatory replay, but the replay did
not use a fresh isolated response cache. Accordingly, these data must not be described
as wholly prospectively collected after registration. The neutral arm was collected
later and is reported with its own timestamps.

## Files

- `call_provenance.csv`: one hash-only row per logical model call.
- `provenance_summary.csv`: counts and timestamp ranges by mode, model, and dose.
- `build_mempoll_provenance.py`: reconstruction and validation code in `scripts/`.

Regenerate only on a machine holding the private cache:

```text
python scripts/build_mempoll_provenance.py
```
