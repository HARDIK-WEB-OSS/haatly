# 0012 Single-replica TigerBeetle in production

- Status: Accepted for now, with known risk
- Date: 2026-11-18

## Context

TigerBeetle's durability comes from replication: it is designed to run 3 or 6 replicas and to survive
disk and node faults by repairing from peers. The first production deployment is one VM.
TigerBeetle has no point-in-time recovery that matches WAL-G for Postgres.

## Decision

Run a single TigerBeetle replica in production for now.

## Risks accepted

- Losing or corrupting the VM's disk loses the TigerBeetle data file. With one replica, TigerBeetle
  cannot repair a corrupted block.
- A Postgres point-in-time restore leaves TigerBeetle **ahead** of Postgres.

## Mitigations

- Every TigerBeetle write is first recorded as a Postgres intent with its deterministic transfer id
  (ADR 0009). After a loss, balances can be rebuilt by re-applying every applied intent from Postgres.
  The ids make the replay idempotent.
- The reconciler handles both directions, including TigerBeetle ahead of Postgres after a restore.
- The M7 restore drill measures the time to rebuild TigerBeetle from Postgres intents, not just the
  Postgres restore.
- Moving to 3 replicas is the trigger point once real money volume justifies three hosts.
