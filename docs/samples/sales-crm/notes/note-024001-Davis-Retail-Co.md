# Sales call note — Davis Retail Co / OmniConnect Proxy / 2026-04-06

**Customer:** Davis Retail Co (Education · ?, ?, USA)
**Deal:** Order #6237 (won, $15.20, closed 2026-04-19)
**Product:** OmniConnect Proxy (uncategorized)
**Salesperson:** Liam Park
**Note type:** internal
**Sentiment:** unspecified

## Notes

Customer reviewed the following challenges:
1. We have many microservices, and the combined connection count is crashing the database.
2. Our load balancer isn't database-aware, so it keeps sending traffic to a node that is overloaded or in maintenance.

Proposed OmniConnect Proxy as a solution to address these needs.

## Win reason

Customer needed to achieve their 99.99% uptime SLA, and their current DNS-based failover was too slow and unreliable.
