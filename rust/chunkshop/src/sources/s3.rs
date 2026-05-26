//! S3 source. Mirrors `python/src/chunkshop/sources/s3.py`.
//!
//! RM-B Task 3: adds `IncrementalSource` impl with ETag-keyed cursor
//! `{key: etag}`. Mirror of Python commit `f875450` + review fixes.
//!
//! **Test seam.** The production path lazily builds an `AmazonS3` client from
//! environment credentials inside `make_store`. Tests inject an
//! `object_store::memory::InMemory` (or any `Arc<dyn ObjectStore>`) via
//! `S3Source::with_store(cfg, store)` so cursor logic can be exercised
//! without contacting AWS.

use anyhow::{Context, Result};
use std::collections::BTreeMap;
use std::future::Future;
use std::sync::Arc;

use crate::config::S3SourceConfig;
use crate::sources::base::{Document, IncrementalSource};

pub struct S3Source {
    cfg: S3SourceConfig,
    /// Optional pre-built store. Tests inject `object_store::memory::InMemory`.
    /// Production callers leave this `None` and `make_store` constructs an
    /// `AmazonS3` from environment credentials.
    test_store: Option<Arc<dyn object_store::ObjectStore>>,
}

impl S3Source {
    pub fn new(cfg: S3SourceConfig) -> Self {
        Self {
            cfg,
            test_store: None,
        }
    }

    /// Test-only constructor: bypass the AWS environment-credentials path
    /// and use the supplied store (e.g., `object_store::memory::InMemory`).
    /// Production code should call `new` instead.
    pub fn with_store(cfg: S3SourceConfig, store: Arc<dyn object_store::ObjectStore>) -> Self {
        Self {
            cfg,
            test_store: Some(store),
        }
    }

    fn make_store(&self) -> Result<Arc<dyn object_store::ObjectStore>> {
        if let Some(s) = &self.test_store {
            return Ok(s.clone());
        }
        use object_store::aws::AmazonS3Builder;
        let mut builder = AmazonS3Builder::from_env().with_bucket_name(&self.cfg.bucket);
        if let Some(endpoint) = &self.cfg.endpoint_url {
            builder = builder.with_endpoint(endpoint);
            builder = builder.with_allow_http(endpoint.starts_with("http://"));
        }
        let s3 = builder
            .build()
            .with_context(|| format!("building S3 client for bucket {}", self.cfg.bucket))?;
        Ok(Arc::new(s3))
    }

    async fn list_metas(
        &self,
        store: &Arc<dyn object_store::ObjectStore>,
    ) -> Result<Vec<object_store::ObjectMeta>> {
        use futures::StreamExt;
        use object_store::path::Path as ObjPath;

        let prefix = if self.cfg.prefix.is_empty() {
            None
        } else {
            Some(ObjPath::from(self.cfg.prefix.clone()))
        };
        let mut listing = store.list(prefix.as_ref());
        let mut metas: Vec<object_store::ObjectMeta> = Vec::new();
        while let Some(item) = listing.next().await {
            metas.push(item.with_context(|| format!("list under {}", self.cfg.prefix))?);
        }
        Ok(metas)
    }

    async fn build_document(
        &self,
        store: &Arc<dyn object_store::ObjectStore>,
        meta: &object_store::ObjectMeta,
    ) -> Result<Document> {
        let key = meta.location.to_string();
        let result = store
            .get(&meta.location)
            .await
            .with_context(|| format!("GET s3://{}/{key}", self.cfg.bucket))?;
        let bytes = result
            .bytes()
            .await
            .with_context(|| format!("read body of s3://{}/{key}", self.cfg.bucket))?;
        let content = String::from_utf8_lossy(&bytes).to_string();
        let etag = meta.e_tag.clone().unwrap_or_default();
        Ok(Document {
            id: format!("s3://{}/{}", self.cfg.bucket, key),
            content,
            title: None,
            metadata: serde_json::json!({
                "bucket": self.cfg.bucket,
                "key": key,
                "size": meta.size,
                "etag": etag,
            }),
            // Mirror Python s3.py: fingerprint carries the ETag for SP-1
            // SyncMode::Fingerprint consumers.
            fingerprint: Some(etag),
        })
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        let store = self.make_store()?;
        let metas = self.list_metas(&store).await?;
        let mut out: Vec<Document> = Vec::with_capacity(metas.len());
        for meta in &metas {
            out.push(self.build_document(&store, meta).await?);
        }
        Ok(out)
    }
}

impl IncrementalSource for S3Source {
    /// `{key: etag}` map. Consumers merge per-doc deltas (returned by
    /// `cursor_from`) into the running cursor to accumulate the full
    /// manifest. Unchanged keys preserve their stored ETags between syncs.
    type Cursor = BTreeMap<String, String>;

    fn empty_cursor(&self) -> Self::Cursor {
        BTreeMap::new()
    }

    fn iter_changes_since(
        &self,
        cursor: &Self::Cursor,
    ) -> impl Future<Output = Result<Vec<Document>>> + Send {
        let cursor = cursor.clone();
        async move {
            let store = self.make_store()?;
            let metas = self.list_metas(&store).await?;
            let mut out: Vec<Document> = Vec::new();
            for meta in &metas {
                let key = meta.location.to_string();
                let etag = meta.e_tag.clone().unwrap_or_default();
                if cursor.get(&key) == Some(&etag) {
                    // Unchanged; skip.
                    continue;
                }
                out.push(self.build_document(&store, meta).await?);
            }
            Ok(out)
        }
    }

    fn cursor_from(&self, last_document: &Document) -> Self::Cursor {
        // The canonical S3 cursor is the full key→etag map. This per-doc
        // helper returns a SINGLE-KEY delta — the consumer merges deltas
        // into the running cursor (see trait docs for the merge contract).
        let key = last_document
            .metadata
            .get("key")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| last_document.id.clone());
        let etag = last_document.fingerprint.clone().unwrap_or_default();
        let mut delta = BTreeMap::new();
        delta.insert(key, etag);
        delta
    }
}
