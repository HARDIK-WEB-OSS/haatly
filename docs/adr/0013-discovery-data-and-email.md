# 0013 Discovery data and email delivery

- Status: Accepted for PIN codes and distance (M3). The email part is Proposed for M7: no
  `EmailSender` port exists yet.
- Date: 2026-11-18

## Decision

### PIN code locations

- Buyers discover stores by PIN code or by distance. PIN centroids come from the India Post pincode
  directory published on data.gov.in under the Government Open Data License – India.
- Attribution is in the README.
- The dataset is loaded into the discovery schema by a versioned import task, not committed as a
  migration. A PIN that is not in the dataset is rejected at input.
- Distance uses PostGIS `geography` and a GiST index. Text search uses `tsvector` plus `pg_trgm`.

### Email (Proposed, M7)

- Email will go through an `EmailSender` port.
- The development adapter will write each message to the console or to a file.
- The real provider is chosen in M7, together with password reset and email verification
  (`docs/roadmap.md`). Until then, no flow may depend on email being delivered.

Correction, 2026-11-19: this ADR used to say the email port came "from M2". It was never
built, and the review of 2026-11-18 reported the claim as finding H2.
