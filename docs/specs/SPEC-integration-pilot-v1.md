# Approved Phase 5 integration pilot 001

Version: wp5.integration_pilot.v1. Owner: Anteneh. User explicitly approved the
repository BTC/USD daily sample, fixed 25% and 75% exposures, simulation only,
and /Users/antenehhailu/Desktop/MIE_EVIDENCE/phase5-integration-pilot-001.
H-025 is excluded. No migration, repair, legacy cutoff or prospective activation.

Entrypoint: `python -m app.research.integration_pilot` with explicit source,
SHA256, existing empty evidence root and owner. Root is caller-owned. It creates
an exclusive PLAN before the registry or either trial. Both variants and all
contracts are frozen together. Partial failure is preserved, with no automatic
retry, deletion, completion or reset. Every evaluation reservation is counted.

Pinned source: data_csv/BTCUSD_1d.csv SHA256
`d3085c72f4d0e0b646a050756fa3f3252e1befe214910417999b70bcfc744841`.
Provider provenance is not independently verified. Sealing parses all CSV rows
for commitments; this is not a pristine unseen source. Strategies receive only
causal VAL prefixes after reservation. No holdout strategy/evaluator is called.

Fixed CLI boundaries (UTC half-open): TRAIN 2018-01-01 to 2024-01-01,
VAL 2024-01-01 to 2025-01-01, HOLDOUT 2025-01-01 to 2026-07-09.
Only VAL is evaluated. No parameter search or selection. Capital 100000 simulated
USD; native BTC/USD execution profile including fees/slippage; 90% absolute
loss/drawdown limits; daily UTC grid returns from first close. Two exposures are
correlated, not independent strategies. DSR uses complete ledger N and both
outcomes, frozen threshold .95. Any invalid trial or failed DSR stops promotion.
Even PASS does not authorize further evaluation or trading.

Observed execution belongs to app.paper.session.observed_engine_v1; the legacy
engine.py stays byte-identical to its frozen source. New code/source hashes form
part of the method. No legacy artifact/hash is relabeled. Default-path parity
and legacy dependency contract are checked independently. The versioned engine
is maintained separately to preserve frozen Phase 4 integrity checks.

The runner verifies both committed executions read-only after both trials,
recomputes current statistics and verifies unchanged registry state (N=2).
REPORT distinguishes engineering status, statistical gate, source limitations,
unevaluated MC/holdout, no promotion and no prospective proof. It is a controlled
session integration pilot, not a substitute result for the separate historical
WF/CPCV/MC/holdout pipeline. Dedicated review required before real execution.
