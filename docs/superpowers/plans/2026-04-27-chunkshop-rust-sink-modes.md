# chunkshop Rust Sink Full-Mode Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rust-sink-modes.md` (in this worktree).

**Goal:** Bring `chunkshop-rs`'s pgvector sink to feature-parity with Python's: `mode: append`, `force_overwrite`, source-tag conflict check, advisory-lock-serialised schema setup, and `promote_metadata` jsonb-to-typed-column writes.

**Architecture:** Replace the parsed-but-unused `promote_metadata: Option<serde_yml::Value>` with a `Vec<PromoteColumn>` of typed structs validated at config-load. Extend `PgVectorSink` with `_append_preflight`, source-tag conflict gating in `overwrite_create`, advisory-lock wrapping in `create_table`, and a `_ensure_promote_columns` helper. Promote columns plumb through `write_document` as additional INSERT columns. New integration test fronts the cross-language append story.

**Tech Stack:** Rust 2021, sqlx (existing), `blake2 = "0.10"` (new — for the deterministic advisory-lock key).

---

## Task 1: Add `PromoteColumn` struct + validators

**Files:** `rust/chunkshop/src/config.rs`

- [ ] **Step 1: Add the dep**

In `rust/chunkshop/Cargo.toml`, append to `[dependencies]`:

```toml
blake2 = { version = "0.10", default-features = false }
```

- [ ] **Step 2: Add the type allowlist + path regex constants**

At the top of `rust/chunkshop/src/config.rs` (after existing `use` block), add:

```rust
const ALLOWED_PROMOTE_TYPES: &[&str] = &[
    "text", "text[]", "int", "bigint", "boolean", "jsonb", "timestamptz", "date",
];
```

(The path-segment regex is composed inline in the validator below.)

- [ ] **Step 3: Add `PromoteColumn` struct with custom Deserialize**

Add to `config.rs`:

```rust
#[derive(Debug, Clone)]
pub struct PromoteColumn {
    pub path: String,
    pub type_: String,
}

impl PromoteColumn {
    /// Postgres column identifier — dots → double-underscore, lowercased.
    /// Mirrors Python's `PromoteColumn.column_name`.
    pub fn column_name(&self) -> String {
        self.path.replace('.', "__").to_lowercase()
    }

    fn validate_path(path: &str) -> Result<(), String> {
        if path.is_empty() {
            return Err("path must not be empty".into());
        }
        let seg_re = Regex::new(r"^[A-Za-z_][A-Za-z0-9_]*$").unwrap();
        for seg in path.split('.') {
            if !seg_re.is_match(seg) {
                return Err(format!(
                    "path segments must match ^[A-Za-z_][A-Za-z0-9_]*$ separated by '.', got {path:?}"
                ));
            }
        }
        Ok(())
    }

    fn validate_type(t: &str) -> Result<(), String> {
        if !ALLOWED_PROMOTE_TYPES.contains(&t) {
            return Err(format!(
                "promote_metadata type must be one of {ALLOWED_PROMOTE_TYPES:?}, got {t:?}"
            ));
        }
        Ok(())
    }
}

impl<'de> serde::Deserialize<'de> for PromoteColumn {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        #[derive(serde::Deserialize)]
        struct Raw {
            path: String,
            #[serde(rename = "type")]
            type_: String,
        }
        let r = Raw::deserialize(d)?;
        Self::validate_path(&r.path).map_err(serde::de::Error::custom)?;
        Self::validate_type(&r.type_).map_err(serde::de::Error::custom)?;
        Ok(Self { path: r.path, type_: r.type_ })
    }
}
```

(`Regex` is already imported at the top of `config.rs`.)

- [ ] **Step 4: Replace `promote_metadata` field on TargetConfig**

Find:
```rust
/// Accepted in YAML but unused by the Rust MVP (no promoted-column writes).
#[serde(default, skip_serializing)]
#[allow(dead_code)]
pub promote_metadata: Option<serde_yml::Value>,
```

Replace with:
```rust
#[serde(default)]
pub promote_metadata: Vec<PromoteColumn>,
```

- [ ] **Step 5: Add the `mode: append` requires `source_tag` validator**

`TargetConfig` is `Deserialize`. Add a `try_from`-style post-validation by switching to a custom `Deserialize` (smaller change: keep auto-derive and add a `validate()` method called by `load_config`). The simpler path is the latter:

In `config.rs`, add to `impl TargetConfig`:

```rust
impl TargetConfig {
    fn validate(&self) -> Result<(), String> {
        if self.mode == "append" && self.source_tag.is_none() {
            return Err(
                "target.mode='append' requires target.source_tag to identify this cell".into(),
            );
        }
        Ok(())
    }
}
```

Then in `load_config(path)` (find the function — likely returns `Result<CellConfig>`), add right before returning:

```rust
cfg.target.validate().map_err(|e| anyhow::anyhow!(e))?;
```

(If `impl TargetConfig` doesn't already exist, create it. The `validate` is private — `pub(crate) fn validate(&self)` if cross-module use needed.)

- [ ] **Step 6: Build + unit test**

```bash
cd rust && cargo build --workspace
```

Add to `config.rs` `#[cfg(test)] mod tests {}`:

```rust
#[test]
fn rejects_append_without_source_tag() {
    let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { dsn_env: D, schema: s, table: t, mode: append, hnsw: false }
"#;
    let path = std::env::temp_dir().join("chunkshop-rs-cfg-test.yaml");
    std::fs::write(&path, yaml).unwrap();
    let err = crate::config::load_config(&path).unwrap_err().to_string();
    assert!(err.contains("source_tag"), "expected source_tag mention, got: {err}");
}

#[test]
fn rejects_invalid_promote_type() {
    let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  dsn_env: D
  schema: s
  table: t
  mode: overwrite
  hnsw: false
  promote_metadata:
    - { path: entities.ORG, type: bogus_type }
"#;
    let path = std::env::temp_dir().join("chunkshop-rs-cfg-test2.yaml");
    std::fs::write(&path, yaml).unwrap();
    let err = crate::config::load_config(&path).unwrap_err().to_string();
    assert!(err.contains("type"), "expected type complaint, got: {err}");
}

#[test]
fn promote_column_name_lowercases_and_double_underscores() {
    let pc: PromoteColumn = serde_yml::from_str(
        "{ path: entities.ORG, type: text[] }"
    ).unwrap();
    assert_eq!(pc.column_name(), "entities__org");
}
```

- [ ] **Step 7: Run unit tests**

```bash
cd rust && cargo test --lib
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add rust/chunkshop/Cargo.toml rust/chunkshop/src/config.rs
git commit -m "feat(rust): PromoteColumn config type + append-mode source_tag validator"
```

---

## Task 2: Implement sink mode + safety logic

**Files:** `rust/chunkshop/src/sink.rs`

This task replaces the existing dumb `overwrite_create` and the `append` error stub with full Python-parity logic. Ten changes; do them in one commit.

- [ ] **Step 1: Add helpers near the top of `sink.rs` (after `use` block)**

```rust
use blake2::{Blake2b, Digest, digest::consts::U8};

/// Deterministic 64-bit signed int key for `pg_advisory_xact_lock`. Mirrors
/// Python's `_advisory_lock_key`: BLAKE2b-8-byte digest of the schema name,
/// big-endian signed.
fn advisory_lock_key(schema_name: &str) -> i64 {
    let mut hasher = Blake2b::<U8>::new();
    hasher.update(schema_name.as_bytes());
    let digest = hasher.finalize();
    i64::from_be_bytes(digest.into())
}

/// Traverse a dotted path through a JSON value. Returns `None` if any segment
/// is missing or an intermediate is not an object. Mirrors Python's
/// `_jsonb_path_get`.
fn jsonb_path_get<'a>(meta: &'a serde_json::Value, path: &str) -> Option<&'a serde_json::Value> {
    let mut cur = meta;
    for seg in path.split('.') {
        let obj = cur.as_object()?;
        cur = obj.get(seg)?;
    }
    Some(cur)
}
```

- [ ] **Step 2: Wrap `create_table` in an advisory-lock transaction**

Replace the body of `create_table` with:

```rust
pub async fn create_table(&self) -> Result<()> {
    let mut tx = self.pool.begin().await.context("begin tx")?;
    let key = advisory_lock_key(&self.cfg.schema_name);
    sqlx::query("SELECT pg_advisory_xact_lock($1)")
        .bind(key)
        .execute(&mut *tx)
        .await
        .context("acquire schema advisory lock")?;

    sqlx::query("CREATE EXTENSION IF NOT EXISTS vector")
        .execute(&mut *tx)
        .await
        .context("CREATE EXTENSION vector")?;

    let schema_stmt = format!(r#"CREATE SCHEMA IF NOT EXISTS "{}""#, self.cfg.schema_name);
    sqlx::query(&schema_stmt)
        .execute(&mut *tx)
        .await
        .context("CREATE SCHEMA")?;

    match self.cfg.mode.as_str() {
        "overwrite" => self.overwrite_create_in_tx(&mut tx).await?,
        "create_if_missing" => self.create_if_missing_in_tx(&mut tx).await?,
        "append" => self.append_preflight_in_tx(&mut tx).await?,
        other => return Err(anyhow!("unknown target.mode: {other:?}")),
    }
    tx.commit().await.context("commit schema setup tx")?;
    Ok(())
}
```

- [ ] **Step 3: Convert table-existence + dim helpers to take a transaction**

Replace `table_exists` with `table_exists_in_tx<'a>(...)` taking `&mut sqlx::Transaction<'_, sqlx::Postgres>`. Same for the new helpers. Keep the original `table_exists()` if any non-tx caller exists; if not, remove it. Show the `_in_tx` version:

```rust
async fn table_exists_in_tx(
    &self,
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
) -> Result<bool> {
    let row = sqlx::query(
        "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=$1 AND tablename=$2)",
    )
    .bind(&self.cfg.schema_name)
    .bind(&self.cfg.table)
    .fetch_one(&mut **tx)
    .await?;
    Ok(row.get::<bool, _>(0))
}

async fn current_embed_dim_in_tx(
    &self,
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
) -> Result<Option<usize>> {
    let row = sqlx::query(
        r#"
        SELECT format_type(atttypid, atttypmod) AS t
        FROM pg_attribute
        WHERE attrelid = (
            SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = $1 AND n.nspname = $2
        ) AND attname = 'embedding'
        "#,
    )
    .bind(&self.cfg.table)
    .bind(&self.cfg.schema_name)
    .fetch_optional(&mut **tx)
    .await?;
    let Some(r) = row else { return Ok(None) };
    let s: String = r.get("t");
    let re = regex::Regex::new(r"^vector\((\d+)\)$").unwrap();
    Ok(re.captures(&s).and_then(|c| c.get(1)).and_then(|m| m.as_str().parse().ok()))
}
```

- [ ] **Step 4: Implement `overwrite_create_in_tx` with the source-tag conflict check**

```rust
async fn overwrite_create_in_tx(
    &self,
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
) -> Result<()> {
    if self.table_exists_in_tx(tx).await? && !self.cfg.force_overwrite {
        let stmt = format!(
            "SELECT DISTINCT source FROM {} WHERE source IS NOT NULL LIMIT 10",
            self.fq_table()
        );
        let rows = sqlx::query(&stmt).fetch_all(&mut **tx).await?;
        let existing: std::collections::BTreeSet<String> = rows
            .into_iter()
            .filter_map(|r| r.try_get::<String, _>("source").ok())
            .collect();
        let my_tag = self.cfg.source_tag.clone();
        let foreign: Vec<&String> = existing
            .iter()
            .filter(|t| my_tag.as_deref() != Some(t.as_str()))
            .collect();
        if !foreign.is_empty() {
            return Err(anyhow!(
                "overwrite refuses to drop {schema}.{table}: table holds rows with \
                 source_tag values {foreign:?} that differ from this cell's source_tag \
                 {my_tag:?}. Set target.force_overwrite: true in YAML to bypass.",
                schema = self.cfg.schema_name,
                table = self.cfg.table,
                foreign = foreign,
                my_tag = my_tag,
            ));
        }
    }
    if self.table_exists_in_tx(tx).await? {
        let drop_stmt = format!("DROP TABLE {}", self.fq_table());
        sqlx::query(&drop_stmt).execute(&mut **tx).await.context("DROP TABLE")?;
    }
    self.create_base_ddl_in_tx(tx).await
}
```

- [ ] **Step 5: Implement `create_if_missing_in_tx` and `append_preflight_in_tx`**

```rust
async fn create_if_missing_in_tx(
    &self,
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
) -> Result<()> {
    if !self.table_exists_in_tx(tx).await? {
        return self.create_base_ddl_in_tx(tx).await;
    }
    let alter = format!(
        "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source text",
        self.fq_table()
    );
    sqlx::query(&alter).execute(&mut **tx).await.context("ADD COLUMN source")?;
    self.ensure_promote_columns_in_tx(tx).await
}

async fn append_preflight_in_tx(
    &self,
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
) -> Result<()> {
    if !self.table_exists_in_tx(tx).await? {
        return Err(anyhow!(
            "append mode: table {}.{} does not exist. Use mode='create_if_missing' on the first cell.",
            self.cfg.schema_name, self.cfg.table
        ));
    }
    let current_dim = self.current_embed_dim_in_tx(tx).await?;
    let Some(d) = current_dim else {
        return Err(anyhow!(
            "append mode: table {}.{} has no 'embedding' vector column. Not a chunkshop \
             table — pick a different target or use mode='overwrite'.",
            self.cfg.schema_name, self.cfg.table
        ));
    };
    if d != self.embed_dim {
        return Err(anyhow!(
            "append mode: target embedding dim is {d}, cell embedder dim is {own}. \
             Vectors are not comparable. Use a different target or re-ingest into overwrite.",
            d = d, own = self.embed_dim,
        ));
    }
    let alter = format!(
        "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source text",
        self.fq_table()
    );
    sqlx::query(&alter).execute(&mut **tx).await.context("ADD COLUMN source")?;
    self.ensure_promote_columns_in_tx(tx).await
}
```

- [ ] **Step 6: Implement `ensure_promote_columns_in_tx`**

```rust
async fn ensure_promote_columns_in_tx(
    &self,
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
) -> Result<()> {
    for pc in &self.cfg.promote_metadata {
        // pc.type_ is allowlisted in PromoteColumn::validate_type — safe to interpolate.
        let stmt = format!(
            r#"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS "{col}" {ty}"#,
            tbl = self.fq_table(),
            col = pc.column_name(),
            ty = pc.type_,
        );
        sqlx::query(&stmt).execute(&mut **tx).await.context("ADD COLUMN promote")?;
    }
    Ok(())
}
```

- [ ] **Step 7: Convert `create_base_ddl` to `create_base_ddl_in_tx` and add the promote-column DDL call**

The existing `create_base_ddl` opens the same pool (no tx). Convert to `_in_tx`, change `execute(&self.pool)` to `execute(&mut **tx)`, and at the end (after the optional HNSW index) call `self.ensure_promote_columns_in_tx(tx).await`.

- [ ] **Step 8: Update `write_document` to write promote columns**

Replace the SQL build + `bind` block with a dynamic-column version. The base columns stay; promote columns are appended:

```rust
pub async fn write_document(
    &self,
    chunks: &[Chunk],
    embeddings: &[Vec<f32>],
) -> Result<()> {
    if chunks.len() != embeddings.len() {
        return Err(anyhow!(
            "chunks ({}) and embeddings ({}) length mismatch",
            chunks.len(), embeddings.len()
        ));
    }
    if chunks.is_empty() { return Ok(()); }

    let promote = &self.cfg.promote_metadata;
    let n_base = 9; // id, doc_id, seq_num, original_content, embedded_content, tags, metadata, embedding, source
    let n_promote = promote.len();

    // Column list and placeholders.
    let mut col_idents: Vec<String> = vec![
        "id".into(), "doc_id".into(), "seq_num".into(),
        "original_content".into(), "embedded_content".into(),
        "tags".into(), "metadata".into(), "embedding".into(), "source".into(),
    ];
    let mut placeholders: Vec<String> = (1..=n_base)
        .map(|i| match i {
            7 => format!("${i}::jsonb"),
            _ => format!("${i}"),
        })
        .collect();
    for (i, pc) in promote.iter().enumerate() {
        col_idents.push(format!(r#""{}""#, pc.column_name()));
        // Cast jsonb-text passes through ::<type>; we bind as text and let pg cast.
        placeholders.push(format!("${}::{}", n_base + 1 + i, pc.type_));
    }
    let cols = col_idents.iter().map(|c| {
        if c.starts_with('"') { c.clone() } else { format!(r#""{c}""#) }
    }).collect::<Vec<_>>().join(", ");
    let vals = placeholders.join(", ");

    // ON CONFLICT UPDATE: skip id, doc_id, seq_num, AND source. Include promote.
    let mut update_cols: Vec<String> = vec![
        "original_content".into(), "embedded_content".into(),
        "tags".into(), "metadata".into(), "embedding".into(),
    ];
    for pc in promote {
        update_cols.push(pc.column_name());
    }
    let updates = update_cols.iter()
        .map(|c| format!(r#""{c}" = EXCLUDED."{c}""#))
        .collect::<Vec<_>>()
        .join(", ");

    let insert_sql = format!(
        "INSERT INTO {tbl} ({cols}) VALUES ({vals}) ON CONFLICT (id) DO UPDATE SET {updates}",
        tbl = self.fq_table()
    );

    let mut tx = self.pool.begin().await?;
    let empty_tags: Vec<String> = Vec::new();
    for (c, emb) in chunks.iter().zip(embeddings.iter()) {
        let id = format!("{}::{}", c.doc_id, c.seq_num);
        let vec = pgvector::Vector::from(emb.clone());
        let meta_str = serde_json::to_string(&c.metadata)?;

        let mut q = sqlx::query(&insert_sql)
            .bind(id)
            .bind(&c.doc_id)
            .bind(c.seq_num as i32)
            .bind(&c.original_content)
            .bind(&c.embedded_content)
            .bind(&empty_tags)
            .bind(&meta_str)
            .bind(&vec)
            .bind(self.cfg.source_tag.as_deref());

        for pc in promote {
            // For all types, bind the raw JSON-stringified value as text and
            // let Postgres cast via the placeholder type. Missing path -> NULL.
            let v = jsonb_path_get(&c.metadata, &pc.path);
            let bind_val: Option<String> = v.map(|jv| match jv {
                serde_json::Value::String(s) => s.clone(),
                other => serde_json::to_string(other).unwrap_or_default(),
            });
            q = q.bind(bind_val);
        }

        q.execute(&mut *tx).await.context("INSERT chunk row")?;
    }
    tx.commit().await?;
    Ok(())
}
```

(Yes — for typed cols like `int`, the `Option<String>` bind with the `::int` placeholder cast will round-trip; Postgres's text → int cast is lossless. For `text[]` we serialize the JSON array and let pg parse; this works for arrays like `["a","b"]` because Postgres `'["a","b"]'::text[]` parses JSON-array literals. **Verify this in Step 9** — if pg refuses, switch the `text[]` path to bind `Vec<String>` directly.)

- [ ] **Step 9: Build + run existing tests**

```bash
cd rust && cargo build --workspace 2>&1 | tail -10
cd rust && cargo test --lib 2>&1 | tail -10
```

Expected: clean build, lib tests pass.

If the `text[]` cast complains about JSON-array literals, change Step 8's promote-bind logic: detect array values and bind as `Vec<String>` directly with a `::text[]` cast (no `Option<String>` wrapping for arrays — sqlx supports `Option<Vec<String>>`).

- [ ] **Step 10: Commit**

```bash
git add rust/chunkshop/src/sink.rs
git commit -m "feat(rust): sink full-mode parity — append + promote + force_overwrite + advisory lock"
```

---

## Task 3: ⛔ Drift Check DC-001 + DC-002

- [ ] **Step 1:** Re-read `skill-output/mission-brief/Mission-Brief-rust-sink-modes.md`.

- [ ] **Step 2:** Verify scope:
  - Added: PromoteColumn type, validators, append/force/advisory/promote sink logic. ✓
  - NOT added: any new chunker, framer, extractor, source. NOT changed: Python. NOT touched: embedder.

- [ ] **Step 3:** Eyeball one promote-column SQL from a `tracing::debug!` or by adding an `info!` log in `ensure_promote_columns_in_tx`. Confirm:
  - Identifier comes from `pc.column_name()`.
  - Type interpolated as a literal (allowlisted, safe).
  - Quoted with double-quotes only around the identifier — type text is bare.

- [ ] **Step 4:** Confirm `source` is NOT in the ON CONFLICT UPDATE column list (Step 8 of Task 2).

---

## Task 4: Cross-language append integration test

**Files:**
- Create: `rust/chunkshop/tests/sink_modes_parity.rs`
- (Optional) Create: a small fixture corpus or reuse `tests/parity-fixtures/handbook-intro.md`.

The test exercises:
1. Python cell A: `mode: overwrite`, `source_tag: py_a`, ingest 3 docs.
2. Rust cell B: `mode: append`, `source_tag: rs_b`, `promote_metadata: [{path: heading, type: text}]` (heading is set on every chunk by `sentence_aware`'s metadata? or by hierarchy's metadata.heading — the test config picks one). Ingest 3 more docs.
3. Post-conditions queried via SQLx:
   - `SELECT COUNT(*) ...` total rows = py rows + rs rows.
   - `SELECT COUNT(DISTINCT source) ...` = 2.
   - `SELECT COUNT(*) WHERE source = 'rs_b' AND heading IS NOT NULL` > 0 (promote column populated for Rust rows).
   - `SELECT COUNT(*) WHERE source = 'py_a' AND heading IS NOT NULL` = (py chunk count if Python's cell also had promote_metadata; otherwise = 0 — test asserts whichever is true given the Python cell config you pick).

- [ ] **Step 1: Decide the test's Python invocation**

The simplest path: shell out to `uv run --project python python -m chunkshop.cli ingest --config <yaml>` from the Rust test, like `scripts/parity_check.py` already does. Skip if `uv` or `python` isn't on PATH.

- [ ] **Step 2: Write the test**

```rust
//! Cross-language sink-mode parity:
//!   Python writes with mode=overwrite + source_tag=py_a,
//!   Rust appends with mode=append + source_tag=rs_b + promoted column,
//!   asserts both rows live, source filter works, promoted column populated.

use std::env;
use std::path::PathBuf;
use std::process::Command;

use chunkshop::{load_config, run_cell};

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

fn write_yaml(path: &std::path::Path, body: &str) {
    std::fs::write(path, body).unwrap();
}

#[tokio::test]
async fn cross_language_append_with_promote_column() {
    let dsn = match env::var("CHUNKSHOP_TEST_DSN") {
        Ok(v) => v,
        Err(_) => {
            eprintln!("CHUNKSHOP_TEST_DSN not set; skipping append-parity test");
            return;
        }
    };
    env::set_var("CHUNKSHOP_TEST_DSN", &dsn);

    // Cleanup any leftover schema from a previous run.
    use sqlx::Row;
    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(1)
        .connect(&dsn)
        .await
        .expect("connect");
    let _ = sqlx::query("DROP SCHEMA IF EXISTS chunkshop_rust_sink_parity CASCADE")
        .execute(&pool)
        .await;

    let glob = format!("{}/handbook-intro.md", fixtures_dir().display());

    // 1. Python cell A: overwrite + source_tag=py_a, hierarchy chunker so
    //    `heading` is in chunk metadata (matches what we'll promote in Rust).
    let py_yaml = format!(r#"cell_name: cross_lang_py
source: {{ type: files, glob: "{glob}", id_from: stem, encoding: utf-8 }}
chunker: {{ type: hierarchy, prefix_heading: true, max_chars: 800 }}
embedder: {{ type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768, batch_size: 1, threads: 1 }}
target:
  dsn_env: CHUNKSHOP_TEST_DSN
  schema: chunkshop_rust_sink_parity
  table: rows
  mode: overwrite
  source_tag: py_a
  hnsw: false
  promote_metadata:
    - {{ path: heading, type: text }}
"#);
    let py_path = std::env::temp_dir().join("chunkshop-rs-cross-py.yaml");
    write_yaml(&py_path, &py_yaml);

    let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors().nth(2).unwrap().to_path_buf();
    let py_status = Command::new("uv")
        .args(["run", "--project", "python", "python", "-m", "chunkshop.cli",
               "ingest", "--config", py_path.to_str().unwrap()])
        .current_dir(&repo_root)
        .status()
        .expect("spawn uv run");
    assert!(py_status.success(), "Python cell A failed");

    // 2. Rust cell B: append + source_tag=rs_b. Ingest the SAME doc; collisions
    //    are fine — the (doc_id, seq_num) PK will upsert and write-once `source`
    //    semantics keep py_a as the original tag for any colliding rows. To
    //    make the test deterministic we point at a *different* file id_from to
    //    produce different doc_ids.
    let glob_rs = format!("{}/hierarchy_corpus.txt", fixtures_dir().display());
    let rs_yaml = format!(r#"cell_name: cross_lang_rs
source: {{ type: files, glob: "{glob_rs}", id_from: sha1, encoding: utf-8 }}
chunker: {{ type: hierarchy, prefix_heading: true, max_chars: 800 }}
embedder: {{ type: fastembed, model_name: Xenova/bge-base-en-v1.5-int8, dim: 768, batch_size: 1, threads: 1 }}
target:
  dsn_env: CHUNKSHOP_TEST_DSN
  schema: chunkshop_rust_sink_parity
  table: rows
  mode: append
  source_tag: rs_b
  hnsw: false
  promote_metadata:
    - {{ path: heading, type: text }}
"#);
    let rs_path = std::env::temp_dir().join("chunkshop-rs-cross-rs.yaml");
    write_yaml(&rs_path, &rs_yaml);
    let cfg = load_config(&rs_path).expect("load rust cfg");
    let result = run_cell(cfg).await.expect("rust ingest");
    assert!(result.chunks_written > 0);

    // 3. Post-conditions.
    let total: i64 = sqlx::query("SELECT COUNT(*) FROM chunkshop_rust_sink_parity.rows")
        .fetch_one(&pool).await.unwrap().get(0);
    assert!(total >= 2, "expected at least 2 rows total, got {total}");

    let distinct_sources: i64 = sqlx::query(
        "SELECT COUNT(DISTINCT source) FROM chunkshop_rust_sink_parity.rows WHERE source IS NOT NULL"
    ).fetch_one(&pool).await.unwrap().get(0);
    assert_eq!(distinct_sources, 2, "expected source_tags py_a + rs_b");

    let rs_rows: i64 = sqlx::query(
        "SELECT COUNT(*) FROM chunkshop_rust_sink_parity.rows WHERE source = 'rs_b'"
    ).fetch_one(&pool).await.unwrap().get(0);
    assert_eq!(rs_rows as usize, result.chunks_written, "rs_b rows match Rust write count");

    let rs_with_heading: i64 = sqlx::query(
        "SELECT COUNT(*) FROM chunkshop_rust_sink_parity.rows \
         WHERE source = 'rs_b' AND heading IS NOT NULL"
    ).fetch_one(&pool).await.unwrap().get(0);
    assert!(rs_with_heading > 0, "expected promote_metadata to populate heading for rs_b rows");

    // Cleanup.
    let _ = sqlx::query("DROP SCHEMA IF EXISTS chunkshop_rust_sink_parity CASCADE")
        .execute(&pool).await;
}
```

- [ ] **Step 3: Run with DSN set**

```bash
cd /home/yonk/yonk-tools/chunkshop-rust-sink-modes
export CHUNKSHOP_TEST_DSN="postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg"
cd rust && cargo test --test sink_modes_parity -- --nocapture
```

Expected: PASS. If the Python cell fails to find `chunkshop.cli`, ensure `python/` is sync'd (`cd python && uv sync --frozen --extra dev --extra extractors --extra nlp`).

- [ ] **Step 4: Commit**

```bash
git add rust/chunkshop/tests/sink_modes_parity.rs
git commit -m "test(rust): cross-language append + promote_metadata integration test"
```

---

## Task 5: README + CHANGELOG

**Files:** `rust/README.md`, `CHANGELOG.md`

- [ ] **Step 1:** In `rust/README.md`, "What works" → expand the `target` row:

```markdown
| target    | pgvector table; modes `overwrite` / `append` / `create_if_missing`; `force_overwrite`; `source_tag` write-once on `ON CONFLICT`; `promote_metadata` jsonb-to-typed-column writes; HNSW index optional; concurrent-cell safe via schema-name advisory lock |
```

Remove the lines from "What does NOT work" that say:
- `Target mode: append — returns a runtime error pointing at the Python impl.`
- `Promoted columns (promote_metadata) — parsed but not written.`

Remove the matching rows from the roadmap table.

- [ ] **Step 2:** CHANGELOG entry under `## Unreleased / ### Changed`:

```markdown
- **`chunkshop-rs` sink reaches full-mode parity with Python.** Adds
  `mode: append` (with table-existence + dim-match + ALTER preflight),
  `force_overwrite` flag, the source-tag-conflict safety check on
  `mode: overwrite`, the BLAKE2b-keyed `pg_advisory_xact_lock` that
  serializes concurrent-cell schema setup, and `promote_metadata`
  jsonb-to-typed-column writes (allowlisted types, identifier-safe
  paths). `source` stays write-once on `ON CONFLICT` — provenance
  is preserved across cells. Cross-language verified by
  `rust/chunkshop/tests/sink_modes_parity.rs`: a Python `overwrite`
  cell + a Rust `append` cell both write into one table, both rows
  are queryable by `WHERE source = ...`, and the promoted column
  holds the right typed values for the Rust rows.
```

- [ ] **Step 3:** Commit.

```bash
git add rust/README.md CHANGELOG.md
git commit -m "docs(rust): sink full-mode parity — README + CHANGELOG"
```

---

## Task 6: ⛔ DC-FINAL — verify all SC met

- [ ] **Step 1:** Re-read the brief.

- [ ] **Step 2:** Walk every SC and write evidence:

```
SC-001 (PromoteColumn struct + validation) — Evidence: ____________________
SC-002 (append requires source_tag) — Evidence: cargo test config::tests::rejects_append_without_source_tag output ____________________
SC-003 (overwrite refuses on tag conflict) — Evidence: ____________________
SC-004 (append preflight) — Evidence: ____________________
SC-005 (write_document writes promote columns) — Evidence: cargo test --test sink_modes_parity output ____________________
SC-006 (advisory lock) — Evidence: code reference + behavior sanity check ____________________
SC-007 (cross-language append test) — Evidence: cargo test --test sink_modes_parity ____________________
SC-008 (no regressions) — Evidence: cargo test --workspace + pytest -q outputs ____________________
SC-009 (README + CHANGELOG) — Evidence: ____________________
```

- [ ] **Step 3:** Final tree state + handoff to `superpowers:finishing-a-development-branch`.

---

## Self-review notes

- **Spec coverage:** every SC is mapped. SC-001/002 → Task 1; SC-003/004/005/006 → Task 2; SC-007 → Task 4; SC-008 → Tasks 2+4 regression; SC-009 → Task 5. DC-001/002 → Task 3; DC-FINAL → Task 6.
- **No placeholders.**
- **Type consistency:** `PromoteColumn`, `TargetConfig`, `PgVectorSink`, `Chunk`, `jsonb_path_get`, `advisory_lock_key` all referenced consistently.
- **Verification before claiming done:** Task 4's E2E test is the load-bearing proof for the whole brief. If it doesn't pass, none of this ships.
