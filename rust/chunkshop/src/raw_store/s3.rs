//! S3 RawStore. Key layout: `<prefix><sha256(doc_id)>`.
//!
//! Mirrors `python/src/chunkshop/raw_store/s3.py`. Uses `object_store`'s
//! `AmazonS3` so the same crate that powers `S3Source` is reused — the
//! credential resolution chain (env / IAM / credential file) is therefore
//! consistent across the source and the raw_store.
//!
//! **Fingerprint storage**: Python uses S3 object metadata
//! (`x-amz-meta-fingerprint`). object_store 0.11's
//! `PutOptions { attributes, .. }` carries the equivalent for AWS, so
//! cross-implementation S3 stores are wire-compatible.
//!
//! **Test seam**: tests inject `object_store::memory::InMemory` via
//! `S3RawStore::with_store(cfg, store)` to exercise the put/get/exists/
//! delete cycle without contacting AWS.

use anyhow::{Context, Result};
use object_store::path::Path as ObjPath;
use object_store::{Attribute, AttributeValue, Attributes, ObjectStore, PutOptions, PutPayload};
use std::future::Future;
use std::sync::Arc;

use super::doc_id_hash;
use super::{RawStore, S3RawStoreConfig};

pub struct S3RawStore {
    cfg: S3RawStoreConfig,
    store: Arc<dyn ObjectStore>,
}

const FINGERPRINT_META_KEY: &str = "fingerprint";
const DOC_ID_META_KEY: &str = "doc_id";

impl S3RawStore {
    pub fn new(cfg: S3RawStoreConfig) -> Result<Self> {
        use object_store::aws::AmazonS3Builder;
        let mut b = AmazonS3Builder::from_env().with_bucket_name(&cfg.bucket);
        if let Some(endpoint) = &cfg.endpoint_url {
            b = b.with_endpoint(endpoint);
            b = b.with_allow_http(endpoint.starts_with("http://"));
        }
        let s3 = b
            .build()
            .with_context(|| format!("building S3 client for bucket {}", cfg.bucket))?;
        Ok(Self {
            cfg,
            store: Arc::new(s3),
        })
    }

    /// Test-only constructor — bypass AWS env credentials and use any
    /// `ObjectStore` implementation (typically `InMemory`).
    pub fn with_store(cfg: S3RawStoreConfig, store: Arc<dyn ObjectStore>) -> Self {
        Self { cfg, store }
    }

    fn key(&self, doc_id: &str) -> String {
        format!("{}{}", self.cfg.prefix, doc_id_hash(doc_id))
    }
}

impl RawStore for S3RawStore {
    fn put(
        &self,
        doc_id: &str,
        data: &[u8],
        content_type: &str,
        meta: Option<&serde_json::Value>,
    ) -> impl Future<Output = Result<String>> + Send {
        let key = self.key(doc_id);
        let path = ObjPath::from(key.clone());
        let bucket = self.cfg.bucket.clone();
        let payload: PutPayload = data.to_vec().into();
        let store = self.store.clone();

        let mut attrs = Attributes::new();
        attrs.insert(
            Attribute::ContentType,
            AttributeValue::from(content_type.to_string()),
        );
        attrs.insert(
            Attribute::Metadata(DOC_ID_META_KEY.into()),
            AttributeValue::from(doc_id.to_string()),
        );
        if let Some(serde_json::Value::Object(m)) = meta {
            if let Some(fp) = m.get(FINGERPRINT_META_KEY).and_then(|v| v.as_str()) {
                attrs.insert(
                    Attribute::Metadata(FINGERPRINT_META_KEY.into()),
                    AttributeValue::from(fp.to_string()),
                );
            }
        }

        async move {
            let opts = PutOptions {
                attributes: attrs,
                ..Default::default()
            };
            store
                .put_opts(&path, payload, opts)
                .await
                .with_context(|| format!("PUT s3://{bucket}/{key}"))?;
            Ok(format!("s3://{bucket}/{key}"))
        }
    }

    fn get(&self, ref_: &str) -> impl Future<Output = Result<Vec<u8>>> + Send {
        // ref_ is `s3://<bucket>/<key>` — strip the bucket portion to derive
        // the path. We trust `bucket` matches `self.cfg.bucket`; cross-bucket
        // reads from a single raw_store config aren't supported.
        let path = ref_
            .strip_prefix(&format!("s3://{}/", self.cfg.bucket))
            .map(|p| p.to_string())
            .unwrap_or_else(|| ref_.to_string());
        let path = ObjPath::from(path);
        let store = self.store.clone();
        let owned_ref = ref_.to_string();
        async move {
            let result = store
                .get(&path)
                .await
                .with_context(|| format!("GET {owned_ref}"))?;
            let bytes = result
                .bytes()
                .await
                .with_context(|| format!("read body of {owned_ref}"))?;
            Ok(bytes.to_vec())
        }
    }

    fn exists(
        &self,
        doc_id: &str,
        fingerprint: Option<&str>,
    ) -> impl Future<Output = Result<bool>> + Send {
        let key = self.key(doc_id);
        let path = ObjPath::from(key);
        let store = self.store.clone();
        let fp = fingerprint.map(|s| s.to_string());
        async move {
            // `head()` on object_store only returns base metadata (location,
            // size, e_tag, last_modified) — custom attributes are exposed via
            // `get`'s GetResult. For the no-fingerprint case `head` is
            // sufficient; the fingerprint path falls through to a full GET so
            // attributes are accessible.
            let Some(fp) = fp else {
                return Ok(store.head(&path).await.is_ok());
            };
            let result = match store.get(&path).await {
                Ok(r) => r,
                Err(_) => return Ok(false),
            };
            let stored: Option<String> = result
                .attributes
                .get(&Attribute::Metadata(FINGERPRINT_META_KEY.into()))
                .map(|v| v.as_ref().to_string());
            Ok(stored.as_deref() == Some(fp.as_str()))
        }
    }

    fn delete(&self, doc_id: &str) -> impl Future<Output = Result<()>> + Send {
        let key = self.key(doc_id);
        let path = ObjPath::from(key.clone());
        let store = self.store.clone();
        let bucket = self.cfg.bucket.clone();
        async move {
            // Mirror Python: silent on missing object.
            let _ = store.delete(&path).await.with_context(|| {
                format!("DELETE s3://{bucket}/{key} (treated as no-op if missing)")
            });
            Ok(())
        }
    }
}
