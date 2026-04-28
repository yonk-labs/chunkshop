//! Files source. Mirrors `python/src/chunkshop/sources/files.py`.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use serde_json::json;
use sha1::{Digest, Sha1};

use crate::config::{FilesSourceConfig, JsonCorpusSourceConfig};

/// Analogue of Python's `sources.base.Document`.
#[derive(Debug, Clone)]
pub struct Document {
    pub id: String,
    pub content: String,
    pub title: Option<String>,
    pub metadata: serde_json::Value,
}

pub struct FilesSource {
    cfg: FilesSourceConfig,
}

impl FilesSource {
    pub fn new(cfg: FilesSourceConfig) -> Self {
        Self { cfg }
    }

    /// Enumerate files matching the glob, in sorted order, reading each as text.
    pub fn iter_documents(&self) -> Result<Vec<Document>> {
        let mut paths: Vec<PathBuf> = glob::glob(&self.cfg.glob)
            .with_context(|| format!("invalid glob {:?}", self.cfg.glob))?
            .filter_map(std::result::Result::ok)
            .collect();
        if paths.is_empty() {
            return Err(anyhow!("no files matched glob: {}", self.cfg.glob));
        }
        paths.sort();

        let mut out = Vec::with_capacity(paths.len());
        for p in paths {
            let text = std::fs::read_to_string(&p)
                .with_context(|| format!("reading {}", p.display()))?;
            let doc_id = self.id_for(&p)?;
            let title = p
                .file_name()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string());
            out.push(Document {
                id: doc_id,
                content: text,
                title,
                metadata: json!({ "source_path": p.display().to_string() }),
            });
        }
        Ok(out)
    }

    fn id_for(&self, path: &Path) -> Result<String> {
        match self.cfg.id_from.as_str() {
            "path" => Ok(path.display().to_string()),
            "stem" => path
                .file_stem()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string())
                .ok_or_else(|| anyhow!("file has no stem: {}", path.display())),
            "sha1" => {
                let mut hasher = Sha1::new();
                hasher.update(path.display().to_string().as_bytes());
                Ok(format!("{:x}", hasher.finalize()))
            }
            other => Err(anyhow!("unknown id_from: {other}")),
        }
    }
}

/// JSON-corpus source. Mirrors `python/src/chunkshop/sources/json_corpus.py`.
/// Reads a JSON file, extracts the array under `documents_key`, and yields
/// one `Document` per row. Field-name configuration matches Python's pydantic
/// defaults (`id` / `content` / `title`).
pub struct JsonCorpusSource {
    cfg: JsonCorpusSourceConfig,
}

impl JsonCorpusSource {
    pub fn new(cfg: JsonCorpusSourceConfig) -> Self {
        Self { cfg }
    }

    pub fn iter_documents(&self) -> Result<Vec<Document>> {
        let bytes = std::fs::read(&self.cfg.path)
            .with_context(|| format!("reading {}", self.cfg.path))?;
        let parsed: serde_json::Value = serde_json::from_slice(&bytes)
            .with_context(|| format!("parsing JSON from {}", self.cfg.path))?;
        let arr = parsed
            .get(&self.cfg.documents_key)
            .and_then(|v| v.as_array())
            .ok_or_else(|| {
                anyhow!(
                    "no array at key {:?} in {}",
                    self.cfg.documents_key,
                    self.cfg.path
                )
            })?;

        let mut out = Vec::with_capacity(arr.len());
        for (i, row_value) in arr.iter().enumerate() {
            let row = row_value.as_object().ok_or_else(|| {
                anyhow!("row {i} in {} is not a JSON object", self.cfg.path)
            })?;
            let id = row
                .get(&self.cfg.id_field)
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    anyhow!(
                        "row {i} missing string field {:?} in {}",
                        self.cfg.id_field,
                        self.cfg.path
                    )
                })?
                .to_string();
            let content = row
                .get(&self.cfg.content_field)
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    anyhow!(
                        "row {i} missing string field {:?} in {}",
                        self.cfg.content_field,
                        self.cfg.path
                    )
                })?
                .to_string();
            let title = self
                .cfg
                .title_field
                .as_ref()
                .and_then(|tf| row.get(tf).and_then(|v| v.as_str()).map(String::from));

            // Metadata = row keys minus id/content/title fields. Preserve raw
            // JSON values so downstream extractors / promote_metadata can
            // pull typed values.
            let mut meta = serde_json::Map::new();
            for (k, v) in row.iter() {
                if k == &self.cfg.id_field {
                    continue;
                }
                if k == &self.cfg.content_field {
                    continue;
                }
                if let Some(tf) = &self.cfg.title_field {
                    if k == tf {
                        continue;
                    }
                }
                meta.insert(k.clone(), v.clone());
            }
            out.push(Document {
                id,
                content,
                title,
                metadata: serde_json::Value::Object(meta),
            });
        }
        Ok(out)
    }
}
