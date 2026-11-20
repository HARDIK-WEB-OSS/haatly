# 0016 Catalog, price visibility and discovery

- Status: Implemented (716b5aa, e80d1e1, f280b84). Catalog, price visibility and discovery in
  716b5aa; the first latency measurement in e80d1e1; indexed search candidates (ADR 0017) and the
  M3 latency targets in f280b84.
- Date: 2026-11-18

## Context

v1 hid prices in its catalog endpoints, but checkout and order responses still returned them
(audit F-2). The spec says hidden means hidden end to end, and that public catalog reads go
through views that already apply product and price visibility. Discovery needs distance from a PIN
code or a point, and full-text plus fuzzy search, over at least 10 000 products.

## Decision

### Who may see what

| Viewer of store S | Public product | Members-only product | Price, when S shows prices to everyone | Price, when S shows prices to members only |
|---|---|---|---|---|
| anyone, S live | yes | no | yes | no |
| active member of S | yes | yes | yes | yes |
| seller of S | yes, any status | yes, any status | yes | yes |

- A store is live when it is approved (`active`) and published.
- A product is shown when it is `active`. Drafts and archived products are seen only by their
  seller.
- Admins get no extra catalog access.

### Prices live in their own table, guarded by RLS

- `catalog.variants` has no price. Prices are in `catalog.variant_prices`, which has its own
  `FORCE` RLS policy. A row is readable only when its variant is readable and the price rule above
  allows it.
- So a hidden price is not filtered out by application code: it is absent from every query the
  app role can run, including the ones checkout will run in M4. A non-member's checkout simply
  finds no price and returns 404.
- Every read goes through `security_invoker` views, `catalog.storefront_products` and
  `catalog.storefront_variants`. The variant view left-joins the price, so a hidden price reads as
  `NULL`. The views add no privileges of their own.

### Modules publish read views

ADR 0001 allows cross-module reads through `public.py` or a `SECURITY DEFINER` function. Policies
and discovery queries need set-based joins, and calling a PL/pgSQL function per row would be slow.
So each module may also publish `security_invoker` views in its own schema, as its SQL-level public
surface:

- `stores.directory`: live stores (id, name, slug, description, price visibility).
- `membership.my_active_memberships`: the current actor's active memberships.
- `catalog.storefront_products` and `catalog.storefront_variants`.

Other modules' policies and queries may read these views, and never the tables behind them. The
views run with the caller's policies, so they reveal nothing the caller could not already read.

### Search and distance

- `catalog.products.search` is a stored generated `tsvector` (`simple` configuration, title
  weighted above description), with a GIN index. `simple` does no stemming, so Hinglish and
  regional product names survive intact.
- Titles also have a `pg_trgm` GIN index. A search matches on either the full-text query or
  trigram **word** similarity (`query <% title`), so typos still find results. Results are ranked
  by full-text rank plus `word_similarity(query, title)`.
- Word similarity, not whole-string similarity. The first version used `title % query`, which
  compares the query with the whole title, so a misspelt word only matched short titles:
  "kanjivaram" scored 0.32 against "Kanjeevaram silk saree" but 0.29 against "Kanjeevaram cotton
  blend", under the 0.3 threshold. Word similarity scores the best-matching part of the title and
  gave 0.53 for both. Each search sets `pg_trgm.word_similarity_threshold` to 0.5 for its own
  transaction (the default, 0.6, missed these typos). Unrelated titles scored below 0.1.
- Store locations are `geography(Point, 4326)` in `discovery.store_locations`, with a GiST index.
  The radius filter is `ST_DWithin`, and results are ordered by `ST_Distance`.
- Responses give distance in kilometres, rounded to 0.1 km, and never a store's coordinates.

### PIN codes

- `discovery.pin_codes` holds one centroid per PIN, averaged from the India Post office
  coordinates that fall inside India's bounding box (ADR 0013).
- `tasks.ps1 import-pins <csv>` loads the data.gov.in CSV idempotently. It records the file's
  SHA-256 and row count in `discovery.pin_imports`.
- A seller's location must use a known PIN. Without explicit coordinates, the PIN centroid is
  used.
- Tests use a small synthetic fixture in the same format (`tests/discovery/pin_fixture.py`, written
  to a temporary CSV): offices averaged into one centroid, an office at (0, 0) and one without
  coordinates ignored, and a malformed PIN skipped. The real file is downloaded by the owner and
  is not committed.

### Generated suites

- **Leak suite.** Each victim store now also has a public product, a members-only product and a
  draft product. The members-only and draft products, their variants and their prices are
  victim secrets.
- **Query parameters.** The suite fills them the way it fills path parameters, by name, and fails
  on any required name it does not know.
- **Price-field scan.** The suite runs every route as the four non-member actors against a live
  store that shows prices to members only. No response may contain either of that store's sentinel
  prices.
- **Price-field scan control.** At least one route must return the sentinel price to the store's
  own member, so the scan proves that prices do flow when they should.

## Consequences

- Updating a price writes `variant_prices`. Readers who may not see the price never see the
  change either.
- The policies are semi-joins on small, indexed views, so the planner can use hash joins rather
  than calling a function per row. The latency test measures what that costs.

### First measurement (superseded)

These numbers were measured on unanalysed tables. The seed's `ANALYZE` ran as `haatly_app`, which
owns no tables, so Postgres skipped every one of them. They are kept for the record and are
superseded by "Measured cost after ADR 0017" below.

`tests/discovery/integration/test_discovery_latency.py` seeds 200 live stores around one PIN with
50 active products each (10 000 products, one priced variant each; a third of the stores hide
prices from non-members and a fifth of the products are members-only). It then sends 200 timed
requests per case through the real app, over three origins, radii of 5, 10 and 25 km, and ten
searches including misspellings, after 20 warm-up requests. It writes
`build/discovery-latency.json`. The first run on the development machine (Windows, Docker Desktop,
Postgres in a container):

| Case | p50 | p95 | p99 |
|---|---|---|---|
| `/discover/stores`, anonymous | 20.9 ms | 27.3 ms | 33.3 ms |
| `/discover/products`, anonymous | 50.1 ms | 150.0 ms | 170.8 ms |
| `/discover/products`, member of 20 stores | 70.7 ms | 166.2 ms | 204.6 ms |

Inside `./tasks.ps1 check`, with coverage tracing on, the same test measured p95s of
37.4 ms, 151.7 ms and 170.5 ms on the committed code.

The test fails above a p95 of 500 ms. That budget is about three times the local figure, because
CI runners are slower. It catches a lost index or a policy that turns into a per-row function
call, not small drift.

**Where the product search time goes.** Row-level security does not let Postgres use an index
for an operator that is not leakproof, and neither `@@` nor `<%` is. So the GIN indexes on
`search` and `title` are not used under the app role. The plan walks the active products of each
live store and checks the policies and the search condition row by row (10 000 store checks for
10 000 products). Computing the nearby stores first (a materialised CTE on the GiST index) did not
change p95, because a 25 km radius covers most of the seeded stores. Cost therefore grows with
the number of products within the radius. If it outgrows the budget, the fix is a candidate search
that can use the indexes, for example a `SECURITY DEFINER` function returning matching product ids
that the RLS-checked views then filter. That is a new decision and needs its own ADR.

That fix is ADR 0017.

### Measured cost after ADR 0017 (M3 exit, f280b84)

The latency test now analyzes the seeded tables as the owner before it measures. It runs four kinds
of search, each over three origins and radii of 5, 10 and 25 km:

| Search | Matching products (of 10 000) |
|---|---|
| every product: "handwoven", in every description | 10 000 |
| a tenth: "saree" | 1 055 |
| under 1%: "pashmeena shawl" | 88 |
| mixed: the ten searches above, with misspellings | 259 to 1 807 each |

Two runs on the committed code, on the development machine (Windows, Docker Desktop, Postgres in a
container), without coverage, in milliseconds:

| Case | Run 1 p50 / p95 / p99 | Run 2 p50 / p95 / p99 |
|---|---|---|
| `/discover/stores`, anonymous | 21.8 / 35.7 / 50.8 | 17.9 / 24.6 / 36.3 |
| products, every product, anonymous | 50.3 / 93.0 / 115.2 | 49.1 / 92.7 / 127.1 |
| products, every product, member of 20 stores | 91.9 / 138.5 / 168.6 | 88.1 / 140.8 / 166.5 |
| products, a tenth, anonymous | 29.9 / 44.0 / 66.2 | 29.4 / 41.7 / 50.0 |
| products, a tenth, member of 20 stores | 69.6 / 120.7 / 172.0 | 61.9 / 85.1 / 109.1 |
| products, under 1%, anonymous | 27.9 / 44.8 / 65.1 | 24.1 / 36.0 / 51.3 |
| products, under 1%, member of 20 stores | 59.2 / 117.6 / 170.9 | 55.5 / 76.3 / 91.7 |
| products, mixed, anonymous | 33.0 / 51.5 / 60.4 | 33.7 / 52.2 / 62.9 |
| products, mixed, member of 20 stores | 67.1 / 104.9 / 233.7 | 67.1 / 91.3 / 118.5 |

### M3 latency targets

The owner set these targets on 2026-11-20 from the evidence below:

- **Anonymous narrow and typical searches: p95 < 50 ms.**
  - Met for stores, for the tenth and for under 1%.
  - Missed for the mixed set: p95 51.5 and 52.2 ms on the committed code, against 49.5 ms in one
    run before the commit.
  - The owner accepted this miss on 2026-11-20 as within run-to-run spread on the development
    machine. No further tuning in M3. The mixed set is re-measured on Linux staging in M7, and
    again after the first M4 item.
- **Broad searches and signed-in requests: p95 < 150 ms on the development machine.** Met in both
  runs. The worst case is 140.8 ms.
- **Ranking order is unchanged.** A cheaper ranking that alters results was rejected.
- These are development-machine numbers. The resume numbers are re-measured on Linux staging in
  M7.

### Where the time goes now

`EXPLAIN (ANALYZE, BUFFERS)` of the slowest case: every product, member of 20 stores, 25 km.

- **Ranking, about 41 ms of the function's 48 ms.** `ts_rank + word_similarity` runs for each of
  the 7 450 visible matches, about 5.5 µs a row. The GIN bitmap scan over `product_search` takes
  5 ms. The hash semi join against the caller's 220 visible scopes takes about 1 ms. This cost grows
  with the number of visible matches within the radius, and keeping the ranking order means
  paying it.
- **Signed-in overhead, about 24–30 ms per request, outside discovery.**
  - A signed-in request opens two extra transactions before its own: identity resolves the
    session, then stores looks up whether the user owns a store.
  - Each Postgres round trip costs 1.2–1.5 ms through Docker Desktop.
  - `/me` alone takes 25.6 ms. `/discover/stores` takes 18.5 ms anonymous against 42.3 ms for a
    signed-in buyer.
  - Merging that work into one transaction is the first item of M4 (`docs/roadmap.md`), with a
    target of halving the overhead. This table is re-measured after it.
- **Planning the outer discovery query, 5–13 ms.** The storefront views and their policies are
  inlined, so the plan is large.

The test still fails above a p95 of 500 ms, as a regression guard for CI.

### Re-measured after the first M4 item (8f69cab)

Signed-in callers now resolve their context in one transaction instead of two (ADR 0015). Two
runs, in milliseconds (p50 / p95 / p99):

| Case | Run 1 | Run 2 |
|---|---|---|
| `/discover/stores`, anonymous | 18.2 / 25.2 / 33.6 | 19.1 / 33.8 / 40.2 |
| products, every product, anonymous | 50.8 / 95.2 / 128.2 | 48.2 / 94.9 / 110.9 |
| products, every product, member of 20 stores | 69.0 / 117.0 / 135.6 | 70.8 / 127.3 / 154.1 |
| products, a tenth, anonymous | 28.6 / 49.4 / 75.0 | 28.3 / 38.9 / 47.1 |
| products, a tenth, member of 20 stores | 51.9 / 71.2 / 121.4 | 49.8 / 71.4 / 81.5 |
| products, under 1%, anonymous | 34.2 / 70.6 / 117.8 | 23.9 / 32.5 / 35.9 |
| products, under 1%, member of 20 stores | 45.4 / 71.2 / 102.8 | 44.8 / 83.0 / 183.1 |
| products, mixed, anonymous | 35.5 / 54.2 / 77.5 | 34.1 / 55.6 / 83.6 |
| products, mixed, member of 20 stores | 57.6 / 81.9 / 108.7 | 55.1 / 75.4 / 95.4 |

- **Member cases dropped at p50 by about 13–18 ms** against the M3 exit runs, in line with the
  overhead measured on `/discover/stores`: 27.3 ms before, 13.7 ms after. Every broad and
  signed-in case stays under the 150 ms p95 target.
- **The anonymous path did not change in this commit.** Its spread between runs (under 1%: p95
  70.6 in run 1, 32.5 in run 2) is noise. The machine had 0.2–0.6 GB of free memory during these
  runs, with other projects' containers and the development TigerBeetle (1.3 GB) running next to
  the test containers.
- Anonymous mixed searches stay at p95 54–56 ms, against the accepted miss recorded above.
