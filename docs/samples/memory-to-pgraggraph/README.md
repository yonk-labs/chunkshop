# Memory → pg-raggraph bridge example

End-to-end example that takes raw agent session events all the way to a
pg-raggraph ingest call:

1. Stage events into chunkshop's `chunkshop_staging` table.
2. Run the chunkshop `consolidate.yaml` cell to build the two-tier
   `agent_memory.memory` store (episode chunks + atomic fact rows).
3. Use `chunkshop.memory.read_pre_chunked(dsn)` to read the consolidated
   store back out in the shape pg-raggraph's
   `GraphRAG.ingest_records()` accepts.
4. Hand the records to pg-raggraph (or any consumer with the same
   ingest contract).

## Why this lives in chunkshop, not pg-raggraph

chunkshop owns the schema (`agent_memory.memory` and the SP-A column
contract), so the bridge that translates *out of* that schema belongs
here. Keeping the helper in chunkshop means:

- pg-raggraph stays generic (no chunkshop-specific reader code).
- Consumers depend on **one** repo to read SP-A memory, not two.
- Schema drift fails at the bridge boundary, not silently downstream.
- chunkshop has no runtime dependency on pg-raggraph — the helper
  yields plain dicts; pg-raggraph imports happen only in the consumer
  (this example).

## O2 defaults

`read_pre_chunked()` enforces SP-A operational invariant O2
("consolidated-wins") at the read layer by default:

- `tier="consolidated"` — provisional/realtime rows are not surfaced.
- `include_retracted=False` — soft-invalidated facts are hidden.

Pass `tier=None` or `include_retracted=True` to override.

## Run it

```bash
export CHUNKSHOP_MEMORY_DSN="postgresql://postgres:postgres@localhost:5434/chunkshop_test"

# 1. Stage + consolidate (writes agent_memory.memory)
python docs/samples/memory-to-pgraggraph/run.py --seed --consolidate

# 2. Hand the records to pg-raggraph
python docs/samples/memory-to-pgraggraph/run.py --ingest
```

(Step 2 requires `pip install pg-raggraph[chunkshop]>=0.4.3` in the
consumer env. chunkshop itself has no such dep.)

## Mapping table

| `agent_memory.memory` column | pg-raggraph record field |
|---|---|
| `original_content` | `pre_chunked[i].content` |
| `embedded_content` | `pre_chunked[i].embedded_content` |
| `embedding` | `pre_chunked[i].embedding` |
| `subject` (kind=fact) | `known_relationships[i].src` |
| `predicate` (kind=fact) | `known_relationships[i].rel_type` |
| `object` (kind=fact) | `known_relationships[i].dst` |
| `support_span` (kind=fact) | `known_relationships[i].description` |
| `confidence` (kind=fact) | `known_relationships[i].weight` |
| `session_id` | `metadata.session_id`, `source_id` suffix |
| `namespace` | `metadata.namespace`, `source_id` infix |
| `tier`, `recorded_at` | `metadata.tier`, `metadata.recorded_at` |

`skip_llm=True` is set unconditionally — SP-A's consolidation already
extracted whatever structured triples it could, so re-running
pg-raggraph's LLM extractor would duplicate work.

See chunkshop's
[SP-A design spec](../../superpowers/specs/2026-05-19-chunkshop-memory-primitives-sp-a-design.md)
and pg-raggraph's `docs/cookbook/chunkshop-integration.md` Pattern C.
