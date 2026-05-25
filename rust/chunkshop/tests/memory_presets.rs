//! RM-A Tasks 11 + 12: verify the memory/ preset YAMLs load and dispatch
//! through the correct variants. No DB — pure config-load assertion.

#![cfg(feature = "memory")]

use chunkshop::config::{
    load_config, ChunkerConfig, ConsolidatorConfig, FramerConfig, MemoryTier, SessionStagingMode,
    SourceConfig, TargetConfig,
};

const REALTIME: &str = "configs/memory/realtime.yaml";
const CONSOLIDATE: &str = "configs/memory/consolidate.yaml";

#[test]
fn realtime_preset_loads_and_dispatches() {
    let cfg = load_config(std::path::Path::new(REALTIME)).expect("realtime.yaml must parse");
    // source: session_staging mode=realtime
    let s = match cfg.source {
        SourceConfig::SessionStaging(s) => s,
        other => panic!("expected SessionStaging; got {other:?}"),
    };
    assert_eq!(s.mode, SessionStagingMode::Realtime);
    assert_eq!(s.staging_table, "chunkshop_staging");
    assert_eq!(s.staging_schema, "public");
    // framer: identity (realtime doesn't segment into episodes)
    assert!(matches!(cfg.framer, FramerConfig::Identity(_)));
    // chunker: fixed_overlap
    assert!(matches!(cfg.chunker, ChunkerConfig::FixedOverlap(_)));
    // target.memory: tier=provisional, supersede=false
    let mem = match cfg.target {
        TargetConfig::Postgres(p) => p.memory.expect("memory block required"),
        _ => panic!("expected postgres target"),
    };
    assert_eq!(mem.tier, MemoryTier::Provisional);
    assert!(!mem.supersede);
}

#[test]
fn consolidate_preset_loads_and_dispatches() {
    let cfg = load_config(std::path::Path::new(CONSOLIDATE)).expect("consolidate.yaml must parse");
    // source: session_staging mode=consolidate
    let s = match cfg.source {
        SourceConfig::SessionStaging(s) => s,
        other => panic!("expected SessionStaging; got {other:?}"),
    };
    assert_eq!(s.mode, SessionStagingMode::Consolidate);
    assert_eq!(s.min_age_seconds, 3600);
    // framer: session_episode
    assert!(matches!(cfg.framer, FramerConfig::SessionEpisode(_)));
    // chunker: consolidation with extractive consolidator
    let cc = match cfg.chunker {
        ChunkerConfig::Consolidation(c) => c,
        other => panic!("expected Consolidation; got {other:?}"),
    };
    assert!(matches!(cc.consolidator, ConsolidatorConfig::Extractive(_)));
    assert_eq!(cc.fact_max_chars, 1200);
    assert!(matches!(*cc.base, ChunkerConfig::SentenceAware(_)));
    // target.memory: tier=consolidated, supersede=true
    let mem = match cfg.target {
        TargetConfig::Postgres(p) => p.memory.expect("memory block required"),
        _ => panic!("expected postgres target"),
    };
    assert_eq!(mem.tier, MemoryTier::Consolidated);
    assert!(mem.supersede);
}
