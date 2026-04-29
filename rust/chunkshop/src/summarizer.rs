//! Summarizer dispatch — turns a `SummarizerConfig` into a
//! `(text, doc_metadata) -> Result<String>` callable. Mirrors
//! `python/src/chunkshop/chunkers/_summarizer.py`.
//!
//! Three modes:
//!   - **passthrough**: identity — returns the chunk text unchanged. Baseline
//!     for A/B comparisons.
//!   - **external**: pulls a pre-computed summary from `doc.metadata[field]`.
//!     Errors clearly when the field is missing or non-string.
//!   - **callable**: named-module dispatch. The Rust port currently recognizes
//!     a small built-in registry (just `chunkshop.summarizers.passthrough`).
//!     Unknown modules return a clear error directing the user to the Python
//!     side or a custom Rust binary that registers their summarizer. lede /
//!     sumy integration is an explicit follow-up brief.

use anyhow::{anyhow, Result};
use serde_json::Value;

use crate::config::{CallableSummarizerConfig, ExternalSummarizerConfig, SummarizerConfig};

pub trait SummarizerImpl: Send + Sync {
    fn summarize(&self, text: &str, doc_metadata: &Value) -> Result<String>;
}

/// Build a boxed summarizer from a config. Mirrors Python's `build_summarizer`.
pub fn build_summarizer(cfg: &SummarizerConfig) -> Result<Box<dyn SummarizerImpl>> {
    match cfg {
        SummarizerConfig::Passthrough(_) => Ok(Box::new(PassthroughSummarizer)),
        SummarizerConfig::External(c) => Ok(Box::new(ExternalSummarizer { cfg: c.clone() })),
        SummarizerConfig::Callable(c) => Ok(Box::new(CallableSummarizer::new(c.clone())?)),
    }
}

pub struct PassthroughSummarizer;

impl SummarizerImpl for PassthroughSummarizer {
    fn summarize(&self, text: &str, _doc_metadata: &Value) -> Result<String> {
        Ok(text.to_string())
    }
}

pub struct ExternalSummarizer {
    cfg: ExternalSummarizerConfig,
}

impl SummarizerImpl for ExternalSummarizer {
    fn summarize(&self, _text: &str, doc_metadata: &Value) -> Result<String> {
        let obj = doc_metadata.as_object().ok_or_else(|| {
            anyhow!(
                "external summarizer: doc.metadata is not an object, got {}",
                value_type_name(doc_metadata),
            )
        })?;
        let value = obj.get(&self.cfg.field).ok_or_else(|| {
            let mut keys: Vec<&String> = obj.keys().collect();
            keys.sort();
            anyhow!(
                "external summarizer: doc.metadata has no field {:?}. Available keys: {:?}",
                self.cfg.field,
                keys
            )
        })?;
        let s = value.as_str().ok_or_else(|| {
            anyhow!(
                "external summarizer: doc.metadata[{:?}] must be a string, got {}",
                self.cfg.field,
                value_type_name(value),
            )
        })?;
        Ok(s.to_string())
    }
}

fn value_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "null",
        Value::Bool(_) => "bool",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

/// Callable summarizer with a built-in registry of known modules. Unknown
/// modules produce a clear error at construction time. Adding a new module
/// is a matter of recognizing its name here and dispatching to a Rust impl.
pub struct CallableSummarizer {
    cfg: CallableSummarizerConfig,
    inner: Box<dyn SummarizerImpl>,
}

impl CallableSummarizer {
    pub fn new(cfg: CallableSummarizerConfig) -> Result<Self> {
        let inner: Box<dyn SummarizerImpl> = match cfg.module.as_str() {
            "chunkshop.summarizers.passthrough" => Box::new(PassthroughSummarizer),
            #[cfg(feature = "lede")]
            "chunkshop.summarizers.lede" => Box::new(LedeSummarizer::from_kwargs(&cfg.kwargs)?),
            other => {
                let lede_hint = if cfg!(feature = "lede") {
                    String::new()
                } else {
                    " To enable the lede module, build with --features lede.".to_string()
                };
                return Err(anyhow!(
                    "callable summarizer: module {other:?} is not registered in the Rust \
                     runtime. Built-in modules: {modules}. \
                     Run this YAML on Python (where lede/sumy modules are registered) or \
                     compile a custom chunkshop-rs binary that registers your summarizer.{hint}",
                    modules = built_in_modules(),
                    hint = lede_hint,
                ));
            }
        };
        Ok(Self { cfg, inner })
    }
}

fn built_in_modules() -> &'static str {
    if cfg!(feature = "lede") {
        "[\"chunkshop.summarizers.passthrough\", \"chunkshop.summarizers.lede\"]"
    } else {
        "[\"chunkshop.summarizers.passthrough\"]"
    }
}

#[cfg(feature = "lede")]
pub struct LedeSummarizer {
    max_length: usize,
    mode: lede::Mode,
}

#[cfg(feature = "lede")]
impl LedeSummarizer {
    /// Parse `max_length` (usize, default 500 to match Python's lede default)
    /// and `mode` (string: "default" / "legacy" / "coverage", default "default")
    /// from the YAML kwargs map. Extra kwargs are ignored with a tracing warn
    /// so future lede knobs don't break old chunkshop-rs binaries.
    pub fn from_kwargs(kwargs: &serde_json::Map<String, Value>) -> Result<Self> {
        let max_length: usize = match kwargs.get("max_length") {
            Some(Value::Number(n)) => n.as_u64().ok_or_else(|| {
                anyhow!("callable summarizer (lede): max_length must be a positive integer")
            })? as usize,
            Some(other) => {
                return Err(anyhow!(
                    "callable summarizer (lede): max_length must be an integer, got {}",
                    value_type_name(other)
                ))
            }
            None => 500,
        };
        let mode = match kwargs.get("mode") {
            Some(Value::String(s)) => match s.as_str() {
                "default" => lede::Mode::Default,
                "legacy" => lede::Mode::Legacy,
                "coverage" => lede::Mode::Coverage,
                other => {
                    return Err(anyhow!(
                        "callable summarizer (lede): unknown mode {other:?}. \
                         Valid: \"default\", \"legacy\", \"coverage\"."
                    ))
                }
            },
            Some(other) => {
                return Err(anyhow!(
                    "callable summarizer (lede): mode must be a string, got {}",
                    value_type_name(other)
                ))
            }
            None => lede::Mode::Default,
        };
        // Warn-ignore extra kwargs so future lede params don't trip old binaries.
        for key in kwargs.keys() {
            if !matches!(key.as_str(), "max_length" | "mode") {
                tracing::warn!(
                    "callable summarizer (lede): ignoring unrecognized kwarg {key:?}"
                );
            }
        }
        Ok(Self { max_length, mode })
    }
}

#[cfg(feature = "lede")]
impl SummarizerImpl for LedeSummarizer {
    fn summarize(&self, text: &str, _doc_metadata: &Value) -> Result<String> {
        if text.trim().is_empty() {
            return Ok(String::new());
        }
        let result = lede::summarize(text, self.max_length, self.mode);
        Ok(result.summary)
    }
}

impl SummarizerImpl for CallableSummarizer {
    fn summarize(&self, text: &str, doc_metadata: &Value) -> Result<String> {
        // kwargs are ignored by passthrough — kept on cfg for forward compat.
        let _ = &self.cfg.kwargs;
        self.inner.summarize(text, doc_metadata)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn passthrough_returns_text_unchanged() {
        let s = PassthroughSummarizer;
        assert_eq!(s.summarize("hello", &json!({})).unwrap(), "hello");
    }

    #[test]
    fn external_pulls_field_from_metadata() {
        let s = ExternalSummarizer {
            cfg: ExternalSummarizerConfig {
                field: "summary".into(),
            },
        };
        let meta = json!({"summary": "sum text", "other": "x"});
        assert_eq!(s.summarize("ignored", &meta).unwrap(), "sum text");
    }

    #[test]
    fn external_errors_on_missing_field() {
        let s = ExternalSummarizer {
            cfg: ExternalSummarizerConfig {
                field: "missing".into(),
            },
        };
        let meta = json!({"summary": "x", "other": "y"});
        let err = s.summarize("t", &meta).unwrap_err().to_string();
        assert!(err.contains("missing"), "expected field name in error: {err}");
        assert!(err.contains("Available keys"), "expected key listing: {err}");
    }

    #[test]
    fn external_errors_on_non_string_value() {
        let s = ExternalSummarizer {
            cfg: ExternalSummarizerConfig {
                field: "summary".into(),
            },
        };
        let meta = json!({"summary": 42});
        let err = s.summarize("t", &meta).unwrap_err().to_string();
        assert!(err.contains("must be a string"), "got: {err}");
    }

    #[test]
    fn callable_with_passthrough_module_works() {
        let cfg = CallableSummarizerConfig {
            module: "chunkshop.summarizers.passthrough".into(),
            function: "summarize".into(),
            kwargs: serde_json::Map::new(),
        };
        let s = CallableSummarizer::new(cfg).unwrap();
        assert_eq!(s.summarize("hi", &json!({})).unwrap(), "hi");
    }

    #[test]
    fn callable_unknown_module_errors_clearly() {
        let cfg = CallableSummarizerConfig {
            module: "chunkshop.summarizers.does_not_exist".into(),
            function: "summarize".into(),
            kwargs: serde_json::Map::new(),
        };
        let err = match CallableSummarizer::new(cfg) {
            Ok(_) => panic!("expected error for unknown module"),
            Err(e) => e.to_string(),
        };
        assert!(
            err.contains("not registered"),
            "expected 'not registered' in error: {err}"
        );
        assert!(err.contains("passthrough"), "should list built-ins: {err}");
        // When feature is OFF, error should hint at the feature flag.
        // When feature is ON, lede is registered (this branch can't run).
        #[cfg(not(feature = "lede"))]
        assert!(
            err.contains("--features lede"),
            "expected feature-flag hint: {err}"
        );
    }

    #[cfg(feature = "lede")]
    #[test]
    fn lede_callable_returns_summary_for_real_text() {
        let mut kwargs = serde_json::Map::new();
        kwargs.insert("max_length".into(), json!(120));
        kwargs.insert("mode".into(), json!("default"));
        let cfg = CallableSummarizerConfig {
            module: "chunkshop.summarizers.lede".into(),
            function: "summarize".into(),
            kwargs,
        };
        let s = CallableSummarizer::new(cfg).expect("lede callable should construct");
        let text = "The quick brown fox jumps over the lazy dog. \
                    Pack my box with five dozen liquor jugs. \
                    How vexingly quick daft zebras jump. \
                    Sphinx of black quartz, judge my vow. \
                    The five boxing wizards jump quickly. \
                    Bright vixens jump; dozy fowl quack. \
                    Quick wafting zephyrs vex bold Jim.";
        let out = s.summarize(text, &json!({})).expect("summarize ok");
        assert!(!out.is_empty(), "summary should be non-empty");
        assert!(
            out.chars().count() <= 120,
            "summary should respect max_length=120, got {} chars: {out:?}",
            out.chars().count()
        );
    }

    #[cfg(feature = "lede")]
    #[test]
    fn lede_callable_errors_on_unknown_mode() {
        let mut kwargs = serde_json::Map::new();
        kwargs.insert("mode".into(), json!("bogus"));
        let cfg = CallableSummarizerConfig {
            module: "chunkshop.summarizers.lede".into(),
            function: "summarize".into(),
            kwargs,
        };
        let err = match CallableSummarizer::new(cfg) {
            Ok(_) => panic!("expected error for unknown mode"),
            Err(e) => e.to_string(),
        };
        assert!(
            err.contains("unknown mode"),
            "expected 'unknown mode' in error: {err}"
        );
        assert!(
            err.contains("\"default\"") && err.contains("\"legacy\"") && err.contains("\"coverage\""),
            "expected valid mode list in error: {err}"
        );
    }
}
