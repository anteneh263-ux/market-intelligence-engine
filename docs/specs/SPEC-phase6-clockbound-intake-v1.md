# Native-clock source-intake binding v1

Scope: local implementation and synthetic integration of ClockBoundSourceIntake.
No live source, receipt endpoint, new evidence store or prospective activation.
Existing ObjectWriter ownership and explicit physical root remain required.

The binding constructs and owns one ClockBoundClock and one SourceIntake. The
library path/hash are explicit; the maximum offset comes from the plan. A frame
has no supplied clock argument. receive calls SourceIntake.receive with the owned
clock's sample method, capturing application-intake time before normalization or
storage. The probe (including native library identity and observed uncertainty),
receipt and chained intake commit are persisted through the existing store.

Any admitted clock, normalization, persistence or reentry failure stops both
owners permanently. There is no fallback, clock replacement or retry. Close
attempts both resources even if one fails. Foreign process/thread calls refuse
without touching the native context. Construction failure closes a created clock
and preserves the original exception; a failed clock acquisition does not claim
the supplied writer. Private members are implementation details, not a security
boundary against arbitrary code in the same Python process.

This binds real adapter and intake code, but tests replace native FFI and timing
with deterministic fixtures. It does not authenticate network origin, bound
transport buffering, establish daemon policy or run an admitted pilot. All existing
source_authenticated, clock_authenticated and runtime_accepted flags remain false.
Production admission, dependency/daemon permissions and actual transport/service
wiring require separate verification. Diagnostic008 is native-clock evidence, not
a native execution of this new binding.
