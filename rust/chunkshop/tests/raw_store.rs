//! RM-B Task 5: RawStore trait + LocalRawStore + S3RawStore.
//!
//! Mirrors `python/tests/chunkshop/test_raw_store_{protocol,local,factory,s3}.py`.

use std::sync::Arc;

use chunkshop::raw_store::{
    load_raw_store, AnyRawStore, LocalRawStore, LocalRawStoreConfig, RawStore, RawStoreConfig,
};
use serde_json::json;
use tempfile::TempDir;

// ----- Local backend ------------------------------------------------------

#[tokio::test]
async fn local_put_get_roundtrip() {
    let tmp = TempDir::new().unwrap();
    let store = LocalRawStore::new(tmp.path()).unwrap();
    let ref_ = store
        .put(
            "doc::1",
            b"hello",
            "text/plain",
            Some(&json!({"fingerprint": "fp1"})),
        )
        .await
        .unwrap();
    let bytes = store.get(&ref_).await.unwrap();
    assert_eq!(bytes, b"hello");
}

#[tokio::test]
async fn local_exists_with_and_without_fingerprint() {
    let tmp = TempDir::new().unwrap();
    let store = LocalRawStore::new(tmp.path()).unwrap();
    store
        .put(
            "doc::1",
            b"hello",
            "text/plain",
            Some(&json!({"fingerprint": "fp1"})),
        )
        .await
        .unwrap();
    assert!(store.exists("doc::1", None).await.unwrap());
    assert!(store.exists("doc::1", Some("fp1")).await.unwrap());
    assert!(!store.exists("doc::1", Some("other")).await.unwrap());
    assert!(!store.exists("missing", None).await.unwrap());
}

#[tokio::test]
async fn local_delete_removes_blob() {
    let tmp = TempDir::new().unwrap();
    let store = LocalRawStore::new(tmp.path()).unwrap();
    store.put("doc::1", b"x", "text/plain", None).await.unwrap();
    store.delete("doc::1").await.unwrap();
    assert!(!store.exists("doc::1", None).await.unwrap());
}

#[tokio::test]
async fn local_doc_id_with_path_separators_is_safe() {
    // The Python parity test: ids like "s3://bucket/key/../../etc" must not
    // escape the root. SHA-256 hashing of the doc_id collapses any path
    // separators into a single hex string.
    let tmp = TempDir::new().unwrap();
    let store = LocalRawStore::new(tmp.path()).unwrap();
    let evil_id = "s3://b/k/../../etc";
    let ref_ = store.put(evil_id, b"x", "text/plain", None).await.unwrap();
    let bytes = store.get(&ref_).await.unwrap();
    assert_eq!(bytes, b"x");
    assert!(store.exists(evil_id, None).await.unwrap());
    // The actual on-disk path is under tmp.path() — no traversal escape.
    let abs_ref = std::path::Path::new(&ref_);
    assert!(
        abs_ref.starts_with(tmp.path()),
        "ref {ref_} escaped root {tmp:?}"
    );
}

#[tokio::test]
async fn local_layout_matches_python_sha256() {
    // Concrete byte-identical-with-Python check: the directory name is
    // hex(sha256(doc_id)) — same exact path Python writes to.
    let tmp = TempDir::new().unwrap();
    let store = LocalRawStore::new(tmp.path()).unwrap();
    store
        .put("doc::abc", b"data", "text/plain", None)
        .await
        .unwrap();
    // sha256("doc::abc") = e7b00e7c... (verified separately)
    let expected_hash = {
        use sha2::{Digest, Sha256};
        let mut h = Sha256::new();
        h.update(b"doc::abc");
        format!("{:x}", h.finalize())
    };
    let dir = tmp.path().join(&expected_hash);
    assert!(dir.join("blob").is_file(), "blob at {dir:?}");
    assert!(dir.join("meta.json").is_file(), "meta.json at {dir:?}");
    // meta.json contains the original doc_id.
    let meta: serde_json::Value =
        serde_json::from_slice(&std::fs::read(dir.join("meta.json")).unwrap()).unwrap();
    assert_eq!(meta["doc_id"], "doc::abc");
    assert_eq!(meta["content_type"], "text/plain");
}

// ----- Factory ------------------------------------------------------------

#[tokio::test]
async fn factory_constructs_local_from_config() {
    let tmp = TempDir::new().unwrap();
    let cfg = RawStoreConfig::Local(LocalRawStoreConfig {
        root: tmp.path().to_string_lossy().into_owned(),
    });
    let store = load_raw_store(&cfg).unwrap();
    match &store {
        AnyRawStore::Local(_) => {}
        #[cfg(feature = "source")]
        _ => panic!("expected local variant"),
    }
    let ref_ = store.put("d1", b"abc", "text/plain", None).await.unwrap();
    assert_eq!(store.get(&ref_).await.unwrap(), b"abc");
}

#[tokio::test]
async fn factory_yaml_round_trip_local() {
    let yaml = "type: local\nroot: /tmp/chunkshop_raw_store_test_xyz";
    let cfg: RawStoreConfig = serde_yaml_ng::from_str(yaml).unwrap();
    match cfg {
        RawStoreConfig::Local(c) => assert!(c.root.starts_with("/tmp/")),
        #[cfg(feature = "source")]
        _ => panic!("expected local variant"),
    }
}

// ----- S3 backend (gated on source feature) ------------------------------

#[cfg(feature = "source")]
mod s3_tests {
    use super::*;
    use chunkshop::raw_store::{S3RawStore, S3RawStoreConfig};
    use object_store::memory::InMemory;

    fn cfg() -> S3RawStoreConfig {
        S3RawStoreConfig {
            bucket: "test".into(),
            prefix: "raw/".into(),
            endpoint_url: None,
        }
    }

    #[tokio::test]
    async fn s3_put_get_roundtrip() {
        let store_impl: Arc<dyn object_store::ObjectStore> = Arc::new(InMemory::new());
        let store = S3RawStore::with_store(cfg(), store_impl);
        let ref_ = store
            .put(
                "doc::1",
                b"hello-s3",
                "text/plain",
                Some(&json!({"fingerprint": "fp1"})),
            )
            .await
            .unwrap();
        assert!(ref_.starts_with("s3://test/raw/"));
        assert_eq!(store.get(&ref_).await.unwrap(), b"hello-s3");
    }

    #[tokio::test]
    async fn s3_exists_with_fingerprint() {
        let store_impl: Arc<dyn object_store::ObjectStore> = Arc::new(InMemory::new());
        let store = S3RawStore::with_store(cfg(), store_impl);
        store
            .put(
                "doc::1",
                b"hello",
                "text/plain",
                Some(&json!({"fingerprint": "fp1"})),
            )
            .await
            .unwrap();
        assert!(store.exists("doc::1", None).await.unwrap());
        assert!(store.exists("doc::1", Some("fp1")).await.unwrap());
        assert!(!store.exists("doc::1", Some("other")).await.unwrap());
        assert!(!store.exists("missing", None).await.unwrap());
    }

    #[tokio::test]
    async fn s3_delete_removes_object() {
        let store_impl: Arc<dyn object_store::ObjectStore> = Arc::new(InMemory::new());
        let store = S3RawStore::with_store(cfg(), store_impl);
        store.put("doc::1", b"x", "text/plain", None).await.unwrap();
        store.delete("doc::1").await.unwrap();
        assert!(!store.exists("doc::1", None).await.unwrap());
    }

    #[tokio::test]
    async fn s3_key_uses_sha256_under_prefix() {
        let store_impl: Arc<dyn object_store::ObjectStore> = Arc::new(InMemory::new());
        let store = S3RawStore::with_store(cfg(), store_impl);
        let ref_ = store
            .put("doc::abc", b"x", "text/plain", None)
            .await
            .unwrap();
        let expected_hash = {
            use sha2::{Digest, Sha256};
            let mut h = Sha256::new();
            h.update(b"doc::abc");
            format!("{:x}", h.finalize())
        };
        assert_eq!(ref_, format!("s3://test/raw/{expected_hash}"));
    }
}
