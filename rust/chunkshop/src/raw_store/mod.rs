//! RawStore: pluggable storage for raw source artifacts.
//!
//! Mirrors `python/src/chunkshop/raw_store/` (SP-1). When a `raw_store:`
//! block is configured, connector/upload paths stage the original bytes
//! here so re-processing doesn't require re-fetching, deltas can short-
//! circuit via `exists()`, and the original can be served / audited.
//!
//! Backends:
//! - `LocalRawStore` — filesystem-backed, zero new deps. Layout matches
//!   Python's exactly so a store written by either implementation is
//!   readable by the other.
//! - `S3RawStore` — object_store-backed (AWS S3 / minio / R2 / any
//!   S3-compatible). Requires the `source` feature for the object_store
//!   transitive dep.

use anyhow::Result;
use serde::Deserialize;
use std::future::Future;

pub mod local;
pub use local::LocalRawStore;

#[cfg(feature = "source")]
pub mod s3;
#[cfg(feature = "source")]
pub use s3::S3RawStore;

/// Pluggable storage for raw source artifacts.
///
/// `put` returns an opaque `ref` usable by `get`. `exists(doc_id,
/// fingerprint)` lets callers short-circuit re-fetches when the source's
/// per-doc fingerprint hasn't changed. `delete` removes the doc.
///
/// Same async-trait convention as `IncrementalSource`: explicit
/// `impl Future + Send` rather than the `async fn` sugar.
pub trait RawStore: Send + Sync {
    fn put(
        &self,
        doc_id: &str,
        data: &[u8],
        content_type: &str,
        meta: Option<&serde_json::Value>,
    ) -> impl Future<Output = Result<String>> + Send;

    fn get(&self, ref_: &str) -> impl Future<Output = Result<Vec<u8>>> + Send;

    fn exists(
        &self,
        doc_id: &str,
        fingerprint: Option<&str>,
    ) -> impl Future<Output = Result<bool>> + Send;

    fn delete(&self, doc_id: &str) -> impl Future<Output = Result<()>> + Send;
}

/// Discriminated-union config mirroring Python's `RawStoreConfig`.
/// Dispatch on `type:` in YAML.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum RawStoreConfig {
    Local(LocalRawStoreConfig),
    #[cfg(feature = "source")]
    S3(S3RawStoreConfig),
}

#[derive(Debug, Clone, Deserialize)]
pub struct LocalRawStoreConfig {
    pub root: String,
}

#[cfg(feature = "source")]
#[derive(Debug, Clone, Deserialize)]
pub struct S3RawStoreConfig {
    pub bucket: String,
    #[serde(default)]
    pub prefix: String,
    /// Optional S3-compatible endpoint URL (minio, R2). When None, the
    /// AWS default endpoint is used.
    #[serde(default)]
    pub endpoint_url: Option<String>,
}

/// Runtime polymorphism over the concrete RawStore impls. The enum lets
/// `load_raw_store` return a single type without `dyn` plumbing (which the
/// AFIT-style trait doesn't support cleanly).
pub enum AnyRawStore {
    Local(LocalRawStore),
    #[cfg(feature = "source")]
    S3(S3RawStore),
}

impl AnyRawStore {
    pub async fn put(
        &self,
        doc_id: &str,
        data: &[u8],
        content_type: &str,
        meta: Option<&serde_json::Value>,
    ) -> Result<String> {
        match self {
            AnyRawStore::Local(s) => s.put(doc_id, data, content_type, meta).await,
            #[cfg(feature = "source")]
            AnyRawStore::S3(s) => s.put(doc_id, data, content_type, meta).await,
        }
    }

    pub async fn get(&self, ref_: &str) -> Result<Vec<u8>> {
        match self {
            AnyRawStore::Local(s) => s.get(ref_).await,
            #[cfg(feature = "source")]
            AnyRawStore::S3(s) => s.get(ref_).await,
        }
    }

    pub async fn exists(&self, doc_id: &str, fingerprint: Option<&str>) -> Result<bool> {
        match self {
            AnyRawStore::Local(s) => s.exists(doc_id, fingerprint).await,
            #[cfg(feature = "source")]
            AnyRawStore::S3(s) => s.exists(doc_id, fingerprint).await,
        }
    }

    pub async fn delete(&self, doc_id: &str) -> Result<()> {
        match self {
            AnyRawStore::Local(s) => s.delete(doc_id).await,
            #[cfg(feature = "source")]
            AnyRawStore::S3(s) => s.delete(doc_id).await,
        }
    }
}

pub fn load_raw_store(cfg: &RawStoreConfig) -> Result<AnyRawStore> {
    match cfg {
        RawStoreConfig::Local(c) => Ok(AnyRawStore::Local(LocalRawStore::new(&c.root)?)),
        #[cfg(feature = "source")]
        RawStoreConfig::S3(c) => Ok(AnyRawStore::S3(S3RawStore::new(c.clone())?)),
    }
}

/// SHA-256 hex digest of `doc_id` — used by both Local and S3 backends to
/// derive a stable, path-traversal-safe key. Matches Python's
/// `hashlib.sha256(doc_id.encode("utf-8")).hexdigest()` exactly.
pub(crate) fn doc_id_hash(doc_id: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(doc_id.as_bytes());
    format!("{:x}", h.finalize())
}
