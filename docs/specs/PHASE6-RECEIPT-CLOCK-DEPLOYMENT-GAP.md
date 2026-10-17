# Receipt service clock deployment gap — 2026-10-17

Status: unresolved deployment decision, not activation authority.

Actual call chain: build_receipt_service receives clock() from its host, passes it
to ReceiptAppendAttempt and indexed_challenged_head/original_receipt. The HTTP
boundary accepts only trusted AWS-generated Function URL IAM events. Local tests
supply synthetic integer times. No deployed endpoint or qualified service clock
exists. The source transport callback does not supply independent retention.

ClockBound diagnostic008 qualifies a bounded ARM64 Linux daemon/client setup.
It does not qualify Lambda: the required daemon, shared memory, Chrony and VMClock
configuration have not been established there. Passing ClockBound.utc_ms into a
local service factory would therefore not resolve deployment authenticity or
clock authority. Never replace this with time.time or caller-supplied signed time.
The service trust's maximum skew must also agree with an enforceable measured
clock bound; current synthetic zero-skew fixtures are not deployment parameters.

The next deployment work package should evaluate a separate Linux receipt-service
host with its own qualified native clock and service-only signing/storage access.
It must replace or correctly bridge the Function URL authentication boundary;
untrusted HTTP JSON must never be treated as an AWS IAM event. Concrete work:

1. Choose and price an authenticated ingress design, explicit identities and host
   isolation, before adding resources. Keep source and independent receipt owner
   separate. A second host by itself does not establish independent retention.
2. Define request authentication/replay policy and service clock evidence/uncertainty
   binding, including failure before/after pending, retained PUT and commit.
3. Review an ADR and deployment plan with endpoint, retention, key custody,
   least-privilege roles, logs and cleanup. No paid-plan upgrade is implied.
4. Implement/test that chosen ingress and actual cloud append/lookup/head path.
5. Freeze the complete configuration before the separately approved future pilot.

The existing Lambda candidate is not silently replaced by this document. Avoid
creating a second speculative service stack until the decision is concrete and
reviewed. Existing receipt/store schemas and H025 remain unchanged.
