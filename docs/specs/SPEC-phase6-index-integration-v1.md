# Index integration into append and head v1

Local simulated-client assembly under ADR-MIE-007; no resources or cloud tests.
ReceiptAppendAttempt now requires a distinct explicit index table ARN. Actual S3
verification is followed by conditional index publication and only then head commit.
Index conflicts/timeouts block commit and consume the attempt. Orphan index rows
may remain after subsequent failures; no retry/repair is authorized.

ReceiptVersionIndex.read_entry reads by exact run/sequence without requiring prior
knowledge of the request; canonical hash, configured service/bucket and embedded
run/sequence are checked. Exact-request lookup retains its content-conflict check.
FOUND_UNCONFIRMED still does not authorize acknowledgement or recovery.

indexed_challenged_head reads the current committed bounded count, resolves every
entry strongly consistently and calls the existing full-prefix storage/signature
inspection. Missing/corrupt/orphan-only pending states refuse. The low-level helper
accepting supplied references remains available and grants no runtime authority.
A concurrent newer head is detected by prefix-length comparison and subsequent
state consistency checks. Full-prefix workload/latency still requires measurement.

This wires version persistence and head lookup locally, but does not implement an
authenticated HTTP API or safe original-receipt return for repeated append requests.
Trusted clocks, deployment permissions/costs and AWS fault tests remain outstanding.
