# Contributing to Haatly

Read this file before changing anything. It is the complete set of working rules. The reasons
behind them, and every design decision, live in [`docs/adr/`](docs/adr).

## Rules

1. **Zero comments and zero docstrings** in every source and config file: Python, SQL, Jinja, JS,
   CSS, PowerShell, YAML, TOML, Dockerfile, Caddyfile. Markdown is exempt.
   `tools/check_no_comments.py` enforces this in pre-commit and CI. `# pragma: no cover` is not
   allowed. The only permitted comments are `# noqa: CODE` and `# type: ignore[code]`, always with
   a code (ADR 0006).
2. **The why lives in `docs/adr/`.** Every ADR has a status line: `Proposed`, `Accepted`, or
   `Implemented (<commit sha>)`. Nothing is marked Implemented without a commit and a test that
   proves it.
3. **Money is integer paise.** No `float` or `Decimal` anywhere in a money path.
4. **Money-path tests are written first and must fail first.** Record the red run in the milestone
   report, then commit the test and the implementation together, so every commit stays green.
5. **Never call an external service inside a Postgres transaction** (ADR 0003, ADR 0009).
6. **One milestone at a time.** At the end of a milestone, run its exit tests, report the real
   numbers, and stop.
7. **Never claim a result you did not run.** Never weaken a test, constraint, type check or CI
   gate to make something pass.
8. **Ask before adding any dependency** that is not already in `pyproject.toml`.
9. **Small functions, small commits.** Split functions longer than about 40 lines. Use
   Conventional Commits, one logical change per commit.
10. **Dev is Windows and PowerShell.** Services run in Docker Desktop. Every command goes through
    `tasks.ps1` (ADR 0007).
11. **Git workflow.** Work and commit on `main`, locally. Never push, open pull requests, merge,
    make releases or change the repository on GitHub. When work is committed and ready, say
    **READY TO PUSH** and stop. The owner pushes.
    - The only GitHub commands a contributor may run are read-only: `gh run list`, `gh run view`,
      `gh run watch` and `gh run view --log-failed`.
    - After the owner pushes, watch the latest CI run for `main` with `gh run watch`. If it is
      green, continue to the next step. If it is red, read the failed log, fix it locally with a
      new commit, and say **READY TO PUSH** again.
12. **Every file read and write names `encoding="utf-8"`**: `open`, `Path.read_text`,
    `Path.write_text`, `subprocess.run(..., encoding="utf-8")` instead of `text=True`, and bytes
    decoded with `.decode("utf-8")`. This applies to project code and to any one-off script used
    to edit files. Windows defaults to cp1252, and a script that read and rewrote ADR 0015 with the
    default garbled its dashes and ellipses on every pass. Ruff's `PLW1514` (enabled on its own
    with `explicit-preview-rules`) enforces it in project code.
13. **No AI-assistant files.** No `CLAUDE.md`, `AGENTS.md` or similar.
14. **Milestone order is fixed:** M3, then M4, M5, M6, M7. Deferred work is listed per milestone in
    [`docs/roadmap.md`](docs/roadmap.md).
15. **Every file is LF**, except `*.ps1`, which is CRLF (`.gitattributes`, `.editorconfig`). Edit
    scripts write with `newline="\n"`: `open(path, "w", encoding="utf-8", newline="\n")` or
    `Path.write_text(text, encoding="utf-8", newline="\n")`. Without it, Python on Windows turns
    every `\n` into `\r\n`, and one script rewrote a whole module as CRLF on 2026-11-20.
    `ruff format` (`line-ending = "lf"`) and the pre-commit hook `mixed-line-ending --fix=lf`
    catch what slips through.

## Everyday commands

```powershell
./tasks.ps1 setup    # Python 3.12, dependencies, .env, git hooks
./tasks.ps1 up       # Postgres, TigerBeetle and Caddy in Docker
./tasks.ps1 check    # every gate CI runs, except the Docker smoke boot
./tasks.ps1 smoke    # build the image and boot the full stack
```

Operational procedures are in [`docs/runbook.md`](docs/runbook.md).
