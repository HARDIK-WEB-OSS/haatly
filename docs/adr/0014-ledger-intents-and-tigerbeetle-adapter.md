# 0014 Ledger intents, the TigerBeetle adapter and the platform kernel

- Status: Implemented (0719c34)
- Date: 2026-11-18

## Context

ADR 0009 sets the write path (record intent, apply, mark) and the reconciler. M1 had to settle the
details, and running against a real TigerBeetle 0.17.9 replica showed several behaviours the design
has to absorb.

Observed against the replica on 2026-11-18:

- `create_transfers` returns one result per event, including `created`.
- A transfer that failed with a transient code such as `exceeds_credits` returns
  `id_already_failed` on every later retry with the same id.
- Re-submitting an identical **linked chain** returns `exists` for the first event and
  `linked_event_failed` for the rest. `exists` breaks a chain like any other failure.
- A pending transfer with `timeout = 1` was voided automatically within 3 s. Voiding or posting it
  afterwards returns `pending_transfer_expired`.
- A `QueryFilter` with every field zero except `timestamp_min` and `limit` scans all transfers in
  commit order.
- The client never gives up on an unreachable cluster. A request waits forever.
- The client accepts IP addresses only. `localhost:3000` fails with `InitError(ADDRESS_INVALID)`.

## Decision

### Ledger module and shared transactions

- `haatly.ledger` owns schema `ledger`: `accounts`, `intents`, `intent_accounts`, `transfers`,
  `drift` and `cursors`. TigerBeetle u128 ids are stored as `uuid` (`UUID(int=id)`).
- A module that needs a ledger write calls `ledger.public.record_intent` with a unit of work that
  also exposes `intents`. That means the ledger's own code writes the ledger schema inside the
  caller's transaction, the same way every module uses the platform outbox. It is not a
  cross-schema write by the caller (ADR 0001).
- `record_intent` is idempotent on `(kind, reference_id)`. The same intent again returns the first
  one; a different intent for the same reference raises `IntentConflictError`.
- An intent declares every account its movements touch. Accounts are created in TigerBeetle before
  the transfers, in a separate request, because `debit_account_not_found` burns the transfer id
  for ever. An account declared with a different ledger, code or flags than the stored one raises
  `AccountConflictError`.
- All transfers in an intent form one linked chain. A void must be its own intent, because an
  expired pending transfer makes the void fail with `pending_transfer_expired`, which would fail
  the whole chain.

### Settling results

| Result | Intent becomes |
|---|---|
| every event `created` or `exists`; a void that gets `pending_transfer_expired` or `pending_transfer_already_voided` | `applied` |
| first event `exists`, the rest `linked_event_failed` (a resubmitted chain, which is atomic) | `applied` |
| a business refusal: `exceeds_credits`, `exceeds_debits`, `id_already_failed`, `pending_transfer_expired` (post), `pending_transfer_already_posted/voided`, closed accounts | `rejected`, with the code |
| anything else, including every `exists_with_different_*` and any account result other than `created`/`exists` | `flagged` for review |

Settling is a conditional update (`WHERE status = 'pending'`). Exactly one caller wins, and only that
caller writes the `ledger.intent_settled` outbox event, whose payload carries kind, reference,
status and code. Owning modules move their domain state from that event, whichever path settled it.

### Who applies

- The request handler may apply inline (`IntentApplier.apply`).
- `record_intent` enqueues a `ledger.apply_intent` backstop job in the same transaction, delayed
  by `HAATLY_LEDGER_BACKSTOP_SECONDS` (30 s). The job is a no-op once the intent is settled.
- The reconciler (`ledger.reconcile`, every `HAATLY_LEDGER_RECONCILE_EVERY_SECONDS`, default 60 s)
  also settles intents still pending after the same grace period.

### Reconciler

Each run does four steps.

1. **Stale pending:** apply every pending intent older than the grace period.
2. **Postgres ahead:** every newly applied intent is verified once. Its landed transfers are
   looked up in TigerBeetle, and missing ones are re-submitted with the same ids. If the replay is
   refused, the intent is recorded as drift `replay_failed:<code>`. After a TigerBeetle loss
   (ADR 0012), setting `verified_at = NULL` on every applied intent makes the next runs replay the
   whole ledger.
3. **TigerBeetle ahead:** transfers are scanned from a cursor (`ledger.cursors`, the TigerBeetle
   timestamp). Each one is matched to its intent: a pending intent is adopted by applying it, which
   returns `exists`, and an unknown transfer or one belonging to a rejected or flagged intent
   becomes drift. Every transfer carries its intent id in `user_data_128`, so drift rows name the
   intent TigerBeetle believes in. Nothing is ever deleted from TigerBeetle.
4. **Balances:** each registered `BalanceProjection` states the expected value per account and how
   to read it from a TigerBeetle balance. Differences are reported. M1 registers none. Inventory
   registers display stock when it lands in M4.

A run is clean when there is no open drift and no mismatch. A dirty run logs a warning from the
`haatly.ledger` logger.

### The TigerBeetle adapter

- Every call is bounded by `HAATLY_TIGERBEETLE_TIMEOUT_SECONDS` (5 s) and raises
  `LedgerUnavailableError` on timeout. The intent stays pending.
- A timed-out request is shielded, not cancelled: cancelling leaves the packet in the client's
  in-flight table, and `close()` then fails an assertion. The request may still land later, which is
  safe because the ids are deterministic and the reconciler adopts it.
- Hostnames in `HAATLY_TIGERBEETLE_ADDRESSES` are resolved to IPv4 addresses before the client is
  created, so compose can keep using `tigerbeetle:3000`.

### Containers that run the client (amends ADR 0007)

- The TigerBeetle **client** library also uses io_uring on Linux. Under Docker's default seccomp
  profile the worker panicked at start with `failed to initialize IO: PermissionDenied`.
  The compose smoke boot still passed, because it only checked `/ready` on the API.
- The `worker` service now gets `seccomp:unconfined` too. The `api` service will need the same
  when it applies checkout holds inline (M4).
- The smoke task now also requires the worker to still be running 10 s after boot. With the
  exemption removed it fails with `worker is 'exited'`.
- `seccomp:unconfined` on the `tigerbeetle`, `worker` and `api` services is accepted for
  development only.
- **M7 exit criterion:** a custom seccomp profile replaces `seccomp:unconfined` on every service.
  It is Docker's default profile plus only the io_uring syscalls (`io_uring_setup`,
  `io_uring_enter`, `io_uring_register`). The smoke test proves the stack runs under it: the API
  is ready and the worker is still running.

### Platform kernel

- `haatly.platform.kernel` re-exports the platform's pure types and use cases (ports, outbox,
  jobs, scheduler, tenancy, clock), with nothing from adapters.
- The `app` layer of every other module imports platform only through `kernel`. An import-linter
  contract forbids `app` layers from importing any module's `public`, which pulls in SQLAlchemy.
  This keeps the "app does no I/O" contract checking indirect imports. `public.py` is still the
  only surface for composition roots and adapters.
- The top-level dependency order from ADR 0001 is now enforced, because the second module has
  landed: `main | worker` above `ledger` above `platform`.

### Coverage gate

`tools/check_package_coverage.py` reads `coverage.json` and requires 100% of lines and branches in
each strict package from ADR 0008 that exists. `tasks.ps1 test` runs it after pytest.

## Consequences

- The test TigerBeetle runs in testcontainers with `seccomp=unconfined` and its data file on a
  tmpfs. It is shared by the whole session, so tests use fresh UUIDv7-derived ids, and reconciler
  tests start the cursor at the newest transfer.
- Expiry tests use 2 s timeouts and real sleeps, because TigerBeetle's clock cannot be injected.
