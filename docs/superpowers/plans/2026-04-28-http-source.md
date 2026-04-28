# http source — Python + Rust Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Mission Brief:** `skill-output/mission-brief/Mission-Brief-http-source.md`.

**Goal:** Replace Python's HttpSource stub with a real implementation; add the matching Rust port. Both fetch URLs (and optional sitemap), build Documents with consistent metadata.

**Architecture:** Python uses stdlib (`urllib.request` + `xml.etree.ElementTree`). Rust uses `reqwest` (transitive via fastembed → hf-hub; promoted to direct dep). Both extract HTML title via regex, populate `{url, status_code, content_type}` metadata. Tests boot an in-process HTTP server on each side.

**Tech Stack:** Python 3.12 stdlib; Rust + `reqwest` (existing transitive).

---

## Tasks

1. **Python impl + test** — replace `HttpSource.iter_documents` with real fetch + sitemap parse; add `tests/chunkshop/test_http_source.py` using `http.server` in a thread.
2. **Rust config** — add `HttpSourceConfig { urls, sitemap }`; add `SourceConfig::Http` variant.
3. **Rust deps** — promote `reqwest` to direct dep with the minimum feature set fastembed already enables.
4. **Rust impl** — `HttpSource` struct with `async fn iter_documents` matching Python's contract.
5. **Rust runner dispatch** — add `Http` arm to `AnySource`.
6. **Rust test** — `tests/http_source.rs` boots a tokio TcpListener with hand-rolled HTTP/1.1 responses; same scenario as Python test.
7. **Cross-language metadata sanity check** — eyeball that `{url, status_code, content_type}` keys match exactly between the two impls.
8. **Docs** — README + CHANGELOG.
9. **DC-FINAL + finishing-a-development-branch.**
