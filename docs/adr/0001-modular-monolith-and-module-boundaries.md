# 0001 Modular monolith and module boundaries

- Status: Implemented (0719c34). Amended in M1: platform kernel, module dependency order.
- Date: 2026-11-18

## Context

Haatly is one product run by one small team. It has strong consistency needs around money and
stock, and no need for independent deployment. v1 was a monolith whose boundaries were checked by
a hand-written test, and its audit found business logic leaking into HTTP handlers (F-22) and a
510-line service mixing orders, payments and inventory.

## Decision

One deployable Python package, `haatly`, made of modules:
identity, stores, membership, catalog, inventory, ordering, payments, ledger, payouts,
reconciliation, discovery, notifications, platform.

Each module has the same shape:

| Layer | May do | May not do |
|---|---|---|
| `domain/` | Pure types and rules | Any I/O; import SQLAlchemy, asyncpg, FastAPI, httpx, settings |
| `app/` | Use cases; each opens one unit of work (one transaction); declares ports as `Protocol`s | Import SQL, drivers, HTTP frameworks |
| `adapters/` | SQL repositories, routes, external clients, settings | Be imported from outside the module |
| `public.py` | The only importable surface of the module | |

Composition roots (`haatly.main`, `haatly.worker`) import modules only through `public.py`.

### The platform kernel (amended in M1)

`public.py` re-exports adapters, so importing it pulls in SQLAlchemy and asyncpg. The `app` layer of
another module cannot import it without breaking the "`app` does no I/O" contract, which checks
indirect imports.

- `haatly.platform.kernel` is a second, pure surface of the platform module. It re-exports the
  platform's ports, outbox and job use cases, scheduler task type, tenancy, clock and event and job
  types, and nothing from `adapters`.
- The `app` layer of every other module imports the platform only through `kernel`. A `forbidden`
  contract stops any `app` layer from importing any module's `public`.
- A `forbidden` contract keeps `kernel` free of sqlalchemy, asyncpg, httpx and tigerbeetle,
  including indirect imports. A planted `kernel -> platform.public` import was shown to break it.
- Adapters, composition roots and tests keep using `public.py`.
- Other modules get a kernel only when another module's `app` layer needs them.

import-linter enforces this in CI and pre-commit. M0 started with four kinds of contract:
a `layers` contract per module, a `protected` contract per module (internals importable only from
inside the module), a `forbidden` contract keeping `domain` pure, and a `forbidden` contract keeping
`app` free of I/O libraries. A planted `haatly.main -> haatly.platform.adapters` import was shown to
break the protected contract.

Modules are only created when they get code. Empty stubs are not committed (v1 audit F-19).

### Data ownership

- One Postgres schema per module, named after the module.
- A module reads and writes only its own schema.
- Foreign keys may point only to upstream modules, in the same direction as allowed imports.
  The module dependency order is fixed in a top-level import-linter `layers` contract. The second
  module landed in M1, so the order is now `main | worker` above `ledger` above `platform`. Each
  new module takes its place in that contract when it lands.
- No cross-schema writes, ever.
- Cross-module reads go through the other module's `public.py`, or through a `SECURITY DEFINER`
  function owned by the other module's schema when a database policy or view needs the answer
  (for example a membership check inside an RLS policy).

## Consequences

- Moving a module out later means replacing `public.py` calls with a client, and cross-schema FKs
  with eventual checks. Both are visible in one place.
- Units of work are per module. A use case that must record an outbox event in the same transaction
  uses the platform outbox repository on the same connection (see ADR 0003).
- Some duplication of small value types across modules is accepted rather than sharing internals.
