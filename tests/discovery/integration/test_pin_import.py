import asyncio
import csv
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from haatly.discovery.public import (
    Discovery,
    SqlDiscoveryUnitOfWorkFactory,
    import_file,
    import_pins_main,
)
from haatly.platform.public import FrozenClock, Settings
from tests.conftest import truncate_everything
from tests.discovery.pin_fixture import HEADER, KNOWN_PINS, ROWS, write_fixture
from tests.world import Api


def discovery(engine: AsyncEngine) -> Discovery:
    return Discovery(SqlDiscoveryUnitOfWorkFactory(engine), FrozenClock())


@pytest.mark.anyio
async def test_the_fixture_imports_once_per_file_version(
    app_engine: AsyncEngine, tmp_path: Path
) -> None:
    path = write_fixture(tmp_path / "pincodes.csv")
    first = await import_file(discovery(app_engine), path)
    assert (first.rows, first.pins, first.imported) == (len(ROWS), len(KNOWN_PINS), True)
    again = await import_file(discovery(app_engine), path)
    assert (again.version, again.imported) == (first.version, False)


@pytest.mark.anyio
async def test_centroids_ignore_offices_without_usable_coordinates(
    app_engine: AsyncEngine, tmp_path: Path
) -> None:
    service = discovery(app_engine)
    await import_file(service, write_fixture(tmp_path / "pincodes.csv"))
    gpo = await service.pin("560001")
    assert (gpo.district, gpo.state) == ("Bengaluru Urban", "Karnataka")
    assert (gpo.centroid.latitude, gpo.centroid.longitude) == pytest.approx((12.975, 77.585))
    koramangala = await service.pin(" 560034 ")
    assert koramangala.centroid.latitude == pytest.approx(12.9352)


@pytest.mark.anyio
async def test_a_new_file_version_updates_existing_pins(
    app_engine: AsyncEngine, tmp_path: Path
) -> None:
    service = discovery(app_engine)
    await import_file(service, write_fixture(tmp_path / "first.csv"))
    renamed = tmp_path / "second.csv"
    with renamed.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerow((*ROWS[6][:7], "CHENNAI CENTRAL", *ROWS[6][8:]))
    summary = await import_file(service, renamed)
    assert (summary.rows, summary.pins, summary.imported) == (1, 1, True)
    assert (await service.pin("600001")).district == "Chennai Central"
    assert (await service.pin("110001")).district == "New Delhi"


@pytest.mark.anyio
async def test_pin_lookups_over_http(api: Api, app_engine: AsyncEngine, tmp_path: Path) -> None:
    await import_file(discovery(app_engine), write_fixture(tmp_path / "pincodes.csv"))
    found = await api.call("GET", "/v1/pin-codes/560066")
    assert found.json() == {
        "pin_code": "560066",
        "district": "Bengaluru Urban",
        "state": "Karnataka",
    }
    assert (await api.call("GET", "/v1/pin-codes/560095")).status_code == 404
    assert (await api.call("GET", "/v1/pin-codes/01100")).status_code == 422
    assert (await api.call("GET", "/v1/pin-codes/999999")).status_code == 404


def test_the_command_line_imports_and_reports(
    migrated: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_fixture(tmp_path / "pincodes.csv")
    try:
        assert import_pins_main([], migrated) == 2
        assert import_pins_main([str(tmp_path / "missing.csv")], migrated) == 2
        assert import_pins_main([str(path)], migrated) == 0
        assert import_pins_main([str(path)], migrated) == 0
    finally:
        asyncio.run(truncate_everything(migrated))
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0].startswith(f"imported: {len(ROWS)} rows, {len(KNOWN_PINS)} PIN codes, version ")
    assert lines[1].startswith("already imported: ")
    assert captured.err.count("usage: import-pins") == 2
