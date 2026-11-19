from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import cast, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from haatly.discovery.adapters.tables import Geography, pin_codes, pin_imports, store_locations
from haatly.discovery.domain.model import (
    NearbyProduct,
    NearbyStore,
    PinCode,
    Point,
    StoreLocation,
    rounded_km,
)

STORES_NEAR = text("""
WITH origin AS (
    SELECT ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography AS point
)
SELECT d.id, d.name, d.slug, ST_Distance(l.location, origin.point) AS meters
FROM discovery.store_locations AS l
JOIN stores.directory AS d ON d.id = l.store_id
CROSS JOIN origin
WHERE ST_DWithin(l.location, origin.point, :radius)
ORDER BY meters, d.id
LIMIT :limit
""")

PRODUCTS_NEAR = text("""
WITH origin AS (
    SELECT ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography AS point
),
nearby AS MATERIALIZED (
    SELECT d.id AS store_id, d.name AS store_name, ST_Distance(l.location, origin.point) AS meters
    FROM discovery.store_locations AS l
    JOIN stores.directory AS d ON d.id = l.store_id
    CROSS JOIN origin
    WHERE ST_DWithin(l.location, origin.point, :radius)
),
candidates AS MATERIALIZED (
    SELECT candidate AS product_id
    FROM catalog.search_candidates(
        :text,
        ARRAY(SELECT store_id FROM nearby ORDER BY store_id),
        ARRAY(SELECT meters FROM nearby ORDER BY store_id),
        :limit
    ) AS candidate
),
wanted AS (SELECT websearch_to_tsquery('simple', :text) AS query)
SELECT p.product_id, p.store_id, n.store_name, p.title, n.meters,
    (SELECT min(v.price_paise) FROM catalog.storefront_variants AS v
     WHERE v.product_id = p.product_id) AS price_from
FROM candidates AS c
CROSS JOIN LATERAL (
    SELECT shown.product_id, shown.store_id, shown.title, shown.search
    FROM catalog.storefront_products AS shown
    WHERE shown.product_id = c.product_id
    LIMIT 1
) AS p
CROSS JOIN LATERAL (
    SELECT near.store_name, near.meters FROM nearby AS near WHERE near.store_id = p.store_id
    LIMIT 1
) AS n
CROSS JOIN wanted
ORDER BY ts_rank(p.search, wanted.query) + word_similarity(:text, p.title) DESC, n.meters,
    p.product_id
LIMIT :limit
""")


ALLOW_MISSPELLINGS = text("SELECT set_config('pg_trgm.word_similarity_threshold', '0.5', true)")


def geography(point: Point) -> Any:
    return cast(
        func.ST_SetSRID(func.ST_MakePoint(point.longitude, point.latitude), 4326), Geography()
    )


class SqlPinDirectory:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def get(self, pin_code: str) -> PinCode | None:
        statement = select(
            pin_codes.c.pin_code,
            pin_codes.c.district,
            pin_codes.c.state,
            func.ST_Y(text("centroid::geometry")).label("latitude"),
            func.ST_X(text("centroid::geometry")).label("longitude"),
        ).where(pin_codes.c.pin_code == pin_code)
        row = (await self.connection.execute(statement)).first()
        if row is None:
            return None
        return PinCode(row.pin_code, row.district, row.state, Point(row.latitude, row.longitude))

    async def upsert(self, pins: Sequence[PinCode], version: str) -> None:
        if not pins:
            return
        statement = insert(pin_codes).values(
            [
                {
                    "pin_code": pin.pin_code,
                    "district": pin.district,
                    "state": pin.state,
                    "centroid": geography(pin.centroid),
                    "import_version": version,
                }
                for pin in pins
            ]
        )
        await self.connection.execute(
            statement.on_conflict_do_update(
                index_elements=[pin_codes.c.pin_code],
                set_={
                    "district": statement.excluded.district,
                    "state": statement.excluded.state,
                    "centroid": statement.excluded.centroid,
                    "import_version": statement.excluded.import_version,
                },
            )
        )

    async def record_import(self, version: str, rows: int, pins: int, now: datetime) -> bool:
        statement = (
            insert(pin_imports)
            .values(version=version, row_count=rows, pin_count=pins, imported_at=now)
            .on_conflict_do_nothing(index_elements=[pin_imports.c.version])
            .returning(pin_imports.c.version)
        )
        return (await self.connection.execute(statement)).first() is not None


class SqlLocationRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def put(self, location: StoreLocation, now: datetime) -> None:
        values = {
            "pin_code": location.pin_code,
            "location": geography(location.point),
            "updated_at": now,
        }
        statement = insert(store_locations).values(store_id=location.store_id, **values)
        await self.connection.execute(
            statement.on_conflict_do_update(
                index_elements=[store_locations.c.store_id], set_=values
            )
        )

    async def get(self, store_id: UUID) -> StoreLocation | None:
        statement = select(
            store_locations.c.store_id,
            store_locations.c.pin_code,
            func.ST_Y(text("location::geometry")).label("latitude"),
            func.ST_X(text("location::geometry")).label("longitude"),
        ).where(store_locations.c.store_id == store_id)
        row = (await self.connection.execute(statement)).first()
        if row is None:
            return None
        return StoreLocation(row.store_id, row.pin_code, Point(row.latitude, row.longitude))


class SqlNearbySearch:
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    def params(self, origin: Point, radius_m: float, limit: int) -> dict[str, object]:
        return {
            "latitude": origin.latitude,
            "longitude": origin.longitude,
            "radius": radius_m,
            "limit": limit,
        }

    async def stores(self, origin: Point, radius_m: float, limit: int) -> Sequence[NearbyStore]:
        rows = await self.connection.execute(STORES_NEAR, self.params(origin, radius_m, limit))
        return [NearbyStore(row.id, row.name, row.slug, rounded_km(row.meters)) for row in rows]

    async def products(
        self, wanted: str, origin: Point, radius_m: float, limit: int
    ) -> Sequence[NearbyProduct]:
        params = self.params(origin, radius_m, limit) | {"text": wanted}
        await self.connection.execute(ALLOW_MISSPELLINGS)
        rows = await self.connection.execute(PRODUCTS_NEAR, params)
        return [
            NearbyProduct(
                row.product_id,
                row.store_id,
                row.store_name,
                row.title,
                row.price_from,
                rounded_km(row.meters),
            )
            for row in rows
        ]
