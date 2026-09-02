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
        Ok(Self {
            binary,
            labels_file,
            llm,
            timeout_s,
            labels_sha256,
            version,
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
    fn params(&self) -> Value {
        json!({
            "llm": self.llm,
            "labels_sha256": self.labels_sha256,
        })
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
        use std::os::unix::fs::PermissionsExt;
        std::fs::create_dir_all(dir).unwrap();
        let p = dir.join("ross");
        std::fs::write(
            &p,
            format!(
                "#!/bin/sh\ncase \"$1\" in --version) echo \"ross 9.9.9\"; exit 0;; esac\n{body}\n"
            ),
        )
        .unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        p.display().to_string()
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
        let with_llm = retry(|| RossDeriver::new(a.clone(), None, true, 10)).unwrap();
        assert_ne!(base.params(), with_llm.params());
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
