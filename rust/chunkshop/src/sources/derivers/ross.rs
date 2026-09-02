//! Rung 5 for media: the `ross` binary, run as a subprocess. Mirrors
//! `python/src/chunkshop/sources/derivers/ross.py`.
//!
//! `ross` (<https://github.com/yonk-labs/ross>, Apache-2.0) is a single Rust
//! binary that takes media in and emits metadata, tags, a description and
//! optionally an LLM summary. It sniffs type by MAGIC BYTES rather than by
//! extension, so one deriver covers image, audio and video — which is how
//! chunkshop gets an audio tagger it has no in-process rung for.
//!
//! # Why a subprocess and not a linked crate
//!
//! Recorded in `DECISIONS.md` (2026-09-01) and not revisited here:
//! CLAUDE.md's standing rule is that sibling repos are user-wired via config
//! and never imported into core; ross is v0.1.0 and unpublished, so a
//! `Cargo.toml` dependency would make chunkshop unbuildable without a local
//! checkout; NN-4 defines rung 5 as exactly this escape hatch; and ross's
//! taggers hold a `Mutex<Session>` that serialises inference anyway.
//!
//! The consequence worth naming: **this module is behind no Cargo feature at
//! all.** It uses `std::process`, `serde_json` and `sha2`, every one of
//! which is already unconditional in this crate. `--no-default-features`
//! builds it, and a text-only user pays nothing (NN-6).
//!
//! # What is always passed
//!
//! `--json --quiet --clip --clap`. The two tagger flags are unconditional
//! rather than configurable: without them and with `--no-llm`, ross emits
//! deterministic facts and NO `description` at all, so every document would
//! derive to empty text and trip `on_empty`. ross's flags are per-modality,
//! so passing both is how one deriver covers images (CLIP) and audio (CLAP)
//! from a single config block.
//!
//! # The cache-soundness invariant
//!
//! (See `CacheableDeriver`.) Two of ross's inputs are NOT this file's bytes:
//!
//! * **The filename.** When the local tagger is gated — which ross's own
//!   README reports is the common case on specialised corpora — the
//!   description falls back to a humanisation of the file's BASENAME
//!   (`clap_tag::humanize`, which rsplits the path on `/` first). So this
//!   deriver is not a pure content function and `cache_key_extra` returns
//!   the basename: exactly the material consumed, no more.
//! * **The vendored label vocabulary.** ross embeds a 50-label list
//!   (`src/clip_labels.txt`, `src/clap_labels.txt`) and its precomputed text
//!   features INSIDE the binary. Nothing on ross's CLI surface reports it:
//!   `--json` does not echo the label set, and `--doctor` prints only
//!   external binaries, endpoint config and model cache paths. The strongest
//!   available identifier is therefore the binary's own version, which this
//!   deriver obtains at construction by RUNNING `ross --version` — never
//!   hardcoded. `ClipTagsDeriver` hardcodes `FASTEMBED_VERSION` with a
//!   `Cargo.lock` tripwire test precisely because it has no runtime option;
//!   this rung does, so it uses it.
//!
//!   **LIMITATION, stated plainly:** if a future ross build changes
//!   `clip_labels.txt` WITHOUT bumping its package version, cached
//!   derivations from the older vocabulary stay reachable and go stale. A
//!   user-supplied `labels_file` has no such gap — its CONTENT is hashed
//!   into `params["labels_sha256"]`. Mitigation for the vendored case is
//!   `cache: {enabled: false}` or purging the cache after upgrading a ross
//!   built from source.
//!
//! A third, smaller gap worth knowing: ross's `metadata` block comes from
//! `exiftool` (images) and `ffprobe` (audio/video) when those are installed,
//! and from a native PNG/WebP header fallback when they are not — so
//! installing exiftool mid-corpus changes `ross_metadata` for files already
//! cached. Same mitigation.
//!
//! # The LLM endpoint is an input, and it is in the hash
//!
//! With `llm: true`, ross calls an OpenAI-compatible endpoint resolved from
//! the ENVIRONMENT (`ROSS_VISION_URL` > `ROSS_URL`, `ROSS_VISION_MODEL` >
//! `ROSS_MODEL`, and the same shape for the key), per modality. None of that
//! appears in the config block, so for a while none of it reached `params()`
//! — and swapping `ROSS_MODEL` between two runs of the same cell silently
//! served the first model's text out of the cache. It now does: at
//! construction, `llm: true` runs `ross --doctor` and folds the
//! per-modality **url + model + whether a key is set** into
//! `params["llm_endpoints"]`.
//!
//! Resolution is delegated to ross rather than reimplemented here on
//! purpose. ross's precedence (`--<mod>-url > ROSS_<MOD>_URL > --url >
//! ROSS_URL`) is its own; re-deriving it in two host languages is the drift
//! class this rung already has enough of. The cost is that `--doctor` prints
//! for humans (`--doctor --json` is not supported as of ross 0.1.0), so this
//! module parses human-readable output — deliberately strictly: anything it
//! cannot read is a construction-time ERROR, never a fallback to a hash that
//! omits the model, because a silent fallback IS the bug.
//!
//! The API key's VALUE is never read, hashed, stamped or printed. Only the
//! boolean ross itself reports (`key=set` / `key=none`) enters the params,
//! and it earns its place: adding a key to a previously keyless endpoint
//! turns a 401-and-`llm_error` degraded derivation into a real one, which
//! must re-derive rather than serve the degraded text forever.
//!
//! # What is still not deterministic, and cannot be
//!
//! With `llm: true` the derived text comes from a remote model doing
//! sampling. **Two fresh derivations of the same image can differ** even
//! with the endpoint, the model and the prompt all identical — the hash pins
//! the INPUTS, not the output. So on this rung the cache is not an
//! optimisation, it is what makes a corpus stable: every row derived once
//! and thereafter served from that one entry. The corollary is worth stating
//! plainly because it is a materially different property from rungs 0 and 1,
//! which are pure functions of the bytes: **`chunkshop cache purge` followed
//! by a re-ingest can legitimately change the derived text, and therefore
//! the embeddings, with no config change at all.** Purge this cache only
//! when you mean to re-derive the corpus.

use std::io::Read;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use super::provenance::stamp_provenance;
use crate::sources::parsers::{CacheableDeriver, Deriver, ParseResult};

/// The rung name, the `engine` recorded in provenance, and the `model` id.
///
/// `engine` is `"ross"` — neither `"python"` nor `"rust"` — because BOTH
/// ports shell out to the SAME binary. The derivation is genuinely ross's,
/// not either port's, and stamping the host language would claim otherwise.
/// It is also what lets a ross-derived cache entry coexist with a
/// fastembed-derived one without either masquerading as the other.
pub const ENGINE: &str = "ross";
pub const RUNG: &str = "ross";
pub const MODEL_ID: &str = "ross";

const INSTALL_HINT: &str = "Install ross (https://github.com/yonk-labs/ross), \
     or set `binary:` in the `rung: ross` derive block to its full path.";

/// The modalities `ross --doctor` reports, in ross's own print order. Fixed
/// rather than discovered: a ross that grows a fourth modality must be a
/// loud parse failure here, not a silently unhashed new endpoint.
const MODALITIES: [&str; 3] = ["vision", "audio", "video"];

const DOCTOR_HINT: &str = "With `llm: true` this rung resolves its endpoint \
     from the environment (ROSS_VISION_URL > ROSS_URL, ROSS_VISION_MODEL > \
     ROSS_MODEL, ...) by running `ross --doctor`, and folds the url + model \
     into the derivation hash so that swapping models re-derives instead of \
     serving the previous model's text. Run `ross --doctor` yourself to see \
     what could not be read. If ross's output format has changed, this \
     deriver needs updating -- do not work around it by disabling the cache, \
     which would put the model back outside the key.";

/// Exit codes ross documents in its README, mapped to what the user should
/// actually do about them. Anything else falls through to the bare status.
fn exit_hint(code: i32) -> Option<&'static str> {
    Some(match code {
        2 => "ross ran with --strict and at least one file errored",
        3 => "no LLM endpoint is configured; set ROSS_URL + ROSS_MODEL, or \
              leave `llm: false` in the derive block to stay offline",
        4 => "ross rejected the input or the arguments",
        5 => "ross could not load its local CLIP/CLAP models (first run \
              downloads ~89MB/78MB into ~/.cache/ross-clap; check network \
              and disk)",
        _ => return None,
    })
}

pub struct RossDeriver {
    pub binary: String,
    pub labels_file: Option<String>,
    pub llm: bool,
    pub timeout_s: u64,
    /// sha256 of the resolved `labels_file`'s CONTENT, or `None` for ross's
    /// vendored vocabulary. Content, not path: moving the file must not
    /// re-derive, editing it in place MUST.
    labels_sha256: Option<String>,
    /// What `ross --version` reported at construction.
    version: String,
    /// What `ross --doctor` reported at construction, or `None` when
    /// `llm` is false. `None` is what keeps `llm_endpoints` out of the
    /// params object entirely rather than present-and-null, which is what
    /// keeps the keyless path's hash byte-identical to what it was before
    /// this field existed.
    llm_endpoints: Option<Value>,
}

impl RossDeriver {
    /// Both probes run EAGERLY, here. `FilesSource::new` builds its derivers
    /// before `runner::run_cell` reaches `sink.create_table()`, so a missing
    /// binary or an unreadable labels file fails before a `mode: overwrite`
    /// cell has dropped the user's table — the same property
    /// `preflight_derive` exists to buy. It also makes `cache_key_parts()`
    /// infallible, which it must be: its signature has no error path.
    pub fn new(
        binary: String,
        labels_file: Option<String>,
        llm: bool,
        timeout_s: u64,
    ) -> anyhow::Result<Self> {
        let labels_sha256 = match &labels_file {
            None => None,
            Some(p) => Some(hash_labels_file(p)?),
        };
        let version = probe_version(&binary, timeout_s)?;
        // Third eager probe, and only when it can matter: `llm: false` must
        // not pay a `--doctor` subprocess, and must produce params
        // byte-identical to the ones it produced before this probe existed.
        let llm_endpoints = if llm {
            Some(probe_llm_endpoints(&binary, timeout_s)?)
        } else {
            None
        };
        Ok(Self {
            binary,
            labels_file,
            llm,
            timeout_s,
            labels_sha256,
            version,
            llm_endpoints,
        })
    }

    pub fn version(&self) -> &str {
        &self.version
    }

    /// Everything that changes ross's OUTPUT, and nothing that doesn't.
    ///
    /// `binary` and `timeout_s` are deliberately absent: the path a binary
    /// is reached by does not change what it computes (its version does,
    /// and that is `model_version`), and a timeout only bounds how long we
    /// wait. `llm` and the vocabulary identity DO change output.
    ///
    /// `llm_endpoints` is present ONLY when `llm` is true. That is not
    /// tidiness — it is what makes the keyless path's hash byte-identical
    /// to what it was before this key existed, so no existing cache entry
    /// was invalidated to fix a bug that could not affect it.
    fn params(&self) -> Value {
        let mut m = Map::new();
        m.insert("llm".to_string(), json!(self.llm));
        m.insert("labels_sha256".to_string(), json!(self.labels_sha256));
        if let Some(e) = &self.llm_endpoints {
            m.insert("llm_endpoints".to_string(), e.clone());
        }
        Value::Object(m)
    }

    fn argv(&self, path: &Path) -> Vec<String> {
        let mut argv = vec![
            path.display().to_string(),
            "--json".to_string(),
            "--quiet".to_string(),
            "--clip".to_string(),
            "--clap".to_string(),
        ];
        if !self.llm {
            argv.push("--no-llm".to_string());
        }
        if let Some(f) = &self.labels_file {
            argv.push("--labels-file".to_string());
            argv.push(f.clone());
        }
        argv
    }

    /// ross prints ONE object for a single input, an array for many.
    fn parse_entry(&self, stdout: &str, path: &Path) -> anyhow::Result<Value> {
        let payload: Value = serde_json::from_str(stdout).map_err(|e| {
            anyhow::anyhow!(
                "ross deriver: could not parse `{}` --json output for {}: {e}. \
                 First 200 bytes: {:?}",
                self.binary,
                path.display(),
                &stdout[..stdout.len().min(200)]
            )
        })?;
        match payload {
            Value::Array(mut a) => {
                if a.is_empty() {
                    // ross SKIPS non-media files (magic-byte sniff) rather
                    // than erroring — an empty array is that, and it is a
                    // real configuration mistake worth naming, not an empty
                    // success.
                    anyhow::bail!(
                        "ross deriver: `{}` produced no entry for {}; ross sniffs \
                         by magic bytes and skips files it does not recognise as \
                         image/audio/video.",
                        self.binary,
                        path.display()
                    );
                }
                Ok(a.remove(0))
            }
            v @ Value::Object(_) => Ok(v),
            other => anyhow::bail!(
                "ross deriver: unexpected `{}` --json shape for {}: {}",
                self.binary,
                path.display(),
                kind_of(&other)
            ),
        }
    }

    /// Map ross's JSON onto a `ParseResult`.
    ///
    /// The metadata key set is FIXED — every key below is always present,
    /// holding null/`[]`/`{}` when ross omitted the field. ross's own output
    /// keys are conditional (`tags` only appears when a tagger cleared its
    /// bar), and a key set that varies by input cannot be asserted for
    /// cross-port parity, nor promoted to a column.
    ///
    /// Deliberately NOT mapped:
    /// * `path` — `FilesSource` stamps `source_path` itself.
    /// * `sha256`, `bytes` — chunkshop already carries the content hash as
    ///   the document `fingerprint`.
    /// * `mtime` — an input invisible to the cache key; caching it would
    ///   serve a stale value the moment a file is touched.
    /// * `duration_ms` — wall-clock timing. Storing it would make a cache
    ///   HIT differ from a fresh derivation on every row.
    pub(crate) fn build_result(&self, entry: &Value) -> ParseResult {
        let description = as_str(entry.get("description"));
        let summary = as_str(entry.get("summary"));
        let tags: Vec<String> = entry
            .get("tags")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        let warnings: Vec<String> = ["clip_error", "clap_error", "llm_error"]
            .iter()
            .filter_map(|k| {
                let s = as_str(entry.get(*k));
                if s.is_empty() {
                    None
                } else {
                    Some(s)
                }
            })
            .collect();

        // description, then summary, then the tags. `ClipTagsDeriver` builds
        // its text the same shape (base text + tags), so a reader moving
        // between rungs sees one convention. No de-duplication: when the
        // local tagger fires, ross's own `description` restates the tags,
        // which is harmless for embedding and keeps the two ports trivially
        // identical.
        let mut parts: Vec<String> = Vec::new();
        for p in [&description, &summary] {
            if !p.is_empty() {
                parts.push(p.clone());
            }
        }
        parts.extend(tags.iter().cloned());
        let text = parts.join(" ");

        let mut metadata = Map::new();
        metadata.insert("ross_type".into(), owned(entry.get("type")));
        metadata.insert("ross_mime".into(), owned(entry.get("mime")));
        metadata.insert("ross_description".into(), nullable(&description));
        metadata.insert("ross_summary".into(), nullable(&summary));
        metadata.insert("ross_tags".into(), json!(tags));
        metadata.insert(
            "ross_tag_confidence".into(),
            owned(entry.get("tag_confidence")),
        );
        // ross's own report of WHICH model produced the text:
        // "clip:clip-vit-base-patch32" for the local tagger, or the LLM
        // model name when the endpoint path won. Distinct from
        // `derived.model` (always "ross"), which identifies the tool.
        // ross's own report of WHICH model produced the text:
        // "clip:clip-vit-base-patch32" for the local tagger, or the LLM
        // model name when the endpoint path won. Distinct from
        // `derived.model` (always "ross"), which identifies the tool.
        //
        // This is the second half of the endpoint-in-the-hash fix, and the
        // half that stays true when the first cannot: the hash is computed
        // at CONSTRUCTION from `--doctor`, while this is read off the
        // RESPONSE, so a row is attributable even if resolution moved in
        // between (or the endpoint served a different model than it
        // advertised).
        //
        // NOT defaulted, on purpose. ross stamps the local tagger's label
        // only when no endpoint is configured at all, and stamps the LLM's
        // name only when the call SUCCEEDED — so when the LLM was attempted
        // and failed, `model` is absent and this lands null. That is the
        // honest record: the kept text came from the local tagger, ross does
        // not say which build of it, and inventing either the LLM's name (a
        // lie) or a tagger label ross did not emit (a guess) would be worse
        // than null. The `llm_error` itself is carried in `ross_warnings`,
        // so a null here is always explicable from the same row.
        metadata.insert("ross_model".into(), owned(entry.get("model")));
        metadata.insert(
            "ross_metadata".into(),
            match entry.get("metadata") {
                Some(v @ Value::Object(_)) => v.clone(),
                _ => Value::Object(Map::new()),
            },
        );
        metadata.insert("ross_warnings".into(), json!(warnings));

        stamp_provenance(
            ParseResult::with_metadata(text, metadata),
            RUNG,
            MODEL_ID,
            &self.version,
            ENGINE,
            &self.params(),
        )
    }
}

impl Deriver for RossDeriver {
    /// Same class name in both ports.
    fn parser_name(&self) -> &'static str {
        "RossDeriver"
    }

    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let out = run_with_timeout(&self.binary, &self.argv(path), self.timeout_s).map_err(
            |e| match e {
                RunError::Spawn(msg) => anyhow::anyhow!(
                    "ross deriver: cannot run the ross binary `{}`: {msg}. {INSTALL_HINT}",
                    self.binary
                ),
                RunError::Timeout => anyhow::anyhow!(
                    "ross deriver: `{}` did not finish within {}s for {}. Raise \
                     `timeout_s` in the `rung: ross` derive block, or check that ross \
                     is not blocked downloading its CLIP/CLAP models on first run.",
                    self.binary,
                    self.timeout_s,
                    path.display()
                ),
                RunError::Other(msg) => anyhow::anyhow!(
                    "ross deriver: `{}` failed for {}: {msg}",
                    self.binary,
                    path.display()
                ),
            },
        )?;

        if out.code != Some(0) {
            let hint = out
                .code
                .and_then(exit_hint)
                .map(|h| format!(" ({h})"))
                .unwrap_or_default();
            let status = match out.code {
                Some(c) => c.to_string(),
                None => "signal (killed)".to_string(),
            };
            // ross's own stderr is surfaced verbatim, never swallowed: when
            // a run fails because ffprobe/exiftool is absent, ross's message
            // names the missing binary and ours cannot.
            let stderr = out.stderr.trim();
            anyhow::bail!(
                "ross deriver: `{}` exited with status {status}{hint} for {}. \
                 ross stderr: {}",
                self.binary,
                path.display(),
                if stderr.is_empty() { "(empty)" } else { stderr }
            );
        }

        let entry = self.parse_entry(&out.stdout, path)?;
        if let Some(err) = entry.get("error").and_then(Value::as_str) {
            anyhow::bail!(
                "ross deriver: ross reported an error for {}: {err}",
                path.display()
            );
        }
        Ok(self.build_result(&entry))
    }
}

impl CacheableDeriver for RossDeriver {
    fn cache_key_parts(&self) -> (String, String, String, Value) {
        (
            RUNG.to_string(),
            MODEL_ID.to_string(),
            self.version.clone(),
            self.params(),
        )
    }

    /// `"ross"`, not the default `"rust"` — see `ENGINE`.
    fn engine(&self) -> &'static str {
        ENGINE
    }

    /// The file's BASENAME — ross's only per-document non-content input.
    ///
    /// When the local tagger clears its confidence bar the description is a
    /// pure function of the pixels/samples, but when it is GATED ross falls
    /// back to `humanize(basename)`. `humanize` rsplits the path on `/` and
    /// strips the extension, so the parent directories are genuinely not
    /// consumed and must not be keyed on; the basename is.
    ///
    /// Returning the basename rather than the full path keeps the key as
    /// narrow as correctness allows: the same file ingested by relative and
    /// absolute glob still shares one entry, while two byte-identical files
    /// under DIFFERENT names correctly do not.
    fn cache_key_extra(&self, path: &Path) -> Option<String> {
        Some(
            path.file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default(),
        )
    }
}

// --- subprocess plumbing --------------------------------------------------

fn hash_labels_file(p: &str) -> anyhow::Result<String> {
    let bytes = std::fs::read(p).map_err(|e| {
        anyhow::anyhow!(
            "ross deriver: cannot read labels_file {p:?}: {e}. Fix the path in \
             the `rung: ross` derive block, or drop `labels_file` to use ross's \
             built-in vocabulary."
        )
    })?;
    let mut h = Sha256::new();
    h.update(&bytes);
    Ok(format!("{:x}", h.finalize()))
}

/// `ross --version` -> `"0.1.0"`. Never hardcoded.
///
/// Doubles as the NN-5 existence check: a missing, non-executable or wrong
/// binary produces the actionable error here rather than a confusing failure
/// on the first image.
fn probe_version(binary: &str, timeout_s: u64) -> anyhow::Result<String> {
    let out = run_with_timeout(binary, &["--version".to_string()], timeout_s).map_err(|e| {
        match e {
            RunError::Spawn(msg) => anyhow::anyhow!(
                "ross deriver: cannot run the ross binary `{binary}`: {msg}. {INSTALL_HINT}"
            ),
            RunError::Timeout => anyhow::anyhow!(
                "ross deriver: `{binary}` --version did not finish within \
                 {timeout_s}s. {INSTALL_HINT}"
            ),
            RunError::Other(msg) => anyhow::anyhow!(
                "ross deriver: cannot run the ross binary `{binary}`: {msg}. {INSTALL_HINT}"
            ),
        }
    })?;
    if out.code != Some(0) {
        let status = match out.code {
            Some(c) => c.to_string(),
            None => "signal (killed)".to_string(),
        };
        anyhow::bail!(
            "ross deriver: `{binary}` --version exited with status {status}. \
             {INSTALL_HINT} ross stderr: {}",
            out.stderr.trim()
        );
    }
    // clap's `#[command(version)]` prints "<name> <semver>".
    let token = out
        .stdout
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().last())
        .unwrap_or("");
    if token.is_empty() {
        anyhow::bail!(
            "ross deriver: `{binary}` --version printed nothing parseable \
             ({:?}); is this really the ross binary? {INSTALL_HINT}",
            out.stdout
        );
    }
    Ok(token.to_string())
}

/// `ross --doctor` -> the per-modality endpoint identity.
///
/// Called only when `llm` is true: ross is otherwise run with `--no-llm`
/// and no endpoint is consulted at all, so there is nothing to key on and
/// no reason to spend a subprocess finding that out.
///
/// This asks ROSS to resolve its own precedence rather than reimplementing
/// `--<mod>-url > ROSS_<MOD>_URL > --url > ROSS_URL` in two host languages.
/// The deriver passes no `--url`/`--model` flags of its own, so a bare
/// `--doctor` resolves exactly what the eventual derive call will use.
///
/// Eager, alongside the other two probes, for the same reason: it must fail
/// before `sink.create_table()` has dropped a `mode: overwrite` table, and
/// `cache_key_parts()` must stay infallible.
fn probe_llm_endpoints(binary: &str, timeout_s: u64) -> anyhow::Result<Value> {
    let out = run_with_timeout(binary, &["--doctor".to_string()], timeout_s).map_err(|e| match e {
        RunError::Timeout => anyhow::anyhow!(
            "ross deriver: `{binary}` --doctor did not finish within \
             {timeout_s}s. {DOCTOR_HINT}"
        ),
        RunError::Spawn(m) | RunError::Other(m) => anyhow::anyhow!(
            "ross deriver: cannot run the ross binary `{binary}`: {m}. {INSTALL_HINT}"
        ),
    })?;
    if out.code != Some(0) {
        let status = out
            .code
            .map(|c| c.to_string())
            .unwrap_or_else(|| "a signal".to_string());
        let stderr = out.stderr.trim();
        return Err(anyhow::anyhow!(
            "ross deriver: `{binary}` --doctor exited with status {status}. \
             {DOCTOR_HINT} ross stderr: {}",
            if stderr.is_empty() { "(empty)" } else { stderr }
        ));
    }
    parse_doctor(&out.stdout, binary)
}

/// A rendering of `--doctor` output that is safe to put in an error.
///
/// ross 0.1.0 prints `key=none` / `key=set` and never the key itself, so
/// echoing its output verbatim would be safe TODAY. But the only path that
/// reaches an error message is the path where ross's format is not what we
/// expect, which is exactly when that assumption stops being checkable. So:
/// modality lines are truncated at ` key=`, and every other line is reduced
/// to its leading label. The hint tells the user to run `ross --doctor`
/// themselves, where the real output is theirs to see.
fn redact_doctor(stdout: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    for raw in stdout.lines() {
        let line = raw.trim();
        if line.is_empty() {
            continue;
        }
        let (label, rest) = split_label(line);
        if MODALITIES.contains(&label) {
            let body = rest.split(" key=").next().unwrap_or("").trim_end();
            out.push(format!("{label} {body} key=<hidden>").trim().to_string());
        } else {
            out.push(format!("{label} ..."));
        }
    }
    if out.is_empty() {
        "(no output)".to_string()
    } else {
        out.join("\n")
    }
}

/// Split a line into its first whitespace-delimited token and the rest.
fn split_label(line: &str) -> (&str, &str) {
    match line.find(char::is_whitespace) {
        Some(i) => (&line[..i], line[i..].trim_start()),
        None => (line, ""),
    }
}

/// Parse `ross --doctor` into `{modality: {url, model, key_set} | null}`.
///
/// ross prints one line per modality, either:
///
/// ```text
/// vision     http://host:8082/v1 model=some/model key=none
/// audio      NOT SET
/// ```
///
/// Parsed strictly and from the RIGHT: the model id is whatever sits
/// between ` model=` and ` key=`, so a url or a model containing spaces
/// would be a named failure rather than a quietly truncated hash input.
///
/// A modality reported `NOT SET` maps to `null`, which is the honest
/// record: ross will emit `llm_error: no endpoint configured` for it, and
/// configuring one later must re-derive.
///
/// Every failure here is a hard error. There is no degraded mode: falling
/// back to a hash that omits the model is precisely the defect this
/// function exists to close.
fn parse_doctor(stdout: &str, binary: &str) -> anyhow::Result<Value> {
    let mut found: Map<String, Value> = Map::new();
    for raw in stdout.lines() {
        let line = raw.trim();
        let (label, rest) = split_label(line);
        if !MODALITIES.contains(&label) {
            continue;
        }
        if rest == "NOT SET" {
            found.insert(label.to_string(), Value::Null);
            continue;
        }
        let parsed = rest
            .rsplit_once(" key=")
            .and_then(|(head, key)| head.rsplit_once(" model=").map(|(u, m)| (u, m, key)))
            .map(|(url, model, key)| (url.trim(), model.trim(), key.trim()))
            .filter(|(url, model, _)| !url.is_empty() && !model.is_empty());
        let (url, model, key) = match parsed {
            Some(t) => t,
            None => {
                return Err(anyhow::anyhow!(
                    "ross deriver: could not parse the `{label}` line of \
                     `{binary}` --doctor; expected \
                     `{label} <url> model=<model> key=<none|set>` or \
                     `{label} NOT SET`. {DOCTOR_HINT} Output (key redacted):\n{}",
                    redact_doctor(stdout)
                ))
            }
        };
        if key != "none" && key != "set" {
            return Err(anyhow::anyhow!(
                "ross deriver: `{binary}` --doctor reported an unexpected key \
                 state for `{label}`; expected `none` or `set`. {DOCTOR_HINT} \
                 Output (key redacted):\n{}",
                redact_doctor(stdout)
            ));
        }
        // The key's VALUE is never read. Its PRESENCE is: adding a key to a
        // previously keyless endpoint turns a 401 (and an `llm_error`
        // degraded derivation) into a real one, and that must re-derive.
        found.insert(
            label.to_string(),
            json!({ "url": url, "model": model, "key_set": key == "set" }),
        );
    }

    let missing: Vec<&str> = MODALITIES
        .iter()
        .copied()
        .filter(|m| !found.contains_key(*m))
        .collect();
    if !missing.is_empty() {
        return Err(anyhow::anyhow!(
            "ross deriver: `{binary}` --doctor did not report {}. {DOCTOR_HINT} \
             Output (key redacted):\n{}",
            missing.join(", "),
            redact_doctor(stdout)
        ));
    }
    // Rebuilt in ross's own order so the object is stable regardless of the
    // order the lines happened to arrive in. (`Map` is a `BTreeMap` here, so
    // this is belt-and-braces against a `preserve_order` flip — the same
    // defence `provenance::sort_json_keys` documents.)
    let mut ordered = Map::new();
    for m in MODALITIES {
        ordered.insert(m.to_string(), found[m].clone());
    }
    Ok(Value::Object(ordered))
}

struct Output {
    code: Option<i32>,
    stdout: String,
    stderr: String,
}

enum RunError {
    Spawn(String),
    Timeout,
    Other(String),
}

/// Run a command with a wall-clock timeout, capturing both streams.
///
/// Written by hand rather than pulled in as a crate (`wait-timeout`,
/// `subprocess`, ...) because this rung's whole premise is adding NO
/// dependency to either language. Both pipes are drained on dedicated
/// threads before the wait loop, so a child that fills a 64KB pipe buffer
/// cannot deadlock against a parent that is only polling `try_wait` — the
/// classic bug in the naive version of this function.
fn run_with_timeout(binary: &str, args: &[String], timeout_s: u64) -> Result<Output, RunError> {
    let mut child = Command::new(binary)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| RunError::Spawn(e.to_string()))?;

    let mut out_pipe = child.stdout.take().expect("stdout piped");
    let mut err_pipe = child.stderr.take().expect("stderr piped");
    let out_thread = std::thread::spawn(move || {
        let mut s = Vec::new();
        let _ = out_pipe.read_to_end(&mut s);
        s
    });
    let err_thread = std::thread::spawn(move || {
        let mut s = Vec::new();
        let _ = err_pipe.read_to_end(&mut s);
        s
    });

    let deadline = Instant::now() + Duration::from_secs(timeout_s);
    let status = loop {
        match child.try_wait() {
            Ok(Some(st)) => break st,
            Ok(None) => {}
            Err(e) => return Err(RunError::Other(e.to_string())),
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            // Join so the reader threads are not left holding pipe ends.
            let _ = out_thread.join();
            let _ = err_thread.join();
            return Err(RunError::Timeout);
        }
        std::thread::sleep(Duration::from_millis(10));
    };

    let stdout = out_thread.join().unwrap_or_default();
    let stderr = err_thread.join().unwrap_or_default();
    Ok(Output {
        code: status.code(),
        stdout: String::from_utf8_lossy(&stdout).into_owned(),
        stderr: String::from_utf8_lossy(&stderr).into_owned(),
    })
}

fn as_str(v: Option<&Value>) -> String {
    v.and_then(Value::as_str).unwrap_or("").trim().to_string()
}

fn nullable(s: &str) -> Value {
    if s.is_empty() {
        Value::Null
    } else {
        Value::String(s.to_string())
    }
}

fn owned(v: Option<&Value>) -> Value {
    v.cloned().unwrap_or(Value::Null)
}

fn kind_of(v: &Value) -> &'static str {
    match v {
        Value::Null => "null",
        Value::Bool(_) => "bool",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

/// Is a ross binary reachable? For test gating, mirroring how the DB tests
/// skip on an unreachable DSN. Mirrors Python's `ross.ross_available`.
pub fn ross_available(binary: &str) -> bool {
    run_with_timeout(binary, &["--version".to_string()], 30)
        .map(|o| o.code == Some(0))
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A fake `ross` that answers `--version` and then runs `body`.
    ///
    /// Unix-only, like the rest of this module's test surface — ross's own
    /// README states it is untested on Windows and splits paths on `/`.
    #[cfg(unix)]
    fn stub(dir: &Path, body: &str) -> String {
        stub_with_doctor(dir, body, None)
    }

    /// `stub`, plus an optional `--doctor` arm.
    ///
    /// `doctor` is shell, not a literal, so a test can point it at a file it
    /// controls. That is how the model-swap tests change what ross reports
    /// WITHOUT mutating this process's environment — `std::env::set_var` is
    /// process-global and cargo runs these tests on many threads, so an
    /// env-driven stub would be a flake generator, not a test.
    #[cfg(unix)]
    fn stub_with_doctor(dir: &Path, body: &str, doctor: Option<&str>) -> String {
        use std::os::unix::fs::PermissionsExt;
        std::fs::create_dir_all(dir).unwrap();
        let p = dir.join("ross");
        let arms = match doctor {
            None => "case \"$1\" in --version) echo \"ross 9.9.9\"; exit 0;; esac".to_string(),
            Some(d) => format!(
                "case \"$1\" in --version) echo \"ross 9.9.9\"; exit 0;; \
                 --doctor) {d}\nexit 0;; esac"
            ),
        };
        std::fs::write(&p, format!("#!/bin/sh\n{arms}\n{body}\n")).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        p.display().to_string()
    }

    /// A `--doctor` arm that prints `text` verbatim, via a file so no shell
    /// quoting of the payload is involved.
    #[cfg(unix)]
    fn doctor_printing(dir: &Path, text: &str) -> String {
        std::fs::create_dir_all(dir).unwrap();
        let f = dir.join("doctor.txt");
        std::fs::write(&f, text).unwrap();
        format!("cat \"{}\"", f.display())
    }

    /// A deriver with `llm: true` whose `--doctor` prints `doctor`.
    #[cfg(unix)]
    fn llm_deriver(dir: &Path, doctor: &str, body: &str) -> anyhow::Result<RossDeriver> {
        let arm = doctor_printing(dir, doctor);
        let binary = stub_with_doctor(dir, body, Some(&arm));
        retry(|| RossDeriver::new(binary.clone(), None, true, 30))
    }

    fn doctor_text(model: &str, key: &str) -> String {
        format!(
            "ffprobe    ok\nexiftool   MISSING (optional)\n\
             vision     http://endpoint:8082/v1 model={model} key={key}\n\
             audio      http://endpoint:8082/v1 model={model} key={key}\n\
             video      NOT SET\n\nclap       ok (/x)\n"
        )
    }

    fn err_of<T>(r: anyhow::Result<T>) -> String {
        match r {
            Ok(_) => panic!("expected an error, got Ok"),
            Err(e) => format!("{e:#}"),
        }
    }

    /// Retry past `ETXTBSY` ("Text file busy").
    ///
    /// Not papering over a product bug: `Command::spawn` forks, and between
    /// the fork and the exec the child holds every fd its parent had open —
    /// including another concurrently-running test's still-open write
    /// handle to ITS stub script. Linux then refuses to exec that script
    /// until the handle closes. Cargo runs these tests on multiple threads
    /// by default, so the window is real and the failure is spurious.
    fn retry<T>(mut f: impl FnMut() -> anyhow::Result<T>) -> anyhow::Result<T> {
        for _ in 0..100 {
            match f() {
                Err(e) if format!("{e:#}").contains("Text file busy") => {
                    std::thread::sleep(Duration::from_millis(20));
                }
                other => return other,
            }
        }
        f()
    }

    fn try_deriver(binary: &str) -> anyhow::Result<RossDeriver> {
        retry(|| RossDeriver::new(binary.to_string(), None, false, 30))
    }

    fn deriver(binary: &str) -> RossDeriver {
        try_deriver(binary).unwrap()
    }

    // --- NN-5: never a dead end. These run with or without ross installed.

    #[test]
    fn missing_binary_names_the_binary_and_what_to_do() {
        let dir = tempfile::tempdir().unwrap();
        let missing = dir.path().join("definitely-not-ross").display().to_string();
        let msg = err_of(try_deriver(&missing));
        assert!(msg.contains(&missing), "{msg}");
        assert!(msg.contains("github.com/yonk-labs/ross"), "{msg}");
        assert!(msg.contains("`binary:`"), "{msg}");
    }

    #[cfg(unix)]
    #[test]
    fn a_non_executable_binary_is_a_named_error_not_a_panic() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("ross");
        std::fs::write(&p, "#!/bin/sh\nexit 0\n").unwrap(); // no +x
        let msg = err_of(try_deriver(&p.display().to_string()));
        assert!(msg.contains("github.com/yonk-labs/ross"), "{msg}");
    }

    #[cfg(unix)]
    #[test]
    fn a_binary_that_is_not_ross_is_rejected_at_construction() {
        // `--version` printing nothing parseable must not become a silent
        // empty `model_version` folded into the cache key.
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("ross");
        std::fs::write(&p, "#!/bin/sh\nexit 0\n").unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        let msg = err_of(try_deriver(&p.display().to_string()));
        assert!(msg.contains("parseable"), "{msg}");
    }

    #[cfg(unix)]
    #[test]
    fn a_nonzero_version_exit_surfaces_ross_stderr() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("ross");
        std::fs::write(&p, "#!/bin/sh\necho boom >&2\nexit 7\n").unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        let msg = err_of(try_deriver(&p.display().to_string()));
        assert!(msg.contains("status 7"), "{msg}");
        assert!(msg.contains("boom"), "{msg}");
    }

    #[test]
    fn unreadable_labels_file_fails_at_construction() {
        let dir = tempfile::tempdir().unwrap();
        let msg = err_of(retry(|| {
            RossDeriver::new(
                "ross".to_string(),
                Some(dir.path().join("nope.txt").display().to_string()),
                false,
                30,
            )
        }));
        assert!(msg.contains("labels_file"), "{msg}");
    }

    #[cfg(unix)]
    #[test]
    fn a_nonzero_derive_exit_surfaces_ross_stderr_with_a_hint() {
        // Exit 5 means ross could not load its local models. The user must
        // see ross's own message — when a run fails for a missing
        // ffprobe/exiftool, only ross's stderr names the missing binary.
        let dir = tempfile::tempdir().unwrap();
        let d = deriver(&stub(dir.path(), "echo 'ffprobe not found' >&2\nexit 5"));
        let msg = err_of(retry(|| d.derive(Path::new("whatever.png"))));
        assert!(msg.contains("status 5"), "{msg}");
        assert!(msg.contains("CLIP/CLAP"), "{msg}");
        assert!(msg.contains("ffprobe not found"), "{msg}");
    }

    #[cfg(unix)]
    #[test]
    fn a_timeout_is_a_named_error_naming_the_knob() {
        let dir = tempfile::tempdir().unwrap();
        let binary = stub(dir.path(), "sleep 30");
        let d = retry(|| RossDeriver::new(binary.clone(), None, false, 1)).unwrap();
        let msg = err_of(retry(|| d.derive(Path::new("whatever.png"))));
        assert!(msg.contains("timeout_s"), "{msg}");
    }

    #[cfg(unix)]
    #[test]
    fn unparseable_json_is_a_named_error() {
        let dir = tempfile::tempdir().unwrap();
        let d = deriver(&stub(dir.path(), "echo 'not json at all'"));
        let msg = err_of(retry(|| d.derive(Path::new("whatever.png"))));
        assert!(msg.contains("not json"), "{msg}");
    }

    #[cfg(unix)]
    #[test]
    fn a_ross_reported_per_file_error_is_never_an_empty_derivation() {
        let dir = tempfile::tempdir().unwrap();
        let d = deriver(&stub(
            dir.path(),
            r#"echo '{"path":"x","error":"image too large"}'"#,
        ));
        let msg = err_of(retry(|| d.derive(Path::new("whatever.png"))));
        assert!(msg.contains("image too large"), "{msg}");
    }

    #[cfg(unix)]
    #[test]
    fn a_skipped_non_media_file_is_named_rather_than_indexed_empty() {
        let dir = tempfile::tempdir().unwrap();
        let d = deriver(&stub(dir.path(), "echo '[]'"));
        let msg = err_of(retry(|| d.derive(Path::new("notes.png"))));
        assert!(msg.contains("magic bytes"), "{msg}");
    }

    // --- provenance and cache key ----------------------------------------

    #[cfg(unix)]
    #[test]
    fn model_version_is_probed_not_hardcoded() {
        // `ClipTagsDeriver` hardcodes its model version because it has no
        // runtime option; this rung does, so a constant would be a
        // regression.
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(deriver(&stub(dir.path(), "exit 0")).version(), "9.9.9");
    }

    #[cfg(unix)]
    #[test]
    fn the_labels_file_content_is_in_the_params_not_its_path() {
        // Vocabulary identity. Editing the labels file MUST re-derive;
        // moving it must not.
        let dir = tempfile::tempdir().unwrap();
        let binary = stub(dir.path(), "exit 0");
        let a = dir.path().join("a").join("labels.txt");
        let b = dir.path().join("b").join("labels.txt");
        for p in [&a, &b] {
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(p, "a cat\na dog\n").unwrap();
        }
        let mk = |p: &Path| {
            retry(|| {
                RossDeriver::new(binary.clone(), Some(p.display().to_string()), false, 30)
            })
            .unwrap()
        };
        let da = mk(&a);
        let db = mk(&b);
        assert_eq!(
            da.params(),
            db.params(),
            "the labels file's PATH must not affect the derivation"
        );

        std::fs::write(&b, "a cat\na dog\na bird\n").unwrap();
        assert_ne!(
            da.params(),
            mk(&b).params(),
            "the labels file's CONTENT must affect the derivation"
        );
    }

    #[cfg(unix)]
    #[test]
    fn the_vendored_vocabulary_is_identified_by_the_binary_version() {
        // With no `labels_file` the vocabulary lives INSIDE the binary and
        // nothing on ross's CLI reports it, so the version is the only
        // available identity — a bump must therefore invalidate.
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let old = deriver(&stub(&dir.path().join("old"), "exit 0"));

        let newer = dir.path().join("new");
        std::fs::create_dir_all(&newer).unwrap();
        let p = newer.join("ross");
        std::fs::write(
            &p,
            "#!/bin/sh\ncase \"$1\" in --version) echo \"ross 9.9.10\"; exit 0;; esac\n",
        )
        .unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        let newer = deriver(&p.display().to_string());

        let h = |d: &RossDeriver| {
            let (r, m, v, params) = d.cache_key_parts();
            super::super::provenance::derivation_hash(&r, &m, &v, d.engine(), &params)
        };
        assert_ne!(h(&old), h(&newer));
    }

    #[cfg(unix)]
    #[test]
    fn llm_changes_the_params_and_timeout_and_path_do_not() {
        let dir = tempfile::tempdir().unwrap();
        let a = stub(&dir.path().join("a"), "exit 0");
        let b = stub(&dir.path().join("b"), "exit 0");
        let base = retry(|| RossDeriver::new(a.clone(), None, false, 10)).unwrap();
        // Neither the binary path nor the timeout changes what ross
        // COMPUTES, so neither may fragment the cache.
        let moved = retry(|| RossDeriver::new(b.clone(), None, false, 999)).unwrap();
        assert_eq!(base.params(), moved.params());
        assert_eq!(base.cache_key_parts(), moved.cache_key_parts());
        // `llm: true` needs a readable `--doctor`, because with the LLM on
        // the endpoint and model are hash inputs — see the LLM section.
        let with_llm =
            llm_deriver(&dir.path().join("llm"), &doctor_text("m", "none"), "exit 0").unwrap();
        assert_ne!(base.params(), with_llm.params());
    }

    // --- the LLM endpoint is an input, and it is in the hash -------------
    //
    // The defect these close, measured before the fix: same cell, same
    // images, only ROSS_MODEL changed between runs -> run 2 finished in
    // 0.35s against run 1's 9.22s, served run 1's three cache entries
    // verbatim, and the rows carried the first model's words with nothing
    // recording which model wrote them.

    /// The `llm: false` derivation hash, PINNED. Its whole point is that
    /// this fix did not move it: `llm_endpoints` is absent from the params
    /// when the LLM is off, so every cache entry written on the keyless
    /// path before the fix is still reachable after it. A future edit that
    /// changes this literal is invalidating real user caches and must say
    /// so out loud.
    const LLM_FALSE_HASH: &str =
        "314091bc293d054ae41970523db6b580df4c3acf2e6af04a36293c526531d829";

    #[cfg(unix)]
    #[test]
    fn the_llm_false_params_and_hash_did_not_move() {
        let dir = tempfile::tempdir().unwrap();
        let d = deriver(&stub(dir.path(), "exit 0"));
        assert_eq!(d.params(), json!({"llm": false, "labels_sha256": null}));
        let (r, m, v, params) = d.cache_key_parts();
        assert_eq!(
            super::super::provenance::derivation_hash(&r, &m, &v, d.engine(), &params),
            LLM_FALSE_HASH
        );
    }

    #[cfg(unix)]
    #[test]
    fn llm_false_never_runs_doctor() {
        // The keyless path must not pay a `--doctor` subprocess, and must
        // not be able to fail because of one.
        let dir = tempfile::tempdir().unwrap();
        let binary = stub_with_doctor(
            dir.path(),
            "exit 0",
            Some("echo \"boom\" >&2; exit 9"),
        );
        let d = retry(|| RossDeriver::new(binary.clone(), None, false, 30)).unwrap();
        assert_eq!(d.params(), json!({"llm": false, "labels_sha256": null}));
    }

    #[cfg(unix)]
    #[test]
    fn a_model_swap_changes_the_derivation_hash() {
        // THE regression test. Same binary, same config, same file — only
        // what `--doctor` resolves differs, exactly as swapping ROSS_MODEL
        // between two runs does.
        let dir = tempfile::tempdir().unwrap();
        let h = |d: &RossDeriver| {
            let (r, m, v, params) = d.cache_key_parts();
            super::super::provenance::derivation_hash(&r, &m, &v, d.engine(), &params)
        };
        let a = llm_deriver(
            &dir.path().join("a"),
            &doctor_text("mlx-community/gemma-4-e4b-it-4bit", "none"),
            "exit 0",
        )
        .unwrap();
        let b = llm_deriver(
            &dir.path().join("b"),
            &doctor_text("deepreinforce-ai/Ornith-1.0-9B", "none"),
            "exit 0",
        )
        .unwrap();
        assert_ne!(h(&a), h(&b), "the LLM model must be in the derivation hash");

        // ...and swapping BACK still agrees, so this is a key change and
        // not a blanket cache defeat.
        let c = llm_deriver(
            &dir.path().join("c"),
            &doctor_text("mlx-community/gemma-4-e4b-it-4bit", "none"),
            "exit 0",
        )
        .unwrap();
        assert_eq!(h(&a), h(&c));
    }

    #[cfg(unix)]
    #[test]
    fn a_model_swap_forces_a_real_re_derivation() {
        // The hash moving is only half the claim; the other half is that
        // the cache actually MISSES and the second model's text is what
        // lands. Driven end-to-end through `cached_derive`.
        use super::super::cache::{cached_derive, DerivationCache};

        let dir = tempfile::tempdir().unwrap();
        let img = dir.path().join("sprite_knight.png");
        std::fs::write(&img, b"fake png bytes").unwrap();
        let content_hash = {
            let mut h = Sha256::new();
            h.update(b"fake png bytes");
            format!("{:x}", h.finalize())
        };
        let cache = DerivationCache::new(Some(dir.path().join("cache")));
        // The derive arm echoes back the model, the way the real binary
        // stamps `model` when the LLM path wins.
        let body = |m: &str| {
            format!(
                r#"printf '{{"type":"image","description":"a knight, per {m}","model":"{m}"}}'"#
            )
        };
        let hash_of = |d: &RossDeriver| {
            let (r, mo, v, params) = d.cache_key_parts();
            super::super::provenance::derivation_hash(&r, &mo, &v, d.engine(), &params)
        };

        let a = llm_deriver(
            &dir.path().join("a"),
            &doctor_text("gemma", "none"),
            &body("gemma"),
        )
        .unwrap();
        let (ra, hit_a) = cached_derive(
            Some(&cache),
            &content_hash,
            &hash_of(&a),
            a.cache_key_extra(&img).as_deref(),
            || a.derive(&img),
        )
        .unwrap();
        assert!(!hit_a);

        let b = llm_deriver(
            &dir.path().join("b"),
            &doctor_text("ornith", "none"),
            &body("ornith"),
        )
        .unwrap();
        let (rb, hit_b) = cached_derive(
            Some(&cache),
            &content_hash,
            &hash_of(&b),
            b.cache_key_extra(&img).as_deref(),
            || b.derive(&img),
        )
        .unwrap();
        assert!(
            !hit_b,
            "a model swap must re-derive, not serve the old model's text"
        );
        assert_ne!(ra.text, rb.text);
        assert!(ra.text.contains("gemma"), "{}", ra.text);
        assert!(rb.text.contains("ornith"), "{}", rb.text);
    }

    #[cfg(unix)]
    #[test]
    fn the_endpoint_url_is_in_the_hash_too() {
        // Same model name served by two different hosts is two different
        // models in practice — a local mlx build and a hosted one do not
        // agree.
        let dir = tempfile::tempdir().unwrap();
        let one = doctor_text("m", "none");
        let two = one.replace("http://endpoint:8082/v1", "http://other:8082/v1");
        let a = llm_deriver(&dir.path().join("a"), &one, "exit 0").unwrap();
        let b = llm_deriver(&dir.path().join("b"), &two, "exit 0").unwrap();
        assert_ne!(a.params(), b.params());
    }

    #[cfg(unix)]
    #[test]
    fn key_presence_but_never_its_value_is_in_the_params() {
        // Adding a key to a previously keyless endpoint turns a 401 (and an
        // `llm_error` degraded derivation) into a real one, so it must
        // re-derive.
        let dir = tempfile::tempdir().unwrap();
        let none =
            llm_deriver(&dir.path().join("n"), &doctor_text("m", "none"), "exit 0").unwrap();
        let set = llm_deriver(&dir.path().join("s"), &doctor_text("m", "set"), "exit 0").unwrap();
        assert_ne!(none.params(), set.params());
        assert_eq!(none.params()["llm_endpoints"]["vision"]["key_set"], json!(false));
        assert_eq!(set.params()["llm_endpoints"]["vision"]["key_set"], json!(true));
    }

    #[cfg(unix)]
    #[test]
    fn no_api_key_reaches_the_params_the_metadata_or_an_error() {
        // The explicit leak assertion: a ross that PRINTS the key must not
        // get it into anything chunkshop keeps.
        let secret = "sk-do-not-leak-9f3a2b";
        let dir = tempfile::tempdir().unwrap();
        let d = llm_deriver(
            &dir.path().join("k"),
            &doctor_text("m", "set"),
            r#"printf '{"type":"image","description":"a knight","model":"m"}'"#,
        )
        .unwrap();
        let img = dir.path().join("x.png");
        std::fs::write(&img, b"fake").unwrap();
        let md = retry(|| d.derive(&img)).unwrap().metadata;

        let leaky = format!(
            "vision     http://h/v1 model=m key={secret}\naudio      NOT SET\nvideo      NOT SET\n"
        );
        let blobs = [
            d.params().to_string(),
            serde_json::to_string(&md).unwrap(),
            redact_doctor(&leaky),
            format!("{:#}", parse_doctor(&leaky, "ross").unwrap_err()),
        ];
        for b in &blobs {
            assert!(!b.contains(secret), "leaked: {b}");
        }
        assert!(
            blobs[0].contains("key_set"),
            "the BOOLEAN is kept; only the value is not"
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_not_set_modality_is_null_and_configuring_one_re_derives() {
        // `NOT SET` is the honest record — ross emits `llm_error: no
        // endpoint configured` for that modality — and pointing it
        // somewhere must re-derive.
        let dir = tempfile::tempdir().unwrap();
        let unset = llm_deriver(
            &dir.path().join("u"),
            "vision     NOT SET\naudio      NOT SET\nvideo      NOT SET\n",
            "exit 0",
        )
        .unwrap();
        assert_eq!(
            unset.params()["llm_endpoints"],
            json!({"vision": null, "audio": null, "video": null})
        );
        let set = llm_deriver(&dir.path().join("s"), &doctor_text("m", "none"), "exit 0").unwrap();
        assert_ne!(unset.params(), set.params());
    }

    #[cfg(unix)]
    #[test]
    fn an_unreadable_doctor_is_a_named_error_not_a_degraded_hash() {
        // A silent fallback to a hash that omits the model would
        // reintroduce the exact bug this closes, so every unreadable shape
        // is fatal — at CONSTRUCTION, before a `mode: overwrite` cell has
        // dropped the table.
        let cases = [
            // ross grew a format we cannot read.
            (
                "vision     http://h/v1 :: m\naudio      NOT SET\nvideo      NOT SET\n",
                "could not parse",
            ),
            // A modality vanished or was renamed — the silently-unhashed case.
            ("vision     NOT SET\naudio      NOT SET\n", "did not report video"),
            // `--doctor` printed nothing at all.
            ("", "did not report"),
            // An unexpected key state.
            (
                "vision     http://h/v1 model=m key=maybe\naudio      NOT SET\nvideo      NOT SET\n",
                "unexpected key state",
            ),
        ];
        for (i, (doctor, needle)) in cases.iter().enumerate() {
            let dir = tempfile::tempdir().unwrap();
            let msg = err_of(llm_deriver(&dir.path().join(i.to_string()), doctor, "exit 0"));
            assert!(msg.contains(needle), "case {i}: {msg}");
            assert!(
                msg.contains("ross --doctor"),
                "case {i}: the error must say how to see the real output: {msg}"
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn a_failing_doctor_is_a_named_error() {
        let dir = tempfile::tempdir().unwrap();
        let binary = stub_with_doctor(
            dir.path(),
            "exit 0",
            Some("echo \"no config\" >&2; exit 3"),
        );
        let msg = err_of(retry(|| RossDeriver::new(binary.clone(), None, true, 30)));
        assert!(msg.contains("status 3"), "{msg}");
        assert!(msg.contains("no config"), "ross's own stderr must be surfaced: {msg}");
    }

    // --- cross-port -------------------------------------------------------

    #[cfg(unix)]
    #[test]
    fn llm_params_match_the_python_port() {
        // Both ports must build the SAME params from the SAME `--doctor`
        // bytes. The fixture carries the literal output, so each port
        // replays identical input through its own parser rather than
        // through two hand-kept copies of a stub script. Params and key
        // sets are compared, never the derivation hash — see this file's
        // header and the fixture's own note on why.
        //
        // Lives here rather than in `tests/ross_deriver.rs` because it must
        // NOT be gated on the real binary: a machine without ross is
        // exactly where a parser divergence would otherwise go unnoticed.
        let fixture: Value = serde_json::from_str(
            &std::fs::read_to_string(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/tests/parity-fixtures/media_derivation_reference.json"
            ))
            .unwrap(),
        )
        .unwrap();
        let section = &fixture["rung5_llm_params"];
        let doctor = section["doctor_stdout"].as_str().expect(
            "fixture has no rung5_llm_params.doctor_stdout — regenerate with \
             tests/parity/generate_media_derivation_reference.py",
        );

        let dir = tempfile::tempdir().unwrap();
        let d = llm_deriver(dir.path(), doctor, "exit 0").unwrap();
        assert_eq!(
            d.params(),
            section["params"],
            "rung5: `llm: true` params must match Python's"
        );
        assert_eq!(
            json!({"llm": false, "labels_sha256": null}),
            section["params_llm_false"],
            "rung5: the `llm: false` params must match Python's, and must not \
             have grown a key"
        );
    }

    // --- Part B: the model actually used ---------------------------------

    #[cfg(unix)]
    #[test]
    fn ross_model_records_the_llm_that_actually_answered() {
        // The hash is computed before the run; the response says what
        // really answered.
        let dir = tempfile::tempdir().unwrap();
        let d = deriver(&stub(dir.path(), "exit 0"));
        let md = d
            .build_result(&json!({
                "type": "image",
                "description": "a knight",
                "model": "deepreinforce-ai/Ornith-1.0-9B",
            }))
            .metadata;
        assert_eq!(md["ross_model"], json!("deepreinforce-ai/Ornith-1.0-9B"));
        assert_eq!(md["derived"]["model"], json!("ross"), "the TOOL is still ross");
    }

    #[cfg(unix)]
    #[test]
    fn ross_model_never_claims_the_llm_when_the_llm_failed() {
        // ross stamps `model` only when the LLM call SUCCEEDED (it stamps
        // the local tagger's label only when no endpoint is configured at
        // all), so a kept-local-result-after-`llm_error` entry has no
        // `model` — and this deriver must not invent one.
        let dir = tempfile::tempdir().unwrap();
        let d = deriver(&stub(dir.path(), "exit 0"));
        let md = d
            .build_result(&json!({
                "type": "image",
                "description": "a knight",
                "tags": ["knight"],
                "llm_error": "endpoint returned 401",
            }))
            .metadata;
        assert_eq!(md["ross_model"], Value::Null);
        assert_eq!(md["ross_warnings"], json!(["endpoint returned 401"]));
        assert_eq!(md["ross_description"], json!("a knight"));
    }

    #[cfg(unix)]
    #[test]
    fn cache_key_extra_is_the_basename_only() {
        // ross's gated-tagger fallback humanises the BASENAME (it rsplits
        // the path on '/' first), so the parent directories are genuinely
        // not consumed and keying on them would needlessly fragment.
        let dir = tempfile::tempdir().unwrap();
        let d = deriver(&stub(dir.path(), "exit 0"));
        assert_eq!(
            d.cache_key_extra(Path::new("/one/two/knight.png")),
            Some("knight.png".to_string())
        );
        assert_eq!(
            d.cache_key_extra(Path::new("/other/knight.png")),
            Some("knight.png".to_string())
        );
        assert_ne!(
            d.cache_key_extra(Path::new("/one/two/other.png")),
            d.cache_key_extra(Path::new("/one/two/knight.png"))
        );
    }

    #[cfg(unix)]
    #[test]
    fn engine_is_ross_in_both_the_stamp_and_the_cache_key() {
        let dir = tempfile::tempdir().unwrap();
        let d = deriver(&stub(dir.path(), "exit 0"));
        assert_eq!(d.engine(), "ross");
        let r = d.build_result(&json!({"type": "image", "description": "a cat"}));
        assert_eq!(r.metadata["derived"]["engine"], json!("ross"));
        assert_eq!(r.metadata["derived"]["rung"], json!("ross"));
        assert_eq!(r.metadata["derived"]["model"], json!("ross"));
        assert_eq!(r.metadata["derived"]["model_version"], json!("9.9.9"));
    }

    #[cfg(unix)]
    #[test]
    fn cache_key_parts_match_the_stamped_provenance_tuple() {
        // Provenance and the cache key must never drift apart: rebuild the
        // hash from the RECORDED stamp, not from the test's own literals.
        let dir = tempfile::tempdir().unwrap();
        let d = deriver(&stub(dir.path(), "exit 0"));
        let (rung, model, version, params) = d.cache_key_parts();
        let expected =
            super::super::provenance::derivation_hash(&rung, &model, &version, d.engine(), &params);
        let md = d.build_result(&json!({"description": "a cat"})).metadata;
        let rec = md["derived"].as_object().unwrap();
        let rebuilt = super::super::provenance::hash_from_parts(
            rec["rung"].as_str().unwrap(),
            rec["model"].as_str().unwrap(),
            rec["model_version"].as_str().unwrap(),
            rec["engine"].as_str().unwrap(),
            rec["params_hash"].as_str().unwrap(),
        );
        assert_eq!(rebuilt, expected);
    }

    // --- the JSON -> ParseResult mapping ---------------------------------

    #[cfg(unix)]
    fn mapped(entry: Value) -> ParseResult {
        let dir = tempfile::tempdir().unwrap();
        deriver(&stub(dir.path(), "exit 0")).build_result(&entry)
    }

    #[cfg(unix)]
    #[test]
    fn the_metadata_key_set_is_fixed_regardless_of_what_ross_emitted() {
        // ross's own keys are conditional (`tags` appears only when a
        // tagger cleared its bar). A key set that varied by input could not
        // be asserted for cross-port parity, nor promoted to a column.
        let sparse = mapped(json!({"type": "image"}));
        let full = mapped(json!({
            "type": "image", "mime": "image/png", "description": "A cat.",
            "summary": "it is a cat", "tags": ["cat"], "tag_confidence": 0.4,
            "model": "clip:clip-vit-base-patch32",
            "metadata": {"width": 8, "height": 8}, "llm_error": "nope",
        }));
        let keys = |r: &ParseResult| {
            let mut k: Vec<String> = r.metadata.keys().cloned().collect();
            k.sort();
            k
        };
        assert_eq!(keys(&sparse), keys(&full));
        assert_eq!(
            keys(&sparse),
            vec![
                "content_origin",
                "derived",
                "derived_rung",
                "ross_description",
                "ross_metadata",
                "ross_mime",
                "ross_model",
                "ross_summary",
                "ross_tag_confidence",
                "ross_tags",
                "ross_type",
                "ross_warnings",
            ]
        );
    }

    #[cfg(unix)]
    #[test]
    fn text_is_description_then_summary_then_tags() {
        let r = mapped(json!({
            "description": "A cat.", "summary": "a cat sits",
            "tags": ["cat", "animal"],
        }));
        assert_eq!(r.text, "A cat. a cat sits cat animal");
    }

    #[cfg(unix)]
    #[test]
    fn nondeterministic_and_duplicated_fields_are_not_mapped() {
        // `duration_ms` is wall-clock timing and `mtime` is invisible to
        // the cache key: storing either would make a cache HIT differ from
        // a fresh derivation. `sha256`/`bytes` duplicate the document
        // fingerprint chunkshop already carries.
        let r = mapped(json!({
            "description": "x", "duration_ms": 22, "mtime": 1788304249,
            "sha256": "deadbeef", "bytes": 661, "path": "/tmp/x.png",
        }));
        let blob = serde_json::to_string(&r.metadata).unwrap();
        for banned in ["duration_ms", "mtime", "sha256", "bytes", "1788304249"] {
            assert!(!blob.contains(banned), "{banned} leaked into metadata: {blob}");
        }
    }

    #[cfg(unix)]
    #[test]
    fn soft_tagger_errors_are_collected_rather_than_swallowed() {
        let r = mapped(json!({"description": "x", "clip_error": "no model"}));
        assert_eq!(r.metadata["ross_warnings"], json!(["no model"]));
        let clean = mapped(json!({"description": "x"}));
        assert_eq!(clean.metadata["ross_warnings"], json!([]));
    }
}
