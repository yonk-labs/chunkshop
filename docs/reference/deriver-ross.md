# `ross` deriver (rung 5 — the `ross` binary, as a subprocess)

**Module**: `chunkshop.sources.derivers.ross`
**Type**: Deriver — rung 5 of the media escalation ladder (the escape hatch)
**Ship status**: verified
**Optional extra**: none — no Python extra, no Cargo feature. Requires the `ross` binary at **runtime**, plus `ffprobe`/`ffmpeg` for audio and video.
**Since**: 2026-09-01 (commit `bb3e8d1`; Rust `a196adc`)

## Purpose

Derive text from **images, audio and video** by shelling out to
[`ross`](https://github.com/yonk-labs/ross) (yonk-labs/ross, Apache-2.0) — a
single Rust binary that takes media in and emits deterministic facts, tags, a
description and optionally an LLM summary.

Two things make this rung worth reaching for:

- **It is the only audio path anywhere in chunkshop.** ross sniffs the file
  type by **magic bytes**, not by extension, so one `derive:` block covers all
  three modalities: CLIP for images and video, CLAP for audio, with the same
  metadata key set either way. There is no in-process audio tagger in any
  rung, plan, or port.
- **It is natural-language, and keyless anyway.** `llm: false` (the default)
  runs ross's local taggers fully offline after a one-time model download.
  `llm: true` adds one call per file to any OpenAI-compatible endpoint for
  full descriptions.

Being a subprocess is the point, not a compromise. Rung 5 in the spec's ladder
(NN-5, "never a dead end") is defined as exactly this shape, and it means a
text-only user — and a `cargo build --no-default-features` — pays literally
nothing for it.

## Config schema

`chunkshop.config.RossDeriverConfig` (pydantic v2, `extra="forbid"`;
Rust `#[serde(deny_unknown_fields)]`). Configured under `source.derive`, keyed
by a pipe-separated extension list.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `rung` | `Literal["ross"]` | **Required** | Discriminator. Note the key is `rung`, not `type`. |
| `binary` | `str` | `"ross"` | Path to, or name of, the executable. The default resolves on `$PATH`. Not in the derivation hash — the path a binary is reached by does not change what it computes; its version does, and that is `model_version`. |
| `labels_file` | `str?` | `None` | Replace ross's vendored 50-label vocabulary (`--labels-file`). The file's **content** is sha256'd into the derivation params, so editing it in place re-derives while moving it does not. |
| `llm` | `bool` | `False` | Run ross's LLM pass. `False` passes `--no-llm`. |
| `timeout_s` | `int` | `120` | Per-file subprocess timeout, and also the timeout on the construction-time probes. Sized for ross's first run, which downloads its CLIP/CLAP weights (~89 MB / ~78 MB) into `~/.cache/ross-clap`. Not in the derivation hash — a timeout only bounds how long chunkshop waits. |

**`--clip` and `--clap` are not configurable.** Both ports always pass them.
Without them, and with `--no-llm`, ross emits deterministic facts and **no
`description` at all** — every document would derive to empty text and trip
`on_empty`. Passing both is also how one deriver covers images (CLIP) and
audio (CLAP) from a single config block.

## Public API

```python
from chunkshop.sources.derivers.ross import RossDeriver, ross_available

class RossDeriver:
    supported_extensions = [
        "png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif",   # image
        "wav", "mp3", "flac", "ogg", "m4a", "aac",                   # audio
        "mp4", "mov", "webm", "mkv", "avi",                          # video
    ]

    def __init__(self, binary: str = "ross", labels_file: str | None = None,
                 llm: bool = False, timeout_s: int = 120) -> None: ...
    def derive(self, path: Path) -> ParseResult: ...
    def derivation_hash(self) -> str: ...
    def cache_key_extra(self, path: Path) -> str | None: ...

def ross_available(binary: str = "ross") -> bool: ...   # for test gating
```

`supported_extensions` documents what the tool covers; `FilesSource` routes
purely on the extensions named in your `derive:` key.

## Behavior contract

1. **Argv is fixed.** `ross <path> --json --quiet --clip --clap`, plus
   `--no-llm` unless `llm: true`, plus `--labels-file <path>` when set.
2. **Three probes run eagerly at construction**, before `sink.create_table()`
   and therefore before a `mode: overwrite` cell has dropped anything:
   - `labels_file` is read and sha256'd (skipped when unset);
   - `ross --version` is run — this doubles as the existence check and is what
     `model_version` records, never hardcoded;
   - `ross --doctor` is run **only when `llm: true`**, and its per-modality
     url + model + key-set boolean are folded into `params["llm_endpoints"]`.
3. **Text is `description`, then `summary`, then `tags`**, space-joined,
   skipping empties. No de-duplication: when the local tagger fires, ross's own
   description restates the tags, which is harmless for embedding and keeps the
   two ports trivially identical.
4. **The metadata key set is fixed at nine keys**, always present, holding
   `null` / `[]` / `{}` when ross omitted the field. ross's own output keys are
   conditional, and a key set that varies by input cannot be asserted for
   cross-port parity nor promoted to a column.
5. **`engine` is `"ross"`, not `"python"`/`"rust"`.** Both ports drive the same
   binary, so the derivation is genuinely ross's rather than either port's —
   and this is what lets a Rust `chunkshop-rs ingest` legitimately serve rows
   from cache entries a prior Python run wrote.
6. **`cache_key_extra` is the file's basename**, not `None` and not the full
   path. When the local tagger is gated, ross falls back to a description
   humanised from the basename (`clap_tag::humanize` rsplits on `/` first), so
   the deriver is not a pure content function — but the parent directories
   genuinely are not consumed, so keying on them would needlessly break sharing.
7. **Nothing degrades to a silent skip.** A missing, non-executable, non-ross
   or failing binary, a timeout, an unparseable `--json` payload, a
   ross-reported per-file error, and an empty result array each produce a
   named error.

## Metadata stamped

```python
{
    "ross_type":           "image",         # str|None — "image" | "audio" | "video"
    "ross_mime":           "image/png",     # str|None
    "ross_description":    "sprite knight.",# str|None — the description, or None if empty
    "ross_summary":        None,            # str|None — the LLM summary, when there is one
    "ross_tags":           [],              # list[str] — always a list, possibly empty
    "ross_tag_confidence": None,            # ross's own confidence for the tag set, or None
    "ross_model":          "clip:clip-vit-base-patch32",  # str|None — see below
    "ross_metadata":       {"width": 128, "height": 128,  # dict — ross's fact block
                            "note": "exiftool not found; minimal metadata only"},
    "ross_warnings":       [],              # list[str] — clip_error/clap_error/llm_error
    # plus the provenance block:
    "content_origin": "derived",
    "derived_rung": "ross",
    "derived": {"rung": "ross", "model": "ross", "model_version": "0.1.0",
                "engine": "ross", "params_hash": "...", "ts": "..."},
}
```

**`ross_model` vs `derived.model`.** `derived.model` is always `"ross"` and
identifies the *tool*; `ross_model` is ross's own report of *which model
produced the text* — `clip:clip-vit-base-patch32` for the local tagger, or the
LLM's model name when the endpoint path won. It is read off the **response**,
whereas `derived.model_version` is fixed at **construction**, so a row stays
attributable even if endpoint resolution moved in between.

It is deliberately **not defaulted**: ross stamps the local tagger's label only
when no endpoint is configured at all, and the LLM's name only when the call
*succeeded* — so when an LLM call was attempted and failed, this lands `None`.
That is the honest record; the `llm_error` itself is in `ross_warnings`, so a
null here is always explicable from the same row.

**Deliberately not mapped:** ross's `path` (`FilesSource` stamps `source_path`),
`sha256`/`bytes` (chunkshop already carries the content hash as the document
fingerprint), `mtime` (invisible to the cache key — caching it would serve a
stale value the moment a file is touched) and `duration_ms` (wall-clock timing,
which would make a cache hit differ from a fresh derivation on every row).

## Inputs

A path to any file ross recognises by magic bytes. The extension only decides
*routing*: whatever you name in the `derive:` key goes to this deriver.

## Outputs

| Field | Value |
|-------|-------|
| `ParseResult.text` | `description` + `summary` + `tags`, space-joined, empties skipped. |
| `ParseResult.metadata` | The nine `ross_*` keys plus the provenance block. |

## Errors

| Exception | When |
|-----------|------|
| `RuntimeError` (construction) | The binary is missing, not executable, not runnable, times out on `--version`, exits non-zero, or prints nothing parseable. Message names the binary and points at ross's repo. |
| `RuntimeError` (construction) | `labels_file` cannot be read. Names the path. |
| `RuntimeError` (construction, `llm: true` only) | `ross --doctor` times out, exits non-zero, or prints a shape this deriver cannot parse. There is **no degraded mode** — falling back to a hash that omits the model is precisely the defect this probe exists to close. Any `--doctor` output quoted into the message is redacted at ` key=`. |
| `ParserError` | ross exits non-zero for a file. Message carries ross's documented exit-code meaning **and ross's own stderr verbatim** — which is what names a missing `ffprobe`/`exiftool`, since chunkshop cannot. |
| `ParserError` | The per-file subprocess exceeds `timeout_s`. Names the knob. |
| `ParserError` | `--json` output is unparseable, an unexpected shape, or an empty array (ross skips files it does not recognise as media). |
| `ParserError` | ross reported a per-file `error` field. Never an empty derivation. |

Exit codes ross documents, and what the message says about them:

| Exit | Meaning surfaced |
|---|---|
| 2 | ross ran with `--strict` and at least one file errored |
| 3 | no LLM endpoint configured — set `ROSS_URL` + `ROSS_MODEL`, or leave `llm: false` |
| 4 | ross rejected the input or the arguments |
| 5 | ross could not load its local CLIP/CLAP models (first run downloads ~89 MB/78 MB into `~/.cache/ross-clap`) |

## The LLM pass

`llm: false` is the default and means keyless and offline. With `llm: true`,
ross resolves its endpoint, model and key **from the environment**, by its own
precedence, per modality:

| Variable | Falls back to |
|---|---|
| `ROSS_VISION_URL` / `ROSS_AUDIO_URL` / `ROSS_VIDEO_URL` | `ROSS_URL` |
| `ROSS_VISION_MODEL` / `ROSS_AUDIO_MODEL` / `ROSS_VIDEO_MODEL` | `ROSS_MODEL` |
| `ROSS_VISION_API_KEY` / … | `ROSS_API_KEY` |

**None of this is read from chunkshop config**, so credentials stay out of your
YAML and out of provenance. Resolution is delegated to ross rather than
reimplemented, because duplicating its precedence in two host languages is a
drift class this rung already has enough of.

They are still in the derivation hash. The API key's **value** is never read,
hashed, stamped or logged; only the boolean ross itself prints
(`key=set` / `key=none`) enters the params — and it earns its place, because
adding a key to a previously keyless endpoint turns a 401-and-`llm_error`
degraded derivation into a real one, which must re-derive rather than serve the
degraded text forever.

> **LLM output is not deterministic.** Rungs 0 and 1 are pure functions of
> their bytes; this one, with `llm: true`, is not. Two fresh derivations of the
> same image can differ even with the endpoint, model and prompt identical —
> the hash pins the **inputs**, not the output. On this rung the cache is
> therefore not an optimisation, it is what makes a corpus stable, and purging
> it before a re-ingest can legitimately change your derived text and therefore
> your embeddings with no config change at all.

## Known cache limitations

Both are properties of ross's inputs that its CLI does not expose, so they
cannot be folded into the key.

1. **The vendored vocabulary is identified only by `ross --version`.** ross
   embeds a 50-label list (`src/clip_labels.txt`, `src/clap_labels.txt`) and
   its precomputed text features inside the binary; `--json` does not echo the
   label set and `--doctor` reports only external binaries, endpoint config and
   model cache paths. **A ross built from source that edits `clip_labels.txt`
   without bumping its package version will silently serve stale cached
   derivations.** A user-supplied `labels_file` has no such gap.
2. **ross's `metadata` block varies with what is installed** — `exiftool` for
   images, `ffprobe` for audio/video, with a native PNG/WebP header fallback
   when absent. Installing `exiftool` part-way through a corpus changes
   `ross_metadata` for files already cached.

Mitigation for both: `cache: {enabled: false}`, or purge the cache after
upgrading a source-built ross or changing the installed tool set.

`ross --doctor --json` is accepted but ignored as of ross 0.1.0, so both ports
hand-parse human-readable output. A ross release that rewords `--doctor` breaks
this rung **loudly at construction** — the correct failure mode, but one worth
removing upstream.

## Example: minimal

```yaml
source:
  type: files
  glob: /media/**/*
  derive:
    png|jpg|wav|mp3|mp4:
      rung: ross
```

## Example: realistic (the shipped recipe)

[`docs/samples/media-ross.yaml`](../samples/media-ross.yaml):

```yaml
cell_name: media_ross
source:
  type: files
  glob: docs/samples/images/*.png
  id_from: stem
  derive:
    png|jpg|jpeg|webp|gif|bmp|tiff|wav|mp3|flac|ogg|m4a|mp4|mov|webm:
      rung: ross
      # binary: ross          # default; set to a full path if not on $PATH
      # labels_file: ./my-vocabulary.txt   # replace ross's vendored 50 labels
      # llm: false            # true needs ROSS_URL + ROSS_MODEL
      # timeout_s: 120        # per file; the first run downloads models
chunker:
  type: fixed_overlap
  window_words: 512
  step_words: 512
embedder:
  type: fastembed
  model_name: Xenova/bge-base-en-v1.5-int8
  dim: 768
extractor:
  type: none
target:
  type: sqlite
  dsn_env: SQLITE_PATH
  database: ignored
  table: media
  mode: overwrite
  hnsw: false
```

```bash
export SQLITE_PATH=/tmp/chunkshop-media.db
chunkshop ingest --config docs/samples/media-ross.yaml
```

```
[chunkshop.runner] cell media_ross starting
[chunkshop.runner] creating target table
[chunkshop.runner] cell media_ross DONE docs=3 chunks=3 wall=6.1s
```

```sql
SELECT doc_id, content_origin, derived_rung, original_content FROM media;
```

```
chart_revenue        derived  ross  Logo, diagram. logo diagram
screenshot_settings  derived  ross  User interface. user interface
sprite_knight        derived  ross  sprite knight.
```

`sprite_knight` is the gated-tagger fallback in action: no label cleared ross's
confidence bar, so the description is the humanised basename — which is exactly
why `cache_key_extra` is the basename and not `None`.

Measured on one developer machine over that three-image corpus with ross's
models already cached: **6.1 s cold cache, 0.6 s warm.**

## How it integrates with the pipeline

Identical to any other deriver. The one difference worth planning around is the
shared cache: because `engine` is `"ross"` in both ports, Python and Rust
compute the **same** derivation hash for this rung and share cache entries —
unlike rungs 0 and 1, whose `engine` is `"python"`/`"rust"` and which therefore
maintain separate caches per implementation.

Audio and video need no extra wiring beyond listing their extensions in the
`derive:` key.

## Tests proving the contract

- `python/tests/chunkshop/test_deriver_ross.py` — 39 tests, including:
  - missing / non-executable / not-really-ross / non-zero-exit binaries each
    produce a named construction error
  - unreadable `labels_file` fails at construction
  - a non-zero derive exit surfaces ross's stderr with a hint; a timeout names
    the knob; unparseable JSON is a named error
  - a ross-reported per-file error is never an empty derivation
  - `model_version` is probed, not hardcoded; the vendored vocabulary is
    identified by the binary version; `labels_file` **content** (not path) is
    in the params
  - `llm` changes the derivation hash; `timeout_s` and `binary` do not
  - `llm: false` never runs `--doctor`, and its params/hash are pinned as
    literals so they cannot drift
  - a model swap forces a re-derivation; the endpoint url is in the hash
  - `cache_key_extra` is the basename only
- `rust/chunkshop/tests/ross_deriver.rs` — the mirrored Rust suite.
- Sample: [`docs/samples/media-ross.yaml`](../samples/media-ross.yaml).

## See also

- Reference: [`deriver-image-meta`](deriver-image-meta.md) — rung 0, in-process and keyless
- Reference: [`deriver-clip-tags`](deriver-clip-tags.md) — rung 1, in-process CLIP tagging
- [`docs/media-ingest.md`](../media-ingest.md) — the ladder, the cache, the non-determinism caveat
- `DECISIONS.md` (2026-09-01) — why ross is an added rung and not a replacement for rungs 0/1
- [ross](https://github.com/yonk-labs/ross) — the binary itself
