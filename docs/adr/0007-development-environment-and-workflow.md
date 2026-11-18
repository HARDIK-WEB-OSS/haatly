# 0007 Development environment and workflow

- Status: Accepted
- Date: 2026-11-18

## Context

The developer machine is Windows with PowerShell. v1's lockfile pinned `uvloop` without a platform
marker, so it could not be installed on Windows at all (audit F-15). v1 also had bash-only scripts.

## Decision

### Toolchain

- Python 3.12 runs natively on Windows, installed and pinned by uv (`.python-version`).
- One lockfile (`uv.lock`) covers runtime and dev dependencies. Platform-specific packages carry
  markers. `uvloop` comes in through `uvicorn[standard]` with
  `sys_platform != 'win32'`, verified in the lock.
- SQLAlchemy is capped below 2.1 to keep to the specified 2.0 line.
- Postgres (PostGIS 16-3.5), TigerBeetle 0.17.9 and Caddy 2.10 run in Docker Desktop.
  Ports are published on `127.0.0.1` only: 55432 for Postgres, 13000 for TigerBeetle, 8443 for
  Caddy.
- Every dev command goes through `tasks.ps1`: `setup`, `env`, `up`, `down`, `migrate`,
  `migrate-roundtrip`, `test`, `lint`, `typecheck`, `audit`, `check`, `run-api`, `run-worker`,
  `smoke`, `hooks`. CI runs the same tasks under `pwsh` on Ubuntu, so local and CI gates cannot drift.
- `setup` writes `.env` with fresh random passwords, so no real-looking secret is ever committed.
- Files used inside containers are LF via `.gitattributes`. `*.ps1` files are CRLF.
- Paths are built with `pathlib`. No hardcoded separators.
- The Docker image installs uv with `pip install uv==0.13.9` instead of copying it from
  `ghcr.io/astral-sh/uv`, because that image could not be pulled reliably here. The version is pinned
  either way.

### TigerBeetle under Docker Desktop

- TigerBeetle needs `io_uring`, which Docker's default seccomp profile blocks. Only the `tigerbeetle`
  service gets `security_opt: seccomp:unconfined`.
- Verified on 2026-11-18 on Docker Desktop 29.1.3 (WSL2 kernel). `tigerbeetle format` and `start`
  succeeded, and the replica reached `transition_to_normal_from_recovering_status … primary` and was
  listening on port 3000. TigerBeetle uses io_uring exclusively on Linux, so a running replica
  confirms io_uring works.
- `--development` is used locally. The replica allocated about 1.45 GiB at start, which Docker
  Desktop must have available.
- The Python client and the server are pinned to the same version (0.17.9). The client lands in M1.

### Workflow

- One branch per milestone (`m0-skeleton`, `m1-tigerbeetle`, …) and a pull request to `main`.
  Nothing is pushed to `main` directly.
- Conventional Commits, one logical change per commit. Every check is green before each commit.
- **Money-path tests come first.** The failing run (the red output) is recorded in the milestone
  report. The test and its implementation are then committed together, so every commit stays green.
  `xfail` is not used for this.
- New dependencies need explicit approval.

## Consequences

- Contributors on macOS or Linux can run `tasks.ps1` with `pwsh`.
