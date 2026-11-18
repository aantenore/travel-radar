# Evals

## Deterministic (run with the unit tests)

- `tests/test_evals.py`
  - excerpt gate on every labelled excerpt (the true price passes, a wrong price is rejected);
  - source tiers of the fixture URLs;
  - advise backtests (`tests/fixtures/advise_backtests.yaml`: synthetic price histories with expected verdicts).
- `tests/test_core.py`: no price alert without an independent second observation.
- `tests/test_verified.py`: recording rules.

## Extraction accuracy (model-dependent, run by hand)

Question: can the fare-reader agent run on a cheaper model?

1. For each case in `tests/fixtures/fare_excerpts.yaml`, give the excerpt to the extractor under test (the fare-reader agent with the candidate `model:`) and save its JSON answer under the case id in `outputs.json`.
2. Run `python3 evals/score_extraction.py outputs.json --threshold 0.95`.
3. Adopt the candidate model only if `exact_match_rate` is ≥ 0.95, then record the date, model and rate in `docs/architecture.md` (ADR-3).

**Model aliases are not pinned.** `model: sonnet` or `model: haiku` in an agent file is an alias that follows the current model version. Re-run this eval when the alias moves to a new model, and whenever the fixtures grow.

| Date | Extractor model | Exact match | Decision |
|---|---|---|---|
| — | sonnet (default) | not measured yet | default until measured |
| — | haiku | not measured yet | not adopted |
