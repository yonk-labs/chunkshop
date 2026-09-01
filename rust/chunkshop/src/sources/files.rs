//! Files source. Mirrors `python/src/chunkshop/sources/files.py`.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use sha1::{Digest, Sha1};

use crate::config::{FilesSourceConfig, OnEmpty};
use crate::sources::base::Document;

pub struct FilesSource {
    cfg: FilesSourceConfig,
}

impl FilesSource {
    pub fn new(cfg: FilesSourceConfig) -> Self {
        Self { cfg }
    }

    /// Enumerate files matching the glob, in sorted order, deriving each
    /// through the extension-dispatched parser registry (plain text passes
    /// through unchanged; recognized binary formats get real extraction or a
    /// named refusal — never a silent UTF-8 mis-decode).
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
            let ext = p
                .extension()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_lowercase();
            let deriver = crate::sources::parsers::registry::get_deriver(&ext);
            let result = deriver
                .derive(&p)
                .with_context(|| format!("deriving {}", p.display()))?;

            if result.text.trim().is_empty() {
                match self.cfg.on_empty {
                    OnEmpty::Error => {
                        return Err(crate::sources::parsers::EmptyDerivationError {
                            path: p.clone(),
                            parser: ext.clone(),
                        }
                        .into());
                    }
                    OnEmpty::Skip => continue,
                    OnEmpty::Index => {} // fall through, index the empty document
                }
            }

            let doc_id = self.id_for(&p)?;
            let title = p
                .file_name()
                .and_then(|s| s.to_str())
                .map(|s| s.to_string());
            let mut metadata = result.metadata;
            metadata.insert(
                "source_path".to_string(),
                serde_json::Value::String(p.display().to_string()),
            );
            out.push(Document {
                id: doc_id,
                content: result.text,
                title,
                metadata: serde_json::Value::Object(metadata),
                fingerprint: None,
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
