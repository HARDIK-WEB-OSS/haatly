from pathlib import Path

import pytest

import check_no_comments as checker

REPO_ROOT = Path(__file__).resolve().parents[2]


def plant(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def lines_flagged(tmp_path: Path, name: str, text: str) -> list[int]:
    return [violation.line for violation in checker.file_violations(plant(tmp_path, name, text))]


@pytest.mark.parametrize(
    ("name", "text", "line"),
    [
        ("module.py", "x = 1\n# planted\n", 2),
        ("module.py", "x = 1  # planted\n", 1),
        ("module.py", "x = 1  # noqa\n", 1),
        ("module.py", "x = 1  # type: ignore\n", 1),
        ("module.py", "x = 1  # pragma: no cover\n", 1),
        ("module.py", "x = 1  # noqa: E501 because\n", 1),
        ("compose.yaml", "services:\n  db: {}\n# planted\n", 3),
        ("ci.yml", "run: echo hi # planted\n", 1),
        ("pyproject.toml", "a = 1#planted\n", 1),
        ("uv.lock", "# planted\n", 1),
        ("schema.sql", "SELECT 1; -- planted\n", 1),
        ("schema.sql", "SELECT\n/* planted\n still */ 1;\n", 2),
        ("page.html", "<p>hi</p>\n{# planted #}\n", 2),
        ("page.jinja", "<!-- planted -->\n", 1),
        ("app.js", "let a = 1; // planted\n", 1),
        ("app.js", "/* planted */ let a = 1;\n", 1),
        ("site.css", "a { color: red; } /* planted */\n", 1),
        ("tasks.ps1", "Write-Host 'x' # planted\n", 1),
        ("tasks.ps1", "<# planted #>\n", 1),
        ("Dockerfile", "FROM scratch\n# planted\n", 2),
        ("Caddyfile", "localhost {\n\t# planted\n}\n", 2),
        (".gitignore", "# planted\n", 1),
        (".editorconfig", "root = true\n# planted\n", 2),
        (".editorconfig", "[*]\n; planted\n", 2),
        ("script.py.mako", "x = ${value}  # planted\n", 1),
    ],
)
def test_planted_comment_is_rejected(tmp_path: Path, name: str, text: str, line: int) -> None:
    assert line in lines_flagged(tmp_path, name, text)


@pytest.mark.parametrize(
    "source",
    [
        '"""planted"""\nx = 1\n',
        'class A:\n    """planted"""\n',
        'def f() -> None:\n    """planted"""\n',
        'async def f() -> None:\n    """planted"""\n',
    ],
)
def test_docstring_is_rejected(tmp_path: Path, source: str) -> None:
    violations = checker.file_violations(plant(tmp_path, "module.py", source))
    assert [violation.reason for violation in violations] == ["docstring"]


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("module.py", "#!/usr/bin/env python\nx = 1\n"),
        ("module.py", "x: int = y  # type: ignore[assignment]\n"),
        ("module.py", "x = 1  # type: ignore[assignment, misc]\n"),
        ("module.py", "import os  # noqa: F401\n"),
        ("module.py", "import os  # noqa: F401, E501\n"),
        ("module.py", "x = '# not a comment'\ndef f() -> str:\n    return 'doc'\n"),
        ("module.py", "def f() -> None:\n    x = 1\n    '''not first'''\n"),
        ("compose.yaml", "url: https://example.com/#anchor\nq: 'a # b'\nr: \"c # d\"\n"),
        ("pyproject.toml", "a = \"x # y\"\nb = 'p#q'\n"),
        ("schema.sql", "SELECT '-- not', 'a''b -- still';\n"),
        ("app.js", "const u = 'https://x.y/z'; const t = `a // b`;\n"),
        ("app.js", 'const e = "a \\" // b";\n'),
        ("page.html", '<a href="#top">Don\'t</a>\n'),
        ("tasks.ps1", '$x = "a # b"\n'),
        ("run.sh", "#!/bin/sh\necho ok\n"),
        (".python-version", "3.12"),
        (".editorconfig", "root = true\n\n[*.ps1]\nend_of_line = crlf\n"),
        ("notes.md", "# Heading\n"),
    ],
)
def test_clean_text_passes(tmp_path: Path, name: str, text: str) -> None:
    assert lines_flagged(tmp_path, name, text) == []


def test_unknown_file_type_is_rejected(tmp_path: Path) -> None:
    violations = checker.file_violations(plant(tmp_path, "data.xyz", "x"))
    assert [violation.reason for violation in violations] == ["no comment rule for this file type"]


def test_unparseable_python_is_rejected(tmp_path: Path) -> None:
    violations = checker.file_violations(plant(tmp_path, "module.py", "def (:\n"))
    assert [violation.reason for violation in violations] == ["cannot parse Python"]


def test_main_fails_on_a_planted_comment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    planted = plant(tmp_path, "planted.py", "x = 1  # planted\n")
    assert checker.main([str(planted)]) == 1
    assert "planted.py:1: comment" in capsys.readouterr().out


def test_main_passes_on_clean_file(tmp_path: Path) -> None:
    assert checker.main([str(plant(tmp_path, "clean.py", "x = 1\n"))]) == 0


def test_repository_has_no_comments() -> None:
    violations = checker.check(checker.tracked_files(REPO_ROOT))
    assert [violation.render() for violation in violations] == []
