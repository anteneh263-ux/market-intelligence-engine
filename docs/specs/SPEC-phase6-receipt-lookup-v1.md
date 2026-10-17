# Exact retained-version inspection v1

Read-only local adapter under ADR-MIE-005. No new store or authority, no deployment.
inspect_retained_version takes an approved expected version descriptor, explicit
bucket owner and inspection time. It performs GetObject with exact VersionId and
expected owner, bounds content to 64KiB, compares SHA256, then checks that the same
version has COMPLIANCE retention through the expected exclusive horizon. Streams
are closed on success and content refusal. No append/retry/recovery mutation API.

Returns detached reference and opaque bytes. Runtime acceptance, signature verified
and current head verified are all false. Deliberately does not parse or authenticate
receipt contents: the caller must pass bytes to the signature/identity/chain path.
A matching untrusted reference is not authoritative; expected descriptor ownership
must be enforced by the future service. The supplied clock is not an independently
measured clock. Expired expected horizons refuse before network activity.

Tests use a read-only fake with no write methods; they establish exact version and
owner parameters, content/size/retention refusals, closed streams and detachment.
No AWS behavior or full-service reconciliation is established. Signed challenged
heads, committed-state lookup and historical idempotency remain outstanding.
