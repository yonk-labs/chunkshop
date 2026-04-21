Release 2026.04 — April release notes

This is the April 2026 release. It is smaller than last month's because we spent most of
the sprint on the migration work described in the engineering docs. The customer-facing
changes are listed below with rationale.

Shipment search now ranks exact-match SKUs above partial-match descriptions. Customer
feedback from the March QBR made it clear that when a warehouse operator types a SKU they
are looking for that SKU, not a keyword match against the long description. The previous
ranking occasionally buried the exact match three screens down, which is the sort of thing
that makes a tool feel broken even though the data is technically correct.

The audit log now persists for 180 days instead of 30. Several customers asked for this in
response to their own compliance audits, where 30 days was shorter than their audit window.
180 days adds disk cost but not enough to be worth pricing separately.

Two internal changes worth flagging. Background jobs now run on a separate worker pool from
the request-serving API. This is a correctness fix — previously, a slow batch job could
starve the request pool and cause user-facing latency spikes. The separation is invisible
to customers but fixes a class of "why is it slow right now" complaints. Second, we moved
the feature-flag service to the new secrets-handling pattern described in the security
handbook.

No breaking changes this release. The next release will deprecate the v1 SKU-search API;
see the migration guide on the developer portal.
