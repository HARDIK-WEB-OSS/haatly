# 0010 Order, refund and return lifecycle

- Status: Accepted. To be implemented in M4 and M5.
- Date: 2026-11-18

## Decision

### Order states

```
pending_payment -> paid | expired | cancelled | refund_pending
expired         -> refund_pending          (payment captured after the hold lapsed)
paid            -> shipped | refund_pending
shipped         -> delivered
delivered       -> return_requested | completed
return_requested -> refund_pending | completed   (completed when the return is rejected)
refund_pending  -> refunded
```

- Every transition writes an `order_events` row and an outbox event in the same transaction.
- `delivered -> completed` happens when the 7-day return window closes.
- The order moves to `refunded` only when the total refunded equals the captured amount. A partial
  refund leaves the order state unchanged.
- **A late capture** (captured after the holds expired, or after the order expired) moves the order to
  `refund_pending` and enqueues an automatic full refund job. The buyer is never left charged with
  nothing to show for it (v1 audit F-3).

### Refunds

- Refunds have their own table and their own states (`requested -> submitted -> processed | failed`).
- The refund row exists before the provider call. The Razorpay idempotency key is the refund id.
- A stuck refund is settled by reconciliation against Razorpay, never by manual SQL (v1 audit F-4).

### Returns

- Returns have their own entity:
  `requested -> approved -> received -> refunded`, or `requested -> rejected`.
- When a return is rejected, the order goes `return_requested -> completed`.
- Restock happens only on `received`, as a TigerBeetle transfer back to the variant account.

### Checkout

- Holds are created only when the buyer presses Pay. A buyer may have at most **3 `pending_payment`
  orders with live holds**, and at most 10 units per variant.
- After the commit there is one inline Razorpay order-creation attempt, with `receipt` set to the
  payment id (a 36-character UUIDv7, within Razorpay's 40-character limit). If it fails, a job
  retries it and the HTMX page polls until the provider order exists.
- A non-member cannot check out a product whose price is hidden from them, and gets 404. Hidden means
  hidden end to end (v1 audit F-2).

## Consequences

- `expired` is not terminal when money arrives late. Every consumer of order states must handle
  `expired -> refund_pending`.
