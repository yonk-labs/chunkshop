# R4-SC-006 — Manual cross-language vector parity check

This is a manual verification step. Full automation lands with RT (Wave 3 matrix test).

## Goal
Write 5 chunks via Python `ClickHouseSink`; query top-5 via Rust `ClickhouseSink::query_top_k`;
assert matching IDs in matching order.

## Prereqs
- ClickHouse 24.10+ running on localhost:8124 (use `docker compose -f docker-compose.test.yaml up -d clickhouse`)
- Both Python (chunkshop) and Rust (chunkshop-rs) crates built

## Steps

### 1. Set env
```bash
export CHUNKSHOP_TEST_DSN_CH='clickhouse://default:chpw@localhost:8124/chunkshop_xlang'
```

### 2. Python writer (5 fixed chunks)
```bash
cd python
uv run python -c '
from chunkshop.config import TargetConfig
from chunkshop.backends.clickhouse import ClickHouseBackend
from chunkshop.sinks.clickhouse import ClickHouseSink
from chunkshop.chunkers.base import Chunk
import numpy as np, json

cfg = TargetConfig(
    type="clickhouse", dsn_env="CHUNKSHOP_TEST_DSN_CH",
    database="chunkshop_xlang", table="parity_chunks",
    mode="overwrite", source_tag="py", hnsw=False,
)
backend = ClickHouseBackend(dsn_env=cfg.dsn_env)
sink = ClickHouseSink(cfg, backend, embed_dim=4)
sink.create_table()
chunks = [Chunk(doc_id="d", seq_num=i, original_content=f"o{i}", embedded_content=f"e{i}", metadata={}) for i in range(5)]
embs = np.array([[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1],[0.5,0.5,0,0]], dtype=np.float32)
tags = [[] for _ in chunks]
sink.write_document("d", chunks, embs, tags)
print("wrote 5 chunks via Python")
'
```

### 3. Rust reader (top-5 cosine)
There is no `chunkshop-rs query` CLI subcommand yet (out of R4 scope). Use a small Rust harness — write to `/tmp/r4_query.rs`:

```rust
// /tmp/r4_query.rs — paste into a Rust scratch project or run via `cargo test`
use chunkshop::backends::ClickhouseBackend;
use chunkshop::config::ClickhouseTargetConfig;
use chunkshop::sinks::ClickhouseSink;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let yaml = "type: clickhouse\ndsn_env: CHUNKSHOP_TEST_DSN_CH\ndatabase: chunkshop_xlang\ntable: parity_chunks\nmode: append\nsource_tag: rs_query\nhnsw: false";
    let raw: serde_yaml_ng::Value = serde_yaml_ng::from_str(yaml).unwrap();
    let target: chunkshop::config::TargetConfig = serde_yaml_ng::from_value(raw).unwrap();
    let chunkshop::config::TargetConfig::Clickhouse(cfg) = target else { unreachable!() };
    let backend = ClickhouseBackend::new(cfg.dsn_env.clone());
    let sink = ClickhouseSink::new(cfg, backend, 4);
    let hits = sink.query_top_k_impl(&[1.0, 0.0, 0.0, 0.0], 5).await?;
    for (doc, seq, dist) in hits {
        println!("{doc}::{seq} dist={dist:.6}");
    }
    Ok(())
}
```

Or simpler: run the existing `query_top_k_returns_nearest_chunks` test and inspect output, then manually compare to Python's writeback rank order.

### Expected result
For query vector `[1, 0, 0, 0]`, Python and Rust must both return:
- doc_id=d seq_num=0 (perfect match, distance ~0)
- doc_id=d seq_num=4 (mid match, [0.5,0.5,0,0])
- doc_id=d seq_num=1, 2, 3 (orthogonal, distance ~1)

ID order on rank 0 and 4 is deterministic. Distance values may differ in last-digit precision between Python's numpy float and Rust's f32, but the ranking must match.

### Cleanup
```bash
docker compose -f /home/yonk/yonk-tools/chunkshop-v4/docker-compose.test.yaml exec clickhouse \
    clickhouse-client -u default --password chpw -q 'DROP DATABASE IF EXISTS chunkshop_xlang SYNC'
```

## Sign-off
SC-006 is satisfied when the Rust top-5 query returns the same `(doc_id, seq_num)` order as the Python reference for the test vector. Full programmatic automation is RT's job.
