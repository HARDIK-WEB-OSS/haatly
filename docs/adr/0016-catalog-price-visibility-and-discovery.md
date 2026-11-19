# 0016 Catalog, price visibility and discovery

- Status: Implemented (716b5aa, e80d1e1). Catalog, price visibility and discovery in 716b5aa; the
  latency measurement in e80d1e1.
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

### Measured cost (M3 exit)

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
