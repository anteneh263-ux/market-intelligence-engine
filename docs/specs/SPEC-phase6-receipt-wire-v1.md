# Single-exchange receipt HTTP wire candidate

serve_one and exchange process one HTTP/1.1 request over an already established
TLS socket. They take ownership and close the socket on success and failure.
An active primary error survives cleanup failure; cleanup errors after otherwise
successful processing propagate. Neither function reconnects or retries.

Server: verified server-side TLS socket, exact POST routes, expected Host and
application/json, one canonical positive Content-Length, Connection: close.
Header section at most8192bytes; request body at most16384ASCIIbytes. Duplicate
headers, folding, controls, extra headers (including Transfer-Encoding/Expect),
ambiguous lengths, query strings and unsupported routes refuse before operations.
The actual mTLS boundary still verifies certificate-to-run authorization.

Client: verified client-side TLS, check_hostname enabled, matching configured SNI,
and exact server leaf SHA256 checked before sending. Only the strict service
response shape and200/400/403/409 status are accepted. Response body at most1MiB,
without truncation. Original response JSON remains untrusted until existing
signature, challenge, request-identity and committed-chain validators approve it.
Returned receipt_verified and retry_allowed are false even for HTTP200.

Each exchange has an absolute10second monotonic I/O budget, reset neither by
partial reads nor writes. TLS handshake, connect and accept happen upstream and
are not covered here. A service callback/fsync is cooperative; timeout is checked
before response, but no claim of forcibly interrupting blocked code is made.
The eventual listener must run operations in the native clock owner thread and
provide independent watchdog and socket-admission limits.

Exactly Content-Length bytes are read. No second request is parsed or reused;
closing does not prove unseen trailing bytes were absent. A failure after body
transmission may follow a committed write: report unknown outcome and use only
contract-authorized lookup, never automatic append retry.

60 focused tests passed, including actual local TLS HTTP-to-append integration,
strict framing faults, server pin refusal before request bytes, absolute deadline
and cleanup-error preservation. Storage clients/native clocks remain synthetic.
There is no listening daemon, connection factory, service clock lifecycle, SSM
session, AWS deployment or prospective activation in this change.
