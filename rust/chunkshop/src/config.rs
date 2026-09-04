//! YAML config parsing.
//!
//! Accepts the same YAML shape as the Python reference implementation, but
//! only the minimal subset: files source, sentence_aware chunker, fastembed
//! embedder, pgvector target. Unknown fields are tolerated at the top level
//! (e.g. `extractor:`, `framer:`, `runtime:` extras) so Python-authored YAMLs
//! parse without edits — per-section structs use serde's default untagged
//! behavior with explicit fields.

use std::path::Path;

use anyhow::{anyhow, Context, Result};
use regex::Regex;
use serde::Deserialize;

const ALLOWED_PROMOTE_TYPES: &[&str] = &[
    "text",
    "text[]",
    "int",
    "bigint",
    "boolean",
    "jsonb",
    "timestamptz",
    "date",
];

/// Allowlist regex for `ClickhouseTargetConfig::engine`. Hardening relative to
/// Python (which interpolates the engine string raw — see
/// python/src/chunkshop/config.py:542). Accepts:
///   - `MergeTree` / `MergeTree()`
///   - `ReplacingMergeTree(<single_ident>)` (the `created_at` dedup column)
///   - Any of the above optionally followed by ` ORDER BY <expr>`
///
/// Rejects engines outside this whitelist (Replicated*, Distributed, Memory,
/// engines with embedded SQL, etc.) — those need explicit user request and a
/// separate brief.
const CLICKHOUSE_ENGINE_RE: &str = r"^(MergeTree(\(\))?|ReplacingMergeTree\(\w+\))( ORDER BY .+)?$";

/// Matches `${VAR}` references inside a `dsn` literal. Only the exact
/// `${NAME}` form is substituted — a bare `$` (common in DSN passwords) is
/// left untouched. Mirrors Python's `_DSN_VAR` in `config.py`.
fn dsn_var_re() -> &'static Regex {
    use std::sync::OnceLock;
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}").unwrap())
}

/// Resolve the effective connection string for a config carrying `dsn`/
/// `dsn_env` (direct `dsn` literal — with `${VAR}` interpolation — taking
/// precedence over the legacy `dsn_env` env-var-name lookup). Mirrors
/// Python's `_DsnResolvable.resolve_dsn()` in `config.py` exactly, including
/// its error text, so operators see the same message regardless of which
/// implementation loaded the config.
///
/// This is the ONE place `${VAR}` substitution happens in Rust — every
/// source/backend that accepts `dsn`/`dsn_env` must call this rather than
/// re-implementing resolution (the pre-existing `session_staging.rs` helper
/// this replaces fell back to `dsn_env` whenever the literal contained `${`,
/// instead of substituting — a parity gap, not intended behavior).
pub fn resolve_dsn(dsn: Option<&str>, dsn_env: Option<&str>) -> Result<String> {
    if let Some(d) = dsn {
        let mut missing: Option<String> = None;
        let resolved = dsn_var_re().replace_all(d, |caps: &regex::Captures| {
            let name = &caps[1];
            match std::env::var(name) {
                Ok(v) => v,
                Err(_) => {
                    if missing.is_none() {
                        missing = Some(name.to_string());
                    }
                    String::new()
                }
            }
        });
        if let Some(name) = missing {
            return Err(anyhow!(
                "dsn references ${{{name}}} but env var '{name}' is not set"
            ));
        }
        return Ok(resolved.into_owned());
    }
    match dsn_env {
        Some(env) => std::env::var(env).with_context(|| format!("DSN env var {env} not set")),
        None => Err(anyhow!("one of `dsn` or `dsn_env` is required")),
    }
}

/// Config-load-time check that at least one of `dsn`/`dsn_env` is set.
/// Mirrors Python's `_DsnResolvable._require_dsn_or_dsn_env` model validator.
/// Only needed on structs where `dsn_env` has no config-level default (the
/// four `*TableSourceConfig`s and `SqliteTargetConfig` here) — the three
/// other target configs default `dsn_env` to `"CHUNKSHOP_DSN"`, so one of the
/// two is always present there without an explicit check.
fn require_dsn_or_dsn_env(dsn: &Option<String>, dsn_env: &Option<String>, field: &str) -> Result<()> {
    if dsn.is_none() && dsn_env.is_none() {
        return Err(anyhow!(
            "{field}: one of `dsn` or `dsn_env` is required"
        ));
    }
    Ok(())
}

/// One promoted jsonb path → typed Postgres column. Mirrors Python's
/// `chunkshop.config.PromoteColumn`. The `path` is dot-separated; each segment
/// must match `^[A-Za-z_][A-Za-z0-9_]*$`. The `type_` must be in
/// `ALLOWED_PROMOTE_TYPES` — this is SQL-injection-prevention by allowlist:
/// `_ensure_promote_columns` interpolates the type as a literal into DDL.
#[derive(Debug, Clone)]
pub struct PromoteColumn {
    pub path: String,
    pub type_: String,
}

impl PromoteColumn {
    /// Postgres column identifier — dots → double-underscore, lowercased.
    /// Mirrors Python's `PromoteColumn.column_name`.
    pub fn column_name(&self) -> String {
        self.path.replace('.', "__").to_lowercase()
    }

    fn validate_path(path: &str) -> std::result::Result<(), String> {
        if path.is_empty() {
            return Err("path must not be empty".into());
        }
        let seg_re = Regex::new(r"^[A-Za-z_][A-Za-z0-9_]*$").unwrap();
        for seg in path.split('.') {
            if !seg_re.is_match(seg) {
                return Err(format!(
                    "path segments must match ^[A-Za-z_][A-Za-z0-9_]*$ separated by '.', got {path:?}"
                ));
            }
        }
        Ok(())
    }

    fn validate_type(t: &str) -> std::result::Result<(), String> {
        if !ALLOWED_PROMOTE_TYPES.contains(&t) {
            return Err(format!(
                "promote_metadata type must be one of {ALLOWED_PROMOTE_TYPES:?}, got {t:?}"
            ));
        }
        Ok(())
    }
}

impl<'de> serde::Deserialize<'de> for PromoteColumn {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        #[derive(serde::Deserialize)]
        struct Raw {
            path: String,
            #[serde(rename = "type")]
            type_: String,
        }
        let r = Raw::deserialize(d)?;
        Self::validate_path(&r.path).map_err(serde::de::Error::custom)?;
        Self::validate_type(&r.type_).map_err(serde::de::Error::custom)?;
        Ok(Self {
            path: r.path,
            type_: r.type_,
        })
    }
}

/// Promoted by default whenever a cell configures `source.derive`. Mirrors
/// Python's `DEFAULT_MEDIA_PROMOTIONS` (`config.py`).
///
/// A VLM caption and a verbatim email are byte-identical once both are
/// `original_content`; these columns are what let a downstream consumer
/// rank, filter, or refuse derived content in the query it already runs.
/// Without them a Rust cell appending to a table a Python cell created
/// writes NULL where Python writes a value, and a downstream
/// `WHERE content_origin = 'derived'` silently drops every Rust-written row.
pub fn default_media_promotions() -> Vec<PromoteColumn> {
    vec![
        PromoteColumn {
            path: "content_origin".to_string(),
            type_: "text".to_string(),
        },
        PromoteColumn {
            path: "derived_rung".to_string(),
            type_: "text".to_string(),
        },
    ]
}

/// One YAML = one cell. Matches `python/src/chunkshop/config.py::CellConfig`.
///
/// `Deserialize` is hand-written below (rather than derived) so the
/// media-promotion injection Python performs in a
/// `@model_validator(mode="after")` runs on every YAML load here too.
#[derive(Debug, Clone)]
pub struct CellConfig {
    pub cell_name: String,
    pub source: SourceConfig,
    pub chunker: ChunkerConfig,
    pub embedder: EmbedderConfig,
    pub target: TargetConfig,
    pub runtime: RuntimeConfig,
    pub framer: FramerConfig,
    pub extractor: ExtractorConfig,
}

impl CellConfig {
    /// Mirror of Python's `CellConfig._promote_media_origin_columns`
    /// (`config.py`): a cell that configures `source.derive` always gets
    /// `content_origin` + `derived_rung` as real promoted columns.
    ///
    /// Python's semantics, matched exactly: user-specified promotions WIN
    /// (nothing is reordered or replaced), defaults only fill gaps, the
    /// gap test is by `column_name()` (not raw `path`, so a user entry of
    /// `Content_Origin` counts as already present), and additions are
    /// APPENDED after the user's entries. Idempotent.
    pub fn apply_default_media_promotions(&mut self) {
        let has_derive = match &self.source {
            SourceConfig::Files(f) => f.derive.is_some(),
            _ => false,
        };
        if !has_derive {
            return;
        }
        let promote = self.target.promote_metadata_mut();
        let existing: std::collections::HashSet<String> =
            promote.iter().map(PromoteColumn::column_name).collect();
        for pc in default_media_promotions() {
            if !existing.contains(&pc.column_name()) {
                promote.push(pc);
            }
        }
    }
}

impl CellConfig {
    /// Everything about `source.derive` that is knowable from config alone
    /// and can still fail. Must run BEFORE any destructive DDL.
    ///
    /// Why this exists: Python raises both of these in
    /// `FilesSource.__init__`, which `runner.py` calls before
    /// `sink.create_table()`. Rust's equivalents were deferred —
    /// `FilesSource`'s ambiguity error is stashed in `derive_error` and only
    /// returned from `iter_documents()`, and a missing-Cargo-feature rung
    /// resolves to a `MissingFeatureDeriver` that fails at point of use.
    /// Both therefore landed AFTER `create_table()`'s DROP+CREATE, so a
    /// `mode: overwrite` cell destroyed and emptied its target table before
    /// reporting an error that was knowable at config-load time.
    ///
    /// Deliberate behaviour change worth naming: in a build without the
    /// `media` feature, a cell with a `derive:` block now fails here even
    /// if its glob happens to match no derive-routed file (see
    /// `derivers::load_deriver`'s note on the old point-of-use asymmetry).
    /// Failing loudly on a config the build cannot honor is preferable to
    /// dropping the user's table first.
    pub fn preflight_derive(&self) -> Result<()> {
        let SourceConfig::Files(f) = &self.source else {
            return Ok(());
        };
        let Some(derive) = &f.derive else {
            return Ok(());
        };

        // (1) Ambiguous routing: the same extension reachable from two
        // different `derive` keys. Same normalization as
        // `FilesSource::new` (split on '|', trim, lowercase, strip a
        // leading '.') so the two agree on what "the same extension" means.
        let mut ext_owner: std::collections::HashMap<String, String> =
            std::collections::HashMap::new();
        for pattern in derive.keys() {
            for raw_ext in pattern.split('|') {
                let ext = raw_ext.trim().to_lowercase();
                let ext = ext.trim_start_matches('.').to_string();
                if let Some(first_key) = ext_owner.get(&ext) {
                    if first_key != pattern {
                        return Err(anyhow::Error::new(
                            crate::sources::parsers::AmbiguousDeriverError {
                                ext,
                                first_key: first_key.clone(),
                                second_key: pattern.clone(),
                            },
                        ));
                    }
                    continue;
                }
                ext_owner.insert(ext, pattern.clone());
            }
        }

        // (2) Cargo-feature availability per rung. Mirrors the gates in
        // `sources::derivers::load_deriver`; kept in sync with the feature
        // names that module names in its errors.
        for dcfg in derive.values() {
            let needed: Option<&str> = match dcfg {
                // Rung 5 shells out; nothing about it is compiled in, so it
                // is available in every build including
                // `--no-default-features`. Its own preflight (does the
                // binary exist, what version is it) happens in
                // `load_deriver`, which `FilesSource::new` calls before the
                // sink's DDL.
                DeriverConfig::Ross(_) => None,
                DeriverConfig::ImageMeta(_) if !cfg!(feature = "media") => Some("media"),
                DeriverConfig::ClipTags(_)
                    if !cfg!(feature = "media") || !cfg!(feature = "embedder-hub") =>
                {
                    Some("media,embedder-hub")
                }
                _ => None,
            };
            if let Some(features) = needed {
                let rung = match dcfg {
                    DeriverConfig::ImageMeta(_) => "image_meta",
                    DeriverConfig::ClipTags(_) => "clip_tags",
                    // Unreachable: `needed` is None for Ross (no feature
                    // gates it). Named rather than `_` so adding a rung
                    // that DOES need a feature is a compile error here.
                    DeriverConfig::Ross(_) => "ross",
                };
                return Err(anyhow!(
                    "derive rung '{rung}' requires the `{features}` Cargo feature(s); \
                     rebuild with --features {features}"
                ));
            }
        }

        Ok(())
    }
}

impl<'de> serde::Deserialize<'de> for CellConfig {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        #[derive(Deserialize)]
        struct Raw {
            cell_name: String,
            source: SourceConfig,
            chunker: ChunkerConfig,
            embedder: EmbedderConfig,
            target: TargetConfig,
            #[serde(default)]
            runtime: RuntimeConfig,
            #[serde(default)]
            framer: FramerConfig,
            #[serde(default)]
            extractor: ExtractorConfig,
        }
        let r = Raw::deserialize(d)?;
        let mut cfg = CellConfig {
            cell_name: r.cell_name,
            source: r.source,
            chunker: r.chunker,
            embedder: r.embedder,
            target: r.target,
            runtime: r.runtime,
            framer: r.framer,
            extractor: r.extractor,
        };
        cfg.apply_default_media_promotions();
        Ok(cfg)
    }
}

/// Discriminated union over extractor types. Mirrors Python's `ExtractorConfig`.
/// Tagged on `type`. Default = `None` (the no-op extractor — equivalent to
/// "no extractor stage" in pre-extractor YAMLs).
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ExtractorConfig {
    None(NoneExtractorConfig),
    Composite(CompositeExtractorConfig),
    RakeKeywords(RakeKeywordsExtractorConfig),
    LangDetect(LangDetectExtractorConfig),
    KeybertPhrases(KeybertPhrasesExtractorConfig),
    SpacyEntities(SpacyEntitiesExtractorConfig),
    LedeTopTerms(LedeTopTermsExtractorConfig),
    LedeReport(LedeReportExtractorConfig),
    LedeEntities(LedeEntitiesExtractorConfig),
}

impl Default for ExtractorConfig {
    fn default() -> Self {
        ExtractorConfig::None(NoneExtractorConfig::default())
    }
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct NoneExtractorConfig {}

#[derive(Debug, Clone, Deserialize)]
pub struct CompositeExtractorConfig {
    #[serde(default)]
    pub extractors: Vec<ExtractorConfig>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RakeKeywordsExtractorConfig {
    #[serde(default = "default_rake_top_k")]
    pub top_k: usize,
    #[serde(default = "default_rake_min_chars")]
    pub min_chars: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct LangDetectExtractorConfig {
    #[serde(default = "default_lang_backend")]
    pub backend: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct KeybertPhrasesExtractorConfig {
    #[serde(default = "default_keybert_top_k")]
    pub top_k: usize,
    #[serde(default = "default_keybert_model")]
    pub model_name: String,
    #[serde(default = "default_keybert_ngram")]
    pub keyphrase_ngram_range: (usize, usize),
}

#[derive(Debug, Clone, Deserialize)]
pub struct SpacyEntitiesExtractorConfig {
    #[serde(default = "default_spacy_model")]
    pub model: String,
    #[serde(default = "default_spacy_whitelist")]
    pub label_whitelist: Vec<String>,
}

/// `lede_top_terms` — top-N salient words/phrases via lede 0.5 `top_terms_scored`.
/// Config mirrors Python's `LedeTopTermsExtractor` (`n`, `kinds`) for YAML
/// portability. `hints`/`hint_focus`/`hint_mode`/`expand` (Python advanced knobs)
/// are not yet wired in Rust and are ignored if present.
#[derive(Debug, Clone, Deserialize)]
pub struct LedeTopTermsExtractorConfig {
    #[serde(default = "default_top_terms_n")]
    pub n: usize,
    #[serde(default = "default_top_terms_kinds")]
    pub kinds: Vec<String>,
}

/// `lede_report` — assembled fact/metadata report. Forward-compatible subset of
/// Python's `readable_report().to_dict()` (no `attributes`/SVO `fact_records` —
/// lede-rs doesn't expose them). See spec D1. Config mirrors Python's
/// `LedeReportExtractor`; `backend` other than `regex` degrades to the
/// deterministic regex+gazetteer path in Rust (no spaCy). `max_chars` /
/// `keep_headings` / `include_toc` (Python readable-report knobs) don't apply to
/// the Rust subset and are ignored if present.
#[derive(Debug, Clone, Deserialize)]
pub struct LedeReportExtractorConfig {
    #[serde(default = "default_lede_report_max_facts")]
    pub max_facts: usize,
    #[serde(default = "default_lede_report_backend")]
    pub backend: String,
    #[serde(default = "default_lede_report_tag_sources")]
    pub tag_sources: Vec<String>,
}

/// `lede_entities` — deterministic gazetteer NER via lede-enrich. Writes the
/// shared `entities` key as `{"unlabeled": [...]}` (schema-uniform with Python's
/// labeled dict, content-divergent). See spec D2.
#[derive(Debug, Clone, Deserialize)]
pub struct LedeEntitiesExtractorConfig {}

fn default_top_terms_n() -> usize {
    10
}
fn default_top_terms_kinds() -> Vec<String> {
    vec!["words".to_string(), "phrases".to_string()]
}
fn default_lede_report_max_facts() -> usize {
    40
}
fn default_lede_report_backend() -> String {
    "regex".to_string()
}
/// Matches Python's default verbatim. Rust skips sources it can't produce
/// (`attributes`, `fact_records`, `spacy_*`) when building tags — see spec D1.
pub fn default_lede_report_tag_sources() -> Vec<String> {
    ["attributes", "key_facts", "dates", "amounts", "entities"]
        .iter()
        .map(|s| s.to_string())
        .collect()
}

fn default_rake_top_k() -> usize {
    10
}
fn default_rake_min_chars() -> usize {
    3
}
fn default_lang_backend() -> String {
    "langdetect".to_string()
}
fn default_keybert_top_k() -> usize {
    10
}
fn default_keybert_model() -> String {
    "all-MiniLM-L6-v2".to_string()
}
fn default_keybert_ngram() -> (usize, usize) {
    (1, 2)
}
fn default_spacy_model() -> String {
    "en_core_web_sm".to_string()
}
fn default_spacy_whitelist() -> Vec<String> {
    vec!["ORG", "PERSON", "GPE", "DATE", "LAW"]
        .into_iter()
        .map(String::from)
        .collect()
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum FramerConfig {
    Identity(IdentityFramerConfig),
    HeadingBoundary(HeadingBoundaryFramerConfig),
    RegexBoundary(RegexBoundaryFramerConfig),
    Jsonpath(JsonPathFramerConfig),
    /// RM-A: agent-memory episode segmentation (gap-based + role/tool
    /// boundary + max-turns/max-words). Mirror of Python
    /// `chunkshop.framers.session_episode.SessionEpisodeFramer`.
    SessionEpisode(SessionEpisodeFramerConfig),
}

impl Default for FramerConfig {
    fn default() -> Self {
        FramerConfig::Identity(IdentityFramerConfig {})
    }
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct IdentityFramerConfig {}

#[derive(Debug, Clone, Deserialize)]
pub struct HeadingBoundaryFramerConfig {
    #[serde(default = "default_heading_pattern")]
    pub pattern: String,
    #[serde(default = "default_true")]
    pub title_from_heading: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RegexBoundaryFramerConfig {
    pub split_pattern: String,
    #[serde(default)]
    pub title_pattern: Option<String>,
    #[serde(default = "default_true")]
    pub body_starts_with_match: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct JsonPathFramerConfig {
    pub row_path: String,
    #[serde(default)]
    pub title_path: Option<String>,
    #[serde(default = "default_jsonpath_body")]
    pub body_path: String,
}

/// RM-A Task 2: gap- and turn-based session episode segmentation.
/// Mirrors Python `SessionEpisodeFramerConfig`. All numeric fields default
/// to match the Python defaults exactly so the same YAML drives both impls.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionEpisodeFramerConfig {
    /// Episode boundary when the gap between consecutive events exceeds
    /// this many seconds. Default 1800 (= 30 min, matches Python).
    #[serde(default = "default_max_gap_seconds")]
    pub max_gap_seconds: u64,
    /// Boundary when an episode reaches this many turns. Default 40.
    #[serde(default = "default_max_turns")]
    pub max_turns: u32,
    /// Boundary when an episode reaches this many words. Default 1200.
    #[serde(default = "default_max_words")]
    pub max_words: u32,
    /// Place a boundary at the role change from non-tool to tool. Default true.
    #[serde(default = "default_true")]
    pub boundary_on_tool: bool,
}

fn default_max_gap_seconds() -> u64 {
    1800
}
fn default_max_turns() -> u32 {
    40
}
fn default_max_words() -> u32 {
    1200
}

fn default_heading_pattern() -> String {
    r"^#+\s".to_string()
}
fn default_true() -> bool {
    true
}
fn default_jsonpath_body() -> String {
    "$".to_string()
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SourceConfig {
    Files(FilesSourceConfig),
    JsonCorpus(JsonCorpusSourceConfig),
    PgTable(PgTableSourceConfig),
    MariadbTable(MariadbTableSourceConfig),
    SqliteTable(SqliteTableSourceConfig),
    Http(HttpSourceConfig),
    S3(S3SourceConfig),
    ClickhouseTable(ClickhouseTableSourceConfig),
    /// RM-A: agent-memory staging-table source. Consumed by the two
    /// `memory/*.yaml` cell presets (realtime + consolidate). Mirror of
    /// Python `chunkshop.sources.session_staging.SessionStagingSource`.
    SessionStaging(SessionStagingSourceConfig),
    /// Library/embedded mode — no automatic iteration. The host application
    /// drives ingestion via `chunkshop::Pipeline::from_yaml(...)` and calls
    /// `pipeline.ingest_text(doc_id, text, metadata)` per document.
    /// `Runner::run_cell` rejects this variant; only `Pipeline` accepts it.
    Inline(InlineSourceConfig),
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct InlineSourceConfig {}

/// Mirrors Python `chunkshop.config.FilesSource`. `on_empty`/`derive`/`cache`
/// (media-ingest fields) now have Rust support (Plan D). `incremental` is
/// still unmodeled for the `files` source specifically (unlike `s3`,
/// `pg_table`, and `http`, which do implement `IncrementalSource`) — that
/// remains a separate, tracked follow-up (see MEDIA-INGEST-ROADMAP.md),
/// not part of this plan. `deny_unknown_fields` still refuses `incremental`
/// with a named error rather than silently full-resyncing.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FilesSourceConfig {
    pub glob: String,
    /// Held as a `String` for source-compatibility with `FilesSource`'s
    /// `id_from.as_str()` dispatch, but the VALUE is validated at
    /// config-load through the `IdFrom` allowlist below — matching
    /// Python's `Literal["path", "stem", "sha1"]` (`config.py:120`), which
    /// rejects `id_from: sha256` at config load rather than mid-run.
    /// `deny_unknown_fields` only validates field *names*; a bare `String`
    /// accepts any value, which is the parity bug this guard closes.
    #[serde(default = "default_id_from", deserialize_with = "deserialize_id_from")]
    pub id_from: String,
    #[serde(default = "default_encoding")]
    pub encoding: String,
    /// What to do when a file that demonstrably held content yields no text.
    /// Mirrors Python's `chunkshop.config.FilesSource.on_empty` — same three
    /// values, same default, same scope rule, same YAML.
    ///
    /// **THE SCOPE RULE.** This setting is reached by exactly two routes,
    /// and "the extracted text was empty" is deliberately NOT one of them:
    ///
    /// 1. the document was routed through the `derive:` block below; or
    /// 2. its deriver made an affirmative `ParseResult::had_content` claim
    ///    that the file held content it failed to extract — a PDF with
    ///    pages, a DOCX with tables, an HTML page built from
    ///    `<img>`/`<svg>`/`<iframe>` and nothing else. This is spec defect
    ///    F5, "empty-derivation is loud, not silent".
    ///
    /// Any other empty document is indexed unconditionally, whatever this
    /// is set to. A zero-byte or whitespace-only `.md` is LEGITIMATELY
    /// empty: bytes went in, no text came out, nothing was lost.
    /// `TextDeriver` has no notion of a content unit and never makes the
    /// route-2 claim, so no text file can reach the loud branch. That
    /// separation is load-bearing — applying a derivation policy to the
    /// plain parser path made an upgrade abort on any corpus containing a
    /// blank file, after `target.mode: overwrite` had already dropped the
    /// table.
    ///
    /// XLSX makes no route-2 claim either: a workbook of empty cells is a
    /// blank template, and neither port's library exposes non-cell content
    /// (charts, images) publicly, so there is no honest signal to raise.
    ///
    /// Route 2 is a deliberate behaviour change from chunkshop 1.0.0-rc6,
    /// where a scanned PDF indexed as a silent empty document. Enforced in
    /// `FilesSource::iter_documents`.
    #[serde(default)]
    pub on_empty: OnEmpty,
    /// `BTreeMap`, not `HashMap`: Python (`files.py:65`) iterates
    /// `cfg.derive.items()` in dict-insertion order to build a per-extension
    /// routing table, so when two pattern keys' extensions overlap the
    /// *later* one wins deterministically. A `HashMap` here would make that
    /// override nondeterministic across runs — `BTreeMap` keeps it stable
    /// (lexicographic key order) even though it won't match Python's
    /// insertion order bit-for-bit; either deterministic order is better
    /// than none for a case that's already an edge case in both languages.
    pub derive: Option<std::collections::BTreeMap<String, DeriverConfig>>,
    #[serde(default)]
    pub cache: DerivationCacheSettings,
}

/// Mirrors Python's `Literal["error", "skip", "index"]` at
/// `config.py:123`. Modeled as an enum (not `String`) so serde rejects an
/// invalid value (e.g. `on_empty: banana`) at config-load time — matching
/// Python's pydantic `Literal` rejection. `deny_unknown_fields` only
/// validates field *names*; a bare `String` field would accept any value
/// silently, which is exactly the parity bug this type prevents.
#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum OnEmpty {
    Error,
    Skip,
    Index,
}

impl Default for OnEmpty {
    fn default() -> Self {
        OnEmpty::Error
    }
}

/// Mirrors Python's `ImageMetaDeriverConfig`/`ClipTagsDeriverConfig` /
/// `DeriverConfig = Annotated[Union[...], Field(discriminator="rung")]`.
/// The discriminator field is `rung`, NOT `type` — unlike every other
/// discriminated union in this file (`SourceConfig`, `ExtractorConfig`,
/// etc.), which tag on `type`. Do not copy that convention here; Python's
/// `config.py:85-103` uses `rung` and the YAML shape must match.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "rung", rename_all = "snake_case")]
pub enum DeriverConfig {
    ImageMeta(ImageMetaDeriverConfig),
    ClipTags(ClipTagsDeriverConfig),
    Ross(RossDeriverConfig),
}

/// `deny_unknown_fields` is load-bearing, not decoration: without it a
/// typo'd key here is silently dropped and the default silently stands,
/// which is exactly the contract CLAUDE.md states ("a typo in YAML produces
/// a validation error, not silent ignore") and Python's `extra="forbid"`
/// enforces. Every nested media config block below carries it for the same
/// reason.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImageMetaDeriverConfig {
    #[serde(default = "default_true")]
    pub path_segments: bool,
    #[serde(default = "default_segment_depth")]
    pub segment_depth: u32,
}

fn default_segment_depth() -> u32 {
    3
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ClipTagsDeriverConfig {
    pub vocabulary: Vec<String>,
    #[serde(default = "default_top_k")]
    pub top_k: usize,
    /// `f64`, NOT `f32`, and that is a parity requirement rather than a
    /// precision preference. This value lands verbatim in the deriver's
    /// `cache_key_parts()` params object, which is canonicalized to JSON
    /// and hashed into `derived.params_hash`. An `f32` widens to `f64` on
    /// the way into `serde_json`, so the shipped default `0.20` serializes
    /// as `0.20000000298023224` in Rust against Python's `0.2` — the two
    /// languages then compute different `params_hash` values for identical
    /// config and silently maintain separate derivation caches. Same
    /// defect class as the `ensure_ascii` canonicalization bug. Python's
    /// `min_score` is a plain `float` (i.e. f64); match it.
    #[serde(default = "default_min_score")]
    pub min_score: f64,
    #[serde(default = "default_vision_model")]
    pub vision_model: String,
    #[serde(default = "default_text_model")]
    pub text_model: String,
}

/// Mirrors Python's `RossDeriverConfig`. Rung 5: the `ross` sibling binary
/// (yonk-labs/ross, Apache-2.0), run as a SUBPROCESS.
///
/// Unlike every other rung, this one is behind NO Cargo feature — there is
/// no compile-time dependency on ross at all, only a binary that must exist
/// when the rung is actually used. That is the whole point of the
/// subprocess shape (NN-6): a text-only user, and a
/// `--no-default-features` build, pay literally nothing for it.
///
/// `--clip`/`--clap` are deliberately NOT configurable in either port:
/// without them and with `--no-llm`, ross emits deterministic facts and no
/// `description` at all, so every document would derive to empty text and
/// trip `on_empty`. See `sources::derivers::ross`.
///
/// `deny_unknown_fields` for the same load-bearing reason as its two
/// siblings above — and specifically because three structs in this file
/// were recently found missing it.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RossDeriverConfig {
    /// Path to (or name of) the ross executable. Default resolves on $PATH.
    #[serde(default = "default_ross_binary")]
    pub binary: String,
    /// Replace ross's vendored 50-label vocabulary (`--labels-file`). Its
    /// CONTENT is hashed into the derivation params, so editing the file
    /// re-derives rather than serving stale cached tags.
    pub labels_file: Option<String>,
    /// Run ross's LLM pass. Off by default: keyless and offline is the
    /// point of this rung.
    #[serde(default)]
    pub llm: bool,
    /// Per-file subprocess timeout, seconds. ross's first run downloads its
    /// CLIP/CLAP weights, which is the slow case this must accommodate.
    #[serde(default = "default_ross_timeout_s")]
    pub timeout_s: u64,
}

fn default_ross_binary() -> String {
    "ross".to_string()
}
fn default_ross_timeout_s() -> u64 {
    120
}

fn default_top_k() -> usize {
    3
}
fn default_min_score() -> f64 {
    0.20
}
fn default_vision_model() -> String {
    "Qdrant/clip-ViT-B-32-vision".to_string()
}
fn default_text_model() -> String {
    "Qdrant/clip-ViT-B-32-text".to_string()
}

/// Mirrors Python's `DerivationCacheSettings` (`config.py:106-114`):
/// `enabled: bool = True`, `root: Optional[str] = None`.
///
/// `Default` is hand-written, NOT derived. `#[derive(Default)]` ignores
/// `#[serde(default = "default_true")]` and uses `bool`'s own `Default`
/// (`false`), while YAML-deserializing an absent `cache:` block uses the
/// serde default (`true`) — those two paths would silently disagree. The
/// hand-written impl below keeps `DerivationCacheSettings::default()` and
/// "no `cache:` key in YAML" in agreement.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DerivationCacheSettings {
    #[serde(default = "default_true")]
    pub enabled: bool,
    pub root: Option<String>,
}

impl Default for DerivationCacheSettings {
    fn default() -> Self {
        Self {
            enabled: true,
            root: None,
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct JsonCorpusSourceConfig {
    pub path: String,
    #[serde(default = "default_documents_key")]
    pub documents_key: String,
    #[serde(default = "default_id_field")]
    pub id_field: String,
    #[serde(default = "default_content_field")]
    pub content_field: String,
    #[serde(default = "default_title_field")]
    pub title_field: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PgTableSourceConfig {
    /// Direct DSN (literal or `${VAR}` interpolated). Takes precedence over
    /// `dsn_env` when both are set. Mirrors Python's `_DsnResolvable.dsn`.
    #[serde(default)]
    pub dsn: Option<String>,
    #[serde(default)]
    pub dsn_env: Option<String>,
    #[serde(rename = "schema")]
    pub schema_name: String,
    pub table: String,
    pub id_column: String,
    pub content_column: String,
    #[serde(default)]
    pub title_column: Option<String>,
    /// Trusted operator-supplied SQL fragment appended after `WHERE`. Mirrors
    /// Python's `pg_table.py` which interpolates this verbatim. NOT validated;
    /// don't expose this field to untrusted YAML authors.
    #[serde(default, rename = "where")]
    pub where_clause: Option<String>,
    /// Extra columns to pull alongside id/content/title and put into each
    /// Document's metadata. Pair with `target.promote_metadata` to surface
    /// specific keys as typed columns in the target table.
    #[serde(default)]
    pub metadata_columns: Vec<String>,
    /// RM-B Task 2 / Python ff01268: optional timestamp column enabling
    /// cursor-based incremental sync. When set, the source implements
    /// `IncrementalSource` with a tuple cursor of shape
    /// `{"after_ts": "<iso ts>", "after_id": "<id>"}`. The tuple ordering
    /// defends against silent row loss when multiple rows commit at the
    /// boundary timestamp.
    #[serde(default)]
    pub updated_at_column: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct MariadbTableSourceConfig {
    /// Direct DSN (literal or `${VAR}` interpolated). Takes precedence over
    /// `dsn_env` when both are set. Mirrors Python's `_DsnResolvable.dsn`.
    #[serde(default)]
    pub dsn: Option<String>,
    #[serde(default)]
    pub dsn_env: Option<String>,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    pub id_column: String,
    pub content_column: String,
    #[serde(default)]
    pub title_column: Option<String>,
    /// Trusted operator-supplied SQL fragment appended after `WHERE`. Same
    /// contract as PgTableSourceConfig.where_clause — NOT validated.
    #[serde(default, rename = "where")]
    pub where_clause: Option<String>,
    #[serde(default)]
    pub metadata_columns: Vec<String>,
}

/// SQLite source. Mirrors `python/src/chunkshop/sources/sqlite_table.py`.
/// `database` is validated as a non-empty ident at config-load (loose parity
/// with Postgres) but ignored at runtime — SQLite has no schema namespace.
#[derive(Debug, Clone, Deserialize)]
pub struct SqliteTableSourceConfig {
    /// Direct DSN (literal or `${VAR}` interpolated). Takes precedence over
    /// `dsn_env` when both are set. Mirrors Python's `_DsnResolvable.dsn`.
    #[serde(default)]
    pub dsn: Option<String>,
    #[serde(default)]
    pub dsn_env: Option<String>,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    pub id_column: String,
    pub content_column: String,
    #[serde(default)]
    pub title_column: Option<String>,
    /// Trusted operator-supplied SQL fragment appended after `WHERE`. Same
    /// contract as PgTableSourceConfig.where_clause — NOT validated.
    #[serde(default, rename = "where")]
    pub where_clause: Option<String>,
    #[serde(default)]
    pub metadata_columns: Vec<String>,
}

/// ClickHouse source. Mirrors `python/src/chunkshop/sources/clickhouse_table.py`.
#[derive(Debug, Clone, Deserialize)]
pub struct ClickhouseTableSourceConfig {
    /// Direct DSN (literal or `${VAR}` interpolated). Takes precedence over
    /// `dsn_env` when both are set. Mirrors Python's `_DsnResolvable.dsn`.
    #[serde(default)]
    pub dsn: Option<String>,
    #[serde(default)]
    pub dsn_env: Option<String>,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    pub id_column: String,
    pub content_column: String,
    #[serde(default)]
    pub title_column: Option<String>,
    /// Trusted operator-supplied SQL fragment appended after `WHERE`. Mirrors
    /// Python's `clickhouse_table.py` which interpolates this verbatim. NOT
    /// validated; don't expose this field to untrusted YAML authors.
    #[serde(default, rename = "where")]
    pub where_clause: Option<String>,
    #[serde(default)]
    pub metadata_columns: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct HttpSourceConfig {
    #[serde(default)]
    pub urls: Vec<String>,
    #[serde(default)]
    pub sitemap: Option<String>,
    /// RM-B Task 4 / Python fcbad65: Depth-bounded link crawl. 0 = current
    /// behavior (fetch only listed URLs + sitemap entries). `>=1` follows
    /// that many link-hops from each seed via `<a href>` extraction.
    /// Capped at 5 to match Python's `ge=0, le=5` Field constraint.
    #[serde(default)]
    pub crawl_depth: u32,
    /// By default the crawler only follows same-host links. Flip to true to
    /// follow off-host links too (still rate-limited, still subject to
    /// `max_pages` and `respect_robots`).
    #[serde(default)]
    pub allow_external: bool,
    /// Minimum delay between outbound requests (per source, not per host).
    /// Default 0.5s matches Python.
    #[serde(default = "default_request_delay_seconds")]
    pub request_delay_seconds: f64,
    /// Enforce robots.txt. One fetch per host, cached. Default true.
    #[serde(default = "default_respect_robots")]
    pub respect_robots: bool,
    /// Hard runaway cap on number of pages fetched per call. Default 1000.
    #[serde(default = "default_max_pages")]
    pub max_pages: u64,
    /// User-Agent header. Default matches Python's `chunkshop/0.6 (+https://…)`.
    #[serde(default = "default_user_agent")]
    pub user_agent: String,
}

fn default_request_delay_seconds() -> f64 {
    0.5
}
fn default_respect_robots() -> bool {
    true
}
fn default_max_pages() -> u64 {
    1000
}
fn default_user_agent() -> String {
    "chunkshop/0.6 (+https://github.com/yonk-labs/chunkshop)".to_string()
}

impl Default for HttpSourceConfig {
    fn default() -> Self {
        Self {
            urls: Vec::new(),
            sitemap: None,
            crawl_depth: 0,
            allow_external: false,
            request_delay_seconds: default_request_delay_seconds(),
            respect_robots: default_respect_robots(),
            max_pages: default_max_pages(),
            user_agent: default_user_agent(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct S3SourceConfig {
    pub bucket: String,
    #[serde(default)]
    pub prefix: String,
    /// Optional S3-compatible endpoint URL (minio, R2, custom). When None,
    /// `object_store` resolves the standard AWS S3 endpoint per the
    /// credential's region.
    #[serde(default)]
    pub endpoint_url: Option<String>,
}

// --- RM-A agent-memory configs (mirror Python SP-A) -------------------------

/// SP-A memory tier — `provisional` (realtime path, supersede=false) or
/// `consolidated` (lazy path, supersede=true).
#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum MemoryTier {
    Provisional,
    Consolidated,
}

/// MemorySink-side block on `TargetConfig::Postgres`. When present, the
/// `load_sink` dispatcher returns a `MemorySink` instead of a plain `PgSink`.
/// Mirror of Python `chunkshop.config.MemoryConfig`. `deny_unknown_fields`
/// per RM-A spec (typos must fail load-time).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryConfig {
    pub tier: MemoryTier,
    /// Default `true` for `consolidated` tier (it should replace prior
    /// provisional rows for the same session); `false` is normal for
    /// `provisional` so realtime cells don't churn the table.
    #[serde(default = "default_memory_supersede")]
    pub supersede: bool,
    /// When None, MemorySink falls back to `source_tag` as the namespace —
    /// matches the Python default.
    #[serde(default)]
    pub namespace: Option<String>,
}

fn default_memory_supersede() -> bool {
    true
}

/// SP-A staging-table source. Reads the chunkshop-owned append-only
/// staging table (`schema.table`, default `public.chunkshop_staging`)
/// and yields one `Document` per session. `mode='realtime'` advances
/// the per-session realtime watermark; `mode='consolidate'` uses a
/// session-level eligibility WHERE so a late event after consolidation
/// triggers a full-staging rebuild (SP-A spec O1 — non-negotiable;
/// see Task 5 of the RM-A plan and Python fix `49861dc`).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionStagingSourceConfig {
    /// Direct DSN (literal or `${VAR}` interpolated at connect time).
    /// At least one of `dsn` / `dsn_env` must be set.
    #[serde(default)]
    pub dsn: Option<String>,
    #[serde(default)]
    pub dsn_env: Option<String>,
    #[serde(default = "default_staging_table")]
    pub staging_table: String,
    #[serde(default = "default_staging_schema")]
    pub staging_schema: String,
    pub mode: SessionStagingMode,
    /// Consolidate-mode: only sessions with `max(event_ts|staged_at) < now() -
    /// min_age_seconds` are selected. Lets quiet sessions consolidate while
    /// active sessions stay provisional. Ignored in realtime mode.
    #[serde(default)]
    pub min_age_seconds: u64,
    /// Cap on yielded sessions per run (None = no cap). Mirrors Python.
    #[serde(default)]
    pub max_sessions: Option<usize>,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum SessionStagingMode {
    Realtime,
    Consolidate,
}

fn default_staging_table() -> String {
    "chunkshop_staging".to_string()
}
fn default_staging_schema() -> String {
    "public".to_string()
}

fn default_id_from() -> String {
    IdFrom::Stem.as_str().to_string()
}

/// The allowlist behind `FilesSourceConfig::id_from`. Mirrors Python's
/// `Literal["path", "stem", "sha1"]`. Modeled as an enum (like `OnEmpty`)
/// so serde produces a named error listing the valid variants.
#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum IdFrom {
    Path,
    Stem,
    Sha1,
}

impl IdFrom {
    pub fn as_str(self) -> &'static str {
        match self {
            IdFrom::Path => "path",
            IdFrom::Stem => "stem",
            IdFrom::Sha1 => "sha1",
        }
    }
}

fn deserialize_id_from<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> std::result::Result<String, D::Error> {
    Ok(IdFrom::deserialize(d)?.as_str().to_string())
}

fn default_encoding() -> String {
    "utf-8".to_string()
}

fn default_documents_key() -> String {
    "documents".to_string()
}
fn default_id_field() -> String {
    "id".to_string()
}
fn default_content_field() -> String {
    "content".to_string()
}
fn default_title_field() -> Option<String> {
    Some("title".to_string())
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ChunkerConfig {
    SentenceAware(SentenceAwareChunkerConfig),
    Hierarchy(HierarchyChunkerConfig),
    FixedOverlap(FixedOverlapChunkerConfig),
    NeighborExpand(NeighborExpandChunkerConfig),
    Semantic(SemanticChunkerConfig),
    SummaryEmbed(SummaryEmbedChunkerConfig),
    HierarchicalSummary(HierarchicalSummaryChunkerConfig),
    /// RM-A: wraps a base chunker to emit `kind=episode` chunks plus
    /// per-triple `kind=fact` chunks from a `Consolidator` callable.
    /// Mirror of Python `chunkshop.chunkers.consolidation.ConsolidationChunker`.
    Consolidation(ConsolidationChunkerConfig),
    /// RM-C: emits one chunk per extracted code symbol (function / class /
    /// method) via tree-sitter. Gated on the `code-aware` feature; per-grammar
    /// dispatch in the factory is gated on `code-aware-<lang>`.
    #[cfg(feature = "code-aware")]
    SymbolAware(SymbolAwareChunkerConfig),
}

impl ChunkerConfig {
    /// Resolve the effective `max_chars` ceiling for this chunker. Wrappers
    /// (`neighbor_expand`, `summary_embed`, `hierarchical_summary`) fall back
    /// to `base.effective_max_chars()` when no explicit override is set.
    /// `fixed_overlap` returns `None` unless the user opted in via `max_chars`.
    /// Mirrors Python's `ChunkerConfig.effective_max_chars` resolver.
    /// Brief SC-003.
    pub fn effective_max_chars(&self) -> Option<usize> {
        match self {
            ChunkerConfig::SentenceAware(c) => Some(c.max_chars),
            ChunkerConfig::Hierarchy(c) => Some(c.max_chars),
            ChunkerConfig::FixedOverlap(c) => c.max_chars,
            ChunkerConfig::Semantic(c) => Some(c.max_chunk_chars),
            ChunkerConfig::NeighborExpand(c) => {
                c.max_chars.or_else(|| c.base.effective_max_chars())
            }
            ChunkerConfig::SummaryEmbed(c) => c.max_chars.or_else(|| c.base.effective_max_chars()),
            ChunkerConfig::HierarchicalSummary(c) => {
                c.max_chars.or_else(|| c.base.effective_max_chars())
            }
            ChunkerConfig::Consolidation(c) => c.base.effective_max_chars(),
            #[cfg(feature = "code-aware")]
            ChunkerConfig::SymbolAware(_) => None,
        }
    }

    /// Borrow the optional `if_oversize` fallback chunker config. Returns
    /// `None` for chunkers that haven't opted in. Brief SC-001.
    pub fn if_oversize(&self) -> Option<&ChunkerConfig> {
        match self {
            ChunkerConfig::SentenceAware(c) => c.if_oversize.as_deref(),
            ChunkerConfig::Hierarchy(c) => c.if_oversize.as_deref(),
            ChunkerConfig::FixedOverlap(c) => c.if_oversize.as_deref(),
            ChunkerConfig::Semantic(c) => c.if_oversize.as_deref(),
            ChunkerConfig::NeighborExpand(c) => c.if_oversize.as_deref(),
            ChunkerConfig::SummaryEmbed(c) => c.if_oversize.as_deref(),
            ChunkerConfig::HierarchicalSummary(c) => c.if_oversize.as_deref(),
            ChunkerConfig::Consolidation(c) => c.if_oversize.as_deref(),
            #[cfg(feature = "code-aware")]
            ChunkerConfig::SymbolAware(_) => None,
        }
    }

    /// Stable lower-snake-case discriminator string for logs/error messages.
    pub fn type_name(&self) -> &'static str {
        match self {
            ChunkerConfig::SentenceAware(_) => "sentence_aware",
            ChunkerConfig::Hierarchy(_) => "hierarchy",
            ChunkerConfig::FixedOverlap(_) => "fixed_overlap",
            ChunkerConfig::NeighborExpand(_) => "neighbor_expand",
            ChunkerConfig::Semantic(_) => "semantic",
            ChunkerConfig::SummaryEmbed(_) => "summary_embed",
            ChunkerConfig::HierarchicalSummary(_) => "hierarchical_summary",
            ChunkerConfig::Consolidation(_) => "consolidation",
            #[cfg(feature = "code-aware")]
            ChunkerConfig::SymbolAware(_) => "symbol_aware",
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct SentenceAwareChunkerConfig {
    #[serde(default = "default_doc_type")]
    pub doc_type: String,
    #[serde(default = "default_max_chars")]
    pub max_chars: usize,
    #[serde(default = "default_min_chars")]
    pub min_chars: usize,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

/// RM-C: config for `SymbolAwareChunker`. Mirrors Python
/// `chunkshop.chunkers.symbol_aware.SymbolAwareChunkerConfig`.
#[cfg(feature = "code-aware")]
#[derive(Debug, Clone, Deserialize, Default)]
pub struct SymbolAwareChunkerConfig {
    /// Optional project_id passed to `code_symbol_node_id` for scoping.
    /// Defaults to "default" to mirror Python.
    #[serde(default)]
    pub project_id: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct HierarchyChunkerConfig {
    #[serde(default = "default_prefix_heading")]
    pub prefix_heading: bool,
    #[serde(default = "default_min_section_chars")]
    pub min_section_chars: usize,
    #[serde(default = "default_max_chars")]
    pub max_chars: usize,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
    /// Optional custom heading regex. When `None` (the default), the chunker
    /// uses the built-in markdown heading pattern (`^#{1,6}\s+.+$`). When
    /// `Some(s)`, `s` is compiled as a `regex::Regex` (eagerly, in
    /// `HierarchyChunker::new`) and used as the section-boundary matcher in
    /// place of the markdown pattern. If the custom regex contains a first
    /// capture group, that group's match is used as the heading text;
    /// otherwise the full match is used. Lets external consumers tune
    /// hierarchy chunking for AsciiDoc, reST, or any custom delimiter shape.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub heading_pattern: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FixedOverlapChunkerConfig {
    #[serde(default = "default_window_words")]
    pub window_words: usize,
    #[serde(default = "default_step_words")]
    pub step_words: usize,
    /// Optional post-hoc char ceiling for emitted chunks. Mirrors Python's
    /// `FixedOverlapChunker.max_chars` added in 0.3.2 (Brief SC-002). When
    /// `None`, behavior is unchanged from 0.3.1 (word-only window). When set,
    /// the chunker pairs with `if_oversize` to fall back over chunks that
    /// exceed this ceiling.
    #[serde(default)]
    pub max_chars: Option<usize>,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct NeighborExpandChunkerConfig {
    pub base: Box<ChunkerConfig>,
    #[serde(default = "default_neighbor_window")]
    pub window: usize,
    /// Explicit char ceiling override. When `None`, the wrapper inherits
    /// `base.effective_max_chars()` (Brief SC-003).
    #[serde(default)]
    pub max_chars: Option<usize>,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

/// RM-A Task 3: ConsolidationChunker wraps a base chunker; emits one
/// `kind=episode` chunk per base chunk and one `kind=fact` chunk per
/// triple returned by the wired `Consolidator`. Mirror of Python
/// `chunkshop.chunkers.consolidation.ConsolidationChunker`.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConsolidationChunkerConfig {
    /// Underlying chunker for episode-text segmentation (typically
    /// `sentence_aware` with a memory-sized ceiling).
    pub base: Box<ChunkerConfig>,
    /// Which consolidator wires the SPO-triple extraction. v1 ships
    /// `mode: extractive` (zero-network default); LLM modes wired by the
    /// host application as Rust trait impls.
    pub consolidator: ConsolidatorConfig,
    /// Hard char cap for `kind=fact` chunks. Default 1200 (Python parity).
    #[serde(default = "default_fact_max_chars")]
    pub fact_max_chars: usize,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

fn default_fact_max_chars() -> usize {
    1200
}

/// Consolidator wiring. `mode: extractive` selects the zero-network Rust
/// default (Task 7). Future v1.x: `mode: llm` once a deterministic LLM
/// seam lands. Custom impls live in host code and aren't selected via
/// YAML — they're injected at `build_chunker` time by the consumer.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub enum ConsolidatorConfig {
    Extractive(ExtractiveConsolidatorConfig),
    Lede(LedeConsolidatorConfig),
}

#[derive(Debug, Clone, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct ExtractiveConsolidatorConfig {}

/// `mode: lede` — salient-sentence propositions via lede 0.5 `key_facts`, with
/// rank-decay confidence. Facts below `confidence_floor` are dropped. Gated
/// behind the `lede` cargo feature (errors at consolidate-time when absent).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LedeConsolidatorConfig {
    #[serde(default = "default_lede_cons_max_facts")]
    pub max_facts: usize,
    #[serde(default)]
    pub confidence_floor: f64,
}
fn default_lede_cons_max_facts() -> usize {
    10
}

#[derive(Debug, Clone, Deserialize)]
pub struct SemanticChunkerConfig {
    #[serde(default = "default_boundary_model")]
    pub boundary_model: String,
    #[serde(default = "default_breakpoint_percentile")]
    pub breakpoint_percentile: u32,
    #[serde(default = "default_min_sents_per_chunk")]
    pub min_sentences_per_chunk: usize,
    #[serde(default = "default_max_chunk_chars")]
    pub max_chunk_chars: usize,
    #[serde(default = "default_sentence_splitter")]
    pub sentence_splitter: String,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

/// Discriminated union over summarizer modes. Mirrors Python's `SummarizerConfig`.
/// Tagged on `mode` (matches the Python YAML).
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case")]
pub enum SummarizerConfig {
    External(ExternalSummarizerConfig),
    Callable(CallableSummarizerConfig),
    Passthrough(PassthroughSummarizerConfig),
}

impl SummarizerConfig {
    /// One of `"external"`, `"callable"`, `"passthrough"` — the value chunkshop
    /// stamps into `metadata.summarizer` for traceability.
    pub fn mode_str(&self) -> &'static str {
        match self {
            SummarizerConfig::External(_) => "external",
            SummarizerConfig::Callable(_) => "callable",
            SummarizerConfig::Passthrough(_) => "passthrough",
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ExternalSummarizerConfig {
    #[serde(default = "default_external_field")]
    pub field: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CallableSummarizerConfig {
    pub module: String,
    #[serde(default = "default_callable_function")]
    pub function: String,
    #[serde(default)]
    pub kwargs: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct PassthroughSummarizerConfig {}

fn default_external_field() -> String {
    "summary".to_string()
}
fn default_callable_function() -> String {
    "summarize".to_string()
}

/// Discriminated union over grouping strategies for HierarchicalSummaryChunker.
/// Mirrors Python's `GroupingConfig`. Tagged on `strategy`.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "strategy", rename_all = "snake_case")]
pub enum GroupingConfig {
    FixedN(FixedNGroupingConfig),
    WordBudget(WordBudgetGroupingConfig),
    SectionAware(SectionAwareGroupingConfig),
}

impl Default for GroupingConfig {
    fn default() -> Self {
        GroupingConfig::FixedN(FixedNGroupingConfig::default())
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct FixedNGroupingConfig {
    #[serde(default = "default_fixed_n")]
    pub n: usize,
}

impl Default for FixedNGroupingConfig {
    fn default() -> Self {
        Self {
            n: default_fixed_n(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct WordBudgetGroupingConfig {
    #[serde(default = "default_word_budget")]
    pub max_words: usize,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct SectionAwareGroupingConfig {}

fn default_fixed_n() -> usize {
    5
}
fn default_word_budget() -> usize {
    2000
}

#[derive(Debug, Clone, Deserialize)]
pub struct SummaryEmbedChunkerConfig {
    pub base: Box<ChunkerConfig>,
    pub summarizer: SummarizerConfig,
    /// Explicit char ceiling override. When `None`, the wrapper inherits
    /// `base.effective_max_chars()` (Brief SC-003).
    #[serde(default)]
    pub max_chars: Option<usize>,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct HierarchicalSummaryChunkerConfig {
    pub base: Box<ChunkerConfig>,
    pub summarizer: SummarizerConfig,
    #[serde(default)]
    pub grouping: GroupingConfig,
    /// Explicit char ceiling override. When `None`, the wrapper inherits
    /// `base.effective_max_chars()` (Brief SC-003). Only fine rows are
    /// checked; coarse rows are exempt by design (Brief SC-005).
    #[serde(default)]
    pub max_chars: Option<usize>,
    #[serde(default)]
    pub if_oversize: Option<Box<ChunkerConfig>>,
}

fn default_window_words() -> usize {
    300
}
fn default_step_words() -> usize {
    150
}
fn default_neighbor_window() -> usize {
    1
}
fn default_boundary_model() -> String {
    "sentence-transformers/all-MiniLM-L6-v2-int8".to_string()
}
fn default_breakpoint_percentile() -> u32 {
    95
}
fn default_min_sents_per_chunk() -> usize {
    3
}
fn default_max_chunk_chars() -> usize {
    2000
}
fn default_sentence_splitter() -> String {
    "naive".to_string()
}

fn default_doc_type() -> String {
    "prose".to_string()
}
fn default_max_chars() -> usize {
    2000
}
fn default_min_chars() -> usize {
    200
}
fn default_prefix_heading() -> bool {
    true
}
fn default_min_section_chars() -> usize {
    100
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum EmbedderConfig {
    Fastembed(FastembedEmbedderConfig),
}

#[derive(Debug, Clone, Deserialize)]
pub struct FastembedEmbedderConfig {
    pub model_name: String,
    pub dim: usize,
    #[serde(default = "default_batch_size")]
    pub batch_size: usize,
    #[serde(default)]
    pub threads: Option<usize>,

    // YAML-driven HF pointer ("BYO embedder"). When `hf_repo` is set, the
    // Rust dispatch routes through the user-defined ONNX path with these
    // values at runtime — no `user_defined_source` edit, no rebuild
    // required. When NOT set, dispatch falls back to the registry
    // (resolve_model_name + user_defined_source for known names).
    #[serde(default)]
    pub hf_repo: Option<String>,
    #[serde(default)]
    pub onnx_path: Option<String>,
    #[serde(default = "default_pooling")]
    pub pooling: String, // "cls" | "mean"
    #[serde(default = "default_additional_files")]
    pub additional_files: Vec<String>,
}

fn default_batch_size() -> usize {
    64
}

fn default_pooling() -> String {
    "cls".to_string()
}

fn default_additional_files() -> Vec<String> {
    vec![
        "tokenizer.json".to_string(),
        "tokenizer_config.json".to_string(),
        "special_tokens_map.json".to_string(),
        "config.json".to_string(),
    ]
}

impl FastembedEmbedderConfig {
    /// True when YAML opted into BYO mode (both hf_repo + onnx_path set).
    pub fn is_byo(&self) -> bool {
        self.hf_repo.is_some() && self.onnx_path.is_some()
    }

    /// Validate field pairing: `hf_repo` and `onnx_path` go together. Called
    /// post-deserialize alongside `validate_ident` in `load_config`.
    pub fn validate(&self) -> Result<()> {
        if self.hf_repo.is_some() != self.onnx_path.is_some() {
            return Err(anyhow!(
                "embedder.hf_repo and embedder.onnx_path must be set together \
                 (BYO mode) or both omitted (registry mode)."
            ));
        }
        if self.hf_repo.is_some() && !matches!(self.pooling.as_str(), "cls" | "mean") {
            return Err(anyhow!(
                "embedder.pooling must be 'cls' or 'mean' for BYO embedders, got {:?}",
                self.pooling
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum TargetConfig {
    Postgres(PostgresTargetConfig),
    Mariadb(MariadbTargetConfig),
    Sqlite(SqliteTargetConfig),
    Clickhouse(ClickhouseTargetConfig),
}

impl TargetConfig {
    /// Post-deserialize validation that crosses field boundaries. Delegates to
    /// the active variant's `validate()`.
    fn validate(&self) -> Result<()> {
        match self {
            TargetConfig::Postgres(t) => t.validate(),
            TargetConfig::Mariadb(t) => t.validate(),
            TargetConfig::Sqlite(t) => t.validate(),
            TargetConfig::Clickhouse(t) => t.validate(),
        }
    }

    /// Variant-agnostic read access to `promote_metadata`.
    pub fn promote_metadata(&self) -> &[PromoteColumn] {
        match self {
            TargetConfig::Postgres(t) => &t.promote_metadata,
            TargetConfig::Mariadb(t) => &t.promote_metadata,
            TargetConfig::Sqlite(t) => &t.promote_metadata,
            TargetConfig::Clickhouse(t) => &t.promote_metadata,
        }
    }

    /// Variant-agnostic mutable access, used by
    /// `CellConfig::apply_default_media_promotions`. Python has one
    /// `TargetConfig` covering all four backends and so needs no such
    /// accessor; Rust models them as four structs behind an enum.
    pub fn promote_metadata_mut(&mut self) -> &mut Vec<PromoteColumn> {
        match self {
            TargetConfig::Postgres(t) => &mut t.promote_metadata,
            TargetConfig::Mariadb(t) => &mut t.promote_metadata,
            TargetConfig::Sqlite(t) => &mut t.promote_metadata,
            TargetConfig::Clickhouse(t) => &mut t.promote_metadata,
        }
    }
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct DocumentStoreConfig {
    #[serde(default)]
    pub enabled: bool,
}

fn validate_no_document_store(documents: &Option<DocumentStoreConfig>) -> Result<()> {
    if documents.as_ref().is_some_and(|d| d.enabled) {
        return Err(anyhow!(
            "target.documents is currently Python/Postgres-only; Rust does not write the companion document table yet"
        ));
    }
    Ok(())
}

#[derive(Debug, Clone, Deserialize)]
pub struct PostgresTargetConfig {
    /// Direct DSN (literal or `${VAR}` interpolated). Takes precedence over
    /// `dsn_env` when both are set. Mirrors Python's `_DsnResolvable.dsn`
    /// (Python models all four target backends as one `TargetConfig`).
    #[serde(default)]
    pub dsn: Option<String>,
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    /// Legacy bool field from 0.3.x — accepted but never preferred. New configs
    /// should use `mode`. Top-level `target.overwrite: true` is still rejected
    /// at config-load by the legacy-form check (Task 13).
    #[serde(default)]
    pub overwrite: bool,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    /// Postgres/pgvector semantic-search metric: cosine, inner_product, or l2.
    #[serde(default = "default_vector_metric")]
    pub vector_metric: String,
    /// `overwrite` (default), `append`, or `create_if_missing`. All three are
    /// implemented in Rust as of MB-3 (sink full-mode parity).
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    /// When true, after upserting chunks for a document, delete any rows for
    /// that document with `seq_num >= len(new_chunks)`. Closes the per-doc
    /// shrink gap (last run wrote 12 chunks; this run writes 8 → drop the 4
    /// orphans inside the same write transaction). Default false to preserve
    /// the historical behavior. See `docs/incremental.md`.
    #[serde(default)]
    pub delete_orphans: bool,
    /// RM-A: when present, `load_sink` returns a MemorySink (extends PgSink
    /// with tier/kind stamping + supersede + soft-invalidate). Mirror of
    /// Python `chunkshop.config.TargetConfig.memory`.
    #[serde(default)]
    pub memory: Option<MemoryConfig>,
    /// Python/Postgres-only document table config. Rust rejects enabled
    /// document stores explicitly so cross-language YAMLs cannot silently
    /// ingest chunks without the companion document rows.
    #[serde(default)]
    pub documents: Option<DocumentStoreConfig>,
}

impl PostgresTargetConfig {
    /// Post-deserialize validation that crosses field boundaries (e.g.
    /// mode/source_tag coupling). Identifier safety is enforced separately in
    /// `load_config` via `validate_ident`.
    fn validate(&self) -> Result<()> {
        if self.mode == "append" && self.source_tag.is_none() {
            return Err(anyhow!(
                "target.mode='append' requires target.source_tag to identify this cell"
            ));
        }
        validate_no_document_store(&self.documents)?;
        if !matches!(
            self.vector_metric.as_str(),
            "cosine" | "inner_product" | "l2"
        ) {
            return Err(anyhow!(
                "target.vector_metric must be one of 'cosine', 'inner_product', or 'l2', got {:?}",
                self.vector_metric
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct MariadbTargetConfig {
    /// Direct DSN (literal or `${VAR}` interpolated). Takes precedence over
    /// `dsn_env` when both are set. Mirrors Python's `_DsnResolvable.dsn`
    /// (Python models all four target backends as one `TargetConfig`).
    #[serde(default)]
    pub dsn: Option<String>,
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    /// Legacy bool field from 0.3.x — accepted but never preferred. Same shape
    /// as PostgresTargetConfig.
    #[serde(default)]
    pub overwrite: bool,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    #[serde(default)]
    pub delete_orphans: bool,
    #[serde(default)]
    pub documents: Option<DocumentStoreConfig>,
}

impl MariadbTargetConfig {
    pub(crate) fn validate(&self) -> Result<()> {
        if self.mode == "append" && self.source_tag.is_none() {
            return Err(anyhow!(
                "target.mode='append' requires target.source_tag to identify this cell"
            ));
        }
        validate_no_document_store(&self.documents)?;
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ClickhouseTargetConfig {
    /// Direct DSN (literal or `${VAR}` interpolated). Takes precedence over
    /// `dsn_env` when both are set. Mirrors Python's `_DsnResolvable.dsn`
    /// (Python models all four target backends as one `TargetConfig`).
    #[serde(default)]
    pub dsn: Option<String>,
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    /// On ClickHouse, `delete_orphans: true` is a NO-OP that emits a single
    /// `tracing::warn!` per process. CH's `ALTER TABLE ... DELETE` is async
    /// and breaks chunkshop's per-document atomic write contract.
    #[serde(default)]
    pub delete_orphans: bool,
    /// Optional engine override. When `None`, the sink emits
    /// `MergeTree() ORDER BY (id)`. To opt into lazy dedup, set
    /// `"ReplacingMergeTree(created_at) ORDER BY (id)"`. Validated against
    /// `CLICKHOUSE_ENGINE_RE` at config-load — a Rust-only hardening relative
    /// to Python which interpolates the field raw.
    #[serde(default)]
    pub engine: Option<String>,
    #[serde(default)]
    pub documents: Option<DocumentStoreConfig>,
}

impl ClickhouseTargetConfig {
    fn validate(&self) -> Result<()> {
        if self.mode == "append" && self.source_tag.is_none() {
            return Err(anyhow!(
                "target.mode='append' requires target.source_tag to identify this cell"
            ));
        }
        if let Some(e) = &self.engine {
            let re = Regex::new(CLICKHOUSE_ENGINE_RE).unwrap();
            if !re.is_match(e) {
                return Err(anyhow!(
                    "target.engine {e:?} not in allowlist. Accepted shapes: \
                     'MergeTree', 'MergeTree()', 'ReplacingMergeTree(<col>)', \
                     each optionally followed by ' ORDER BY <expr>'. Custom engines \
                     are not supported in v0.4 — file an issue if you need one."
                ));
            }
        }
        validate_no_document_store(&self.documents)?;
        Ok(())
    }
}

/// SQLite target. Mirrors Python's `chunkshop.config.SqliteTarget`.
/// `database` is validated as a non-empty ident at config-load (loose parity
/// with Postgres) but ignored at runtime — SQLite has no schema namespace.
/// `target.hnsw=true` is a no-op on SQLite (sqlite-vec is brute-force KNN);
/// the sink emits a one-time process-level warning when set.
#[derive(Debug, Clone, Deserialize)]
pub struct SqliteTargetConfig {
    /// Direct DSN (literal or `${VAR}` interpolated) — path to the SQLite
    /// file (or `:memory:`), or a `${VAR}` reference to one. Takes
    /// precedence over `dsn_env` when both are set. Mirrors Python's
    /// `_DsnResolvable.dsn` (Python models all four target backends as one
    /// `TargetConfig`). One of `dsn` / `dsn_env` is required.
    #[serde(default)]
    pub dsn: Option<String>,
    /// Env var holding the path to the SQLite file (or `:memory:`).
    #[serde(default)]
    pub dsn_env: Option<String>,
    #[serde(rename = "database")]
    pub database_name: String,
    pub table: String,
    /// Legacy bool from 0.3.x — accepted but never preferred. New configs use `mode`.
    #[serde(default)]
    pub overwrite: bool,
    #[serde(default = "default_hnsw")]
    pub hnsw: bool,
    /// `overwrite` (default), `append`, or `create_if_missing`.
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub source_tag: Option<String>,
    #[serde(default)]
    pub promote_metadata: Vec<PromoteColumn>,
    #[serde(default)]
    pub force_overwrite: bool,
    /// Mirror PostgresTargetConfig.delete_orphans. Same per-doc-shrink semantics.
    #[serde(default)]
    pub delete_orphans: bool,
    #[serde(default)]
    pub documents: Option<DocumentStoreConfig>,
}

impl SqliteTargetConfig {
    pub(crate) fn validate(&self) -> Result<()> {
        require_dsn_or_dsn_env(&self.dsn, &self.dsn_env, "target")?;
        if self.mode == "append" && self.source_tag.is_none() {
            return Err(anyhow!(
                "target.mode='append' requires target.source_tag to identify this cell"
            ));
        }
        validate_no_document_store(&self.documents)?;
        Ok(())
    }
}

fn default_dsn_env() -> String {
    "CHUNKSHOP_DSN".to_string()
}
fn default_hnsw() -> bool {
    true
}

fn default_vector_metric() -> String {
    "cosine".to_string()
}
fn default_mode() -> String {
    "overwrite".to_string()
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct RuntimeConfig {
    #[serde(default)]
    pub omp_num_threads: Option<usize>,
    #[serde(default)]
    pub doc_limit: Option<usize>,
    #[serde(default)]
    pub log_path: Option<String>,
    #[serde(default)]
    pub heartbeat_every: Option<usize>,
    /// "text" (default) or "json" — controls the CLI's tracing-subscriber
    /// formatter. JSON emits one structured event per line for log aggregators.
    #[serde(default = "default_log_format")]
    pub log_format: String,
}

fn default_log_format() -> String {
    "text".to_string()
}

/// Validate identifier against Python's regex: `^[a-z_][a-z0-9_]*$`.
pub(crate) fn validate_ident(name: &str, field: &str) -> Result<()> {
    let re = Regex::new(r"^[a-z_][a-z0-9_]*$").unwrap();
    if !re.is_match(name) {
        return Err(anyhow!(
            "{field} must match ^[a-z_][a-z0-9_]*$, got {name:?}"
        ));
    }
    Ok(())
}

/// Pre-deserialize legacy-form rejection (V4-SC-006).
///
/// Walks the raw YAML for known 0.3.x field/value patterns and emits a
/// migration-friendly error when found. Without this pass, serde's default
/// errors are cryptic ("unknown variant `pgvector`") or absent (silently
/// accepted legacy fields).
fn reject_legacy_forms(yaml: &serde_yaml_ng::Value) -> Result<()> {
    let target = yaml.get("target").and_then(|v| v.as_mapping());
    let Some(target) = target else {
        return Ok(()); // No target block; nothing to validate.
    };

    if let Some(t) = target.get("type").and_then(|v| v.as_str()) {
        if t == "pgvector" {
            return Err(anyhow!(
                "target.type 'pgvector' was renamed to 'postgres' in v0.4.0. Update your YAML."
            ));
        }
    }
    if target.get("schema").is_some() {
        return Err(anyhow!(
            "target.schema was renamed to target.database in v0.4.0. Update your YAML."
        ));
    }
    if let Some(o) = target.get("overwrite") {
        if matches!(o.as_bool(), Some(true)) {
            return Err(anyhow!(
                "target.overwrite: true was replaced by target.mode: 'overwrite' in v0.4.0. \
                 Update your YAML."
            ));
        }
    }
    Ok(())
}

pub fn load_config(path: &Path) -> Result<CellConfig> {
    let text = std::fs::read_to_string(path)
        .with_context(|| format!("reading config {}", path.display()))?;

    // V4-SC-006: reject 0.3.x legacy YAML shapes with friendly errors before
    // typed deserialization (which would emit cryptic "unknown variant" errors).
    let raw_value: serde_yaml_ng::Value = serde_yaml_ng::from_str(&text)
        .with_context(|| format!("parsing YAML at {}", path.display()))?;
    reject_legacy_forms(&raw_value)?;

    let cfg: CellConfig = serde_yaml_ng::from_str(&text)
        .with_context(|| format!("parsing YAML {}", path.display()))?;
    match &cfg.target {
        TargetConfig::Postgres(t) => {
            validate_ident(&t.database_name, "target.database")?;
            validate_ident(&t.table, "target.table")?;
            if let Some(tag) = &t.source_tag {
                validate_ident(tag, "target.source_tag")?;
            }
        }
        TargetConfig::Mariadb(t) => {
            validate_ident(&t.database_name, "target.database")?;
            validate_ident(&t.table, "target.table")?;
            if let Some(tag) = &t.source_tag {
                validate_ident(tag, "target.source_tag")?;
            }
        }
        TargetConfig::Sqlite(t) => {
            validate_ident(&t.database_name, "target.database")?;
            validate_ident(&t.table, "target.table")?;
            if let Some(tag) = &t.source_tag {
                validate_ident(tag, "target.source_tag")?;
            }
        }
        TargetConfig::Clickhouse(t) => {
            validate_ident(&t.database_name, "target.database")?;
            validate_ident(&t.table, "target.table")?;
            if let Some(tag) = &t.source_tag {
                validate_ident(tag, "target.source_tag")?;
            }
        }
    }
    if let SourceConfig::PgTable(p) = &cfg.source {
        require_dsn_or_dsn_env(&p.dsn, &p.dsn_env, "source")?;
        validate_ident(&p.schema_name, "source.schema")?;
        validate_ident(&p.table, "source.table")?;
        validate_ident(&p.id_column, "source.id_column")?;
        validate_ident(&p.content_column, "source.content_column")?;
        if let Some(tc) = &p.title_column {
            validate_ident(tc, "source.title_column")?;
        }
        // `where_clause` is intentionally NOT validated — see PgTableSourceConfig docstring.
    }
    if let SourceConfig::MariadbTable(p) = &cfg.source {
        require_dsn_or_dsn_env(&p.dsn, &p.dsn_env, "source")?;
        validate_ident(&p.database_name, "source.database")?;
        validate_ident(&p.table, "source.table")?;
        validate_ident(&p.id_column, "source.id_column")?;
        validate_ident(&p.content_column, "source.content_column")?;
        if let Some(tc) = &p.title_column {
            validate_ident(tc, "source.title_column")?;
        }
        // `where_clause` intentionally NOT validated — same contract as PgTableSourceConfig.
    }
    if let SourceConfig::SqliteTable(s) = &cfg.source {
        require_dsn_or_dsn_env(&s.dsn, &s.dsn_env, "source")?;
        validate_ident(&s.database_name, "source.database")?;
        validate_ident(&s.table, "source.table")?;
        validate_ident(&s.id_column, "source.id_column")?;
        validate_ident(&s.content_column, "source.content_column")?;
        if let Some(tc) = &s.title_column {
            validate_ident(tc, "source.title_column")?;
        }
        // `where_clause` intentionally NOT validated — same contract as PgTableSourceConfig.
    }
    if let SourceConfig::ClickhouseTable(p) = &cfg.source {
        require_dsn_or_dsn_env(&p.dsn, &p.dsn_env, "source")?;
        validate_ident(&p.database_name, "source.database")?;
        validate_ident(&p.table, "source.table")?;
        validate_ident(&p.id_column, "source.id_column")?;
        validate_ident(&p.content_column, "source.content_column")?;
        if let Some(tc) = &p.title_column {
            validate_ident(tc, "source.title_column")?;
        }
        for mc in &p.metadata_columns {
            validate_ident(mc, "source.metadata_columns")?;
        }
        // `where_clause` is intentionally NOT validated — see ClickhouseTableSourceConfig docstring.
    }
    cfg.target.validate()?;
    validate_chunker_config(&cfg.chunker)?;
    match &cfg.embedder {
        EmbedderConfig::Fastembed(e) => e.validate()?,
    }
    Ok(cfg)
}

/// Cross-field validation for chunker configs (recursive, walks any
/// `Box<ChunkerConfig>` base fields). Mirrors Python's pydantic model
/// validators that fire at config-load time.
fn validate_chunker_config(c: &ChunkerConfig) -> Result<()> {
    // Brief SC-001: `if_oversize` without an effective ceiling is nonsensical
    // — there's nothing to compare against. Reject at config-load.
    if c.if_oversize().is_some() && c.effective_max_chars().is_none() {
        return Err(anyhow!(
            "chunker {:?} has `if_oversize` set but no effective `max_chars` ceiling. \
             Either set `max_chars` on this chunker (or on its `base` for wrappers), \
             or remove `if_oversize`.",
            c.type_name()
        ));
    }
    // Recurse into the fallback chunker config so nested chains are validated.
    if let Some(nested) = c.if_oversize() {
        validate_chunker_config(nested)?;
    }
    match c {
        ChunkerConfig::SentenceAware(_)
        | ChunkerConfig::Hierarchy(_)
        | ChunkerConfig::FixedOverlap(_)
        | ChunkerConfig::Semantic(_) => Ok(()),
        ChunkerConfig::NeighborExpand(c) => validate_chunker_config(&c.base),
        ChunkerConfig::SummaryEmbed(c) => validate_chunker_config(&c.base),
        ChunkerConfig::HierarchicalSummary(c) => {
            // Mirror Python's _section_aware_requires_hierarchy_base: when
            // grouping is section_aware, the base chunker MUST be hierarchy.
            if matches!(c.grouping, GroupingConfig::SectionAware(_)) {
                let base_type_name = c.base.type_name();
                if base_type_name != "hierarchy" {
                    return Err(anyhow!(
                        "hierarchical_summary with strategy='section_aware' requires \
                         base.type='hierarchy', got {base_type_name:?}"
                    ));
                }
            }
            validate_chunker_config(&c.base)
        }
        ChunkerConfig::Consolidation(c) => validate_chunker_config(&c.base),
        #[cfg(feature = "code-aware")]
        ChunkerConfig::SymbolAware(_) => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_yaml(body: &str) -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!(
            "chunkshop-rs-cfg-{}.yaml",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::write(&path, body).unwrap();
        path
    }

    #[test]
    fn rejects_append_without_source_tag() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: append, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("source_tag"),
            "expected source_tag mention, got: {err}"
        );
    }

    #[test]
    fn rejects_enabled_document_store_until_rust_parity_exists() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: postgres
  dsn_env: D
  database: s
  table: chunks
  mode: overwrite
  hnsw: false
  documents:
    enabled: true
    table: documents
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("Python/Postgres-only") && err.contains("target.documents"),
            "expected document-store parity complaint, got: {err}"
        );
    }

    #[test]
    fn rejects_invalid_promote_type() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: postgres
  dsn_env: D
  database: s
  table: t
  mode: overwrite
  hnsw: false
  promote_metadata:
    - { path: entities.ORG, type: bogus_type }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("type"),
            "expected promote_metadata type complaint, got: {err}"
        );
    }

    #[test]
    fn rejects_invalid_promote_path() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: postgres
  dsn_env: D
  database: s
  table: t
  mode: overwrite
  hnsw: false
  promote_metadata:
    - { path: "0entities.ORG", type: text }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("path"),
            "expected promote_metadata path complaint, got: {err}"
        );
    }

    #[test]
    fn promote_column_name_lowercases_and_double_underscores() {
        let pc: PromoteColumn =
            serde_yaml_ng::from_str("{ path: entities.ORG, type: \"text[]\" }").unwrap();
        assert_eq!(pc.column_name(), "entities__org");
    }

    #[test]
    fn parses_promote_metadata_into_typed_vec() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: postgres
  dsn_env: D
  database: s
  table: t
  mode: overwrite
  hnsw: false
  promote_metadata:
    - { path: heading, type: text }
    - { path: entities.ORG, type: "text[]" }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        let TargetConfig::Postgres(t) = &cfg.target else {
            panic!("expected Postgres target");
        };
        assert_eq!(t.promote_metadata.len(), 2);
        assert_eq!(t.promote_metadata[0].path, "heading");
        assert_eq!(t.promote_metadata[0].type_, "text");
        assert_eq!(t.promote_metadata[1].column_name(), "entities__org");
    }

    // --- FilesSourceConfig deny_unknown_fields (media-ingest field parity) --

    #[test]
    fn valid_files_source_with_glob_id_from_encoding_still_deserializes() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem, encoding: utf-8 }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("valid files source must still load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        assert_eq!(f.glob, "x");
        assert_eq!(f.id_from, "stem");
        assert_eq!(f.encoding, "utf-8");
    }

    #[test]
    fn valid_files_source_with_only_glob_still_deserializes() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x" }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("glob-only files source must still load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        assert_eq!(f.glob, "x");
        // Defaults still apply.
        assert_eq!(f.id_from, "stem");
        assert_eq!(f.encoding, "utf-8");
    }

    #[test]
    fn files_source_derive_field_now_loads_correctly() {
        // Was `rejects_files_source_derive_field` before Plan D — the
        // interim-guard test is now obsolete because real support landed.
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    "png|jpg":
      rung: image_meta
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("derive block must now load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        let derive = f.derive.as_ref().expect("derive must be Some");
        assert!(derive.contains_key("png|jpg"));
    }

    /// Rung 5's YAML must be byte-identical across ports, so this pins the
    /// field names and the defaults against Python's `RossDeriverConfig`.
    #[test]
    fn ross_rung_loads_with_pythons_field_names_and_defaults() {
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    "png|wav|mp4":
      rung: ross
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let cfg = load_config(&write_yaml(yaml)).expect("rung: ross must load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        let DeriverConfig::Ross(c) = &f.derive.as_ref().unwrap()["png|wav|mp4"] else {
            panic!("expected the Ross variant");
        };
        assert_eq!(c.binary, "ross");
        assert_eq!(c.labels_file, None);
        assert!(!c.llm);
        assert_eq!(c.timeout_s, 120);
    }

    #[test]
    fn ross_rung_accepts_every_field_and_rejects_a_typo() {
        let ok = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    "png":
      rung: ross
      binary: /opt/ross
      labels_file: ./vocab.txt
      llm: true
      timeout_s: 30
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let cfg = load_config(&write_yaml(ok)).expect("all fields must load");
        let SourceConfig::Files(f) = &cfg.source else { panic!() };
        let DeriverConfig::Ross(c) = &f.derive.as_ref().unwrap()["png"] else {
            panic!("expected the Ross variant");
        };
        assert_eq!(c.binary, "/opt/ross");
        assert_eq!(c.labels_file.as_deref(), Some("./vocab.txt"));
        assert!(c.llm);
        assert_eq!(c.timeout_s, 30);

        // `deny_unknown_fields` is load-bearing here: three sibling structs
        // in this file were recently found missing it, and a typo'd key
        // that silently keeps the default is exactly the divergence
        // chunkshop's "a typo in YAML is a validation error" contract
        // forbids.
        let typo = ok.replace("binary: /opt/ross", "binry: /opt/ross");
        let err = format!("{:#}", load_config(&write_yaml(&typo)).unwrap_err());
        assert!(err.contains("binry"), "{err}");
    }

    /// Rung 5 is behind no Cargo feature — it shells out. Preflight must
    /// therefore accept it in EVERY build, including one with no `media`.
    #[test]
    fn preflight_accepts_the_ross_rung_in_any_build() {
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    "png":
      rung: ross
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        load_config(&write_yaml(yaml))
            .unwrap()
            .preflight_derive()
            .expect("rung 5 needs no Cargo feature");
    }

    #[test]
    fn files_source_on_empty_field_now_loads_correctly() {
        // Was `rejects_files_source_on_empty_field`.
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", on_empty: skip }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("on_empty must now load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        assert_eq!(f.on_empty, OnEmpty::Skip);
    }

    #[test]
    fn files_source_cache_field_now_loads_correctly() {
        // Was `rejects_files_source_cache_field`.
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  cache:
    enabled: false
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("cache block must now load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        assert!(!f.cache.enabled);
    }

    #[test]
    fn typo_in_derive_block_is_still_rejected() {
        // deny_unknown_fields must still catch config typos INSIDE the now-
        // modeled derive block — this is the equivalent of Python's
        // test_typo_in_derive_block_is_rejected (test_derive_config.py).
        //
        // The original spelling of this test typed `vocabulary` as
        // `vocabluary`, which made the REQUIRED field missing — so it
        // passed on a "missing field" error and never exercised the
        // unknown-field path at all. It went green for two months while
        // `ClipTagsDeriverConfig` had no `deny_unknown_fields` and silently
        // dropped every unknown key. The unknown key below sits alongside a
        // complete, valid set of required fields, so ONLY
        // `deny_unknown_fields` can reject it.
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    png:
      rung: clip_tags
      vocabulary: ["x"]
      top_kk: 5
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("unknown field `top_kk`"),
            "must fail on the UNKNOWN FIELD, not on a missing required one: {err}"
        );
    }

    #[test]
    fn unknown_field_in_image_meta_block_is_rejected() {
        // `ImageMetaDeriverConfig` has no required fields, so a missing-field
        // error can never stand in for the unknown-field check here.
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    png:
      rung: image_meta
      path_segments: true
      segment_depth: 3
      bogus_field: 1
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("unknown field `bogus_field`"),
            "image_meta block must reject unknown fields: {err}"
        );
    }

    #[test]
    fn unknown_field_in_cache_block_is_rejected() {
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  cache:
    enabled: true
    encrypt: true
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("unknown field `encrypt`"),
            "cache block must reject unknown fields: {err}"
        );
    }

    #[test]
    fn invalid_id_from_value_is_rejected_at_config_load() {
        // Python's `Literal["path","stem","sha1"]` rejects this at config
        // load. Rust used to accept it and only fail per-path inside
        // `FilesSource::id_for` — i.e. mid-run, after DDL.
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: sha256 }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("unknown variant `sha256`"),
            "id_from must be rejected at config load: {err}"
        );
    }

    #[test]
    fn all_three_id_from_values_still_parse() {
        for v in ["path", "stem", "sha1"] {
            let yaml = format!(
                r#"
cell_name: t
source: {{ type: files, glob: "x", id_from: {v} }}
chunker: {{ type: sentence_aware }}
embedder: {{ type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }}
target: {{ type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }}
"#
            );
            let path = write_yaml(&yaml);
            let cfg = load_config(&path).expect("valid id_from must load");
            let SourceConfig::Files(f) = &cfg.source else {
                panic!("expected Files source");
            };
            assert_eq!(f.id_from, v);
        }
    }

    // ---- default media promotions (parity with Python's
    // `CellConfig._promote_media_origin_columns`) -------------------------

    /// The shipped sample is the artifact both ports actually run, so it is
    /// the right subject for the parity assertion. The mirror on the Python
    /// side is
    /// `python/tests/chunkshop/test_derive_config.py::
    /// test_shipped_images_keyless_sample_promotes_media_origin_columns`,
    /// which asserts the SAME (path, type, column_name) triples against the
    /// SAME file — so if either port drifts, exactly one suite goes red.
    #[test]
    fn shipped_images_keyless_sample_promotes_media_origin_columns() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../docs/samples/images-keyless.yaml");
        let cfg = load_config(&path).expect("shipped sample must load");
        let got: Vec<(String, String, String)> = cfg
            .target
            .promote_metadata()
            .iter()
            .map(|p| (p.path.clone(), p.type_.clone(), p.column_name()))
            .collect();
        assert_eq!(
            got,
            vec![
                (
                    "content_origin".to_string(),
                    "text".to_string(),
                    "content_origin".to_string()
                ),
                (
                    "derived_rung".to_string(),
                    "text".to_string(),
                    "derived_rung".to_string()
                ),
            ]
        );
    }

    #[test]
    fn no_derive_block_means_no_default_promotions() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x" }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        assert!(cfg.target.promote_metadata().is_empty());
    }

    #[test]
    fn user_promotions_win_and_defaults_only_fill_gaps() {
        // Python appends the missing defaults AFTER the user's entries and
        // de-dupes by `column_name`, never by raw `path` — so a user entry
        // spelled `Content_Origin` (same column) suppresses that default
        // while `derived_rung` is still added.
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    png:
      rung: image_meta
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: postgres
  dsn_env: D
  database: s
  table: t
  mode: overwrite
  hnsw: false
  promote_metadata:
    - { path: Content_Origin, type: text }
    - { path: heading, type: text }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        let cols: Vec<String> = cfg
            .target
            .promote_metadata()
            .iter()
            .map(|p| p.column_name())
            .collect();
        assert_eq!(cols, vec!["content_origin", "heading", "derived_rung"]);
        // The user's original spelling survives untouched.
        assert_eq!(cfg.target.promote_metadata()[0].path, "Content_Origin");
    }

    #[test]
    fn applying_default_media_promotions_twice_is_idempotent() {
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    png:
      rung: image_meta
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let mut cfg = load_config(&path).expect("load");
        cfg.apply_default_media_promotions();
        cfg.apply_default_media_promotions();
        assert_eq!(cfg.target.promote_metadata().len(), 2);
    }

    // ---- preflight (runs before the sink's destructive DDL) -------------

    #[test]
    fn preflight_rejects_ambiguous_derive_keys() {
        // "png|jpg" and "jpg|webp" both claim "jpg". Rust used to stash
        // this and only surface it from `iter_documents()` — i.e. after
        // `create_table()` had already dropped the target table.
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    "png|jpg":
      rung: image_meta
    "jpg|webp":
      rung: image_meta
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("config itself is well-formed");
        let err = format!("{:#}", cfg.preflight_derive().unwrap_err());
        assert!(
            err.contains("reachable from two different `derive` keys"),
            "expected the ambiguity error, got: {err}"
        );
    }

    #[test]
    #[cfg(not(all(feature = "media", feature = "embedder-hub")))]
    fn preflight_rejects_a_rung_whose_cargo_feature_is_absent() {
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    png:
      rung: clip_tags
      vocabulary: ["a cat"]
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("config itself is well-formed");
        let err = format!("{:#}", cfg.preflight_derive().unwrap_err());
        assert!(
            err.contains("requires the `media,embedder-hub` Cargo feature"),
            "expected the missing-feature error, got: {err}"
        );
    }

    #[test]
    #[cfg(all(feature = "media", feature = "embedder-hub"))]
    fn preflight_accepts_a_rung_whose_cargo_feature_is_present() {
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    png:
      rung: clip_tags
      vocabulary: ["a cat"]
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("config itself is well-formed");
        cfg.preflight_derive().expect("feature is built in");
    }

    #[test]
    fn preflight_is_a_noop_without_a_derive_block() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x" }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        load_config(&path).unwrap().preflight_derive().unwrap();
    }

    #[test]
    fn min_score_is_f64_so_the_shipped_default_canonicalizes_like_python() {
        // The whole point of holding `min_score` as f64: this value flows
        // into `ClipTagsDeriver::cache_key_parts()`, gets canonicalized to
        // JSON, and is hashed into `derived.params_hash`. As an f32 the
        // shipped default rendered as `0.20000000298023224` while Python
        // rendered `0.2`, splitting the derivation cache across ports.
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  derive:
    png:
      rung: clip_tags
      vocabulary: ["a cat"]
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        let DeriverConfig::ClipTags(c) = &f.derive.as_ref().unwrap()["png"] else {
            panic!("expected clip_tags");
        };
        assert_eq!(
            serde_json::to_string(&serde_json::json!({ "min_score": c.min_score })).unwrap(),
            r#"{"min_score":0.2}"#
        );
    }

    #[test]
    fn on_empty_default_is_error_when_absent() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x" }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        assert_eq!(f.on_empty, OnEmpty::Error);
        assert_eq!(OnEmpty::default(), OnEmpty::Error);
    }

    #[test]
    fn on_empty_all_three_valid_values_parse() {
        for (yaml_value, expected) in [
            ("error", OnEmpty::Error),
            ("skip", OnEmpty::Skip),
            ("index", OnEmpty::Index),
        ] {
            let yaml = format!(
                r#"
cell_name: t
source: {{ type: files, glob: "x", on_empty: {yaml_value} }}
chunker: {{ type: sentence_aware }}
embedder: {{ type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }}
target: {{ type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }}
"#
            );
            let path = write_yaml(&yaml);
            let cfg = load_config(&path).unwrap_or_else(|e| {
                panic!("on_empty: {yaml_value} must load, got error: {e:#}")
            });
            let SourceConfig::Files(f) = &cfg.source else {
                panic!("expected Files source");
            };
            assert_eq!(f.on_empty, expected, "on_empty: {yaml_value}");
        }
    }

    #[test]
    fn on_empty_invalid_value_is_rejected() {
        // A bare `String` field would silently accept this — the enum must
        // reject it at config-load time, matching Python's pydantic
        // `Literal["error", "skip", "index"]` rejection.
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", on_empty: banana }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = load_config(&path).expect_err("on_empty: banana must be rejected");
        let msg = format!("{err:#}");
        assert!(
            msg.contains("on_empty") || msg.to_lowercase().contains("banana") || msg.contains("unknown variant"),
            "expected error to reject the invalid on_empty value, got: {msg}"
        );
    }

    #[test]
    fn derivation_cache_settings_default_agrees_with_absent_yaml_block() {
        // Both paths must yield `enabled: true` — this is the exact
        // footgun a derived `Default` impl would reintroduce (see
        // `DerivationCacheSettings`'s doc comment).
        assert!(DerivationCacheSettings::default().enabled);

        let yaml = r#"
cell_name: t
source: { type: files, glob: "x" }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        let SourceConfig::Files(f) = &cfg.source else {
            panic!("expected Files source");
        };
        assert!(
            f.cache.enabled,
            "no cache: block in YAML must also default to enabled: true"
        );
    }

    #[test]
    fn rejects_files_source_incremental_field() {
        // Rust does not implement `IncrementalSource` for the `files` source
        // (only s3/pg_table/http). Before this test, a `files` + `incremental`
        // YAML silently ran full-resync every time instead of erroring — this
        // is a deliberate behavior change: refusing beats silently ignoring a
        // sync policy Rust cannot honor.
        let yaml = r#"
cell_name: t
source:
  type: files
  glob: "x"
  incremental:
    cursor_path: "/tmp/cursor.json"
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("incremental"),
            "expected error to name the unsupported `incremental` field, got: {err}"
        );
    }

    #[test]
    fn postgres_target_vector_metric_defaults_and_validates() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        let TargetConfig::Postgres(t) = &cfg.target else {
            panic!("expected Postgres target");
        };
        assert_eq!(t.vector_metric, "cosine");

        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false, vector_metric: l2 }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        let TargetConfig::Postgres(t) = &cfg.target else {
            panic!("expected Postgres target");
        };
        assert_eq!(t.vector_metric, "l2");

        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false, vector_metric: manhattan }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("vector_metric"),
            "expected vector_metric error, got: {err}"
        );
    }

    #[test]
    fn rejects_section_aware_without_hierarchy_base() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker:
  type: hierarchical_summary
  base: { type: sentence_aware }
  summarizer: { mode: passthrough }
  grouping: { strategy: section_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("section_aware") && err.contains("hierarchy"),
            "expected section_aware/hierarchy mention, got: {err}"
        );
    }

    #[test]
    fn parses_if_oversize_on_every_chunker_variant() {
        // Brief SC-001: every chunker variant accepts an optional if_oversize
        // pointing at any other chunker config.
        for kind in [
            "sentence_aware",
            "hierarchy",
            "fixed_overlap",
            "neighbor_expand",
            "semantic",
            "summary_embed",
            "hierarchical_summary",
        ] {
            let chunker_yaml = match kind {
                "sentence_aware" => "{ type: sentence_aware }".to_string(),
                "hierarchy" => "{ type: hierarchy }".to_string(),
                "fixed_overlap" => "{ type: fixed_overlap, max_chars: 1500 }".to_string(),
                "neighbor_expand" => {
                    "{ type: neighbor_expand, base: { type: hierarchy } }".to_string()
                }
                "semantic" => "{ type: semantic }".to_string(),
                "summary_embed" => "{ type: summary_embed, base: { type: hierarchy }, summarizer: { mode: passthrough } }".to_string(),
                "hierarchical_summary" => "{ type: hierarchical_summary, base: { type: hierarchy }, summarizer: { mode: passthrough } }".to_string(),
                _ => unreachable!(),
            };
            // Inline a minimal cell config and inject if_oversize on the chunker.
            let yaml = format!(
                r#"
cell_name: t
source: {{ type: files, glob: "x", id_from: stem }}
chunker:
  type: {kind}
  {extra}
  if_oversize:
    type: fixed_overlap
    window_words: 100
    step_words: 100
    max_chars: 500
embedder: {{ type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }}
target: {{ type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }}
"#,
                kind = kind,
                extra = match kind {
                    "fixed_overlap" => "max_chars: 1500".to_string(),
                    "neighbor_expand" => "base: { type: hierarchy }".to_string(),
                    "summary_embed" =>
                        "base: { type: hierarchy }\n  summarizer: { mode: passthrough }".to_string(),
                    "hierarchical_summary" =>
                        "base: { type: hierarchy }\n  summarizer: { mode: passthrough }".to_string(),
                    _ => "".to_string(),
                }
            );
            let _ = chunker_yaml; // suppress unused-var lint in this branch
            let path = write_yaml(&yaml);
            let cfg = load_config(&path).unwrap_or_else(|e| {
                panic!("if_oversize on {kind} failed to parse: {e:#}");
            });
            assert!(
                cfg.chunker.if_oversize().is_some(),
                "if_oversize missing for {kind}"
            );
        }
    }

    #[test]
    fn rejects_if_oversize_without_effective_ceiling() {
        // Brief SC-001 NEVER: fixed_overlap without max_chars and with
        // if_oversize is rejected at config-load — there's nothing to
        // compare against.
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker:
  type: fixed_overlap
  window_words: 200
  step_words: 100
  if_oversize:
    type: fixed_overlap
    window_words: 100
    step_words: 50
    max_chars: 500
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("if_oversize") && err.contains("max_chars"),
            "expected if_oversize/max_chars complaint, got: {err}"
        );
    }

    #[test]
    fn effective_max_chars_falls_through_to_base() {
        // Brief SC-003: wrapper without explicit max_chars inherits from base.
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker:
  type: neighbor_expand
  window: 2
  base:
    type: hierarchy
    max_chars: 1234
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        assert_eq!(cfg.chunker.effective_max_chars(), Some(1234));
    }

    #[test]
    fn fixed_overlap_max_chars_is_optional_unset() {
        // Brief SC-002: fixed_overlap without max_chars parses and resolves
        // to None (legacy word-only behavior).
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: fixed_overlap, window_words: 200, step_words: 100 }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        assert!(cfg.chunker.effective_max_chars().is_none());
    }

    #[test]
    fn accepts_section_aware_with_hierarchy_base() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker:
  type: hierarchical_summary
  base: { type: hierarchy }
  summarizer: { mode: passthrough }
  grouping: { strategy: section_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        load_config(&path).expect("should accept section_aware over hierarchy base");
    }

    #[test]
    fn parses_sqlite_target_config() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: chunks, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        match &cfg.target {
            TargetConfig::Sqlite(t) => {
                assert_eq!(t.dsn_env.as_deref(), Some("SQLITE_PATH"));
                assert_eq!(t.database_name, "ignored");
                assert_eq!(t.table, "chunks");
                assert_eq!(t.mode, "overwrite");
            }
            _ => panic!("expected Sqlite target"),
        }
    }

    #[test]
    fn rejects_sqlite_append_without_source_tag() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: chunks, mode: append, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("source_tag"),
            "expected source_tag mention, got: {err}"
        );
    }

    #[test]
    fn parses_sqlite_table_source_config() {
        let yaml = r#"
cell_name: t
source:
  type: sqlite_table
  dsn_env: SQLITE_PATH
  database: ignored
  table: docs
  id_column: id
  content_column: body
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: sqlite, dsn_env: SQLITE_PATH, database: ignored, table: chunks, mode: overwrite, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        match &cfg.source {
            SourceConfig::SqliteTable(s) => {
                assert_eq!(s.dsn_env.as_deref(), Some("SQLITE_PATH"));
                assert_eq!(s.table, "docs");
                assert_eq!(s.id_column, "id");
            }
            _ => panic!("expected SqliteTable source"),
        }
    }

    #[test]
    fn parses_clickhouse_target() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: CHUNKSHOP_DSN_CH
  database: my_db
  table: chunks
  mode: overwrite
  hnsw: true
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load");
        let TargetConfig::Clickhouse(t) = &cfg.target else {
            panic!("expected Clickhouse variant");
        };
        assert_eq!(t.database_name, "my_db");
        assert_eq!(t.table, "chunks");
        assert!(t.engine.is_none());
    }

    #[test]
    fn accepts_replacing_merge_tree_engine() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: D
  database: db
  table: t
  mode: overwrite
  hnsw: false
  engine: "ReplacingMergeTree(created_at) ORDER BY (id)"
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("ReplacingMergeTree should be accepted");
        let TargetConfig::Clickhouse(t) = &cfg.target else {
            unreachable!()
        };
        assert_eq!(
            t.engine.as_deref(),
            Some("ReplacingMergeTree(created_at) ORDER BY (id)")
        );
    }

    #[test]
    fn rejects_arbitrary_engine_string() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: D
  database: db
  table: t
  mode: overwrite
  hnsw: false
  engine: "Memory"
"#;
        let path = write_yaml(yaml);
        let err = format!("{:#}", load_config(&path).unwrap_err());
        assert!(
            err.contains("allowlist") && err.contains("Memory"),
            "got: {err}"
        );
    }

    #[test]
    fn rejects_engine_with_drop_table_injection() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: clickhouse
  dsn_env: D
  database: db
  table: t
  mode: overwrite
  hnsw: false
  engine: "MergeTree(); DROP TABLE other"
"#;
        let path = write_yaml(yaml);
        assert!(
            load_config(&path).is_err(),
            "engine with embedded DROP must be rejected"
        );
    }

    // --- RM-A Task 1: SessionStagingSourceConfig + MemoryConfig ------------

    #[test]
    fn session_staging_source_deserialises() {
        let yaml = r#"
cell_name: m
source:
  type: session_staging
  dsn: "postgresql://localhost/x"
  staging_table: chunkshop_staging
  staging_schema: public
  mode: realtime
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: agent_memory, table: memory, mode: create_if_missing, source_tag: ns1, hnsw: false }
"#;
        let path = write_yaml(yaml);
        let cfg = load_config(&path).expect("load_config should succeed");
        match cfg.source {
            SourceConfig::SessionStaging(s) => {
                assert_eq!(s.staging_table, "chunkshop_staging");
                assert_eq!(s.staging_schema, "public");
                assert_eq!(s.mode, SessionStagingMode::Realtime);
                assert_eq!(s.min_age_seconds, 0);
                assert!(s.dsn.is_some());
            }
            other => panic!("expected SessionStaging variant; got {other:?}"),
        }
    }

    #[test]
    fn session_staging_defaults_match_python() {
        // Only mode is required; staging_schema/staging_table default.
        let yaml = r#"
cell_name: m
source:
  type: session_staging
  dsn_env: D
  mode: consolidate
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: agent_memory, table: memory, mode: create_if_missing, source_tag: ns1, hnsw: false }
"#;
        let cfg = load_config(&write_yaml(yaml)).unwrap();
        if let SourceConfig::SessionStaging(s) = cfg.source {
            assert_eq!(s.staging_table, "chunkshop_staging");
            assert_eq!(s.staging_schema, "public");
            assert_eq!(s.mode, SessionStagingMode::Consolidate);
        } else {
            panic!("not session_staging");
        }
    }

    #[test]
    fn memory_block_on_postgres_target() {
        let yaml = r#"
cell_name: m
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: postgres
  dsn_env: D
  database: agent_memory
  table: memory
  mode: create_if_missing
  source_tag: ns1
  hnsw: false
  memory:
    tier: consolidated
    supersede: true
    namespace: ns1
"#;
        let cfg = load_config(&write_yaml(yaml)).unwrap();
        let mem = match cfg.target {
            TargetConfig::Postgres(p) => p.memory.expect("memory expected"),
            _ => panic!("expected postgres target"),
        };
        assert_eq!(mem.tier, MemoryTier::Consolidated);
        assert!(mem.supersede);
        assert_eq!(mem.namespace.as_deref(), Some("ns1"));
    }

    #[test]
    fn memory_block_unknown_field_rejected() {
        let yaml = r#"
cell_name: m
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target:
  type: postgres
  dsn_env: D
  database: agent_memory
  table: memory
  mode: create_if_missing
  source_tag: ns1
  hnsw: false
  memory: { tier: consolidated, supersede: true, namespace: ns1, bogus_field: yes }
"#;
        let err = load_config(&write_yaml(yaml)).expect_err("bogus_field must fail");
        let msg = format!("{:#}", err);
        assert!(
            msg.contains("bogus_field") || msg.contains("unknown"),
            "expected unknown-field complaint, got: {msg}"
        );
    }

    // --- RM-A Task 2: SessionEpisodeFramerConfig --------------------------

    #[test]
    fn session_episode_framer_deserialises_with_defaults() {
        let yaml = r#"
cell_name: m
source: { type: files, glob: "x", id_from: stem }
framer: { type: session_episode }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: agent_memory, table: memory, mode: create_if_missing, source_tag: ns1, hnsw: false }
"#;
        let cfg = load_config(&write_yaml(yaml)).unwrap();
        match cfg.framer {
            FramerConfig::SessionEpisode(f) => {
                assert_eq!(f.max_gap_seconds, 1800); // python default
                assert_eq!(f.max_turns, 40);
                assert_eq!(f.max_words, 1200);
                assert!(f.boundary_on_tool);
            }
            other => panic!("expected SessionEpisode framer; got {other:?}"),
        }
    }

    #[test]
    fn session_episode_framer_overrides_apply() {
        let yaml = r#"
cell_name: m
source: { type: files, glob: "x", id_from: stem }
framer:
  type: session_episode
  max_gap_seconds: 600
  max_turns: 20
  max_words: 500
  boundary_on_tool: false
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: agent_memory, table: memory, mode: create_if_missing, source_tag: ns1, hnsw: false }
"#;
        let cfg = load_config(&write_yaml(yaml)).unwrap();
        if let FramerConfig::SessionEpisode(f) = cfg.framer {
            assert_eq!(f.max_gap_seconds, 600);
            assert_eq!(f.max_turns, 20);
            assert_eq!(f.max_words, 500);
            assert!(!f.boundary_on_tool);
        } else {
            panic!("not session_episode");
        }
    }

    // --- RM-A Task 3: ConsolidationChunkerConfig --------------------------

    #[test]
    fn consolidation_chunker_deserialises() {
        let yaml = r#"
cell_name: m
source: { type: files, glob: "x", id_from: stem }
framer: { type: session_episode }
chunker:
  type: consolidation
  base:
    type: sentence_aware
    max_chars: 2000
    min_chars: 200
  consolidator:
    mode: extractive
  fact_max_chars: 1200
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: agent_memory, table: memory, mode: create_if_missing, source_tag: ns1, hnsw: false }
"#;
        let cfg = load_config(&write_yaml(yaml)).unwrap();
        match cfg.chunker {
            ChunkerConfig::Consolidation(c) => {
                assert_eq!(c.fact_max_chars, 1200);
                assert!(matches!(*c.base, ChunkerConfig::SentenceAware(_)));
                assert!(matches!(c.consolidator, ConsolidatorConfig::Extractive(_)));
            }
            other => panic!("expected Consolidation; got {other:?}"),
        }
    }

    #[test]
    fn consolidation_chunker_default_fact_max_chars() {
        let yaml = r#"
cell_name: m
source: { type: files, glob: "x", id_from: stem }
chunker:
  type: consolidation
  base: { type: sentence_aware }
  consolidator: { mode: extractive }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: agent_memory, table: memory, mode: create_if_missing, source_tag: ns1, hnsw: false }
"#;
        let cfg = load_config(&write_yaml(yaml)).unwrap();
        if let ChunkerConfig::Consolidation(c) = cfg.chunker {
            assert_eq!(c.fact_max_chars, 1200); // python parity default
            assert!(matches!(*c.base, ChunkerConfig::SentenceAware(_)));
        } else {
            panic!("not consolidation");
        }
    }

    #[test]
    fn consolidation_chunker_unknown_field_rejected() {
        let yaml = r#"
cell_name: m
source: { type: files, glob: "x", id_from: stem }
chunker:
  type: consolidation
  base: { type: sentence_aware }
  consolidator: { mode: extractive }
  bogus_consolidation_field: yes
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: agent_memory, table: memory, mode: create_if_missing, source_tag: ns1, hnsw: false }
"#;
        let err = load_config(&write_yaml(yaml)).expect_err("bogus must fail");
        let msg = format!("{:#}", err);
        assert!(
            msg.contains("bogus") || msg.contains("unknown"),
            "expected unknown-field complaint, got: {msg}"
        );
    }

    #[test]
    fn consolidator_unknown_mode_rejected() {
        let yaml = r#"
cell_name: m
source: { type: files, glob: "x", id_from: stem }
chunker:
  type: consolidation
  base: { type: sentence_aware }
  consolidator: { mode: callable }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: agent_memory, table: memory, mode: create_if_missing, source_tag: ns1, hnsw: false }
"#;
        // `mode: callable` is Python-side only; Rust accepts `extractive` (and
        // future `llm`) but not `callable`. Reject cleanly.
        let err = load_config(&write_yaml(yaml)).expect_err("callable must fail in rust");
        let msg = format!("{:#}", err);
        assert!(
            msg.contains("callable") || msg.contains("variant") || msg.contains("unknown"),
            "expected mode-unknown complaint, got: {msg}"
        );
    }

    #[test]
    fn session_episode_framer_unknown_field_rejected() {
        let yaml = r#"
cell_name: m
source: { type: files, glob: "x", id_from: stem }
framer: { type: session_episode, bogus: 1 }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: agent_memory, table: memory, mode: create_if_missing, source_tag: ns1, hnsw: false }
"#;
        let err = load_config(&write_yaml(yaml)).expect_err("bogus must fail");
        let msg = format!("{:#}", err);
        assert!(
            msg.contains("bogus") || msg.contains("unknown"),
            "expected unknown-field complaint, got: {msg}"
        );
    }

    #[test]
    fn session_staging_unknown_field_rejected() {
        let yaml = r#"
cell_name: m
source:
  type: session_staging
  dsn_env: D
  mode: realtime
  bogus: 1
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: agent_memory, table: memory, mode: create_if_missing, source_tag: ns1, hnsw: false }
"#;
        let err = load_config(&write_yaml(yaml)).expect_err("bogus must fail");
        let msg = format!("{:#}", err);
        assert!(
            msg.contains("bogus") || msg.contains("unknown"),
            "expected unknown-field complaint, got: {msg}"
        );
    }

    // --- dsn / dsn_env parity (mirrors Python's `_DsnResolvable`) ----------

    #[test]
    fn dsn_config_deserializes_and_resolves() {
        let yaml = r#"
cell_name: t
source:
  type: pg_table
  dsn: "postgresql://literal/db"
  schema: public
  table: docs
  id_column: id
  content_column: body
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn: "postgresql://literal/target", database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let cfg = load_config(&write_yaml(yaml)).expect("load");
        match &cfg.source {
            SourceConfig::PgTable(p) => {
                assert_eq!(p.dsn.as_deref(), Some("postgresql://literal/db"));
                assert_eq!(p.dsn_env, None);
                assert_eq!(
                    resolve_dsn(p.dsn.as_deref(), p.dsn_env.as_deref()).unwrap(),
                    "postgresql://literal/db"
                );
            }
            other => panic!("expected PgTable source, got {other:?}"),
        }
        match &cfg.target {
            TargetConfig::Postgres(t) => {
                assert_eq!(t.dsn.as_deref(), Some("postgresql://literal/target"));
                assert_eq!(
                    resolve_dsn(t.dsn.as_deref(), Some(&t.dsn_env)).unwrap(),
                    "postgresql://literal/target"
                );
            }
            other => panic!("expected Postgres target, got {other:?}"),
        }
    }

    #[test]
    fn dsn_wins_over_dsn_env_when_both_set() {
        std::env::set_var(
            "CHUNKSHOP_TEST_DSN_PRECEDENCE",
            "postgresql://from-env-var/db",
        );
        let resolved = resolve_dsn(
            Some("postgresql://from-literal-dsn/db"),
            Some("CHUNKSHOP_TEST_DSN_PRECEDENCE"),
        )
        .unwrap();
        assert_eq!(resolved, "postgresql://from-literal-dsn/db");
        std::env::remove_var("CHUNKSHOP_TEST_DSN_PRECEDENCE");
    }

    #[test]
    fn dsn_interpolates_var() {
        std::env::set_var("CHUNKSHOP_TEST_DSN_VAR", "secret-password");
        let resolved = resolve_dsn(
            Some("postgresql://user:${CHUNKSHOP_TEST_DSN_VAR}@host/db"),
            None,
        )
        .unwrap();
        assert_eq!(resolved, "postgresql://user:secret-password@host/db");
        std::env::remove_var("CHUNKSHOP_TEST_DSN_VAR");
    }

    #[test]
    fn dsn_unset_var_errors_matching_python() {
        std::env::remove_var("CHUNKSHOP_TEST_DSN_VAR_UNSET");
        let err = resolve_dsn(
            Some("postgresql://user:${CHUNKSHOP_TEST_DSN_VAR_UNSET}@host/db"),
            None,
        )
        .unwrap_err();
        let msg = format!("{err}");
        // Mirrors Python's `_DsnResolvable.resolve_dsn`:
        // f"dsn references ${{{name}}} but env var {name!r} is not set"
        assert!(
            msg.contains("CHUNKSHOP_TEST_DSN_VAR_UNSET") && msg.contains("not set"),
            "expected unset-var complaint naming the var, got: {msg}"
        );
    }

    #[test]
    fn dsn_env_only_config_is_unchanged() {
        // Table source: dsn absent, dsn_env present — same shape/behavior as
        // before `dsn` existed.
        let yaml = r#"
cell_name: t
source:
  type: pg_table
  dsn_env: CHUNKSHOP_TEST_DSN_ENV_ONLY
  schema: public
  table: docs
  id_column: id
  content_column: body
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: CHUNKSHOP_TEST_DSN_ENV_ONLY, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let cfg = load_config(&write_yaml(yaml)).expect("load");
        match &cfg.source {
            SourceConfig::PgTable(p) => {
                assert_eq!(p.dsn, None);
                assert_eq!(p.dsn_env.as_deref(), Some("CHUNKSHOP_TEST_DSN_ENV_ONLY"));
            }
            other => panic!("expected PgTable source, got {other:?}"),
        }
        match &cfg.target {
            TargetConfig::Postgres(t) => {
                assert_eq!(t.dsn, None);
                assert_eq!(t.dsn_env, "CHUNKSHOP_TEST_DSN_ENV_ONLY");
            }
            other => panic!("expected Postgres target, got {other:?}"),
        }
        // resolve_dsn falls back to the (lazy) env var read — unset here, so
        // this errors exactly like Python's `os.environ[self.dsn_env]`.
        std::env::remove_var("CHUNKSHOP_TEST_DSN_ENV_ONLY");
        assert!(resolve_dsn(None, Some("CHUNKSHOP_TEST_DSN_ENV_ONLY")).is_err());
        std::env::set_var("CHUNKSHOP_TEST_DSN_ENV_ONLY", "postgresql://from-env/db");
        assert_eq!(
            resolve_dsn(None, Some("CHUNKSHOP_TEST_DSN_ENV_ONLY")).unwrap(),
            "postgresql://from-env/db"
        );
        std::env::remove_var("CHUNKSHOP_TEST_DSN_ENV_ONLY");
    }

    #[test]
    fn rejects_source_missing_both_dsn_and_dsn_env() {
        let yaml = r#"
cell_name: t
source:
  type: pg_table
  schema: public
  table: docs
  id_column: id
  content_column: body
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: postgres, dsn_env: D, database: s, table: t, mode: overwrite, hnsw: false }
"#;
        let err = load_config(&write_yaml(yaml)).expect_err("missing dsn/dsn_env must fail");
        let msg = format!("{:#}", err);
        assert!(
            msg.contains("dsn") && msg.contains("dsn_env"),
            "expected one-of-dsn-or-dsn_env complaint, got: {msg}"
        );
    }

    #[test]
    fn rejects_sqlite_target_missing_both_dsn_and_dsn_env() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: sqlite, database: ignored, table: chunks, mode: overwrite, hnsw: false }
"#;
        let err = load_config(&write_yaml(yaml)).expect_err("missing dsn/dsn_env must fail");
        let msg = format!("{:#}", err);
        assert!(
            msg.contains("dsn") && msg.contains("dsn_env"),
            "expected one-of-dsn-or-dsn_env complaint, got: {msg}"
        );
    }

    #[test]
    fn sqlite_target_accepts_dsn_without_dsn_env() {
        let yaml = r#"
cell_name: t
source: { type: files, glob: "x", id_from: stem }
chunker: { type: sentence_aware }
embedder: { type: fastembed, model_name: BAAI/bge-base-en-v1.5, dim: 768 }
target: { type: sqlite, dsn: ":memory:", database: ignored, table: chunks, mode: overwrite, hnsw: false }
"#;
        let cfg = load_config(&write_yaml(yaml)).expect("load");
        match &cfg.target {
            TargetConfig::Sqlite(t) => {
                assert_eq!(t.dsn.as_deref(), Some(":memory:"));
                assert_eq!(t.dsn_env, None);
            }
            other => panic!("expected Sqlite target, got {other:?}"),
        }
    }
}
