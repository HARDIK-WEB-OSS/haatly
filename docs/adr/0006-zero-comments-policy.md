# 0006 Zero-comments policy

- Status: Implemented (0719c34)
- Date: 2026-11-18

## Context

Comments drift from code, and v1 carried reasoning in comments that later became false. Meaning should
come from names, types and small functions. The reasons behind decisions belong in ADRs.

## Decision

No comments and no docstrings in any source or config file:
Python, SQL, Jinja/HTML, JS, CSS, PowerShell, YAML, TOML (including `uv.lock`), Dockerfile,
Caddyfile, shell, Mako, `.gitignore`, `.gitattributes`, `.dockerignore`, `.env.example`.
Only Markdown is exempt.

`tools/check_no_comments.py` enforces it:

- **Python:** `tokenize` rejects every `COMMENT` token, and `ast` rejects a string expression as the
  first statement of a module, class, function or async function. Only three comments are allowed:
  - a shebang on line 1
  - `# type: ignore[code, …]`
  - `# noqa: CODE, …`
  A bare `# noqa`, a bare `# type: ignore` and `# pragma: no cover` are all rejected.
- **Other languages:** a small string-aware scanner per syntax looks for line markers (`#`, `--`,
  `//`) and block markers (`/* */`, `<!-- -->`, `{# #}`, `<# #>`). It skips quoted strings.
  For YAML, Dockerfile, Caddyfile and shell, `#` only counts at the start of a line or after
  whitespace, matching how those languages parse it.
- **File discovery:** `git ls-files --cached --others --exclude-standard`. A file type with no rule is
  a failure, so new file types cannot slip past.
- It runs in pre-commit and in CI. Its test plants a comment in every supported language, plus every
  docstring position, and also scans the whole repository.

Coverage exclusions live in `[tool.coverage.report] exclude_also` (currently `if TYPE_CHECKING:` and
protocol `...` bodies), never in source pragmas.

Generated code must be cleaned before it is committed. The Alembic template
(`migrations/script.py.mako`) emits no comments or docstrings. Alembic's autogenerate markers are
removed when a migration is reviewed, and the checker fails the commit if one is left.

## Consequences

- Third-party files that carry licence comments (for example a vendored HTMX build) cannot be
  committed as-is. When one is needed (M2), a separate ADR will decide between serving it from a
  pinned CDN and a vendor directory with an explicit, reviewed exemption.
