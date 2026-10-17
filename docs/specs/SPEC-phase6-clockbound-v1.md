# Phase 6 ClockBound adapter — implementation candidate

Status: locally implemented and tested with synthetic native responses. Not
Linux-integrated, deployed, or accepted for a prospective pilot.

The adapter uses the official ClockBound 2.0.3 C ABI on 64-bit Linux. A required
SHA256 identifies the library bytes. A bounded copy is hashed and sealed in a
Linux memfd before loading; pathname replacement and later source-inode mutation
cannot alter the loaded snapshot. Dynamic dependencies and daemon configuration
remain deployment trust boundaries requiring separate pins and review.

Only SYNCHRONIZED status is accepted. The returned UTC interval is rounded
conservatively to milliseconds and expanded by the complete CLOCK_BOOTTIME call
bracket, which includes suspend. The probe applies only at its associated
monotonic instant; it grants no future validity lease. Uncertainty above the
explicit contract (at most 100 ms), regression, native errors and reentry stop
the owner. There is no fallback to an unbounded system clock. Process/thread
ownership is enforced. Event and UTC accessors sample afresh.

Official source archives inspected: clock-bound-ffi 2.0.3 SHA256
8905228362818863465fd127b646494ea309e35736c8903555f1fa8fc2d24112;
clock-bound-d 2.0.3 SHA256
57d1b180cf45b60779c85fe5768cde1c399f286721103269268492af6e523f88.
These archive hashes are not compiled-library hashes. Source commit:
75b754b234c7001021300a3ee10876e98cdfbc71.

Remaining: build and pin the actual library and dependencies, verify native ABI
and sealed loading on Linux, configure and verify matching daemon/chrony drift
policy and VMClock disruption support, test stopped daemon/disruption and slow
calls, and connect the adapter to actual source and receipt-service call sites.
The actual receive timestamp refers to application intake, not physical wire
arrival. Transport buffering must be bounded and tested separately.

The earlier Fargate diagnostic reported 12 synchronized metadata reads with
advertised error bounds 0.1492835–2.479156 ms. That result does not establish a
future validity interval, certify this adapter, or qualify Lambda or the Mac.

Sources: https://github.com/aws/clock-bound
https://crates.io/crates/clock-bound-ffi/2.0.3
https://crates.io/crates/clock-bound-d/2.0.3
