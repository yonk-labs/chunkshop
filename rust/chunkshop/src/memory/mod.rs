//! RM-A agent-memory module.
//!
//! - [`staging`] — append-only session staging table. Owns the `event_id`
//!   derivation that Python `chunkshop.memory.staging` also produces, so an
//!   event staged from one language and re-staged from the other dedupes
//!   on the same row (`ON CONFLICT (event_id) DO NOTHING`).
//!
//! Sources / sinks / framers for memory live in their respective top-level
//! modules (`crate::sources::session_staging`, `crate::sinks::memory_pg`,
//! `crate::framer::SessionEpisodeFramer`, `crate::chunker::ConsolidationChunker`)
//! to slot into the existing dispatch enums. Only the staging API needs its
//! own module home — it's not in any of the stage dispatchers.

pub mod staging;

pub use staging::{
    derive_event_id, ensure_staging_table, prune_staging, stage_event, stage_events, StagedEvent,
};
