//! RM-B Task 1: SyncMode + IncrementalSource + PrunableSource + StaleCursorError.
//!
//! Mirrors `python/tests/chunkshop/test_sources_base.py` and the SP-1 contract
//! in `python/src/chunkshop/sources/base.py`. Pure-type tests — no DB, no network,
//! no tokio runtime needed.

use anyhow::Result;
use chunkshop::sources::base::{
    Document, IncrementalSource, PrunableSource, StaleCursorError, SyncMode,
};
use std::future::Future;

#[test]
fn sync_mode_serde_round_trip_kebab() {
    // Python ships SyncMode as a str-Enum with values "full_resync" / "cursor"
    // / "fingerprint". Rust must emit and accept the same wire strings so a
    // YAML written by either side round-trips.
    let cases = [
        (SyncMode::FullResync, "\"full_resync\""),
        (SyncMode::Cursor, "\"cursor\""),
        (SyncMode::Fingerprint, "\"fingerprint\""),
    ];
    for (mode, wire) in cases {
        let s = serde_json::to_string(&mode).unwrap();
        assert_eq!(s, wire, "serialize {mode:?}");
        let back: SyncMode = serde_json::from_str(wire).unwrap();
        assert_eq!(back, mode, "deserialize {wire}");
    }
}

#[test]
fn sync_mode_default_is_full_resync() {
    // Existing Rust sources auto-inherit FullResync when no impl is provided.
    assert_eq!(SyncMode::default(), SyncMode::FullResync);
}

#[test]
fn document_carries_fingerprint() {
    let d = Document {
        id: "d1".into(),
        content: "hello".into(),
        title: Some("Hi".into()),
        metadata: serde_json::Value::Null,
        fingerprint: Some("sha256:abc".into()),
    };
    assert_eq!(d.fingerprint.as_deref(), Some("sha256:abc"));
    let d2 = Document {
        id: "d2".into(),
        content: "world".into(),
        title: None,
        metadata: serde_json::Value::Null,
        fingerprint: None,
    };
    assert!(d2.fingerprint.is_none());
}

#[test]
fn stale_cursor_error_displays_cleanly() {
    let e = StaleCursorError::new("server-side cursor expired");
    let msg = format!("{e}");
    assert!(
        msg.contains("stale cursor"),
        "expected display to contain 'stale cursor', got: {msg}"
    );
    assert!(msg.contains("server-side cursor expired"));
}

#[test]
fn stale_cursor_error_downcasts_from_anyhow() {
    // Consumers detect stale cursors by downcasting an `anyhow::Error`. This is
    // how the consumer falls back to full resync.
    let err: anyhow::Error = StaleCursorError::new("nope").into();
    assert!(err.downcast_ref::<StaleCursorError>().is_some());
}

// ----- Hand-rolled IncrementalSource impl, smoke-test only -----

#[derive(Default)]
struct FakeIncSource;

impl IncrementalSource for FakeIncSource {
    type Cursor = std::collections::BTreeMap<String, String>;

    fn empty_cursor(&self) -> Self::Cursor {
        Self::Cursor::new()
    }

    fn iter_changes_since(
        &self,
        _cursor: &Self::Cursor,
    ) -> impl Future<Output = Result<Vec<Document>>> + Send {
        async { Ok(Vec::new()) }
    }

    fn cursor_from(&self, _last: &Document) -> Self::Cursor {
        Self::Cursor::new()
    }
}

#[test]
fn fake_incremental_source_compiles_and_runs() {
    let src = FakeIncSource;
    let cursor = src.empty_cursor();
    assert!(cursor.is_empty());

    // Drive the future on a minimal block_on — no tokio runtime needed.
    let docs = futures::executor::block_on(src.iter_changes_since(&cursor)).unwrap();
    assert!(docs.is_empty());
}

// ----- Hand-rolled PrunableSource impl, smoke-test only -----

struct FakePrunable;

impl PrunableSource for FakePrunable {
    type Cursor = std::collections::BTreeMap<String, String>;

    fn empty_prune_cursor(&self) -> Self::Cursor {
        Self::Cursor::new()
    }

    fn iter_deleted_since(
        &self,
        _cursor: &Self::Cursor,
    ) -> impl Future<Output = Result<Vec<String>>> + Send {
        async { Ok(vec!["doc-1".to_string()]) }
    }
}

#[test]
fn fake_prunable_source_compiles_and_runs() {
    let src = FakePrunable;
    let deleted =
        futures::executor::block_on(src.iter_deleted_since(&src.empty_prune_cursor())).unwrap();
    assert_eq!(deleted, vec!["doc-1".to_string()]);
}
