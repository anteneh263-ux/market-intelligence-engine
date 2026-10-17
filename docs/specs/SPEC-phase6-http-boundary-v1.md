# Function URL request boundary v1

Local dispatcher tested with synthetic Lambda events. NOT deployed authentication.
Deployment requires AWS_IAM Function URL with SigV4, approved caller permissions
restricted to Function URL invocation, and no untrusted direct Lambda Invoke/event
source. Otherwise requestContext can be forged by a direct invoker. Exact endpoint
owner/domain/principal allowlist is service configuration, never request content.
Root is excluded. Actual IAM enforcement must be independently tested in AWS.

Only POST /append, /lookup, /head with bounded ASCII JSON accepted. Duplicate keys,
floats/nonfinite, unknown request fields, encoded bodies and query strings refuse.
Callbacks receive validated detached payload only. No internal exception text or
credentials returned. Uncertain operation produces409/retry_allowed=false; no
automatic fallback append or retry. Original receipt lookup is explicitly read-only.

Callbacks must bind actual service-owned append/indexed-head/original-receipt code,
key/clock/SDK instances. This module alone is not such a deployment entrypoint.
Sources checked2026-10-17:
https://docs.aws.amazon.com/lambda/latest/dg/urls-invocation.html
https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html
