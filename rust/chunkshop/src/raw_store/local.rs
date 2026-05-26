//! Filesystem RawStore. Layout: `<root>/<sha256(doc_id)>/{blob, meta.json}`.
//!
//! Mirrors `python/src/chunkshop/raw_store/local.py` exactly so a store
//! written by either implementation is readable by the other (paths match
//! byte-for-byte, meta.json fields agree).
//!
//! `doc_id` is hashed before use as a path component — arbitrary IDs
//! (`s3://bucket/key`, `../../etc/passwd`) cannot traverse outside `root`.

use anyhow::{Context, Result};
use std::future::Future;
use std::path::{Path, PathBuf};

use super::doc_id_hash;
use super::RawStore;

pub struct LocalRawStore {
    root: PathBuf,
}

impl LocalRawStore {
    pub fn new(root: impl Into<PathBuf>) -> Result<Self> {
        let root = root.into();
        std::fs::create_dir_all(&root)
            .with_context(|| format!("create raw_store root {}", root.display()))?;
        Ok(Self { root })
    }

    fn dir(&self, doc_id: &str) -> PathBuf {
        self.root.join(doc_id_hash(doc_id))
    }
}

impl RawStore for LocalRawStore {
    fn put(
        &self,
        doc_id: &str,
        data: &[u8],
        content_type: &str,
        meta: Option<&serde_json::Value>,
    ) -> impl Future<Output = Result<String>> + Send {
        let doc_id = doc_id.to_string();
        let data = data.to_vec();
        let content_type = content_type.to_string();
        let meta = meta.cloned();
        let dir = self.dir(&doc_id);
        async move {
            std::fs::create_dir_all(&dir)
                .with_context(|| format!("create dir {}", dir.display()))?;
            let blob_path = dir.join("blob");
            std::fs::write(&blob_path, &data)
                .with_context(|| format!("write blob {}", blob_path.display()))?;

            let mut record = serde_json::Map::new();
            record.insert(
                "doc_id".to_string(),
                serde_json::Value::String(doc_id.clone()),
            );
            record.insert(
                "content_type".to_string(),
                serde_json::Value::String(content_type),
            );
            if let Some(serde_json::Value::Object(m)) = &meta {
                for (k, v) in m {
                    // doc_id/content_type are canonical — caller-supplied
                    // duplicates lose, matching Python's `{**(meta or {})}`
                    // dict-spread which the canonical keys override afterward.
                    if k != "doc_id" && k != "content_type" {
                        record.insert(k.clone(), v.clone());
                    }
                }
            }
            let meta_path = dir.join("meta.json");
            std::fs::write(&meta_path, serde_json::to_vec(&record).unwrap())
                .with_context(|| format!("write meta {}", meta_path.display()))?;
            Ok(blob_path.to_string_lossy().into_owned())
        }
    }

    fn get(&self, ref_: &str) -> impl Future<Output = Result<Vec<u8>>> + Send {
        let path = PathBuf::from(ref_);
        async move { std::fs::read(&path).with_context(|| format!("read blob {}", path.display())) }
    }

    fn exists(
        &self,
        doc_id: &str,
        fingerprint: Option<&str>,
    ) -> impl Future<Output = Result<bool>> + Send {
        let dir = self.dir(doc_id);
        let fp = fingerprint.map(|s| s.to_string());
        async move {
            let blob = dir.join("blob");
            if !blob_exists(&blob) {
                return Ok(false);
            }
            let Some(fp) = fp else {
                return Ok(true);
            };
            let meta_path = dir.join("meta.json");
            let bytes = match std::fs::read(&meta_path) {
                Ok(b) => b,
                Err(_) => return Ok(false),
            };
            let meta: serde_json::Value = match serde_json::from_slice(&bytes) {
                Ok(v) => v,
                Err(_) => return Ok(false),
            };
            let stored = meta.get("fingerprint").and_then(|v| v.as_str());
            Ok(stored == Some(fp.as_str()))
        }
    }

    fn delete(&self, doc_id: &str) -> impl Future<Output = Result<()>> + Send {
        let dir = self.dir(doc_id);
        async move {
            let _ = std::fs::remove_file(dir.join("blob"));
            let _ = std::fs::remove_file(dir.join("meta.json"));
            let _ = std::fs::remove_dir(&dir);
            Ok(())
        }
    }
}

fn blob_exists(path: &Path) -> bool {
    std::fs::metadata(path)
        .map(|m| m.is_file())
        .unwrap_or(false)
}
