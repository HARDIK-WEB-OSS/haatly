import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

STRICT_PACKAGES = (
    "haatly/inventory",
    "haatly/ordering/domain",
    "haatly/payments",
    "haatly/ledger",
    "haatly/payouts",
)
DEFAULT_REPORT = Path("coverage.json")


@dataclass
class Totals:
    statements: int = 0
    covered: int = 0
    branches: int = 0
    covered_branches: int = 0

    def add(self, summary: dict[str, int]) -> None:
        self.statements += summary["num_statements"]
        self.covered += summary["covered_lines"]
        self.branches += summary["num_branches"]
        self.covered_branches += summary["covered_branches"]

    @property
    def complete(self) -> bool:
        return self.covered == self.statements and self.covered_branches == self.branches

    def describe(self, package: str) -> str:
        return (
            f"{package}: lines {self.covered}/{self.statements}, "
            f"branches {self.covered_branches}/{self.branches}"
        )


def package_of(filename: str) -> str | None:
    posix = PureWindowsPath(filename).as_posix()
    for package in STRICT_PACKAGES:
        if f"/{package}/" in f"/{posix}":
            return package
    return None


def shortfalls(report: Path) -> list[str]:
    files = json.loads(report.read_text(encoding="utf-8"))["files"]
    totals: dict[str, Totals] = {}
    for filename, measured in files.items():
        package = package_of(filename)
        if package is not None:
            totals.setdefault(package, Totals()).add(measured["summary"])
    return [
        total.describe(package) for package, total in sorted(totals.items()) if not total.complete
    ]


def main(argv: Sequence[str]) -> int:
    report = Path(argv[0]) if argv else DEFAULT_REPORT
    if not report.is_file():
        print(f"coverage report not found: {report}")
        return 2
    missing = shortfalls(report)
    for line in missing:
        print(line)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
