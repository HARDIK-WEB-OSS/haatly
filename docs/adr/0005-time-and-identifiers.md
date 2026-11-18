# 0005 Time and identifiers

- Status: Accepted
- Date: 2026-11-18

## Decision

### Time

- One `Clock` protocol with `now() -> datetime`. `SystemClock` in production, `FrozenClock` in
  tests (with `advance`).
- Every timestamp is timezone-aware UTC. `require_aware` rejects naive datetimes. Ruff's `DTZ` rules
  forbid naive constructors. Every column is `timestamptz`. The database and session timezone is UTC.
- No SQL `now()` or `CURRENT_TIMESTAMP` defaults. The application passes the clock's time, so tests
  can travel in time across both Python and SQL.
- TigerBeetle has its own clock for pending-transfer timeouts, which the injected clock cannot move.
  See ADR 0009.

### Identifiers

- Every primary key is a UUIDv7 (RFC 9562): 48-bit Unix milliseconds, version 7, variant bits, then
  74 random bits from `os.urandom`.
- Python 3.12 has no `uuid7`, so `platform/domain/ids.py` implements it as a pure function of
  `(unix_ms, 10 random bytes)`. Hypothesis checks the version and variant bits, the timestamp round
  trip, and millisecond ordering.
- UUIDv7 keeps B-tree inserts mostly append-only and makes ids roughly sortable by creation time.

## Consequences

- Ids leak creation time to the millisecond. That is acceptable for orders and stores. Invitation
  tokens are separate random secrets, not ids.
