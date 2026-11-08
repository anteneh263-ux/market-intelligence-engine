# Void development run (not used)

`layer_B_dev_BUG_epoch_units.json`: first Layer B development run, 2026-11-04. Synthetic timestamps were
written in the wrong unit, so all 2 140 daily bars collapsed onto three calendar days and the returns were
meaningless (per-run dSR around ±44). Kept for transparency. The bug was fixed, a guard now rejects more than
one bar per calendar day, and Layer B was re-run. Nothing in this file is a result.
