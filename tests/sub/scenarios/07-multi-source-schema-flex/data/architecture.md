# Platform Architecture

## Ingestion
The ingestion tier accepts events over HTTPS and writes them to a durable
topic. Schema validation runs at the edge so malformed payloads are rejected
before they consume any downstream capacity. Per-tenant rate limits are
enforced at the same layer.

## Processing
A streaming job consumes the topic and produces enrichments against a warm
cache of customer metadata. Results land in a short-retention kafka topic
that a second job fans out to the long-term store.

## Storage
Hot data lives in the row-store for the first seven days; after that it
rolls into a columnar store optimized for analytical queries. Both stores
share a single schema catalog so consumers see one logical table.
