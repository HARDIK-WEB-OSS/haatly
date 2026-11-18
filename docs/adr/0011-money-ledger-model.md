# 0011 Money ledger model (TigerBeetle ledger 1, INR in paise)

- Status: Accepted (implemented in M5 and M6)
- Date: 2026-11-18

## Decision

### Amounts

- **G** is the gross amount captured from the buyer, in paise.
- **F** is the platform fee:
  `F = floor(G × platform_fee_bps / 10 000)`, computed in integers.
  `platform_fee_bps` is a per-store setting, default 0, and is **snapshotted onto the order at
  checkout** so later changes never touch existing orders.
- The seller always receives **G − F**.
- The Razorpay gateway fee is absorbed by the platform into `gateway_fee_expense`. That includes the
  part Razorpay does not return on refunds.
- No floats or Decimals anywhere on this path.

### Accounts

- `gateway_receivable`, `platform_revenue`, `gateway_fee_expense`, `bank`, `payouts_in_transit`.
- One `seller_payable` per store, with `debits_must_not_exceed_credits`.

### Transfers (debit → credit)

| Event | Legs |
|---|---|
| Capture | linked: `gateway_receivable → seller_payable` (G − F); `gateway_receivable → platform_revenue` (F) |
| Refund of r paise | linked reversal legs, split in proportion: `seller_payable → gateway_receivable` (r − f_r); `platform_revenue → gateway_receivable` (f_r), where `f_r = floor(r × F / G)` and the last refund takes the remainder so the totals reverse exactly |
| Settlement from Razorpay | `bank → gateway_receivable` (net settled); `gateway_fee_expense → gateway_receivable` (fee) |
| Payout (SimulatedPayouts / bank path) | pending `seller_payable → payouts_in_transit`; posted on confirmation; then `payouts_in_transit → bank` when confirmed |
| Payout (RazorpayRoute) | `seller_payable → gateway_receivable` (money leaves the Razorpay balance directly to the seller's linked account) |

- Payouts sit behind a `PayoutGateway` port with two adapters, `RazorpayRoute` and
  `SimulatedPayouts`. `SimulatedPayouts` mirrors the bank path so tests exercise the full chain.
- A payout is made only after the return window has closed, so a refund can never hit a seller
  payable that was already paid out.

## Consequences

- The M5 exit property test drives random event sequences and asserts that every account stays
  consistent. Balances never go negative where flagged, and the sum of all legs is zero.
- The M6 exit reconciles N simulated orders with refunds and payouts to 0 paise unexplained.
