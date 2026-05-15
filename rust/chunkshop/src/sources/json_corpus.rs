//! JSON-corpus source. Mirrors `python/src/chunkshop/sources/json_corpus.py`.
//! Reads a JSON file, extracts the array under `documents_key`, and yields
//! one `Document` per row.

use anyhow::{anyhow, Context, Result};

use crate::config::JsonCorpusSourceConfig;
use crate::sources::base::Document;

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
                    anyhow!("row {i} missing string field {:?} in {}", self.cfg.id_field, self.cfg.path)
                })?
                .to_string();
            let content = row
                .get(&self.cfg.content_field)
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    anyhow!("row {i} missing string field {:?} in {}", self.cfg.content_field, self.cfg.path)
                })?
                .to_string();
            let title = self
                .cfg
                .title_field
                .as_ref()
                .and_then(|tf| row.get(tf).and_then(|v| v.as_str()).map(String::from));

            let mut meta = serde_json::Map::new();
            for (k, v) in row.iter() {
                if k == &self.cfg.id_field { continue; }
                if k == &self.cfg.content_field { continue; }
                if let Some(tf) = &self.cfg.title_field {
                    if k == tf { continue; }
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
