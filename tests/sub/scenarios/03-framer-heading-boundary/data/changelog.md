## Storage engine rewrite
The write path now routes through the new LSM implementation by default. Read
latency drops by 30% on hot data because the bloom filters are tighter and the
memtable is memory-mapped. Legacy engines remain available behind a feature flag
for one release cycle, after which they will be removed.

## Query planner cost model
The planner now accounts for index selectivity when estimating scan costs. In
practice this means a composite index with a high-cardinality leading column is
chosen more often, which matters for dashboards that filter on customer_id. The
old heuristic is retained as a fallback when statistics are missing.

## API deprecation notice
The v1 REST endpoints are formally deprecated as of this release. New code must
use the v2 endpoints; the v1 surface will remain available for six months and
then return 410 Gone. Client libraries handle the upgrade transparently when
pinned to the latest minor.

## Observability additions
Three new metrics are exported: cache hit ratio per shard, slow-query count per
namespace, and replica lag in milliseconds. Existing metrics are unchanged and
scrape URLs are backwards compatible with Prometheus configurations from the
last major release.
