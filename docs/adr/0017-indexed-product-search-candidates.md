# 0017 Indexed product search candidates

- Status: Implemented (f280b84)
- Date: 2026-11-20

## Context

ADR 0016 measured `/discover/products` at a p95 of 150–166 ms over 10 000 products, against an
M3 target of 50 ms. Row-level security was the cause: `@@` and `<%` are not leakproof, so under the
app role Postgres cannot use the GIN indexes on `catalog.products`. It walked every active product
of every nearby live store and checked the policy and the search condition row by row.

ADR 0016 named the fix: a `SECURITY DEFINER` function that can use the indexes and returns product
ids, which the RLS-checked views then filter. This ADR records how that function is built. It also
covers three Postgres behaviours found while measuring it, each of which hid the gain on its own.

## Decision

### A search copy only the owner can read

- `catalog.product_search` holds what search needs from each product: id, store, status,
  visibility, title and the `tsvector`. It has GIN indexes on `search` and on `title`
  (`gin_trgm_ops`).
- An `AFTER INSERT OR UPDATE` trigger (`catalog.sync_product_search`, `SECURITY DEFINER`) keeps it
  in step with `catalog.products`. The composite foreign key `(store_id, product_id)` cascades
  deletes. The migration backfills existing products and analyzes the table.
- `ENABLE` and `FORCE ROW LEVEL SECURITY`, with one policy, `product_search_owner`, `FOR ALL TO
  haatly_owner USING (true)`. `haatly_app` and `haatly_ops` have no grants. If a grant is ever added
  back by mistake, the app role still reads 0 rows, deletes 0 rows and cannot insert. A test adds
  the grant and proves it.
- Only the trigger and the candidate function, both owned by `haatly_owner`, read or write the
  table.

### One definition of who may see a product

- `catalog.visible_product_scopes()` returns the `(store_id, visibility, status)` triples whose
  products the caller may see in the storefront:
  - `public` products of every live store;
  - `members` products of live stores the caller is an active member of;
  - both kinds in the caller's own store, when the caller is a seller.

  Every triple has status `active`.
- It is a plain `LANGUAGE sql STABLE` function, not `SECURITY DEFINER`. Postgres can inline it as
  a subquery, so it costs what the old policy text cost.
- The `products_storefront` policy is now `(store_id, visibility, status) IN (SELECT … FROM
  catalog.visible_product_scopes())`. The candidate function uses the same expression. The rule is
  written once. The storefront views, the variant and price policies that build on it, and the
  candidate function all agree by construction.
- System and seller writes still go through the existing `products_system` and `products_seller`
  policies.

### The candidate function

`catalog.search_candidates(wanted, near_stores, near_meters, max_results) RETURNS SETOF uuid`.

- `SECURITY DEFINER`, `SET search_path = pg_catalog, public`, `EXECUTE` granted to `haatly_app`
  only.
- It returns at most `max_results` product ids. They are the products in `near_stores` that match
  the search and are visible to the caller. They are ordered exactly as discovery orders them:
  full-text rank plus word similarity, then the store's distance, then product id.
- Discovery passes the live stores within the radius it has already computed, with their
  distances, as two arrays sorted by store id. The function filters on them with
  `store_id = ANY(near_stores)` and reads a store's distance with `array_position`.
- The final read still goes through `catalog.storefront_products` under RLS. That read looks up the
  ≤ 50 ids by primary key, re-ranks them, and joins prices through `catalog.storefront_variants`.

### Why this definer function keeps the caller's role

ADR 0015's `SECURITY DEFINER` functions switch `app.role` to `system`, read one fact and restore
the caller's role. That suits a yes/no question about one row. This function answers "which
products may this caller see", so it must evaluate visibility as the caller. It therefore leaves
`app.role`, `app.user_id` and `app.store_id` untouched.

`FORCE ROW LEVEL SECURITY` applies to the owner as well. So `stores.directory` and
`membership.my_active_memberships`, read inside the function, return exactly what the caller would
see. Running as the owner buys one thing only: reading the search copy through its indexes.

### Why it reveals nothing

- It returns ids only, and only ids the caller can already read through `storefront_products`.
  The direct-call tests check this for an anonymous caller, a buyer, an active member, a pending
  requester, an admin, the store's seller and another store's seller. Each of those actors gets
  exactly the set the RLS-checked view returns for the same search. The stores passed in include
  pending, suspended and unpublished stores, and the products include drafts, archived and
  members-only products.
- `system` gets a subset: live-store products only. Discovery never runs as `system`.
- A members-only title is not a search oracle. A non-member searching for its exact words, or a
  misspelling of them, gets nothing.
- Passing store ids the caller cannot see adds nothing, because visibility is checked inside.
- A planted fault is caught: removing the visibility clause fails 12 of the 14 tests, and removing
  the table's RLS fails the stray-grant test.
- The leak suite and the price-field scan run over the changed route unchanged.

### Three Postgres behaviours the design has to absorb

1. **GIN fast update.** The trigger inserts one row at a time, and GIN's default `fastupdate`
   parks those rows in a pending list that every scan reads in full. With 10 000 pending rows a
   one-word search read 191 buffers instead of 5. The planner rightly priced the index above a
   sequential scan, and `<%` then ran on every row: 50 ms for a search that matched 88 products.
   Both indexes are built `WITH (fastupdate = off)`. The search then took 0.8 ms. Catalog writes
   are rare, so a slower insert is the right trade.
2. **Generic plans inside the function.** A `LANGUAGE sql` function plans its body with the
   arguments as parameters. With an unknown search term, the planner estimated 7 matching rows and
   ran the visibility check as a nested-loop semi join. A search matching 9 100 products then
   rescanned the 200 scopes for each one: 180–260 ms. The function is therefore PL/pgSQL with
   `RETURN QUERY EXECUTE … USING`. The query text is a constant, the arguments are bound
   parameters (no string is built from input), and Postgres plans with the real values. The same
   search takes about 48 ms, nearly all of it ranking. Planning costs about 1 ms per call.
3. **JIT.** Each RLS policy adds a subplan to the cost estimate of every row it guards. With the
   function's default estimate of 1 000 rows, the outer query's cost passed `jit_above_cost`, and
   Postgres spent about 200 ms compiling a query that ran in 25 ms. The function now declares
   `ROWS 50`, matching discovery's maximum page. The migration also sets `jit = off` for the
   database (`ALTER DATABASE … SET jit = off`, reset on downgrade). This workload is short OLTP
   queries, where JIT compilation never pays back. The measured case: `EXPLAIN ANALYZE` reported
   `JIT: … Total 204.098 ms` (generation 18 ms, optimisation 10 ms, emission 176 ms) on a query
   whose plan nodes finished in 26 ms. The owner accepted `jit = off` on 2026-11-20.
   **It applies to every session on the database**: the API, the worker, migrations and the
   `haatly_ops` role. A future analytical query that would benefit from JIT has to turn it on
   for its own transaction.

The outer discovery query also drives from the candidates with two `LATERAL … LIMIT 1` lookups:
the product by primary key, then its store among the nearby ones. Without that, the nearby-store
CTE, which RLS makes the planner estimate at 1 row, ended up on the outside of the join. The
planner then either hashed all 7 280 visible products or repeated the 49 lookups 182 times. Both
`LIMIT 1`s are exact, because product ids and nearby store ids are unique.

A test compares the new query with the previous, unindexed one. Both run for an anonymous caller,
a member and a seller, over 75 products with many equal ranks and two stores at the same distance.
The test requires the same rows in the same order.

## Consequences

- Each product write also writes `product_search` and its two GIN indexes, without the fast-update
  buffer.
- `catalog.products` keeps its own GIN indexes, which the app role cannot use. Dropping them is a
  separate change.
- Discovery's cost now grows with the number of visible products matching the search within the
  radius, not with all products within it. Ranking costs about 5.5 µs per match, so a term that
  matches nearly every nearby product still costs about 40 ms in the function. The measured numbers
  are in ADR 0016.
- `jit = off` applies to every session on the database, including migrations and the worker.
