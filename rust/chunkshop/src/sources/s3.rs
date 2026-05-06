//! S3 source. Mirrors `python/src/chunkshop/sources/s3.py`.

use anyhow::{Context, Result};

use crate::config::S3SourceConfig;
use crate::sources::base::Document;

pub struct S3Source {
    cfg: S3SourceConfig,
}

impl S3Source {
    pub fn new(cfg: S3SourceConfig) -> Self {
        Self { cfg }
    }

    pub async fn iter_documents(&self) -> Result<Vec<Document>> {
        use futures::StreamExt;
        use object_store::aws::AmazonS3Builder;
        use object_store::{path::Path as ObjPath, ObjectStore};

        let mut builder = AmazonS3Builder::from_env().with_bucket_name(&self.cfg.bucket);
        if let Some(endpoint) = &self.cfg.endpoint_url {
            builder = builder.with_endpoint(endpoint);
            builder = builder.with_allow_http(endpoint.starts_with("http://"));
        }
        let store = builder
            .build()
            .with_context(|| format!("building S3 client for bucket {}", self.cfg.bucket))?;

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

        let mut out: Vec<Document> = Vec::with_capacity(metas.len());
        for meta in metas {
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
            out.push(Document {
                id: format!("s3://{}/{}", self.cfg.bucket, key),
                content,
                title: None,
                metadata: serde_json::json!({
                    "bucket": self.cfg.bucket,
                    "key": key,
                    "size": meta.size,
                    "etag": etag,
                }),
            });
        }
        Ok(out)
    }
}
