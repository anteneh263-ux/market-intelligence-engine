# Owned receipt roundtrip candidate

Status 2026-10-17: implemented, independently reviewed and locally integration-tested.
No deployed receipt endpoint or prospective acceptance.

`ClockBoundReceiptHost` owns one pinned native clock, one ObjectWriter and a
certificate-to-run allowlist. Append, lookup and challenged-head operations use
that owner's UTC callback. Before returning UTC the callback durably publishes a
hash-linked `p6.service_clock_observation.v1` containing the actual sample/probe.
Clock or publication failure stops the owner; a response after an uncertain write
cannot become an acknowledged append. Service time is never taken from caller JSON.

`accept_once` accepts only a loopback IPv4 listener and verified mTLS context.
The default idle accept budget is 120 seconds, separately followed by a 10-second
TLS-handshake budget; accepted HTTP I/O has the existing separate 10-second budget.
The idle budget accommodates minute-spaced checkpoints. It is not a hard bound on
callbacks or the entire service lifetime; an external watchdog remains required.
`request_once` connects only to the local tunnel, with a combined 10-second
connect/handshake budget. The existing wire layer checks SNI/hostname, CA, exact
server certificate pin, framing and sizes before issuing a request. No retries.

`VerifiedReceiptClient` owns a separate ObjectWriter and a fixed same-session
clock capability. It records the clock before append, outbound request, returned
response, fresh nonce challenge and cryptographic verification. It checks the
entire signed subject prefix and fresh head, including trust validity and retention,
before advancing its private prefix. HTTP 200 alone is not authority. Unknown
append outcomes and invalid responses permanently stop the client; they do not
trigger a second append or automatic repair.

Local tests use actual TCP, TLS and HTTP with the production host/client/retention
operations. Native FFI/time and AWS storage are substituted. Negative cases include
bad certificates/signatures/nonces, lost response after remote commit, publication
failure before and after mutation, reentry and clock-trace failure after head commit.
The latter leaves the remote committed evidence intact without acknowledging it.

Existing ObjectWriter ownership and storage authority are retained. No new store
format, migration, secret, AWS resource or pilot was created by this implementation.
Source `.event()` shares its intake clock/session, but it does not itself persist
an additional probe for every journal event. Do not claim such a per-event probe
archive. Native deployment, readiness, secret provisioning, policies and independent
retention checks remain qualification work.
