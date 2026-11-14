//! delete_orphans must delete vec rows by exact id, not LIKE: `_` is a LIKE
//! wildcard and LIKE is case-insensitive, so shrinking `release_notes` used to
//! delete the vectors of `release-notes` and `RELEASE_NOTES`.

use chunkshop::backends::SQLiteBackend;
use chunkshop::chunker::Chunk;
use chunkshop::config::SqliteTargetConfig;
use chunkshop::sinks::Sink;
use chunkshop::sinks::SqliteSink;
use serde_json::json;
use tempfile::tempdir;

fn chunks(doc_id: &str, n: usize) -> Vec<Chunk> {
    (0..n)
        .map(|i| Chunk {
            doc_id: doc_id.into(),
            seq_num: i,
            original_content: format!("c{i}"),
            embedded_content: format!("c{i}"),
            metadata: json!({}),
        })
        .collect()
}

#[tokio::test]
async fn shrinking_a_doc_leaves_lookalike_doc_ids_intact() {
    let dir = tempdir().unwrap();
    let env = format!("CHUNKSHOP_ORPH_EXACT_{}", std::process::id());
    std::env::set_var(&env, dir.path().join("o.db").to_str().unwrap());
    let cfg = SqliteTargetConfig {
        dsn_env: env.clone(),
        database_name: "ignored".into(),
        table: "chunks".into(),
        overwrite: false,
        hnsw: false,
        mode: "overwrite".into(),
        source_tag: Some("t".into()),
        promote_metadata: vec![],
        force_overwrite: false,
        delete_orphans: true,
        documents: None,
    };
    let sink = SqliteSink::new(cfg, SQLiteBackend::new(env.clone()), 4);
    sink.create_table().await.unwrap();

    let ids = ["release_notes", "release-notes", "Release_Notes", "releaseXnotes"];
    for id in ids {
        sink.write_document(id, &chunks(id, 4), &vec![vec![0.5; 4]; 4], &vec![vec![]; 4])
            .await
            .unwrap();
    }
    // Shrink only `release_notes` from 4 chunks to 2.
    sink.write_document(
        "release_notes",
        &chunks("release_notes", 2),
        &vec![vec![0.5; 4]; 2],
        &vec![vec![]; 2],
    )
    .await
    .unwrap();

    let conn = SQLiteBackend::new(env).connect().await.unwrap();
    let g = conn.lock().await;
    let count = |sql: &str, id: &str| -> i64 {
        g.query_row(sql, [id], |r: &rusqlite::Row| r.get(0)).unwrap()
    };
    for id in ids {
        let want = if id == "release_notes" { 2 } else { 4 };
        assert_eq!(
            count("SELECT COUNT(*) FROM chunks WHERE doc_id = ?", id),
            want,
            "main rows for {id}"
        );
        assert_eq!(
            count(
                "SELECT COUNT(*) FROM chunks_vec WHERE substr(id, 1, length(?1) + 2) = ?1 || '::'",
                id,
            ),
            want,
            "vec rows for {id}"
        );
    }
}
