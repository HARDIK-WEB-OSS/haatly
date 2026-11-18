# 0008 Testing strategy and coverage

- Status: Accepted
- Date: 2026-11-18

## Context

v1 claimed 98% branch coverage while checkout, webhooks and refunds had no tests at all
(audit F-7). F-1 is exactly the kind of bug one test would have caught.

## Decision

### Layers of tests

| Layer | How | Where |
|---|---|---|
| Domain | Plain pytest + Hypothesis, no I/O | `tests/<module>/domain` |
| Integration | Real Postgres (PostGIS image + the same role script as dev) and, from M1, real TigerBeetle, both via testcontainers | `tests/<module>/integration` |
| Adapters to Razorpay | Recorded fixtures (real test-mode payloads, stored as data) plus contract tests that run against Razorpay test mode when credentials are present | `tests/payments/…` |
| Generated suites | RLS leak suite (every route × every foreign-tenant actor) and price-field scan (every response for non-members) | M2, M3 |
| End to end | Playwright happy path and refund path; k6 load | M7 |

- Async tests use anyio's pytest plugin (`pytest.mark.anyio`, backend `asyncio`). pytest-asyncio is
  not used.
- Warnings are errors. That is how an unclosed asyncpg connection in the HTTP tests was found and
  fixed.
- Integration tests truncate module tables after each test as the owner role, rather than using
  savepoints. Concurrency tests need real commits.
- Concurrency claims are tested with real concurrency (`asyncio.gather` over a real pool), never by
  reasoning.

### Coverage

- 90% branch coverage overall, enforced by `fail_under`.
- 100% branch coverage on `inventory`, `ordering/domain`, `payments`, `ledger` and `payouts`,
  enforced per package by a check added with the first of those modules (M1).
- **No coverage exclusions on adapters.** The Razorpay and TigerBeetle adapters are covered by
  recorded-fixture tests and contract tests.
- Exclusions exist only in `pyproject.toml` (`if TYPE_CHECKING:` and protocol `...` bodies). Source
  pragmas are forbidden by ADR 0006.

### Red before green

Money-path tests are written first and run before the implementation exists. The failing output is
recorded in the milestone report. The test and the implementation are then committed together
(ADR 0007).

## Consequences

- The suite needs Docker. CI's Ubuntu runners provide it, and locally Docker Desktop does.
