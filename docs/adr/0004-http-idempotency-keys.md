# 0004 HTTP idempotency keys

- Status: Implemented (0719c34, 840ac2c). Store and gate in 0719c34; owner-matched row-level
  security in 840ac2c. Wiring to the checkout routes is Accepted for M4.
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

### Row-level security (added before M4)

A stored response can hold tenant data (an order, an amount), so the table cannot rely on the
scope string alone (review finding M4).

- Each row records its owner: `actor_role`, `user_id` and `store_id`, copied from the tenant
  context of the transaction that created it (migration 0007).
- `FORCE ROW LEVEL SECURITY` with an owner policy: a row is visible and changeable only when all
  three match the caller's context. `system` has its own policy, for the retention job (M6).
- If two callers ever share a `(scope, key)`, the second one's insert conflicts with a row it
  cannot see, and Postgres refuses it with a row-level security error. It never receives the
  first caller's response. Scopes should still include the actor.
- All anonymous (`public`) callers have the same owner, so `public` routes must not use
  idempotency keys. Money routes require a signed-in user anyway.
- The decision is a pure function in `platform/domain/idempotency.py`, tested over every row of the
  table above. The concurrency claim is tested with 20 simultaneous identical requests: exactly
  1 Proceed and 19 InFlight.

## Consequences

- The lease must exceed the slowest legitimate request. The default is 2 minutes.
- Stored responses are kept until a retention job prunes them (M6).
