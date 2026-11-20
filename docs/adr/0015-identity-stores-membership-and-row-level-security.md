# 0015 Identity, stores, membership and how row-level security enforces them

- Status: Implemented (5cd6d01, bd4f89b, 8f69cab). Identity, stores, membership and row-level
  security in 5cd6d01. The login limits only worked behind Caddy from bd4f89b (review finding
  H1); before that, every login looked like it came from Caddy. The caller's context has been
  resolved in one transaction since 8f69cab.
- Date: 2026-11-18

## Context

ADR 0002 set the roles, the tenant context and the rule that every store-owned table has `FORCE
ROW LEVEL SECURITY`. M2 adds the first tenant data (users, sessions, stores, memberships,
invitations) and the generated leak suite that proves the policies hold. M2 exposes JSON endpoints
only; HTML pages come later.

## Decision

### Identity

- Passwords are hashed with Argon2id (`argon2-cffi`, library defaults) behind the
  `PasswordHasher` port. Hashing runs in a worker thread so it never blocks the event loop.
  A successful login re-hashes when `check_needs_rehash` says the stored parameters are weaker
  than today's. Unknown emails still spend one Argon2 verification so timing does not reveal
  which emails have accounts.
- Emails are trimmed, lower-cased and unique. Passwords are 10–128 characters.
- Sessions are opaque 256-bit tokens. Only their SHA-256 is stored. The cookie is
  `haatly_session`: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`.
- A session dies after 24 h idle or 14 d after creation, whichever comes first. `last_seen_at`
  is written at most once a minute.
- The token rotates on login (the presented token is deleted), on registration (a new session)
  and on the role change that opening a store causes. A password change stores the new hash,
  deletes every session of the user and issues one new one, all in one transaction. Until review
  finding M1 was fixed, the hash and the revocation were two transactions, and a crash between
  them left a stolen session alive under the new password.
- Login is limited per IP (50 per 15 min) and per (email, IP) (10 per 15 min), in
  `platform.rate_limit_counters`. Counters are written in their own transaction before the
  password is checked, so failures are always counted. There is no per-account lockout, so an
  attacker cannot lock a user out (v1 audit F-6).
- Registration is limited to 10 an hour per client IP, join requests to 20 an hour per user,
  and new invitations to 50 a day per store (review finding M3, commit e680ba1). Refused
  registrations count too, which slows down probing for registered emails. Every limit raises
  the platform's `RateLimitedError`, which the HTTP layer turns into one 429 response with
  `Retry-After`.
- **The client IP behind Caddy.** The limits above are keyed on the real client IP. Behind Caddy,
  the TCP peer is Caddy. The addresses whose `X-Forwarded-For` is believed come from
  configuration, `HAATLY_TRUSTED_PROXIES` (a comma-separated list of IP addresses or networks),
  and the app installs uvicorn's `ProxyHeadersMiddleware` with exactly that list. Uvicorn itself
  runs with `--no-proxy-headers`, so there is one place that decides. An entry that is not an
  address or network is refused at start, and production refuses to start with the list empty;
  in development an empty list means the TCP peer is the client. Compose sets it to
  172.30.53.2, Caddy's fixed address on the `edge` network (172.30.53.0/24), and the production
  compose (M7) must set it the same way. Only `caddy`
  and `api` are on `edge`, Caddy is on no other network, and other containers get addresses from
  172.30.53.128/25 only, so Caddy is the only peer whose `X-Forwarded-For` is believed. The network
  gateway (172.30.53.1) is deliberately not trusted, so a process on the host cannot forge the
  header by connecting to the API directly.
  Caddy keeps its default behaviour: it replaces any client-supplied `X-Forwarded-For` with the
  address it sees. A peer outside `edge` is keyed on its own address, whatever header it sends.
  Until this was fixed (review finding H1, 2026-11-18), every login looked like it came from Caddy,
  so the per-IP limit was one global bucket and the (email, IP) limit locked accounts out.
  `tests/identity/integration/test_client_ip_behind_proxy.py` reads the resolved compose
  configuration and builds the app with exactly that setting.

### Who the caller is

Every request resolves to one `TenantContext`:

| Caller | Context |
|---|---|
| no valid session | `public` |
| `users.is_admin` | `admin`, with user |
| owns a store | `seller`, with user and store |
| anyone else | `buyer`, with user |

**One transaction per caller (from 8f69cab).**

- `stores.app.callers.Callers.context_for(token)` resolves the context in one `system`
  transaction.
- Identity's `resolve_session` runs one query that joins the session to its user. It checks the
  idle and absolute limits and touches `last_seen_at` at most once a minute.
- Then stores looks up the caller's own store, `WHERE owner_user_id` is the user that identity
  just verified.
- Before this, a signed-in request opened two transactions: identity resolved the session as
  `system`, then stores ran the store lookup as `buyer`. Each transaction on the development
  machine costs about 4 ms of pool pre-ping, plus `BEGIN`, `COMMIT` and 1.3–1.8 ms per
  statement. Merging them cut the signed-in overhead on `/discover/stores` from 27.3 to 13.7 ms
  at p50 (ADR 0016).
- The store lookup now runs as `system` rather than `buyer`. That is the authentication
  exception ADR 0002 already allows, and the query is bounded by the verified user id. Under
  either role it returns the same row: the owner's store in any status.
- Stores is downstream of identity, so stores calls identity and never the reverse.
  - Identity exposes `resolve_session` through a new pure `haatly.identity.kernel`. It follows the
    platform kernel's pattern (ADR 0001), and an import-linter contract keeps it free of I/O.
  - The stores unit of work for callers carries identity's session repository on the same
    connection, as ADR 0014 lets a module carry ledger intents. Identity's SQL stays in identity.
- `Accounts.resolve`, used by identity's own routes (`/me`, password change), calls the same
  `resolve_session`. Those routes still resolve the session in a transaction of their own.
- A test proves that a signed-in caller is resolved with one pool checkout and three statements.

Admins are made by an operator with the audited `haatly_ops` role. There is no endpoint for it.
`./tasks.ps1 make-admin <email>` (`haatly.identity.adapters.make_admin`) connects as `haatly_ops`,
sets `is_admin` and appends an `identity.admin_granted` audit row in one transaction. Granting an
existing admin changes nothing and writes nothing. `haatly_app` cannot do it, because it has no
`UPDATE` grant on `is_admin`. The procedure is in `docs/runbook.md`. Before this task existed
(review finding H3), a fresh deployment could only get an admin through hand-written SQL.

### Row-level security

- `platform.actor_role()`, `actor_user_id()` and `actor_store_id()` read the transaction-local
  settings. An unset role reads as `none`, which no policy matches, so a forgotten context sees
  nothing.
- Every M2 table has `ENABLE` and `FORCE ROW LEVEL SECURITY`, including identity's. Each has a
  `system` policy for the worker and for authentication, which has to find a user before it
  knows who they are.

| Table | Who sees or changes what |
|---|---|
| `identity.users` | the user reads and updates their own row |
| `identity.sessions` | `system` only |
| `stores.stores` | everyone reads live stores (`active` and published); the owner reads their own; a buyer creates one `pending`, unpublished store for themselves; the seller updates their own; admins read and update all |
| `membership.memberships` | the seller reads and updates their store's rows; a user reads their own rows; a user creates a pending request for a live store they do not own; a user becomes an active `seller_added` member only while holding a claimed invitation for that store |
| `membership.invitations` | the seller creates, reads and revokes their store's invitations; the holder of a token can read and claim that one invitation |
| `platform.audit_log` | anyone may append a row that names themselves as actor; admins and `system` read; nobody updates or deletes |

- **Capabilities.** Presenting an invitation token sets `app.invitation_token_hash` for the
  transaction. The invitation policies match that hash, so knowing the token is exactly what
  makes the row visible. Nothing else can find an invitation.
- **Column grants.** `haatly_app` may only update listed columns (for example a user's
  `password_hash`, never `is_admin` or `email`; a store's `name`, `description`,
  `is_published`, `price_visibility` and `status`, never `owner_user_id` or `slug`).
- **Status changes.** A trigger refuses any change of `stores.status` unless the actor role is
  `admin` or `system`, because a policy cannot compare the old and new row.
- **Cross-module checks.** Membership policies need facts about stores. Following ADR 0001, the
  stores schema owns three `SECURITY DEFINER` functions: `accepts_members`, `is_live` and
  `owner_of`. `FORCE` applies to their owner too, so each one switches `app.role` to `system`
  with `set_config` and restores the caller's value before returning. A function-level `SET
  app.role` is not allowed for a non-superuser owner.
- Errors never echo identifiers. A row hidden by a policy is a plain 404.

### Invitations are single-use under concurrency

Acceptance is one transaction:

1. Present the token (sets the capability) and load the invitation.
2. Refuse if it is closed, if the store is not active, if the user owns the store, is blocked or
   is already a full member.
3. Claim it with a conditional `UPDATE … WHERE accepted_at IS NULL AND revoked_at IS NULL AND
   expires_at > now`. Of two simultaneous claims, the second waits on the row lock and then
   matches nothing, so it gets a 404.
4. Upsert the membership `ON CONFLICT (store_id, user_id) DO UPDATE … WHERE` the existing row is
   neither blocked nor already a full member. If one user accepts two invitations at once, the
   second waits on the unique index, then updates nothing and gets a 409. The transaction rolls
   back, so its invitation is not burned.

### HTTP

- One error shape: `{"error": {"code", "message", "request_id"}}`, with the request id also in
  `X-Request-ID`. Validation errors name the fields but never echo the submitted values.
  Unexpected errors are logged and returned as a generic 500.
- Every unsafe method needs `Origin` (or, failing that, `Referer`) equal to
  `HAATLY_PUBLIC_ORIGIN`. Otherwise it gets a 403.
- Every text input strips NUL bytes and surrounding whitespace, has a maximum length, and must
  not be blank afterwards. Pydantic checks `min_length` before stripping, so a separate
  `NonBlank` check runs last.
- Store approval, suspension and reinstatement write to the append-only `platform.audit_log` in
  the same transaction.
- Caddy now sends `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
- Caddy refuses request bodies over 1 MB with 413 (`request_body max_size 1MB`) before they
  reach the API, which would otherwise parse any size in memory (review finding M7). The smoke
  task sends a 2 MB body and expects 413, and a 900 KB body and expects the API's own answer.
  Uploads will need a larger limit on their own route.

### The generated leak suite

`tests/security/leak_suite.py` builds its cases from the live route table
(`fastapi.routing.iter_route_contexts`, which also yields routes inside included routers), so a
new route is probed without any change to the suite.

- **Actors.** Every route is called by four foreign actors: another store's seller, another
  store's member, a plain buyer and an anonymous caller.
- **Victims.** Each route is tried against a victim store in three states: live, pending
  approval and suspended. Each victim has an owner, an active member, a pending requester and an
  open invitation.
- **Threat model.** A foreign actor knows the victim's identifiers (store, membership and
  invitation ids), but none of its secrets (passwords, session tokens, invitation tokens).
  Request bodies come from the `examples` each request model must declare. Placeholders resolve
  to the acting actor's own credentials, or to a token from the actor's own world.
- **What counts as a leak:**
  - any victim secret in the response body (the private fields of a live store, and every field
    of a hidden one);
  - any change to the victim's existing rows;
  - any new victim row other than the actor's own join request;
  - any 5xx.
- **Controls.** Each route must also return a 2xx to at least one actor with the victim's real
  secrets. Otherwise the probes could be passing only because every request failed validation.
- **Guards on the suite itself:**
  - a body model without an example, or a path parameter the suite cannot fill, fails the suite;
  - a planted route that leaks through a `system` context is caught on every probe.

### Tests run as the app role

The `owner_engine` fixture is gone. Migrations and the truncation between tests use the owner
credentials inside fixtures, and no test can request them. A guard test checks that every engine
a test touches (the shared fixture engine and the HTTP app's engine) is `haatly_app`: not a
superuser, without `BYPASSRLS`, owning no tables. It also checks that RLS really hides rows on
that session. Pointing the fixture at the owner role makes the guard fail.

## Consequences

- Each authenticated request costs one transaction to find its context: `set_config`, one
  session-and-user query and one store lookup.
- Identity runs its authentication queries as `system`. RLS still guards every other read of a
  user row.
- A seller cannot see their members' emails through RLS. The member list asks identity for
  emails only for the user ids that the seller's own RLS-scoped membership query returned.
