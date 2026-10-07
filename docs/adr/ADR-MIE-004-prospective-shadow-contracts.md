# ADR-MIE-004 — prospective shadow contracts

Date: 2026-10-07. Status: proposed design, subject to dedicated review.
Scope: WP6-0 documentation and synthetic contract vectors only. No implementation,
store creation, migration, network connection, pilot activation or deployment.
Parent: ADR-MIE-001 and SPEC-phase6-paper-shadow-v1.md.

## Decision

Use a separately versioned prospective orchestration path. `app.paper.session`
retains simulated execution authority; `app.research` retains experiment and trial
governance. Intake records actual observations; it is not a strategy evaluator.
The coordinator owns sequencing, not prices, fills or statistical decisions.
Detailed contract: SPEC-phase6-wp0-contracts-v1.md.

The first contract profile supports one provider/instrument, fixed UTC24/7 bars,
long-only spot, no borrowed capital, and one bar per closed segment. Its only
permitted mode is ENGINEERING_DIAGNOSTIC. Other calendars, multiple instruments,
strategy promotion, and scientifically eligible forward studies are unsupported,
not silently approximated. This choice avoids certifying unknown historical N.
Future modes require a reviewed version, not a flag flip.

There are no Phase6 identities in the current Phase5 v2 API: its dataset commitment
and trial fingerprint require future data hashes that cannot be known before arrival.
Do not feed placeholders, future forecasts or Phase6 records into v2. A new additive
prospective governance protocol is required under the same `app.research` owner,
in a separately approved new store. The plan commitment precedes intake; segment
content commitments follow observation. One diagnostic run reservation is consumed
before intake. It is not a scientific family trial count or a legacy Phase5 trial_id.
Names remain distinct (`run_id`, not `trial_id`); complete_family_N stays null.
No inference from diagnostic counts and no duplicate strategy opportunity hidden
as recovery. A later scientific bridge must define full-family accounting itself.

## Ownership and physical authority

| Artifact/service | Owner | Authority |
|---|---|---|
| Immutable plan and run reservation | prospective governance under app.research | Diagnostic run identity and consumed reservation |
| Raw receipts and canonical bar segments | explicit intake adapter | What/when observed, not scientific validity of provider prices |
| Session manifest, simulated journal and checkpoint | versioned app.paper.session path | Actual simulated actions and state; no brokerage fills |
| Coordinator events and derived reports | app.research orchestration | Links/transitions only; cannot invent missing execution |
| Independent receipt service | separately selected operator/service | Monotonic externally retained bindings; not market data truth |

Use an explicit existing, absolute, non-symlink evidence root outside source,
owned by a named operator. No constructor/default creates it. The exact root,
registry destination and independent anchor trust location require separate
concrete approval; synthetic tests use isolated temporary roots only.
The proposed store is not created by this ADR. No existing store is migrated,
repaired, deduplicated, renumbered or declared obsolete. Historical schemas stay intact.

## Equivalence and intentional differences

Preserve existing engine.py, observed_engine_v1 and their hashes. A new versioned
session adapter is necessary: actual receive/decision times, carried state and
next-observed-quote fills are not equivalent to virtual OHLCV replay. Compare common
arithmetic cases against existing reference primitives, but do not claim numerical
parity across different timing or fill methods. Segment end never liquidates or
resets strategy state/cash. Orders are simulated; account order APIs are absent.

## Commit and failure boundaries

No distributed exactly-once claim. Immutable local publications, explicit exclusive
writer ownership, external anchoring and read-only verification are separately
observable steps. Unknown commits or an interrupted unanchored step are terminal
for this run version. Preserve data and require investigation; no repair or automatic
re-execution. An orderly checkpoint can be verified after restart, but v1 cannot
resume intake in the same run. This restrictive policy avoids retroactive data gaps.
Power-loss durability and hostile-machine clock integrity are not established.

## Consequences and next authorization

WP6-1 may implement pure validators against reviewed contracts with synthetic data.
WP6-2/3 require dedicated implementation review before using this new durable
protocol; this design is not proof of a working store. Real data still requires the
full WP6-5 owner/root/provider/time/cost/risk/anchor contract and explicit approval.
H-025 and prior pilot artifacts remain excluded. Phase5 scientific holds remain.
