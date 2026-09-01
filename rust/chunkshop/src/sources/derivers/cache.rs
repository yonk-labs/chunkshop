//! Provenance-keyed memo cache for derivations. Mirrors
//! `python/src/chunkshop/sources/derivers/cache.py`. Layout matches
//! `raw_store/local.rs`'s content-addressed-directory pattern
//! (`sha256(key)[:2]/sha256(key).json`) — but `local.rs`'s `put` is a
//! direct `std::fs::write`, NOT atomic, unlike Python's cache.py
//! (tempfile + os.replace). This cache does its own atomic write via
//! `tempfile::Builder::tempfile_in` + `.persist()` rather than copying
//! `local.rs`'s non-atomic pattern verbatim.

use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::json;
use sha2::{Digest, Sha256};

use crate::sources::parsers::ParseResult;

/// `Some(v)` only if `v` is non-empty — mirrors Python's truthiness for
/// `os.environ.get(...)` (`if env:` is False for `""`, not just for
/// missing). `export XDG_CACHE_HOME=` is ordinary in shell scripts and
/// produces `Ok("")` from `std::env::var`, which is NOT the same as unset
/// to a naive `if let Ok(env) = ...` — that was the exact divergence this
/// helper closes.
fn nonempty(v: Option<&str>) -> Option<&str> {
    v.filter(|s| !s.is_empty())
}

/// Pure core of `default_cache_root`, taking the two raw env-var reads
/// (and the resolved home directory) as plain arguments instead of reading
/// `std::env` itself. Env vars are process-global state and Rust runs
/// tests on multiple threads by default, so a test that mutates
/// `CHUNKSHOP_CACHE_DIR`/`XDG_CACHE_HOME` in place would be flaky (or need
/// a mutex/serial guard around every test in the binary, including ones
/// that don't care about this behavior). Testing this pure fn instead
/// sidesteps that entirely and is deterministic by construction.
fn default_cache_root_from(
    chunkshop_cache_dir: Option<&str>,
    xdg_cache_home: Option<&str>,
    home: &Path,
) -> PathBuf {
    if let Some(env) = nonempty(chunkshop_cache_dir) {
        return PathBuf::from(env);
    }
    let base = nonempty(xdg_cache_home)
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".cache"));
    base.join("chunkshop").join("derived")
}

pub fn default_cache_root() -> PathBuf {
    let cache_dir = std::env::var("CHUNKSHOP_CACHE_DIR").ok();
    let xdg_cache_home = std::env::var("XDG_CACHE_HOME").ok();
    default_cache_root_from(cache_dir.as_deref(), xdg_cache_home.as_deref(), &dirs_home())
}

/// Resolves the current user's home directory for the `$XDG_CACHE_HOME`
/// unset/empty fallback (`$HOME/.cache`). The `dirs` crate is NOT a
/// dependency of this workspace (checked `Cargo.toml` — it appears only
/// transitively via other crates, which does not make it usable without
/// declaring it explicitly), so this reads `$HOME` directly instead of
/// adding a new dependency for one lookup. Not Windows-correct (no
/// `%USERPROFILE%` fallback) — accepted here since the rest of this
/// workspace already assumes a POSIX-ish environment (e.g. `raw_store`'s
/// file paths).
///
/// If `HOME` is unset or empty (same empty-is-unset truthiness as
/// `default_cache_root_from`), this deliberately does NOT panic — a
/// cache-root lookup failing should degrade a library caller, not crash
/// it. It falls back to `std::env::temp_dir()` (honours `TMPDIR`, else
/// `/tmp`).
///
/// KNOWN DIVERGENCE FROM PYTHON, deliberate. Python's `Path.home()` falls
/// back to the passwd database, so `default_cache_root()` there still
/// resolves the real home with `HOME` unset or empty (verified: both cases
/// yield `~/.cache/chunkshop/derived`). Rust's std has no `getpwuid`, and
/// matching it would mean taking a new dependency (`libc`/`dirs`/`home` —
/// none are present, even transitively) for an edge case. The temp dir is
/// chosen over the obvious `.` fallback specifically because `.` would
/// silently write a `.cache/` tree into the caller's working directory;
/// `HOME` is routinely unset under systemd units without PAM, under cron,
/// and in some containers. A cache in the temp dir is a legitimate cache;
/// a cache in someone's repo is litter. Consequence: with `HOME` unset the
/// two implementations do not share a cache directory. That costs a
/// recompute, never correctness — the cache key is unchanged.
///
/// Callers who need a guaranteed location should pass an explicit `root`
/// to `DerivationCache::new` rather than relying on `default_cache_root()`.
fn dirs_home() -> PathBuf {
    home_or_fallback(std::env::var("HOME").ok().as_deref())
}

/// Pure half of `dirs_home`, so the empty-vs-unset fallback is testable
/// without mutating process env (same reason `default_cache_root_from`
/// exists).
fn home_or_fallback(home_env: Option<&str>) -> PathBuf {
    nonempty(home_env)
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir)
}

/// The `key[..2]/key.json` half of the cache layout, independent of any
/// particular `root`. `key` is `sha256(content_hash + "\0" +
/// derivation_hash)` hex-encoded — the streaming `update()` calls below are
/// equivalent to Python's single-shot `f"{a}\x00{b}".encode()` since
/// SHA-256 is defined over a byte stream and chunk boundaries don't affect
/// the digest.
///
/// Exposed (not just inlined into `path_for`) so the cross-language parity
/// probe (`examples/hash_parity_probe.rs`) can call the SAME code the cache
/// actually uses, rather than a probe-only reimplementation that could
/// silently drift from it. This is the most parity-critical line in the
/// whole cache: if Rust and Python compute different keys here, the two
/// languages silently keep separate cache directories, which defeats the
/// entire reason `engine` was folded into `derivation_hash` (Task 6) — so
/// both languages can safely share one `--cache-dir`.
pub fn relative_key_path(
    content_hash: &str,
    derivation_hash: &str,
    extra: Option<&str>,
) -> String {
    let mut hasher = Sha256::new();
    hasher.update(content_hash.as_bytes());
    hasher.update(b"\0");
    hasher.update(derivation_hash.as_bytes());
    // `extra` is always folded in — as the empty string when a deriver
    // contributes nothing — so there is exactly ONE key formula rather than
    // a two-shape scheme where a pure deriver and a deriver whose extra
    // happens to be "" could land on different paths.
    hasher.update(b"\0");
    hasher.update(extra.unwrap_or("").as_bytes());
    let key = format!("{:x}", hasher.finalize());
    format!("{}/{}.json", &key[..2], key)
}

/// Memoises derivations on (content_hash, derivation_hash, extra).
///
/// `extra` is the deriver's own per-document cache-key contribution
/// (`CacheableDeriver::cache_key_extra`), and it exists because
/// content-addressing alone is only sound for a deriver that is a PURE
/// function of (bytes, params). `ImageMetaDeriver` is not: it derives its
/// output text from the filename and path segments, so two byte-identical
/// images with different names must NOT share an entry. Rather than fold
/// the path into every key — which would defeat content-addressing for the
/// derivers that genuinely are pure, e.g. two copies of the same PDF that
/// should derive once — each deriver declares what per-document material it
/// actually consumes. Pure derivers contribute `None` and keep the exact
/// sharing behaviour they had before.
pub struct DerivationCache {
    root: PathBuf,
}

impl DerivationCache {
    pub fn new(root: Option<PathBuf>) -> Self {
        Self {
            root: root.unwrap_or_else(default_cache_root),
        }
    }

    fn path_for(
        &self,
        content_hash: &str,
        derivation_hash: &str,
        extra: Option<&str>,
    ) -> PathBuf {
        self.root
            .join(relative_key_path(content_hash, derivation_hash, extra))
    }

    pub fn get(
        &self,
        content_hash: &str,
        derivation_hash: &str,
        extra: Option<&str>,
    ) -> Option<ParseResult> {
        let path = self.path_for(content_hash, derivation_hash, extra);
        let bytes = std::fs::read(&path).ok()?;
        let payload: serde_json::Value = serde_json::from_slice(&bytes).ok()?;
        // Missing, unreadable, malformed, OR structurally wrong (e.g. valid
        // JSON that isn't an object, or an object missing "text"/"metadata")
        // entries are all misses, never errors — mirrors Python's
        // `except (OSError, ValueError, KeyError, TypeError)` catch-all,
        // which the adversarial test below specifically exercises with a
        // syntactically-valid-but-wrong-shape payload (e.g. a JSON array),
        // not just malformed JSON.
        //
        // DELIBERATE DEVIATION from Python (documented, do not "fix"):
        // Python's `get()` does `ParseResult(text=payload["text"],
        // metadata=payload["metadata"])` with NO type check on the loaded
        // values — a cache entry `{"text": 42, "metadata": {}}` is accepted
        // there and yields a ParseResult whose `.text` is an int. The
        // `.as_str()?` / `.as_object()?` calls below instead treat a
        // wrong-typed entry as a miss. Rust's behavior here is correct;
        // Python's is a latent bug this port does not reproduce.
        let obj = payload.as_object()?;
        let text = obj.get("text")?.as_str()?.to_string();
        let metadata = obj.get("metadata")?.as_object()?.clone();
        Some(ParseResult { text, metadata })
    }

    pub fn put(
        &self,
        content_hash: &str,
        derivation_hash: &str,
        result: &ParseResult,
        extra: Option<&str>,
    ) -> anyhow::Result<()> {
        let path = self.path_for(content_hash, derivation_hash, extra);
        let dir = path.parent().expect("path_for always has a parent");
        std::fs::create_dir_all(dir)?;
        let payload = json!({"text": result.text, "metadata": result.metadata});
        let bytes = serde_json::to_vec(&payload)?;

        // Atomic write: temp file in the SAME directory (same filesystem —
        // required for persist()'s rename to be atomic), then persist().
        let mut tmp = tempfile::Builder::new()
            .prefix(".tmp-")
            .tempfile_in(dir)?;
        tmp.write_all(&bytes)?;
        tmp.persist(&path)
            .map_err(|e| anyhow::anyhow!("atomic persist of cache entry failed: {e}"))?;
        Ok(())
    }

    pub fn purge(&self) -> anyhow::Result<usize> {
        let mut count = 0usize;
        if !self.root.exists() {
            return Ok(0);
        }
        for entry in walkdir_json_files(&self.root)? {
            // Deliberately unconditional (matches Python's
            // `unlink(missing_ok=True)` + `count += 1` regardless of
            // outcome): a removal racing a concurrent purge, or a file
            // that vanished between listing and removal, still counts.
            // `purge()` reports "how many entries this pass touched", not
            // "how many were provably deleted by this call".
            let _ = std::fs::remove_file(&entry);
            count += 1;
        }
        Ok(count)
    }
}

/// Two-level walk mirroring `path_for`'s layout exactly: `root /
/// key[..2] / key.json` nests exactly one directory deep, by construction
/// — never more, since `path_for` never produces a deeper path. So this is
/// NOT a general recursive walk and does not need the `walkdir` crate
/// (not a dependency of this workspace). Python's `purge()` uses
/// `Path.rglob("*.json")`, which IS fully recursive: a cache `root`
/// pointed at some deeper, non-chunkshop-owned directory tree (e.g. an
/// existing directory with nested subfolders reused as the cache dir)
/// would see Python delete nested `.json` files this two-level walk would
/// skip. That divergence is accepted and documented here (chunkshop itself
/// never writes anything deeper than two levels under `root`, so it never
/// arises from chunkshop's own writes) rather than "fixed" by adding a
/// general-recursion dependency for a case that can't occur from this
/// cache's own output.
fn walkdir_json_files(root: &Path) -> anyhow::Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    for entry in std::fs::read_dir(root)? {
        let sub = entry?.path();
        if !sub.is_dir() {
            continue;
        }
        for inner in std::fs::read_dir(&sub)? {
            let inner_path = inner?.path();
            if inner_path.extension().and_then(|e| e.to_str()) == Some("json") {
                files.push(inner_path);
            }
        }
    }
    Ok(files)
}

/// Returns `(result, was_cache_hit)`. A `None` cache disables memoisation.
/// `extra` is the deriver's `cache_key_extra(path)` contribution; see
/// `DerivationCache` for why it is per-deriver rather than always the path.
pub fn cached_derive<F>(
    cache: Option<&DerivationCache>,
    content_hash: &str,
    derivation_hash: &str,
    extra: Option<&str>,
    compute: F,
) -> anyhow::Result<(ParseResult, bool)>
where
    F: FnOnce() -> anyhow::Result<ParseResult>,
{
    let Some(cache) = cache else {
        return Ok((compute()?, false));
    };
    if let Some(hit) = cache.get(content_hash, derivation_hash, extra) {
        return Ok((hit, true));
    }
    let result = compute()?;
    cache.put(content_hash, derivation_hash, &result, extra)?;
    Ok((result, false))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cache(tmp: &tempfile::TempDir) -> DerivationCache {
        DerivationCache::new(Some(tmp.path().to_path_buf()))
    }

    #[test]
    fn home_fallback_uses_temp_dir_not_the_cwd() {
        // `.` would silently write a `.cache/` tree into the caller's working
        // directory. HOME is routinely unset under systemd units without PAM,
        // under cron, and in some containers, so this is reachable in practice.
        let tmp = std::env::temp_dir();
        assert_eq!(home_or_fallback(None), tmp, "unset HOME must not yield a relative path");
        assert_eq!(home_or_fallback(Some("")), tmp, "empty HOME is treated as unset");
        assert_ne!(home_or_fallback(None), PathBuf::from("."));
        assert_eq!(home_or_fallback(Some("/home/someone")), PathBuf::from("/home/someone"));
    }

    #[test]
    fn miss_then_hit() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        assert!(c.get("c1", "d1", None).is_none());
        c.put("c1", "d1", &ParseResult::new("cat"), None).unwrap();
        let got = c.get("c1", "d1", None).unwrap();
        assert_eq!(got.text, "cat");
    }

    #[test]
    fn different_derivation_hash_is_a_miss() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("old"), None).unwrap();
        assert!(c.get("c1", "d2", None).is_none());
    }

    #[test]
    fn different_content_hash_is_a_miss() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("old"), None).unwrap();
        assert!(c.get("c2", "d1", None).is_none());
    }

    #[test]
    fn cached_derive_computes_once() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        let calls = std::cell::Cell::new(0);
        let compute = || {
            calls.set(calls.get() + 1);
            Ok(ParseResult::new("expensive"))
        };
        let (r1, hit1) = cached_derive(Some(&c), "c1", "d1", None, compute).unwrap();
        let (r2, hit2) = cached_derive(Some(&c), "c1", "d1", None, compute).unwrap();
        assert_eq!(r1.text, "expensive");
        assert_eq!(r2.text, "expensive");
        assert!(!hit1 && hit2);
        assert_eq!(calls.get(), 1, "second call must not recompute");
    }

    #[test]
    fn purge_empties_the_cache() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("x"), None).unwrap();
        c.put("c2", "d1", &ParseResult::new("y"), None).unwrap();
        assert_eq!(c.purge().unwrap(), 2);
        assert!(c.get("c1", "d1", None).is_none());
    }

    #[test]
    fn malformed_json_entry_is_a_miss() {
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("x"), None).unwrap();
        for entry in std::fs::read_dir(tmp.path()).unwrap() {
            let sub = entry.unwrap().path();
            if sub.is_dir() {
                for f in std::fs::read_dir(&sub).unwrap() {
                    std::fs::write(f.unwrap().path(), b"{not json").unwrap();
                }
            }
        }
        assert!(c.get("c1", "d1", None).is_none());
    }

    #[test]
    fn well_formed_but_wrong_shaped_json_is_also_a_miss() {
        // The adversarial case the Python plan's own retro named:
        // "corrupt entries are misses (tested malformed JSON, missed
        // valid-non-dict)". A syntactically valid JSON ARRAY (not an
        // object with text/metadata) must also degrade to a miss, not
        // panic or silently return garbage.
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("x"), None).unwrap();
        for entry in std::fs::read_dir(tmp.path()).unwrap() {
            let sub = entry.unwrap().path();
            if sub.is_dir() {
                for f in std::fs::read_dir(&sub).unwrap() {
                    std::fs::write(f.unwrap().path(), b"[1, 2, 3]").unwrap();
                }
            }
        }
        assert!(
            c.get("c1", "d1", None).is_none(),
            "a syntactically valid but wrong-shaped JSON payload must be a miss, not a panic"
        );
    }

    #[test]
    fn wrong_typed_text_field_is_a_miss() {
        // Correction 3: proves the deliberate deviation documented on
        // `get()` above. Python accepts this exact payload (its `.text`
        // ends up an `int`); Rust must treat it as a miss.
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("x"), None).unwrap();
        for entry in std::fs::read_dir(tmp.path()).unwrap() {
            let sub = entry.unwrap().path();
            if sub.is_dir() {
                for f in std::fs::read_dir(&sub).unwrap() {
                    std::fs::write(f.unwrap().path(), br#"{"text": 42, "metadata": {}}"#).unwrap();
                }
            }
        }
        assert!(
            c.get("c1", "d1", None).is_none(),
            "a wrong-typed `text` field must be a miss, not an int masquerading as a String"
        );
    }

    #[test]
    fn relative_key_path_is_pinned() {
        // Literal known-good value so a future refactor of the hashing
        // breaks LOUDLY here instead of silently diverging from Python.
        // Computed independently via: printf 'c1\x00d1\x00' | sha256sum
        assert_eq!(
            relative_key_path("c1", "d1", None),
            "c0/c025b0ee86847c86d25f74d8016c44c3db6d52378e26b5679ffeda0c9f98090a.json"
        );
        // A `None` extra and an EMPTY extra are the same key by
        // construction: there is one formula, not two shapes.
        assert_eq!(
            relative_key_path("c1", "d1", None),
            relative_key_path("c1", "d1", Some(""))
        );
        // printf 'c1\x00d1\x00a chart revenue' | sha256sum
        assert_eq!(
            relative_key_path("c1", "d1", Some("a chart revenue")),
            "03/033455ba3ef8e3338f24e29cb3a1a2bd3f2d52aa4543ff7c6d1067d64cc219e1.json"
        );
    }

    #[test]
    fn different_extra_is_a_miss_on_identical_content_and_derivation() {
        // The whole point of the third key component: same bytes, same
        // deriver config, different per-document material -> separate
        // entries, not one poisoned entry serving both documents.
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        c.put("c1", "d1", &ParseResult::new("a chart revenue"), Some("a chart revenue"))
            .unwrap();
        assert!(c.get("c1", "d1", Some("b goblin warrior")).is_none());
        assert_eq!(
            c.get("c1", "d1", Some("a chart revenue")).unwrap().text,
            "a chart revenue"
        );
    }

    #[test]
    fn pure_derivers_still_share_one_entry() {
        // The property being PRESERVED: with no extra, byte-identical
        // input under the same derivation still hits. If this ever fails,
        // the fix has been widened into "path in every key" and
        // content-addressing is gone.
        let tmp = tempfile::tempdir().unwrap();
        let c = cache(&tmp);
        let (_, hit1) =
            cached_derive(Some(&c), "c1", "d1", None, || Ok(ParseResult::new("body"))).unwrap();
        let (r2, hit2) =
            cached_derive(Some(&c), "c1", "d1", None, || Ok(ParseResult::new("body"))).unwrap();
        assert!(!hit1 && hit2);
        assert_eq!(r2.text, "body");
    }

    #[test]
    fn empty_chunkshop_cache_dir_env_treated_as_unset() {
        // Correction 1: `export CHUNKSHOP_CACHE_DIR=` is `Ok("")` from
        // `std::env::var`, not absent — Python's `if env:` truthiness
        // treats both the same way, so Rust must too.
        let home = Path::new("/home/testuser");
        let unset = default_cache_root_from(None, None, home);
        let empty = default_cache_root_from(Some(""), None, home);
        assert_eq!(unset, empty);
        assert_eq!(unset, home.join(".cache").join("chunkshop").join("derived"));
    }

    #[test]
    fn empty_xdg_cache_home_env_treated_as_unset() {
        let home = Path::new("/home/testuser");
        let unset = default_cache_root_from(None, None, home);
        let empty = default_cache_root_from(None, Some(""), home);
        assert_eq!(unset, empty);
    }

    #[test]
    fn nonempty_chunkshop_cache_dir_env_wins() {
        let home = Path::new("/home/testuser");
        let got = default_cache_root_from(Some("/explicit/cache"), Some("/xdg"), home);
        assert_eq!(got, PathBuf::from("/explicit/cache"));
    }

    #[test]
    fn nonempty_xdg_cache_home_env_used_when_chunkshop_cache_dir_unset() {
        let home = Path::new("/home/testuser");
        let got = default_cache_root_from(None, Some("/xdg"), home);
        assert_eq!(got, PathBuf::from("/xdg").join("chunkshop").join("derived"));
    }
}
