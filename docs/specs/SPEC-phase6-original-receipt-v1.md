# Read-only original receipt return v1

original_receipt resolves the exact request's immutable index entry, inspects its
S3 version, binds original signed receipt fields to the request, then obtains the
indexed fully verified challenged head. Rechecking the same immutable index entry
and prefix coverage binds the original to that verified snapshot. Timing, retention
and key-validity completion bounds apply. No append/signature replacement or write
occurs. Missing/conflicting/pending/corrupt evidence refuses rather than retrying.

The response carries the exact original receipt and a fresh nonce-bound head.
It grants neither runtime admission nor recovery authority. Client must verify
nonce, pinning, transport and still-live process/clock continuity. It does not
resume a failed pilot or permit a new attempt. No automatic network retry policy.
Service-provided version index/SDK/key/clock remain trusted configuration; real
AWS behavior, authenticated endpoint and trusted clocks remain pending. Local
simulated-client tests are not prospective execution evidence.
