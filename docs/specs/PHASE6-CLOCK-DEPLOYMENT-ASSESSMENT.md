# Phase 6 clock deployment assessment — 2026-10-17

Status: design investigation, not accepted clock policy and not activation authority.

The local service's ordered integer clock checks prevent regression but cannot
establish an offset bound to UTC. The proposed 100 ms bound must be measured;
NTP-enabled does not itself prove this bound. No code may fill a clock_probe with
an invented bound or extend validity without measured drift/freshness policy.

AWS documents NTP synchronization in Lambda, but the reviewed Lambda documentation
does not establish a per-invocation measurable error-bound interface. This is a
remaining suitability question for the proposed Lambda deployment, not a claim
that AWS time is inaccurate.

Documented alternatives for investigation are ECS Fargate task metadata ClockDrift
(ClockErrorBound, synchronization status and reference timestamp) and EC2 Linux
ClockBound. Both would change deployment/cost/permission planning. Neither has
been selected or deployed. A runtime must reject unsynchronized/stale measurements,
account for sampling delay and future drift, bind process/session ownership and
monotonic time, and preserve the actual probe used at data receipt. An external
HTTP Date header with second precision is insufficient for a 100 ms bound.

Next experiment: evaluate actual measured clock bounds and metadata freshness on
an explicitly approved disposable deployment; freeze the resulting source and
failure policy before a prospective pilot. This experiment cannot replace pilot
receipts or manufacture historical clock evidence. Keep observer and receipt
service separation and their trust assumptions explicit.

Official references inspected:
https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html
https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-metadata-endpoint-v4-fargate-response.html
https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/compare-timestamps-with-clockbound.html
