import json
from pathlib import Path

import pytest

import check_package_coverage as checker


def summary(statements: int, covered: int, branches: int, covered_branches: int) -> dict[str, int]:
    return {
        "num_statements": statements,
        "covered_lines": covered,
        "num_branches": branches,
        "covered_branches": covered_branches,
    }


def write_report(tmp_path: Path, files: dict[str, dict[str, int]]) -> Path:
    path = tmp_path / "coverage.json"
    report = {"files": {name: {"summary": totals} for name, totals in files.items()}}
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_full_coverage_of_strict_packages_passes(tmp_path: Path) -> None:
    report = write_report(
        tmp_path,
        {
            "src\\haatly\\ledger\\domain\\ids.py": summary(10, 10, 4, 4),
            "src/haatly/platform/app/jobs.py": summary(10, 5, 4, 1),
        },
    )
    assert checker.shortfalls(report) == []
    assert checker.main([str(report)]) == 0


def test_a_missed_line_or_branch_in_a_strict_package_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = write_report(
        tmp_path,
        {
            "src/haatly/ledger/domain/ids.py": summary(10, 10, 4, 3),
            "src/haatly/ledger/app/apply.py": summary(10, 9, 0, 0),
            "src/haatly/ordering/domain/order.py": summary(5, 5, 2, 2),
            "src/haatly/ordering/adapters/sql.py": summary(5, 1, 2, 0),
        },
    )
    assert checker.shortfalls(report) == [
        "haatly/ledger: lines 19/20, branches 3/4",
    ]
    assert checker.main([str(report)]) == 1
    assert "haatly/ledger: lines 19/20, branches 3/4" in capsys.readouterr().out


def test_packages_that_do_not_exist_yet_are_skipped(tmp_path: Path) -> None:
    report = write_report(tmp_path, {"src/haatly/platform/app/jobs.py": summary(1, 0, 0, 0)})
    assert checker.shortfalls(report) == []


def test_a_missing_report_is_an_error(tmp_path: Path) -> None:
    assert checker.main([str(tmp_path / "absent.json")]) == 2
