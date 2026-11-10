# Controlled discovery experiment

`app.discovery.experiment` runs one deterministic v2 arm and seeded uniform
controls without replacement. Each arm starts with empty evidence and learns
only from its own development outcomes. This tests learning from scratch, not
transfer from the existing, heterogeneous H corpus. The treatment includes v2
avoidance; controls retain the full remaining candidate pool. All arms share
candidate recipes, CSV rows, matrix and attempt budget.

The draft at `experiments/discovery_v2.draft.json` is NOT an approved scientific
contract. Its windows and assets are proposals. Existing local data have already
been used in research, so no claim of untouched holdout can be made from file
presence. Establish provenance or obtain new data before setting
`holdout_provenance_confirmed` to true. This assertion is recorded, not verified
by software. Do not reuse the holdout to tune the experiment after seeing results.

After agreeing dates, assets and budget, save a finalized contract and run:

```sh
PYTHONHASHSEED=0 .venv/bin/python -m app.discovery.experiment contract.json /tmp/mie-v2-experiment
```

The CLI requires hash seed 0 because the existing recipe family tie resolver
uses Python set iteration. This preserves the production v2 scoring behavior
while making this experiment's tie behavior reproducible. Run the Python API
only in a process started with the same hash seed for comparable runs.

The output directory must be new. `lock.json` is written before evaluations and
contains the contract, data hashes and row counts, candidate traits, policy hash,
matrix hash and source hashes. `events.jsonl` records every selected batch before
its outcomes, score breakdowns, rounds, full reports and evaluation errors.
`summary.json` is written only after successful completion of all arms.
Errors stop the experiment; a partial log is not a completed comparison.
Treatment exhaustion also stops rather than silently shrinking its budget.

All development trajectories finish before any holdout evaluation. Holdout
results never influence selection. Each arm receives N development evaluations
and N holdout evaluations: the draft proposes 100 + 100 per arm, 800 total
across one treatment and three controls. Counts alone do not establish power.

Metrics for each window: Supported / all attempts (including Inconclusive),
verdict counts, unique dominant families, family counts and attempts to first
Supported. No hit is represented by null plus a censoring flag, never zero.
Holdout first-hit means the first hit in the frozen proposal order, not an
adaptive holdout search. The summary is descriptive; overlapping recipes and
market data are correlated, so do not treat individual attempts as independent
replications or infer significance from the control seeds alone.

The runner uses the existing frozen pipeline with `write=False`, temporary
sliced CSVs and no ledger writes. It does not alter H dossiers, discovery history
or the locked forward test. Keep original CSVs and source versions to reproduce
the content hashes; staged temporary CSVs are removed on exit.
