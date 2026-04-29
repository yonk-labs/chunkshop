//! `chunkshop-rs bakeoff` — matrix evaluation. Mirrors
//! `python/src/chunkshop/bakeoff/`. One YAML = one factorial run over
//! `(chunkers × embedders)`, scored against a gold-query set with
//! recall@k + MRR. Outputs results.json + report.md + recommended.yaml.
//!
//! Keys (table-name derivation), score (recall@k + MRR math), and output
//! formatting are byte-identical to Python. Runner is byte-near-identical
//! modulo the embedder's documented ~1e-3 ORT cosine drift, which can flip
//! combos within ~5e-3 MRR of each other on small gold sets.

pub mod config;
pub mod gold;
pub mod keys;
pub mod output;
pub mod runner;
pub mod score;

pub use config::{BakeoffConfig, BakeoffResults, ComboResult};
pub use runner::{run_bakeoff, run_bakeoff_with_base};
