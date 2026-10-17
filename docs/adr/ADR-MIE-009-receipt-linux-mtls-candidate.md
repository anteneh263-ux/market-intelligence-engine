# ADR-MIE-009 — Linux receipt ingress candidate

Status: proposed deployment; local ingress implementation authorized. No resource
activation, pilot approval, store migration or replacement of the Lambda ingress.

## Decision and rationale

Evaluate a separate Linux receipt host running the qualified ClockBound stack,
with end-to-end mTLS at the application. A narrowly scoped SSM forwarding tunnel
provides connectivity; it does not supply application identity. Existing Function
URL IAM validation remains a separate ingress. Never manufacture an IAM event
from an arbitrary HTTP request. Shared transport-neutral receipt operations retain
existing append/pending/index/retention/head semantics and storage owners.

A deployment should bind the TLS listener to loopback on one fixed port (candidate
8443), forward only that port to the exact managed instance, and permit no public
inbound traffic. Source credentials may start only this forwarding document on
this target, not arbitrary remote-host forwarding, shell or RunCommand. A custom
fixed-port document and IAM policy must be reviewed before deployment. SSM Agent,
plugin and endpoint connectivity must be pinned/tested. Tunnel disconnection is
terminal for the attempt; no silent replay of an uncertain append.

## Identity and authority

The service verifies a private CA and requires client certificates. The local
boundary additionally pins exact leaf DER SHA256 values to explicit run IDs.
A CA-signed but unlisted certificate is refused. Client verification must check
service CA, hostname/SAN and the frozen service identity; no hostname bypass for
the local tunnel. TLS ends on the receipt host, not a caller-controlled proxy.
Only authenticated parsed requests reach append/lookup/head. Unknown append is
not retried; original receipt/head reads follow existing challenged-read rules.
Rotation or revocation changes the frozen allowlist and requires a new reviewed
configuration; it must not mutate an active scientific attempt silently.

Signing key, storage-role credentials and clock owner belong to the receipt host.
The source gets neither signing key nor direct S3/DynamoDB writes. Existing S3
compliance retention and committed-head/index checks remain necessary. Separate
hosts under one administrator are process/credential isolation, not independent
administrative custody; disclose that limit in the pilot package.

## Implemented versus remaining

Implemented: transport-neutral operation factory; unchanged IAM wrapper; shared
strict payload parser; mTLS socket boundary and certificate-to-run authorization.
38 tests pass, including real local TLS handshakes and existing simulated-storage
append operations. Synthetic test certificates are never deployment credentials.

Not implemented here: HTTP listener/parser/client, service native-clock lifecycle,
clock-probe evidence binding, cert/key provisioning, fixed-port tunnel policy,
cloud storage deployment, watchdog, native end-to-end verification or pilot.
The boundary accepts already parsed method/path/body from a trusted local HTTP
adapter. That adapter must obtain the same TLS connection and enforce request
length, framing, read timeouts, no ambiguous transfer encoding and sequential
single-owner execution. No user JSON may stand in for its socket.

Trust skew must accommodate measured native bounds; synthetic zero-skew fixtures
are not deployable. Failure must prevent signing/acknowledgement and preserve
unknown writes. The source and receipt clocks must have separate process owners.
TLS certificate validity also depends on host time; qualify it during admission.

## Resource and cost package still required

Proposed diagnostic only: one separate short-lived ARM Linux receipt instance,
encrypted delete-on-termination disk, no inbound security-group rules, service
role, private versioned retention bucket, state/index tables, protected key storage,
and bounded logs. Source host remains separately scoped. No NAT gateway, load
balancer or public listening endpoint is proposed. Compute watchdog and operator
cleanup must be independent. Retained compliance objects cannot be deleted early;
price their full retention horizon even after compute is terminated.

Previously collected component rates are reference data only. Before launch,
produce exact region-specific prices, resource names/ARNs, request/storage quotas,
retention horizon, one-time and residual costs, free-plan availability and an
explicit spending allowance. This ADR does not authorize new resources or claim
an all-in price. Keep receipt retention cleanup distinct from compute cleanup.

Sources checked2026-10-17:
https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html
https://docs.aws.amazon.com/en_en/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html
https://aws.amazon.com/systems-manager/pricing/
SSM forwarding traffic is not recorded by Session Manager session logging;
application receipt evidence and operational logs must be retained separately.
