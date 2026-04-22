# Billing proration on seat changes

Mid-cycle seat adds charge a prorated amount today and roll the full per-seat price into next cycle. Seat removals credit the remainder of the cycle against the next invoice — we do not refund to the original payment method.

## How proration is calculated

We compute days remaining in the current cycle, divide by total cycle days, multiply by the per-seat price. Shown to the admin on the checkout screen before they confirm — no hidden math.

## Timing edge cases

Adds made in the last 48 hours of a cycle round up to a full seat — proration below roughly four percent is noisy and we would rather charge cleanly than send a line item for sixteen cents. Removals in the same window still credit normally.

## When the customer disputes a charge

Pull the invoice from Billing → Invoices and open the "seat changes" tab. Every add and remove during that cycle is listed with the proration math shown. Most disputes resolve as soon as the customer sees the breakdown.
