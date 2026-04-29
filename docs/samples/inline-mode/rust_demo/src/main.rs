//! End-to-end demo of chunkshop's inline (library) mode for Rust.
//!
//! Run from the repo root:
//!
//! ```bash
//! export VECTORS_DB_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg
//! cd docs/samples/inline-mode/rust_demo
//! cargo run
//! ```
//!
//! Mirrors `python_demo.py`. Builds a `chunkshop::Pipeline`, ingests three
//! sales notes, grows one, shrinks it (verifying `delete_orphans`), then
//! deletes another with `delete_document`. Prints the per-doc chunk count
//! between each step so the upsert / shrink-cleanup / delete behavior is
//! visible.

use anyhow::Result;
use serde_json::json;
use sqlx::{postgres::PgPoolOptions, Row};

const NOTE_001_V1: &str = "\
Customer: Acme Corp.

Met with Acme this morning. They're evaluating us against three competitors.
Main concerns: latency, support SLA, and onboarding time. We promised a
follow-up technical deep-dive next Tuesday.";

const NOTE_001_V2_LONGER: &str = "\
Customer: Acme Corp.

Met with Acme this morning. They're evaluating us against three competitors.
Main concerns: latency, support SLA, and onboarding time. We promised a
follow-up technical deep-dive next Tuesday.

UPDATE: Got a call back from their CTO. They want to see benchmark numbers
against their current vendor before we meet. Sending the bake-off doc and
the latency-percentile dashboard ahead of the meeting. Engineering on standby.";

const NOTE_001_V3_SHORTER: &str = "Acme deal closed. Moving to onboarding.";

const NOTE_002: &str = "\
Customer: Northwind Traders.

Northwind renewed for three years at the enterprise tier. Champion is
Sarah Chen on their data team. Risk: their procurement team flagged a
data-residency clause we'll need to negotiate at next renewal.";

const NOTE_003: &str = "\
Customer: Globex Industries.

Discovery call. They have a 50-engineer team, currently on a competitor.
Pain point: prep latency before a query is too high. Demo scheduled for
Thursday.";

async fn show_count(label: &str, dsn: &str) -> Result<()> {
    let pool = PgPoolOptions::new().max_connections(1).connect(dsn).await?;
    let rows = sqlx::query(
        "SELECT doc_id, COUNT(*)::int AS n FROM rag.inline_chunks \
         GROUP BY doc_id ORDER BY doc_id",
    )
    .fetch_all(&pool)
    .await?;
    println!("  [{label}]");
    if rows.is_empty() {
        println!("    (no rows)");
    }
    for r in rows {
        let doc_id: String = r.get(0);
        let n: i32 = r.get(1);
        println!("    {doc_id}: {n} chunk(s)");
    }
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    let dsn = std::env::var("VECTORS_DB_DSN").unwrap_or_else(|_| {
        eprintln!("VECTORS_DB_DSN not set. Example:");
        eprintln!("  export VECTORS_DB_DSN=postgresql://postgres:postgres@localhost:5434/age_bakeoff_pgrg");
        std::process::exit(1);
    });
    // Same yaml the Python demo uses — sample-inline.yaml lives one level up.
    let yaml = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("sample-inline.yaml");
    println!("Loading {}", yaml.display());
    let mut shop = chunkshop::Pipeline::from_yaml(&yaml).await?;

    println!("\nStep 1: ingest 3 sales notes");
    let n1 = shop
        .ingest_text("note-001", NOTE_001_V1, json!({"author": "rep_a", "account": "acme"}))
        .await?;
    let n2 = shop
        .ingest_text("note-002", NOTE_002, json!({"author": "rep_b", "account": "northwind"}))
        .await?;
    let n3 = shop
        .ingest_text("note-003", NOTE_003, json!({"author": "rep_a", "account": "globex"}))
        .await?;
    println!("  wrote {n1} + {n2} + {n3} chunks");
    show_count("after initial ingest", &dsn).await?;

    println!("\nStep 2: re-ingest note-001 with a LONGER body (upsert + new chunks)");
    let n1b = shop
        .ingest_text(
            "note-001",
            NOTE_001_V2_LONGER,
            json!({"author": "rep_a", "account": "acme", "revision": 2}),
        )
        .await?;
    println!("  wrote {n1b} chunks for note-001");
    show_count("after grow", &dsn).await?;

    println!("\nStep 3: re-ingest note-001 with a SHORTER body (delete_orphans drops excess)");
    let n1c = shop
        .ingest_text(
            "note-001",
            NOTE_001_V3_SHORTER,
            json!({"author": "rep_a", "account": "acme", "revision": 3}),
        )
        .await?;
    println!("  wrote {n1c} chunks for note-001");
    show_count("after shrink", &dsn).await?;

    println!("\nStep 4: delete note-002 explicitly");
    let deleted = shop.delete_document("note-002").await?;
    println!("  deleted {deleted} row(s)");
    show_count("after delete", &dsn).await?;

    println!("\nStep 5: inspect a sample row");
    if let Some((seq, preview)) = shop.sample_row("note-001").await? {
        println!("  doc_id=note-001 seq={seq}");
        println!("  preview: {preview}");
    }

    println!(
        "\nDone. Try `psql -d $VECTORS_DB_DSN -c \
         'SELECT doc_id, seq_num, account FROM rag.inline_chunks ORDER BY doc_id, seq_num'`"
    );
    Ok(())
}
