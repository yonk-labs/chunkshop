# Rust lede crate wiring (callable summarizer) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rust-lede-wiring.md`.

**Goal:** Wire `lede = "0.3"` into chunkshop-rs's callable summarizer behind a `lede` feature flag.

**Architecture:** Optional dep + cfg-gated branch in `CallableSummarizer::new`. Build-time toggle, not runtime.

---

## Tasks

1. **Cargo.toml:** add `lede = { version = "0.3", optional = true }` + `[features] lede = ["dep:lede"]`.
2. **summarizer.rs:** new `LedeSummarizer` struct (cfg-gated). Parse `max_length` + `mode` from kwargs at construction. Recognize the module name in `CallableSummarizer::new`.
3. **Error message:** when feature is OFF, append `"To enable lede, build with --features lede."` to the existing not-registered error.
4. **Tests:** 2 new tests in summarizer.rs's tests module, each gated with `#[cfg(feature = "lede")]`.
5. **Default build:** `cargo build --workspace` clean.
6. **With-feature build:** `cargo build --workspace --features lede` clean. `cargo test --workspace --features lede` rc=0 with the new tests.
7. **Docs:** README + CHANGELOG.
8. **DC-FINAL + finishing-a-development-branch.**
