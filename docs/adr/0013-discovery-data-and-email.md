# 0013 Discovery data and email delivery

- Status: Accepted (discovery in M3, email port from M2, provider chosen in M7)
- Date: 2026-11-18

## Decision

### PIN code locations

- Buyers discover stores by PIN code or by distance. PIN centroids come from the India Post pincode
  directory published on data.gov.in under the Government Open Data License – India.
- Attribution is in the README.
- The dataset is loaded into the discovery schema by a versioned import task, not committed as a
  migration. A PIN that is not in the dataset is rejected at input.
- Distance uses PostGIS `geography` and a GiST index. Text search uses `tsvector` plus `pg_trgm`.

### Email

- Email goes through an `EmailSender` port.
- The development adapter writes each message to the console or to a file.
- The real provider is chosen in M7. Until then, no flow may depend on email being delivered.
