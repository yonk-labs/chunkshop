# Sales call note — Johnson Finance LLC / OmniConnect Proxy / 2026-04-10

**Customer:** Johnson Finance LLC (Retail · ?, ?, USA)
**Deal:** Order #6898 (won, $1008.01, closed 2026-04-19)
**Product:** OmniConnect Proxy (uncategorized)
**Salesperson:** Ava Chen
**Note type:** meeting
**Sentiment:** unspecified

## Notes

Customer covered the following challenges:
1. The database is spending more CPU on connection setup/teardown than on running queries.
2. Our developers are not all security experts, and we can't be sure all inputs are properly sanitized.

Proposed OmniConnect Proxy as a solution to address these needs.

## Win reason

Customer is launching in a new geographic region and needs to store user data locally for data sovereignty (e.g., GDPR). The proxy's sharding rules allow them to route queries based on user\\_id to the correct regional database.
