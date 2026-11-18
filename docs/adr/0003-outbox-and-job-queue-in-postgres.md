# 0003 Outbox and job queue in Postgres

- Status: Accepted
- Date: 2026-11-18

## Context

State changes must reliably trigger follow-up work: ledger transfers, Razorpay calls, refunds,
notifications. Redis and Kafka are out of scope. We also must never call an external service inside
a Postgres transaction.

## Decision

### Outbox

- `platform.outbox_events(id, topic, aggregate_id, payload, occurred_at, published_at)`.
- A use case appends events through `record_event` on the **same** unit of work as its state change,
  so the event exists if and only if the change committed.
- The worker's `OutboxRelay` claims unpublished events with `FOR UPDATE SKIP LOCKED`. For each
  `(event, subscribed consumer)` it enqueues one `platform.deliver_event` job, with
  `dedupe_key = deliver:<consumer>:<event_id>`, and marks the event published in the same
  transaction.
- `EventDelivery` runs a consumer inside one transaction that first inserts
  `processed_events(consumer, event_id)`. If that row already exists, the consumer is skipped.
  Delivery is therefore at-least-once, and processing is effectively once.
- Consumers must not call external services. A consumer that needs one enqueues a job, which runs
  outside any transaction.
- Payloads are JSON without floats. Money in payloads is always integer paise.

### Jobs

- `platform.jobs`: `kind`, `payload`, an optional unique `dedupe_key`, `status`
  (`queued`/`running`/`succeeded`/`dead`), `attempts`, `max_attempts`, `run_after`, `locked_until`,
  `last_error`.
- **Claim** is one short transaction: `UPDATE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED)`.
  It sets `running`, increments `attempts` and sets a lease. The transaction commits before the
  handler runs, so handlers may call external services.
- **Finish** (success, reschedule or bury) only applies if the row is still `running` with the same
  `attempts`. A worker whose lease expired and whose job was reclaimed cannot overwrite the new
  owner's result.
- A crashed worker's job is reclaimed when its lease expires, and the crash counts as an attempt.
- **Backoff** is exponential with full jitter: `delay = random(0, min(cap, base · 2^(attempt-1)))`,
  base 2 s, cap 30 min. Random comes from `SystemRandom` and is injected so tests are exact.
- After `max_attempts`, the job is `dead`. Admin can list dead jobs and revive them
  (`retry_dead_job` resets attempts and queues them). The admin UI comes with M2.
- `pg_notify('haatly_wakeup', …)` fires from statement-level `AFTER INSERT` triggers on `jobs`
  and `outbox_events`. The worker `LISTEN`s on a dedicated asyncpg connection and otherwise polls
  every `worker_idle_seconds` (default 5 s). NOTIFY is only a hint; correctness never depends on it.

### Scheduler

- Periodic tasks enqueue a job with `dedupe_key = schedule:<name>:<slot>`, where
  `slot = floor(epoch / interval)`.
- Leadership is `pg_try_advisory_lock` held on a dedicated connection and re-checked every tick.
  If the connection dies, Postgres releases the lock and another worker takes over. An integration
  test terminates the leader's backend and shows the rival taking over.
- Even two simultaneous leaders cannot double-schedule, because of the slot dedupe key.

### Time

All timestamps come from the injected clock and are passed as parameters. No `now()` defaults in
SQL.

## Consequences

- One Postgres is the queue. Throughput is bounded by Postgres, which is ample for this product.
- `processed_events`, succeeded jobs and published outbox rows grow forever until a retention job
  prunes them. That job is added in M6 alongside reconciliation.
