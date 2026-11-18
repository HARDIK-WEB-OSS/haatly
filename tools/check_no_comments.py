import ast
import io
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

ALLOWED_PYTHON_PRAGMA = re.compile(
    r"# (?:type: ignore\[[a-z0-9-]+(?:, ?[a-z0-9-]+)*\]|noqa: [A-Z]+[0-9]+(?:, ?[A-Z]+[0-9]+)*)"
)
DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    reason: str

    def render(self) -> str:
        return f"{self.path.as_posix()}:{self.line}: {self.reason}"


@dataclass(frozen=True)
class Syntax:
    line_markers: tuple[str, ...] = ()
    blocks: tuple[tuple[str, str], ...] = ()
    quotes: str = ""
    marker_needs_boundary: bool = False


HASH = Syntax(line_markers=("#",), quotes="'\"", marker_needs_boundary=True)
TOML = Syntax(line_markers=("#",), quotes="'\"")
SQL = Syntax(line_markers=("--",), blocks=(("/*", "*/"),), quotes="'\"")
POWERSHELL = Syntax(line_markers=("#",), blocks=(("<#", "#>"),), quotes="'\"")
JAVASCRIPT = Syntax(line_markers=("//",), blocks=(("/*", "*/"),), quotes="'\"`")
CSS = Syntax(blocks=(("/*", "*/"),), quotes="'\"")
MARKUP = Syntax(blocks=(("{#", "#}"), ("<!--", "-->")))
PLAIN = Syntax()

SYNTAX_BY_SUFFIX: dict[str, Syntax] = {
    ".sql": SQL,
    ".html": MARKUP,
    ".jinja": MARKUP,
    ".j2": MARKUP,
    ".js": JAVASCRIPT,
    ".mjs": JAVASCRIPT,
    ".css": CSS,
    ".ps1": POWERSHELL,
    ".psm1": POWERSHELL,
    ".yaml": HASH,
    ".yml": HASH,
    ".toml": TOML,
    ".lock": TOML,
    ".mako": HASH,
    ".sh": HASH,
}
SYNTAX_BY_NAME: dict[str, Syntax] = {
    "Dockerfile": HASH,
    "Caddyfile": HASH,
    ".gitignore": HASH,
    ".gitattributes": HASH,
    ".dockerignore": HASH,
    ".env.example": HASH,
    ".python-version": PLAIN,
}
EXEMPT_SUFFIXES = frozenset({".md", ".png", ".jpg", ".jpeg", ".ico", ".woff2", ".svg"})


def is_allowed_python_comment(comment: str, row: int, column: int) -> bool:
    if row == 1 and column == 0 and comment.startswith("#!"):
        return True
    return ALLOWED_PYTHON_PRAGMA.fullmatch(comment) is not None


def python_comment_lines(text: str) -> Iterator[int]:
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.COMMENT:
            continue
        row, column = token.start
        if not is_allowed_python_comment(token.string, row, column):
            yield row


def python_docstring_lines(text: str) -> Iterator[int]:
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, DOCSTRING_OWNERS) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            yield first.lineno


def python_violations(path: Path, text: str) -> list[Violation]:
    try:
        comments = [Violation(path, line, "comment") for line in python_comment_lines(text)]
        docstrings = [Violation(path, line, "docstring") for line in python_docstring_lines(text)]
    except (SyntaxError, tokenize.TokenError) as error:
        return [Violation(path, getattr(error, "lineno", None) or 1, "cannot parse Python")]
    return sorted(comments + docstrings, key=lambda violation: violation.line)


def starts_line_comment(line: str, index: int, syntax: Syntax) -> bool:
    for marker in syntax.line_markers:
        if line.startswith(marker, index):
            return not syntax.marker_needs_boundary or index == 0 or line[index - 1].isspace()
    return False


def opening_block(line: str, index: int, syntax: Syntax) -> tuple[str, str] | None:
    for opener, closer in syntax.blocks:
        if line.startswith(opener, index):
            return opener, closer
    return None


def skip_quoted(line: str, index: int, quote: str) -> int:
    position = index + 1
    while position < len(line) and line[position] != quote:
        position += 2 if line[position] == "\\" else 1
    return position + 1


def scan_line(line: str, syntax: Syntax, open_closer: str | None) -> tuple[str | None, bool]:
    index = 0
    found = open_closer is not None
    while index < len(line):
        if open_closer is not None:
            end = line.find(open_closer, index)
            if end < 0:
                return open_closer, True
            index, open_closer = end + len(open_closer), None
            continue
        if line[index] in syntax.quotes:
            index = skip_quoted(line, index, line[index])
            continue
        if starts_line_comment(line, index, syntax):
            return None, True
        block = opening_block(line, index, syntax)
        if block is not None:
            found, open_closer, index = True, block[1], index + len(block[0])
            continue
        index += 1
    return open_closer, found


def text_comment_lines(text: str, syntax: Syntax) -> Iterator[int]:
    open_closer: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if number == 1 and line.startswith("#!") and "#" in syntax.line_markers:
            continue
        open_closer, found = scan_line(line, syntax, open_closer)
        if found:
            yield number


def syntax_for(path: Path) -> Syntax | None:
    return SYNTAX_BY_NAME.get(path.name) or SYNTAX_BY_SUFFIX.get(path.suffix)


def file_violations(path: Path) -> list[Violation]:
    if path.suffix in EXEMPT_SUFFIXES:
        return []
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".py", ".pyi"}:
        return python_violations(path, text)
    syntax = syntax_for(path)
    if syntax is None:
        return [Violation(path, 1, "no comment rule for this file type")]
    return [Violation(path, line, "comment") for line in text_comment_lines(text, syntax)]


def tracked_files(root: Path) -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=True,
    )
    names = listing.stdout.decode("utf-8").split("\0")
    return [root / name for name in names if name and (root / name).is_file()]


def check(paths: Iterable[Path]) -> list[Violation]:
    return [violation for path in paths for violation in file_violations(path)]


def main(arguments: Sequence[str]) -> int:
    paths = [Path(argument) for argument in arguments] or tracked_files(Path.cwd())
    violations = check(paths)
    for violation in violations:
        print(violation.render())
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
