# Sales call note — Miller Retail Inc / OmniConnect Proxy / 2026-02-18

**Customer:** Miller Retail Inc (Technology · ?, ?, USA)
**Deal:** Order #10599 (won, $99612.72, closed 2026-04-19)
**Product:** OmniConnect Proxy (uncategorized)
**Salesperson:** Sales Rep 19
**Note type:** unspecified
**Sentiment:** unspecified

## Notes

Technical deep-dive with Miller Retail Inc's engineering team. Good discussion on Security code reviews are slow and can't catch every possible injection vector. and Our serverless functions are overwhelming the database by opening thousands of short-lived connections.. Jane Smith asking for reference customers.

## Win reason

Internal security policy mandated real-time alerting on privileged data access (e.g., SELECT \\* on users table). Proxy's logging enables this at the edge.
