# Sales call note — Garcia Education Ltd / OmniConnect Proxy / 2026-02-21

**Customer:** Garcia Education Ltd (Manufacturing · ?, ?, USA)
**Deal:** Order #2200 (won, $504.66, closed 2026-02-21)
**Product:** OmniConnect Proxy (uncategorized)
**Salesperson:** Ava Chen
**Note type:** email
**Sentiment:** unspecified

## Notes

Customer discussed the following challenges:
1. We can't scale writes horizontally, creating a massive bottleneck for our fast-growing application.
2. Our serverless functions are overwhelming the database by opening thousands of short-lived connections.

Proposed OmniConnect Proxy as a solution to address these needs. Garcia Education Ltd team seems very interested.

## Win reason

A read replica lagged too far behind the primary, and the application started serving stale data. The proxy's replication-aware health checks detected the lag and automatically stopped routing traffic to that replica.
