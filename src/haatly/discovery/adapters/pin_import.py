import asyncio
import csv
import hashlib
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from haatly.discovery.adapters.unit_of_work import SqlDiscoveryUnitOfWorkFactory
from haatly.discovery.app.discovery import Discovery, ImportSummary
from haatly.discovery.domain.model import PIN_SHAPE, PostOffice
from haatly.platform.public import DbRole, Settings, SystemClock, create_engine, database_url

MISSING = frozenset({"", "na", "n/a", "null", "none", "-"})


def file_version(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def coordinate(raw: str | None) -> float | None:
    cleaned = (raw or "").strip()
    if cleaned.lower() in MISSING:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def office_of(row: Mapping[str, str]) -> PostOffice | None:
    fields = {key.strip().lower(): (value or "").strip() for key, value in row.items() if key}
    pin_code = fields.get("pincode", "")
    if not PIN_SHAPE.fullmatch(pin_code):
        return None
    return PostOffice(
        pin_code,
        fields.get("district", "").title(),
        fields.get("statename", "").title(),
        coordinate(fields.get("latitude")),
        coordinate(fields.get("longitude")),
    )


def read_offices(path: Path) -> tuple[list[PostOffice], int]:
    rows = 0
    offices = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            office = office_of(row)
            if office is not None:
                offices.append(office)
    return offices, rows


async def import_file(discovery: Discovery, path: Path) -> ImportSummary:
    offices, rows = read_offices(path)
    return await discovery.import_pins(offices, file_version(path), rows)


async def run(path: Path, settings: Settings) -> ImportSummary:
    engine = create_engine(database_url(settings, DbRole.APP), pool_size=1)
    try:
        discovery = Discovery(SqlDiscoveryUnitOfWorkFactory(engine), SystemClock())
        return await import_file(discovery, path)
    finally:
        await engine.dispose()


def describe(summary: ImportSummary) -> str:
    state = "imported" if summary.imported else "already imported"
    return f"{state}: {summary.rows} rows, {summary.pins} PIN codes, version {summary.version[:12]}"


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1 or not Path(arguments[0]).is_file():
        sys.stderr.write("usage: import-pins <data.gov.in pincode CSV>\n")
        return 2
    summary = asyncio.run(run(Path(arguments[0]), settings or Settings()))
    sys.stdout.write(describe(summary) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
