//! Files source. Mirrors `python/src/chunkshop/sources/files.py`.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use serde_json::json;
use sha1::{Digest, Sha1};

use crate::config::FilesSourceConfig;
use crate::sources::base::Document;

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
        if self.cfg.id_from == "stem" {
            Self::check_unique_stems(&paths)?;
        }

        let mut out = Vec::with_capacity(paths.len());
        for p in paths {
            let text =
                std::fs::read_to_string(&p).with_context(|| format!("reading {}", p.display()))?;
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
                fingerprint: None,
            });
        }
        Ok(out)
    }

    /// Mirrors Python `_check_unique_stems`: two files with the same stem
    /// would share a doc id and overwrite each other's rows.
    fn check_unique_stems(paths: &[PathBuf]) -> Result<()> {
        let mut by_id: Vec<(String, Vec<String>)> = Vec::new();
        for p in paths {
            let Some(stem) = p.file_stem().and_then(|s| s.to_str()) else {
                continue;
            };
            let shown = p.display().to_string();
            match by_id.iter_mut().find(|(k, _)| k == stem) {
                Some((_, v)) => v.push(shown),
                None => by_id.push((stem.to_string(), vec![shown])),
            }
        }
        let dups: Vec<_> = by_id.into_iter().filter(|(_, v)| v.len() > 1).collect();
        if dups.is_empty() {
            return Ok(());
        }
        let shown: Vec<String> = dups
            .iter()
            .take(5)
            .map(|(k, v)| format!("  '{k}': {}", v.join(", ")))
            .collect();
        let more = dups.len() - shown.len();
        let more_line = if more > 0 {
            format!("\n  ... and {more} more")
        } else {
            String::new()
        };
        Err(anyhow!(
            "files source id_from='stem' maps multiple files to the same doc id \
             (their chunks would overwrite each other):\n{}{}\n\
             Set source.id_from: path to give each file a unique id.",
            shown.join("\n"),
            more_line
        ))
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
