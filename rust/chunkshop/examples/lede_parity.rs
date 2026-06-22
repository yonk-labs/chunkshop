//! Parity harness: runs the four lede-backed enrichment features on a fixed
//! input and prints one JSON object per feature, so the output can be diffed
//! against the Python reference (`scripts/lede_parity.py`).
//!
//! Run: `cargo run --example lede_parity --features lede`
//!
//! Built behind the `lede` feature (see `[[example]]` in Cargo.toml).

use chunkshop::config::{ConsolidatorConfig, ExtractorConfig};
use chunkshop::consolidators::{build_consolidator, EpisodeInput};
use chunkshop::extractor::build_extractor;

const TEXT: &str = "Acme Corp raised $5 million in 2023. The company hired 40 engineers \
and opened a Berlin office on 2024-01-15. Revenue increased 300 percent. \
CEO Bob Smith said growth would continue.";

fn input_text() -> String {
    // Optional argv override for ad-hoc probing; defaults to TEXT for the
    // reproducible parity run.
    std::env::args().nth(1).unwrap_or_else(|| TEXT.to_string())
}

fn run_extractor(name: &str, cfg_json: &str) {
    let text = input_text();
    let cfg: ExtractorConfig = serde_json::from_str(cfg_json).expect("config parse");
    let ex = build_extractor(cfg).expect("build extractor");
    let r = ex.extract(&text).expect("extract");
    let out = serde_json::json!({
        "impl": "rust",
        "feature": name,
        "tags": r.tags,
        "metadata": r.metadata,
    });
    println!("{}", serde_json::to_string(&out).unwrap());
}

fn main() {
    run_extractor("lede_top_terms", r#"{"type":"lede_top_terms","n":8}"#);
    run_extractor("lede_report", r#"{"type":"lede_report","max_facts":10}"#);
    run_extractor("lede_entities", r#"{"type":"lede_entities"}"#);

    // consolidator: mode lede
    let ccfg: ConsolidatorConfig =
        serde_json::from_str(r#"{"mode":"lede","max_facts":10,"confidence_floor":0.0}"#)
            .expect("consolidator config parse");
    let c = build_consolidator(&ccfg);
    let ep = EpisodeInput {
        text: TEXT,
        frame_seq: 1,
        session_id: "s1",
        episode_start_ts: 0.0,
        episode_end_ts: 0.0,
    };
    let out = c.consolidate(&ep).expect("consolidate");
    let facts: Vec<_> = out
        .facts
        .iter()
        .map(|f| {
            serde_json::json!({
                "subject": f.subject,
                "predicate": f.predicate,
                "object": f.object,
                "support_span": f.support_span,
                "confidence": f.confidence,
            })
        })
        .collect();
    println!(
        "{}",
        serde_json::to_string(&serde_json::json!({
            "impl": "rust",
            "feature": "consolidator_lede",
            "summary": out.summary,
            "facts": facts,
        }))
        .unwrap()
    );
}
