# Rust Extractor Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-rust-extractors.md`.

**Goal:** Port the extractor stage with 4 working impls + 2 Python-only stubs.

**Architecture:** New `extractor.rs` module + `ExtractorImpl` trait. New `ExtractorConfig` enum replaces `Option<serde_yml::Value>`. Runner threads tags + metadata-merge through chunker-wins semantics. Sink's `write_document` gains a tags-per-chunk parameter.

**Tech Stack:** Rust 2021 + new dep `whatlang = "0.16"`.

---

## Tasks

1. **Config:** ExtractorConfig + 6 variants, default = None. Replace the existing opaque field.
2. **`extractor.rs`:** ExtractorImpl trait + ExtractResult struct + 4 real impls (None, Composite, RakeKeywords with hand-rolled algo, LangDetect via whatlang) + 2 stubs (KeybertPhrases, SpacyEntities) that error at construction.
3. **Sink:** `write_document` takes `tags_per_chunk: &[Vec<String>]`. Update existing call sites to pass empty vecs (tests still pass) — runner will pass real tags after Task 4.
4. **Runner:** call `extractor.extract(c.original_content)` per chunk; merge tags + metadata into the chunk; pass to sink.
5. **Unit tests** in extractor.rs: rake on English text returns plausible phrases; lang_detect on English/Spanish returns the right code; composite chains correctly; none returns empty; stubs error at build.
6. **Integration tests:** `extractor_none_composite.rs`, `extractor_lang_detect.rs`.
7. **Regression:** `cross_language_append_with_promote_column` still GREEN.
8. **Docs:** README + CHANGELOG.
9. **DC-FINAL + finishing-a-development-branch.**
