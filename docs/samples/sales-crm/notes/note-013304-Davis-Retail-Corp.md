# Sales call note — Davis Retail Corp / OmniConnect Proxy / 2026-02-21

**Customer:** Davis Retail Corp (Retail · ?, ?, USA)
**Deal:** Order #2282 (won, $574.57, closed 2026-04-06)
**Product:** OmniConnect Proxy (uncategorized)
**Salesperson:** Ava Chen
**Note type:** email
**Sentiment:** unspecified

## Notes

Customer discussed the following challenges:
1. Our load balancer isn't database-aware, so it keeps sending traffic to a node that is overloaded or in maintenance.
2. An attacker used a SQLi vulnerability to exfiltrate our entire customer list.

Proposed OmniConnect Proxy as a solution to address these needs.

## Win reason

Customer needed to enforce a strict allow-list of known-good queries for a specific, high-risk application. OmniConnect was the only solution that could enforce query-level rules.
