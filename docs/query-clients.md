# Querying chunkshop from any language

chunkshop's ingest side is Python today (Rust/Go ports are planned). The **query side is
language-neutral** — chunkshop writes to a plain pgvector table, so any client that can (a)
embed a query string with the same model and (b) talk to Postgres can read the results.

This doc shows a minimal top-K similarity query in Python, JavaScript/TypeScript (Node),
Rust, and Go. Each example:

1. Embeds the query text using the same model the ingest cell used.
2. Runs a pgvector cosine-distance query with `<=>`.
3. Returns the top 3 most-similar chunks.

> **Want hybrid (semantic + keyword) search, fusion, or Fast-mode RAG
> summarization in Python?** This doc is the language-neutral raw-SQL path. For
> the higher-level in-process Python read API — `semantic_search`,
> `keyword_search`, `hybrid_search` (RRF / weighted fusion), and the
> `summarize_hits` Fast-mode helper that collapses K chunks into one
> query-biased summary — see [`hybrid-search.md`](hybrid-search.md). It works on
> all four backends.

## Prerequisites

- Postgres with pgvector, populated by a chunkshop ingest (follow
  [`tutorial.md`](tutorial.md) if you don't have one yet).
- Know your cell's **embedder**. The shipped default is
  `Xenova/bge-base-en-v1.5-int8` (768 dim, cosine distance).
- Know your cell's **schema and table** names.
- Vectors only match when the query embedder is **identical** to the ingest embedder —
  same model ID, same quantization, same pooling. Mixing fp32 vs int8 of the same model is
  usually close enough; mixing `bge-base` with `bge-small` is not.

## The query shape (all languages)

```sql
SELECT doc_id, seq_num, metadata->>'heading', original_content,
       embedding <=> $1 AS distance
FROM   chunkshop_samples.handbook
ORDER  BY embedding <=> $1
LIMIT  3;
```

`<=>` is pgvector's cosine distance — smaller is more similar. The parameter `$1` is a
`vector(768)` literal built client-side from the query embedding.

---

## Python (`fastembed` + `psycopg`)

This is the idiomatic path because chunkshop already registers its int8 variants with
fastembed when you `import chunkshop.embedders`.

```python
# pip install fastembed psycopg[binary] chunkshop
import os, psycopg
from fastembed import TextEmbedding
import chunkshop.embedders  # registers Xenova bge-base-int8 + bge-small-int8

MODEL = "Xenova/bge-base-en-v1.5-int8"
DSN   = os.environ["CHUNKSHOP_DSN"]
QUERY = "how do we rotate API keys"

embedder = TextEmbedding(model_name=MODEL, threads=4)
qvec = list(embedder.embed([QUERY]))[0]          # numpy float32, shape (768,)

with psycopg.connect(DSN) as conn, conn.cursor() as cur:
    cur.execute(
        """
        SELECT doc_id, seq_num, metadata->>'heading', original_content,
               embedding <=> %s::vector AS distance
        FROM chunkshop_samples.handbook
        ORDER BY embedding <=> %s::vector
        LIMIT 3
        """,
        (qvec.tolist(), qvec.tolist()),
    )
    for doc_id, seq, heading, content, dist in cur.fetchall():
        print(f"{dist:.3f}  {doc_id}#{seq}  {heading}  {content[:80]!r}")
```

## JavaScript / TypeScript — Node (`@huggingface/transformers` + `pg` + `pgvector`)

`@huggingface/transformers` (Xenova's project, now official under Hugging Face) runs the
same ONNX files chunkshop uses. Pass `dtype: 'q8'` for the int8 variant (the old
`quantized: true` flag was replaced in v3; Node's WASM default is already `q8` but being
explicit is safer).

```typescript
// npm install @huggingface/transformers pg pgvector
import { pipeline } from '@huggingface/transformers';
import { Client } from 'pg';
import pgvector from 'pgvector/pg';

const MODEL = 'Xenova/bge-base-en-v1.5';   // Xenova repo; dtype below selects int8
const QUERY = 'how do we rotate API keys';

// First call downloads ONNX to the Transformers.js cache
const embedder = await pipeline('feature-extraction', MODEL, { dtype: 'q8' });
const out = await embedder(QUERY, { pooling: 'cls', normalize: true });
const qvec: number[] = Array.from(out.data as Float32Array);   // length 768

const pg = new Client({ connectionString: process.env.CHUNKSHOP_DSN });
await pg.connect();
await pgvector.registerTypes(pg);     // teaches pg to parse `vector` columns

const res = await pg.query(
  `SELECT doc_id, seq_num, metadata->>'heading' AS heading, original_content,
          embedding <=> $1 AS distance
   FROM   chunkshop_samples.handbook
   ORDER  BY embedding <=> $1
   LIMIT  3`,
  [pgvector.toSql(qvec)],
);
for (const r of res.rows) {
  console.log(`${r.distance.toFixed(3)}  ${r.doc_id}#${r.seq_num}  ${r.heading}  ${r.original_content.slice(0, 80)}`);
}
await pg.end();
```

Caveats:
- `pooling: 'cls'` and `normalize: true` are required to match BGE's ingest-side convention.
- `pgvector.toSql(qvec)` handles the vector-literal encoding so you don't hand-roll
  `` `[${qvec.join(',')}]` `` strings.

## Rust (`fastembed-rs` + `sqlx`)

`fastembed-rs` (by @Anush008) is Python `fastembed`'s sibling — same ONNX files, same
pooling conventions.

```toml
# Cargo.toml
[dependencies]
fastembed = "5"                                 # pulls ort + tokenizers
sqlx = { version = "0.8", features = ["runtime-tokio", "postgres"] }
pgvector = { version = "0.4", features = ["sqlx"] }
tokio = { version = "1", features = ["full"] }
anyhow = "1"
```

```rust
use fastembed::{EmbeddingModel, InitOptions, TextEmbedding};
use pgvector::Vector;
use sqlx::{postgres::PgPoolOptions, Row};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let dsn = std::env::var("CHUNKSHOP_DSN")?;
    let query = "how do we rotate API keys";

    // fastembed-rs bundles common BGE variants. For the exact Xenova int8 file,
    // pass a custom HF source via InitOptions { cache_dir, .. } and model_file path.
    let model = TextEmbedding::try_new(
        InitOptions::new(EmbeddingModel::BGEBaseENV15).with_show_download_progress(true),
    )?;
    let embeddings = model.embed(vec![query], None)?;    // Vec<Vec<f32>> length 1
    let qvec = Vector::from(embeddings.into_iter().next().unwrap());

    let pool = PgPoolOptions::new().connect(&dsn).await?;
    let rows = sqlx::query(
        "SELECT doc_id, seq_num, metadata->>'heading' AS heading, original_content,
                (embedding <=> $1) AS distance
         FROM   chunkshop_samples.handbook
         ORDER  BY embedding <=> $1
         LIMIT  3",
    )
    .bind(qvec)
    .fetch_all(&pool)
    .await?;

    for row in rows {
        let doc_id: String = row.get("doc_id");
        let seq: i32 = row.get("seq_num");
        let heading: Option<String> = row.get("heading");
        let content: String = row.get("original_content");
        let dist: f64 = row.get("distance");
        println!("{:.3}  {}#{}  {:?}  {}...", dist, doc_id, seq,
                 heading.as_deref().unwrap_or(""),
                 &content[..content.len().min(80)]);
    }
    Ok(())
}
```

Caveat: `fastembed-rs`'s bundled `BGEBaseENV15` is the fp32 BAAI model. For exact parity
with chunkshop's int8 (same vectors to within quantization noise), either (a) keep fp32
client-side and accept the small distance drift, or (b) point `fastembed-rs` at the
`Xenova/bge-base-en-v1.5-int8` ONNX file via `TextEmbedding::try_new_from_user_defined()`
with a custom `UserDefinedEmbeddingModel`.

## Go (`hugot` + `pgx` + `pgvector-go`)

`hugot` (Knights-Analytics) wraps ONNX Runtime (optionally) and HF tokenizers. Three
session backends: `NewGoSession` (pure-Go, default), `NewORTSession` (C++ ORT, needs build
tag), `NewXLASession` (XLA).

```go
// go get github.com/knights-analytics/hugot
// go get github.com/jackc/pgx/v5
// go get github.com/pgvector/pgvector-go
package main

import (
    "context"
    "fmt"
    "os"

    "github.com/jackc/pgx/v5"
    "github.com/knights-analytics/hugot"
    "github.com/knights-analytics/hugot/pipelines"
    "github.com/pgvector/pgvector-go"
)

func main() {
    ctx := context.Background()
    dsn := os.Getenv("CHUNKSHOP_DSN")
    query := "how do we rotate API keys"

    sess, err := hugot.NewGoSession(ctx)
    must(err)
    defer sess.Destroy()

    // hugot downloads ONNX + tokenizer files into ./models/
    modelPath, err := hugot.DownloadModel(ctx,
        "Xenova/bge-base-en-v1.5", "./models/", hugot.NewDownloadOptions())
    must(err)

    cfg := hugot.FeatureExtractionConfig{
        ModelPath:    modelPath,
        Name:         "bge-base",
        OnnxFilename: "model_quantized.onnx",     // int8 variant (match chunkshop default)
        Options: []hugot.FeatureExtractionOption{
            pipelines.WithNormalization(),         // BGE expects L2-normalized output
        },
    }
    embedder, err := hugot.NewPipeline(sess, cfg)
    must(err)

    out, err := embedder.RunPipeline(ctx, []string{query})
    must(err)
    qvec := pgvector.NewVector(out.Embeddings[0])   // []float32 length 768

    conn, err := pgx.Connect(ctx, dsn)
    must(err)
    defer conn.Close(ctx)

    rows, err := conn.Query(ctx,
        `SELECT doc_id, seq_num, metadata->>'heading', original_content,
                embedding <=> $1 AS distance
         FROM   chunkshop_samples.handbook
         ORDER  BY embedding <=> $1
         LIMIT  3`, qvec)
    must(err)
    defer rows.Close()

    for rows.Next() {
        var docID, heading, content string
        var seq int32
        var dist float64
        must(rows.Scan(&docID, &seq, &heading, &content, &dist))
        if len(content) > 80 { content = content[:80] }
        fmt.Printf("%.3f  %s#%d  %s  %s...\n", dist, docID, seq, heading, content)
    }
}

func must(err error) { if err != nil { panic(err) } }
```

Caveats:
- `hugot.NewGoSession(ctx)` takes a `context.Context` and is pure Go. Use `NewORTSession` if
  you want the C++ ONNX Runtime backend — requires the `ORT` build tag and a local libonnxruntime.
- `OnnxFilename: "model_quantized.onnx"` is the Xenova convention for the int8 file. Drop
  this field to use the default `model.onnx` (fp32).

---

## Tokenizer / pooling parity checklist

Query vectors only match ingested vectors when the client side matches the ingest side on:

| Setting | What to match |
|---|---|
| Model weights | Same `model_name` + same quantization (fp32 vs int8 of same arch is *close* but not bit-exact) |
| Tokenizer | HF tokenizer shipped with the model (all four clients above pull it automatically) |
| Max seq length | BGE = 512 tokens. Queries longer than that are truncated identically across clients |
| Pooling | **CLS** pooling (first token) — set explicitly in the JS/TS and Go examples |
| Normalization | **L2-normalize** output vectors — explicit in JS/TS and Go; automatic in fastembed/fastembed-rs |
| Distance | **cosine** (`<=>`) — never L2 (`<->`) against normalized vectors unless you want squared-distance semantics |

If your retrieval results look "close but off by a few hundredths," one of the rows above is
almost always the cause.

## Why this works

chunkshop's [architecture](architecture.md) only specifies the **target table layout** —
not the query path. That layout is:

- `embedding vector({dim})` — the model-native dim.
- `doc_id`, `seq_num` — stable composite primary key (`{doc_id}::{seq_num}`).
- `original_content` — raw chunk body, grep-friendly, safe to show users.
- `embedded_content` — what was embedded (may include heading prefix, neighbors).
- `metadata jsonb` — per-chunk dict (heading, strategy, framer, extracted entities).
- `tags text[]` — flat tag list.
- `source text` — provenance tag (when `source_tag` was set on the cell).

Any pgvector client can query this. The examples above use `original_content` for display
and `embedding` for similarity — you can of course project any column or filter on
`metadata`, `source`, or promoted metadata columns (see
[`tutorial-multi-source.md`](tutorial-multi-source.md) + [`tutorial-metadata.md`](tutorial-metadata.md)).
