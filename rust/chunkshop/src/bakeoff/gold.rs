//! Gold-query loader. Mirrors `python/src/chunkshop/bakeoff/gold.py`.
//! Resolves `gold_queries: str | list[GoldQuery]` to a concrete `Vec<GoldQuery>`.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};

use super::config::{GoldQueriesSpec, GoldQuery};

pub fn load_gold_queries(spec: &GoldQueriesSpec) -> Result<Vec<GoldQuery>> {
    load_gold_queries_with_base(spec, None)
}

/// Resolve `gold_queries` with an optional base directory for relative paths.
/// The CLI passes the bakeoff-YAML's parent so a path like
/// `docs/samples/bakeoff-ntsb/gold-ntsb.yaml` (or `gold-ntsb.yaml`) resolves
/// regardless of the caller's current working directory.
pub fn load_gold_queries_with_base(
    spec: &GoldQueriesSpec,
    base_dir: Option<&Path>,
) -> Result<Vec<GoldQuery>> {
    match spec {
        GoldQueriesSpec::Inline(list) => Ok(list.clone()),
        GoldQueriesSpec::Path(path_str) => {
            let candidate = PathBuf::from(path_str);
            let path: PathBuf = if candidate.is_absolute() {
                candidate
            } else {
                // Try caller-relative first (current cwd), then base-dir-relative.
                if candidate.exists() {
                    candidate
                } else if let Some(base) = base_dir {
                    base.join(&candidate)
                } else {
                    candidate
                }
            };
            if !path.exists() {
                return Err(anyhow!(
                    "gold_queries file not found: {} (tried as cwd-relative and \
                     bakeoff-config-relative)",
                    path.display()
                ));
            }
            let text = std::fs::read_to_string(&path)
                .with_context(|| format!("read gold_queries file {path_str}"))?;
            let raw: serde_json::Value = match path
                .extension()
                .and_then(|s| s.to_str())
                .map(|s| s.to_ascii_lowercase())
                .as_deref()
            {
                Some("json") => serde_json::from_str(&text)?,
                _ => serde_yaml_ng::from_str(&text)?,
            };
            let arr = raw.as_array().ok_or_else(|| {
                anyhow!(
                    "gold_queries file must be a YAML/JSON list; got {}",
                    match &raw {
                        serde_json::Value::Object(_) => "object",
                        serde_json::Value::String(_) => "string",
                        serde_json::Value::Number(_) => "number",
                        serde_json::Value::Bool(_) => "bool",
                        serde_json::Value::Null => "null",
                        serde_json::Value::Array(_) => unreachable!(),
                    }
                )
            })?;
            arr.iter()
                .map(|v| {
                    serde_json::from_value::<GoldQuery>(v.clone())
                        .map_err(|e| anyhow!("gold query parse error: {e}"))
                })
                .collect()
        }
    }
}
