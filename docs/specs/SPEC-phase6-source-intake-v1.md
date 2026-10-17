# Durable source intake v1

SourceIntake.receive(raw, clock=...) captures a supplied clock before normalization,
checks its probe/session/chronology and enforces the plan's cumulative observed raw
byte limit. It normalizes Binance data and writes actual raw/probe/normalization/
payload/receipt objects before acknowledging a chained intake commit. A duplicate
frame counts again; a non-final bar is retained without an accepted market receipt.
Unknown writes, reentry and invalid input permanently stop that owner. Returned
receipts are detached from the internal predecessor chain. See ADR-MIE-008.

Tests use synthetic frames, supplied clocks and temporary ObjectWriter roots.
No WebSocket connection, authentic clock or prospective run is implemented here.
source_authenticated,clock_authenticated,runtime_accepted remain false.
Constructor rejects invalid reservation sequence, predecessor or start timing before
claiming the writer; the caller still must establish actual authorization/registry.

ClockBoundSourceIntake now provides a fixed-clock owning wrapper; see
SPEC-phase6-clockbound-intake-v1. This does not change the lower-level supplied-clock
API or its authority flags. The wrapper's tests use the real adapter/intake code
with synthetic native intervals; live transport and pilot acceptance remain open.
