# 0009 Consistency between Postgres and TigerBeetle

- Status: Accepted (implemented from M1)
- Date: 2026-11-18

## Context

TigerBeetle is the system of record for every balance: ledger 1 is INR in paise, ledger 2 is
inventory units. Postgres is the system of reference for everything else. The two cannot share a
transaction, and rule 5 forbids calling TigerBeetle from inside a Postgres transaction.
TigerBeetle's guidance for "consistency with foreign databases" is to make the foreign database the
place where intent is recorded, and to use idempotent, deterministic transfer ids so that every retry
is safe.

## Decision

### Write path

1. **Record intent.** In one Postgres transaction, the use case writes its domain change, a
   `ledger_intents` row (the transfers to apply, with their deterministic ids, `status = pending`)
   and an outbox event.
2. **Apply.** After commit, outside any transaction, TigerBeetle is called with those exact
   transfers. For checkout holds the **request handler** does this inline, so the 600 s window starts
   when the buyer presses Pay. The worker is the backstop: a job retries any intent still `pending`.
3. **Mark applied.** A second short transaction sets the intent to `applied`, or to `rejected` with
   TigerBeetle's result code (for example `exceeds_credits` for insufficient stock), and moves the
   domain state.

### Deterministic ids

`transfer_id = (unix_ms(domain_id) << 80) | first_80_bits(SHA-256(domain_id ‖ operation_kind))`.

- The top 48 bits are the UUIDv7 timestamp of the domain object. This keeps TigerBeetle's LSM
  inserts roughly time-ordered, as its id guidance recommends.
- The low 80 bits are fixed for a given `(domain id, operation kind)`. Every retry of the same
  operation produces the same id.
- Ids 0 and 2^128-1 are reserved by TigerBeetle and are rejected before submission.
- A retry that gets `exists` with identical fields is success. `exists_with_different_*` is a bug,
  and the intent is flagged.

### Inventory (ledger 2)

- One account per variant with `debits_must_not_exceed_credits`. Stock receipt credits it.
- A hold is a pending transfer, variant → `sold` account, with `timeout`. The timeout is
  configurable: 600 s in production and 2 s in tests, because TigerBeetle's clock cannot be moved by
  the injected clock.
- Capture posts the pending transfer. Cancel voids it. Timeout voids it automatically inside
  TigerBeetle. Posting or voiding an expired transfer returns `pending_transfer_expired`, which counts
  as "already voided". Restock on return is a new transfer back.
- **Display stock** is a Postgres projection updated from outbox events. It may be slightly stale and
  is used only for listings. TigerBeetle is the authority at hold time.

### Reconciler

A scheduled job compares both stores in **both directions**:

- **Postgres ahead:** an intent is `pending` or `applied` but the transfer is missing in TigerBeetle.
  Re-apply it with the same id, which is safe because ids are deterministic.
- **TigerBeetle ahead:** a transfer exists that Postgres has no applied intent for, for example after
  a Postgres point-in-time restore. Look up the intent by id. If the intent exists, mark it applied.
  If it does not, flag drift for admin review. Unknown transfers are never deleted, because
  TigerBeetle is immutable.
- Account balances are compared with the Postgres projections, and any difference is reported in
  paise or units.

M1's exit test injects a crash between the Postgres commit and the TigerBeetle call, and requires
0 drift after the reconciler runs.

## Consequences

- Every TigerBeetle write is preceded by a Postgres row. The id derivation is the single most
  important function in the ledger and gets property tests.
- The window between commit and apply is visible as `pending` intents, and the reconciler bounds how
  long it can last.
