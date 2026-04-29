# Rust summary_embed + hierarchical_summary Chunkers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rust-summary-chunkers.md`.

**Goal:** Port both summary chunkers + their shared SummarizerConfig + GroupingConfig from Python.

**Architecture:** New `summarizer.rs` module with `SummarizerImpl` trait + 3 modes. Two new `ChunkerConfig` variants with `Box<ChunkerConfig>` for the recursive base. Runner's `build_chunker` recursively builds the base for both wrappers (parallel to `neighbor_expand`). Two cross-language parity integration tests (passthrough + external only — callable can't byte-match without lede).

**Tech Stack:** Rust 2021 + existing deps. No new crates.

---

## Tasks

1. **Config additions:** SummarizerConfig (External/Callable/Passthrough), GroupingConfig (FixedN/WordBudget/SectionAware), SummaryEmbedChunkerConfig, HierarchicalSummaryChunkerConfig. Add the section_aware-requires-hierarchy validator at config-load.
2. **`summarizer.rs`** module with `SummarizerImpl` trait + `build_summarizer()` dispatch. Built-in callable registry recognizes `chunkshop.summarizers.passthrough`; unknown modules error.
3. **`SummaryEmbedChunker`** in `chunker.rs` — wraps base chunker, replaces embedded_content per Python.
4. **`HierarchicalSummaryChunker`** in `chunker.rs` with three grouping strategies. Emits fine + coarse rows linked by group_id.
5. **Runner:** add both arms to `build_chunker` with recursive base construction.
6. **Unit tests:** grouping strategies (3 cases each); summarizer modes (passthrough trivial, external happy + error path, callable known + unknown).
7. **Cross-language parity tests:** `tests/summary_embed_parity.rs` and `tests/hierarchical_summary_parity.rs` against committed Python fixtures (passthrough + external modes).
8. **README + CHANGELOG.**
9. **DC-FINAL + finishing-a-development-branch.**
