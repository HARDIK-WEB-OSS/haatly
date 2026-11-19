# Roadmap

Milestones run in a fixed order: M3, M4, M5, M6, M7. Each one ends with its exit tests and real
numbers (see `CONTRIBUTING.md`).

This file lists the work that was found but deliberately left for later, under the milestone that
will do it. The source is the code review of 2026-11-18 (finding ids H, M and L below). The review
proposed deploying before checkout; that plan was not adopted, and the milestone order stays as it is.

## Already fixed after the review (before M3 was committed)

| Finding | Fix |
|---|---|
| H1 login limits collapsed behind Caddy | uvicorn trusts `X-Forwarded-For` from Caddy's fixed address only (ADR 0015) |
| H3 no way to create an admin | `./tasks.ps1 make-admin <email>` as `haatly_ops`, audited (`docs/runbook.md`) |
| M1 password change not atomic | hash, revocation and new session in one transaction |
| M3 no limits on register, join, invitations | 10/h per IP, 20/h per user, 50/day per store |
| M4 no RLS on platform tables | FORCE RLS on idempotency keys (owner-matched), outbox, jobs and processed events (ADR 0004, ADR 0003) |
| M7 no request body limit | Caddy `request_body max_size 1MB`, proven by the smoke task |

## M4: ordering, checkout, Razorpay, webhooks

- **M5 pagination.** Keyset pagination (`limit` plus `after = (created_at, id)`) for
  `/seller/members`, `/seller/invitations`, `/me/memberships` and `/admin/stores`, which return
  every row today. M4 adds the order lists, which need the same helper, so they land together.
- **L1 duplicated helpers.** `hash_token` exists in identity and membership, and `examples()` in
  several route modules. Move them to `platform.kernel` and `platform.public` when checkout adds
  its routes and token hashing.
- **L2 `system` scope.** Every job that acts for a tenant must run in that tenant's context, not
  `system`. M4 adds the first such jobs, and with them a test that runs a job for store B against
  store A's ids and expects no change. Document `stores.owner_of` as internal-only.
- **L3 misleading error.** An admin calling `POST /stores` gets `already_has_store`; it should be a
  403 "admins cannot open stores".
- **L4 no `/v1` prefix.** Mount the routers under `/v1` before the Razorpay webhook URL is
  registered, because that is the first external client.
- **L6 readiness ignores TigerBeetle.** Checkout holds make the API call TigerBeetle inline, so
  `/ready` must check it from M4.
- **L8 repeat approve.** Approving an already-decided member returns 404; return 409
  "already decided".
- **Dead jobs in admin.** `list_dead_jobs` and `retry_dead_job` exist (ADR 0003) but no admin
  route exposes them. M4 adds the first jobs that call Razorpay, so an operator must be able to
  see and retry a dead one.

## M6: payouts and reconciliation

- **M6 pruning.** A scheduled job deletes sessions past their absolute expiry and rate-limit
  counters older than a day. It joins the retention job ADR 0003 and ADR 0004 already place in M6
  (published outbox rows, processed events, succeeded jobs, stored idempotency responses).

## M7: deployment and operations

- **H2 account recovery.** An `EmailSender` port with a console/file adapter, single-use hashed
  password-reset tokens (30 minutes, sessions revoked on reset, rate-limited), email verification
  with store creation gated on it, and the real email provider (ADR 0013).
- **M2 registration reveals existing emails.** Once H2 exists, register always answers 202
  "check your email" and sends either a sign-in link or a "you already have an account" email.
  Until then, the register rate limit is the mitigation.
- **M8 DPDP Act 2023.** Record consent version and time at registration, publish a privacy notice,
  add account deletion (anonymise the email, revoke sessions, keep audit rows), state why sellers
  see member emails, and document retention periods.
- **L5 logging.** JSON logging configuration with the request id, and never emails or tokens.
- **L7 comment checker outside git.** Fall back to walking the tree when there is no git checkout,
  or document the requirement.
- **L9 downgrades.** Every `downgrade()` is `DROP SCHEMA … CASCADE`. Document that downgrades are
  development-only and production uses forward fixes.
- **L10 slow suite.** Run the generated suites in their own CI job, in parallel, and mark the
  latency test as slow.
- **Trusted proxy.** The production compose sets `HAATLY_TRUSTED_PROXIES` to the production Caddy's
  address only; the API refuses to start in production without it (ADR 0015).
- Already planned for M7: production compose and real domain, WAL-G point-in-time recovery and the
  restore drill (ADR 0012), k6 and Playwright, and the custom seccomp profile that replaces
  `seccomp:unconfined` (ADR 0014).
