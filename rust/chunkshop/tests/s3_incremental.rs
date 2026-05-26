//! RM-B Task 3: ETag-keyed IncrementalSource impl for S3Source.
//!
//! Mirrors `python/tests/chunkshop/test_s3_incremental.py`. Uses
//! `object_store::memory::InMemory` injected via `S3Source::with_store` so
//! no network or AWS credentials are required.

use std::collections::BTreeMap;
use std::sync::Arc;

use chunkshop::config::S3SourceConfig;
use chunkshop::sources::base::IncrementalSource;
use chunkshop::sources::S3Source;
use object_store::memory::InMemory;
use object_store::path::Path as ObjPath;
use object_store::ObjectStore;

fn cfg() -> S3SourceConfig {
    S3SourceConfig {
        bucket: "test-bucket".to_string(),
        prefix: String::new(),
        endpoint_url: None,
    }
}

async fn put(store: &Arc<dyn ObjectStore>, key: &str, body: &str) {
    store
        .put(&ObjPath::from(key), body.as_bytes().to_vec().into())
        .await
        .unwrap();
}

/// Merge per-doc cursor deltas into the running cursor — the canonical
/// consumer pattern from `IncrementalSource` trait docs.
fn merge_cursor(
    src: &S3Source,
    prev: BTreeMap<String, String>,
    docs: &[chunkshop::sources::base::Document],
) -> BTreeMap<String, String> {
    let mut next = prev;
    for d in docs {
        next.extend(src.cursor_from(d));
    }
    next
}

#[tokio::test]
async fn s3_empty_cursor_emits_all_objects_with_etag_fingerprint() {
    let store: Arc<dyn ObjectStore> = Arc::new(InMemory::new());
    put(&store, "k1", "one").await;
    put(&store, "k2", "two").await;

    let src = S3Source::with_store(cfg(), store);
    let docs = src.iter_changes_since(&src.empty_cursor()).await.unwrap();
    let ids: Vec<String> = docs.iter().map(|d| d.id.clone()).collect();
    let mut sorted = ids.clone();
    sorted.sort();
    assert_eq!(
        sorted,
        vec![
            "s3://test-bucket/k1".to_string(),
            "s3://test-bucket/k2".to_string()
        ]
    );
    // Every emitted doc carries an ETag fingerprint.
    for d in &docs {
        assert!(
            d.fingerprint.is_some() && !d.fingerprint.as_deref().unwrap_or("").is_empty(),
            "doc {:?} missing fingerprint",
            d.id
        );
    }
}

#[tokio::test]
async fn s3_cursor_skips_unchanged_etags_and_emits_only_changed_keys() {
    let store: Arc<dyn ObjectStore> = Arc::new(InMemory::new());
    put(&store, "k1", "one").await;
    put(&store, "k2", "two").await;

    let src = S3Source::with_store(cfg(), store.clone());

    // First sync: emit both, build cursor.
    let cursor0 = src.empty_cursor();
    let first = src.iter_changes_since(&cursor0).await.unwrap();
    assert_eq!(first.len(), 2);
    let cursor1 = merge_cursor(&src, cursor0, &first);
    assert_eq!(cursor1.len(), 2, "cursor must accumulate full manifest");
    assert!(cursor1.contains_key("k1") && cursor1.contains_key("k2"));

    // Second sync, nothing changed → no emit.
    let unchanged = src.iter_changes_since(&cursor1).await.unwrap();
    assert!(
        unchanged.is_empty(),
        "unchanged sync should emit nothing, got {} docs",
        unchanged.len()
    );

    // Overwrite k2 with new content → new ETag → only k2 re-emitted.
    put(&store, "k2", "two-updated!").await;
    let changed = src.iter_changes_since(&cursor1).await.unwrap();
    let ids: Vec<&str> = changed.iter().map(|d| d.id.as_str()).collect();
    assert_eq!(
        ids,
        vec!["s3://test-bucket/k2"],
        "only k2's content changed; got {ids:?}"
    );

    // Merging the new delta into the running cursor preserves k1's etag.
    let cursor2 = merge_cursor(&src, cursor1.clone(), &changed);
    assert_eq!(cursor2.len(), 2);
    assert_eq!(
        cursor2.get("k1"),
        cursor1.get("k1"),
        "k1's etag must be preserved across merges"
    );
    assert_ne!(
        cursor2.get("k2"),
        cursor1.get("k2"),
        "k2's etag must reflect the new content"
    );
}

#[tokio::test]
async fn s3_cursor_serde_round_trips_as_json_object() {
    // The cursor is `BTreeMap<String, String>`, which serializes to a flat
    // JSON object — same wire shape as Python's dict cursor.
    let store: Arc<dyn ObjectStore> = Arc::new(InMemory::new());
    put(&store, "k1", "one").await;
    let src = S3Source::with_store(cfg(), store);

    let docs = src.iter_changes_since(&src.empty_cursor()).await.unwrap();
    let cursor = merge_cursor(&src, src.empty_cursor(), &docs);

    let json = serde_json::to_string(&cursor).unwrap();
    assert!(
        json.starts_with('{') && json.contains("k1"),
        "cursor should serialize as JSON object: {json}"
    );
    let back: BTreeMap<String, String> = serde_json::from_str(&json).unwrap();
    assert_eq!(cursor, back);
}
