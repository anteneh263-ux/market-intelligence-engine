# Receipt mTLS boundary candidate

ReceiptMtlsBoundary requires an actual server-side ssl.SSLSocket with peer
verification CERT_REQUIRED and negotiated TLS1.2/1.3. SHA256 of the peer leaf
certificate must match a service-owned mapping to allowed run IDs. POST only;
shared schema validation rejects malformed/duplicate/noninteger JSON, extra fields
and oversized bodies. The authenticated certificate's allowed runs are checked
before append, lookup or challenged-head operations. Identity cannot come from
headers, JSON or a supplied certificate string. The code is not a defense against
arbitrary code execution inside the service process.

build_receipt_operations extracts the existing trusted operations without changing
store protocols or clock semantics. build_receipt_service preserves the separate
Function URL IAM boundary. ReceiptRequestDispatcher only validates payloads and
calls operations; it must never be exposed as an unauthenticated endpoint.

Actual local TLS tests use temporary synthetic CA/server/client keys, default
hostname verification on the client, required peer certificates on the server,
and simulated S3/DynamoDB. An allowed client reaches actual receipt append code;
unlisted/absent certificates, unauthorized runs, fake IAM JSON and malformed
requests do not. This is not an HTTP deployment or native service-clock test.
See ADR-MIE-009 for deployment constraints, pricing and unresolved work.
