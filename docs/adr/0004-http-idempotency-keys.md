# 0004 HTTP idempotency keys

- Status: Accepted (store and use case in M0; wired to checkout routes in M4)
- Date: 2026-11-18

## Context

Checkout must be safe to retry. v1 returned the old order when a key was reused with a different
cart (audit F-10), and concurrent retries both created Razorpay orders (F-11).

## Decision

- Unsafe money endpoints require an `Idempotency-Key` header: 1–255 printable characters.
- `platform.idempotency_keys(scope, key)` stores the SHA-256 of the raw request body, a state
  (`in_progress`/`completed`), a lease, and the final status and body. The scope includes the actor,
  for example `checkout:<user_id>`, so two users never collide.
- `IdempotencyGate.begin` runs in one transaction that takes
  `pg_advisory_xact_lock(hashtextextended(scope ␟ key))`, reads the row, then decides:

| Stored | Same body hash | Result |
|---|---|---|
| none | — | **Proceed**: row inserted as `in_progress` with a lease |
| any | no | **BodyMismatch** → HTTP 422 |
| `completed` | yes | **Replay** the stored status and body |
| `in_progress`, lease live | yes | **InFlight** → HTTP 409 |
| `in_progress`, lease expired | yes | **Proceed** (takeover after a crash) |

- `complete` stores the response. `abandon` deletes an `in_progress` row so the client may retry
  after a failure that changed nothing.
- The decision is a pure function in `platform/domain/idempotency.py`, tested over every row of the
  table above. The concurrency claim is tested with 20 simultaneous identical requests: exactly
  1 Proceed and 19 InFlight.

## Consequences

- The lease must exceed the slowest legitimate request. The default is 2 minutes.
- Stored responses are kept until a retention job prunes them (M6).
