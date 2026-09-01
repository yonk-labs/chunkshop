//! Derivation provenance: the hash that keys the cache, and the metadata
//! stamp that records how a derivation was produced. Mirrors
//! `python/src/chunkshop/sources/derivers/provenance.py` byte-for-byte in
//! shape (same 6-part hash input: schema version, rung, model, version,
//! engine, params-hash), including the `engine` field (Task 6).

use chrono::Utc;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::sources::parsers::ParseResult;

/// Bump when the *shape* of the derivation hash changes, so old cache
/// entries invalidate cleanly. Mirrors Python's `CACHE_SCHEMA_VERSION`.
const CACHE_SCHEMA_VERSION: &str = "1";

/// Canonical hash of a params object. `serde_json::to_string` on a
/// `Map<String, Value>` iterates in insertion order by default — the
/// caller must build `params` with keys in a stable (e.g. sorted) order for
/// this to be order-independent the way Python's `json.dumps(sort_keys=True)`
/// is. See the params-ordering test below; do not assume `serde_json`
/// sorts for you.
fn params_hash(params: &Value) -> String {
    let sorted = sort_json_keys(params);
    let canonical = serde_json::to_string(&sorted).expect("params must be JSON-serializable");
    let mut hasher = Sha256::new();
    hasher.update(canonical.as_bytes());
    format!("{:x}", hasher.finalize())[..16].to_string()
}

/// Recursively rebuild a `Value` with object keys in sorted order, so
/// `serde_json::to_string` output is order-independent — matching Python's
/// `json.dumps(..., sort_keys=True)`. Made explicit rather than relying on
/// `serde_json::Map`'s insertion-order-by-default behavior (this workspace
/// does not enable the `preserve_order` feature's BTreeMap alternative).
fn sort_json_keys(v: &Value) -> Value {
    match v {
        Value::Object(m) => {
            let mut sorted = Map::new();
            let mut keys: Vec<&String> = m.keys().collect();
            keys.sort();
            for k in keys {
                sorted.insert(k.clone(), sort_json_keys(&m[k]));
            }
            Value::Object(sorted)
        }
        Value::Array(a) => Value::Array(a.iter().map(sort_json_keys).collect()),
        other => other.clone(),
    }
}

/// Identity of *how* a derivation was produced.
///
/// Combined with the content hash this forms the cache key, which means the
/// cache key IS the provenance record: change the model, the version, the
/// engine, or any parameter and the derivation is redone rather than
/// silently reused.
///
/// `engine` distinguishes implementations that can diverge even with the
/// same model id/version (e.g. Python's fastembed vs Rust's fastembed-rs,
/// or CTranslate2 vs whisper-rs for ASR) — SC-12's contract-level parity
/// clause requires the engine be attributable, not just the model.
pub fn derivation_hash(
    rung: &str,
    model_id: &str,
    model_version: &str,
    engine: &str,
    params: &Value,
) -> String {
    let parts = [
        CACHE_SCHEMA_VERSION,
        rung,
        model_id,
        model_version,
        engine,
        &params_hash(params),
    ];
    let mut hasher = Sha256::new();
    hasher.update(parts.join("\0").as_bytes());
    format!("{:x}", hasher.finalize())
}

/// Return a new `ParseResult` carrying full derivation provenance.
pub fn stamp_provenance(
    result: ParseResult,
    rung: &str,
    model_id: &str,
    model_version: &str,
    engine: &str,
    params: &Value,
) -> ParseResult {
    let mut metadata = result.metadata;
    metadata.insert(
        "content_origin".to_string(),
        Value::String("derived".to_string()),
    );
    metadata.insert("derived_rung".to_string(), Value::String(rung.to_string()));
    let mut derived = Map::new();
    derived.insert("rung".to_string(), Value::String(rung.to_string()));
    derived.insert("model".to_string(), Value::String(model_id.to_string()));
    derived.insert(
        "model_version".to_string(),
        Value::String(model_version.to_string()),
    );
    derived.insert("engine".to_string(), Value::String(engine.to_string()));
    derived.insert(
        "params_hash".to_string(),
        Value::String(params_hash(params)),
    );
    derived.insert("ts".to_string(), Value::String(rfc3339_now()));
    metadata.insert("derived".to_string(), Value::Object(derived));
    ParseResult {
        text: result.text,
        metadata,
    }
}

/// RFC3339 UTC timestamp, mirroring Python's
/// `datetime.now(timezone.utc).isoformat()`. `chrono` is already in the
/// dependency tree transitively (`sqlx`'s `chrono` feature, pulled by the
/// `sink` feature) and is declared as a direct dependency for this module
/// (see `Cargo.toml`) — no new crate.
fn rfc3339_now() -> String {
    Utc::now().to_rfc3339()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn hash_is_stable_for_same_inputs() {
        let a = derivation_hash("clip_tags", "clip-ViT-B-32", "1", "rust", &json!({"top_k": 3}));
        let b = derivation_hash("clip_tags", "clip-ViT-B-32", "1", "rust", &json!({"top_k": 3}));
        assert_eq!(a, b);
    }

    #[test]
    fn hash_ignores_dict_key_ordering() {
        let a = derivation_hash("r", "m", "1", "rust", &json!({"x": 1, "y": 2}));
        let b = derivation_hash("r", "m", "1", "rust", &json!({"y": 2, "x": 1}));
        assert_eq!(a, b, "params_hash must be order-independent — the real adversarial case, not just 'same object twice'");
    }

    #[test]
    fn hash_changes_when_engine_changes() {
        let base = derivation_hash("clip_tags", "m", "1", "python", &json!({}));
        assert_ne!(derivation_hash("clip_tags", "m", "1", "rust", &json!({})), base);
    }

    #[test]
    fn hash_changes_when_any_other_input_changes() {
        let base = derivation_hash("clip_tags", "m", "1", "rust", &json!({"top_k": 3}));
        assert_ne!(derivation_hash("ocr", "m", "1", "rust", &json!({"top_k": 3})), base);
        assert_ne!(derivation_hash("clip_tags", "other", "1", "rust", &json!({"top_k": 3})), base);
        assert_ne!(derivation_hash("clip_tags", "m", "2", "rust", &json!({"top_k": 3})), base);
        assert_ne!(derivation_hash("clip_tags", "m", "1", "rust", &json!({"top_k": 5})), base);
    }

    #[test]
    fn stamp_and_hash_agree_on_the_same_tuple() {
        // Provenance and the cache key must never drift apart — this test
        // proves AGREEMENT (both fed the literal same call), not two
        // independent assertions that happen to use equal-looking literals.
        let rung = "clip_tags";
        let model_id = "Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text";
        let model_version = "5.13.3";
        let engine = "rust";
        let params = json!({"top_k": 3, "min_score": 0.2});

        let h = derivation_hash(rung, model_id, model_version, engine, &params);
        let stamped = stamp_provenance(
            ParseResult::new("cat"), rung, model_id, model_version, engine, &params,
        );
        let d = stamped.metadata["derived"].as_object().unwrap();
        assert_eq!(d["model"], model_id);
        assert_eq!(d["model_version"], model_version);
        assert_eq!(d["engine"], engine);
        // The params_hash embedded in the stamp must be the SAME 16-char
        // hash `derivation_hash` folds into its own output — recompute
        // independently and compare, proving the two functions can't
        // silently diverge on what "the params" means.
        assert!(h.len() == 64);
        assert_eq!(d["params_hash"].as_str().unwrap(), params_hash(&params));
    }
}
