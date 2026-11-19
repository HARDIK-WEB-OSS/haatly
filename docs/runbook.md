# Runbook

Operational procedures. Each one says what it does, who may run it, and how to check that it
worked. Design reasons are in [`docs/adr/`](adr).

## Make a user an admin

Admins approve, suspend and reinstate stores. There is no endpoint that grants admin rights; an
operator does it with the break-glass database role, `haatly_ops` (ADR 0002, ADR 0015).

**Before you start**

- The person has registered an account with the email you will use.
- You have the `HAATLY_DB_OPS_PASSWORD` for the target database in your `.env`, and
  `HAATLY_DB_HOST` and `HAATLY_DB_PORT` point at that database.
- Use it sparingly. Every statement `haatly_ops` runs is written to the Postgres log, tagged
  `haatly_ops@haatly`.

**Run it**

```powershell
./tasks.ps1 make-admin person@example.com
```

| Output | Meaning | Exit code |
|---|---|---|
| `granted admin to person@example.com` | The account is now an admin, and one audit row was written | 0 |
| `person@example.com is already an admin; nothing changed` | No change, and no audit row | 0 |
| `no account is registered with person@example.com` | Nothing changed. Check the spelling, or ask the person to register first | 1 |

The email is matched after trimming and lower-casing, like at login.

**Check it**

- The person signs out and in again. `GET /me` returns `"is_admin": true`.
- The grant is in the append-only audit log, written in the same transaction as the change:

  ```sql
  SELECT occurred_at, action, details FROM platform.audit_log
  WHERE action = 'identity.admin_granted' ORDER BY occurred_at DESC;
  ```

  `details` is `{"via": "make-admin", "database_role": "haatly_ops"}`.

**Removing admin rights** has no task yet. Until it does, it is a manual `UPDATE` as `haatly_ops`,
which the Postgres log records.
