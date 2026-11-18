# 0002 Tenancy context, database roles and row-level security

- Status: Accepted (roles and tenant context in M0; RLS policies arrive with the first store-owned
  table in M2)
- Date: 2026-11-18

## Context

Every store's data must be invisible to every other tenant even if an application query forgets a
`WHERE store_id = …`. v1 relied on application scoping plus composite foreign keys. The audit found
no IDOR, but a single missed filter would have been a full leak.

## Decision

### Roles

| Role | Login | Owns | BYPASSRLS | Used by |
|---|---|---|---|---|
| `haatly_owner` | yes | database, every module schema, every table | no | Alembic only |
| `haatly_app` | yes | nothing | no | API and worker at runtime |
| `haatly_ops` | yes | nothing | **yes** | Audited break-glass access |

- No runtime role is a superuser.
- Default privileges give `haatly_app` and `haatly_ops` DML (not DDL) on everything
  `haatly_owner` creates. Each migration grants schema `USAGE` explicitly.
- `haatly_ops` has `log_statement = all` and `application_name = haatly-break-glass` set on the
  role, so every statement it runs reaches the Postgres log with the role name in the line prefix.
  No extension is needed.
- Extensions (`postgis`, `pg_trgm`) are created by the bootstrap superuser in
  `docker/postgres/initdb/01-roles.sql`, because PostGIS is not a trusted extension.

### Tenant context

Every unit of work starts with:

```sql
SELECT set_config('app.role', $1, true),
       set_config('app.user_id', $2, true),
       set_config('app.store_id', $3, true)
```

`set_config(…, true)` is the parameterisable form of `SET LOCAL`, so the values die with the
transaction and cannot leak through the connection pool. An integration test runs on a one-connection
pool to prove it.

`app.role` is one of `public`, `buyer`, `seller`, `admin`, `system`. `TenantContext` rejects
inconsistent combinations: a seller without a store, a buyer without a user, or public/system with a
user.

### Row-level security (from M2)

- Every store-owned table has `ENABLE` and `FORCE ROW LEVEL SECURITY`, so the owner role is
  subject to policies too.
- Composite foreign keys `(store_id, id)` are a second wall under RLS.
- Every view is created `WITH (security_invoker = true)`, so the caller's policies apply and the
  view owner's privileges don't.
- Public catalog reads run with `app.role = 'public'` and have their own RLS policies.
- The worker runs with `app.role = 'system'`. Policies grant cross-tenant access to `system`
  explicitly, table by table.

## Consequences

- A forgotten filter returns zero rows instead of another tenant's rows.
- Migrations that need to backfill tenant data must set `app.role = 'system'` too, because FORCE
  RLS applies to the owner.
- The generated RLS leak suite (M2) runs every route with every foreign-tenant actor.
