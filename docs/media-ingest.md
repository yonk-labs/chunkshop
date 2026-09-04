# Media ingest — turning non-text bytes into searchable text

chunkshop's pipeline has always been `Source → Chunker → Embedder → Extractor
→ Sink`, and every stage after the source assumes it is handed **text**. Media
ingest does not change that. It adds one step in front of it: a **deriver**
converts an image (or, through rung 5, audio and video) into text, and that
text then flows through the ordinary pipeline and lands in the **same table,
in the same columns, as any other document**.

There is no second vector space, no fusion step, and no new search verb. A
derived image row and a verbatim email row differ in exactly two places: the
`content_origin` / `derived_rung` columns that say where the text came from,
and the `derived.*` provenance block in `metadata`.

Media is **opt-in per cell**. A `files` source with no `derive:` block behaves
exactly as it always has, builds no cache, and imports nothing from the media
path.

## The escalation ladder

Derivers are organised as *rungs*: the cheapest, most deterministic thing
first, with each step up in richness costing something you have to opt into.
Three rungs ship today.

| Rung | `rung:` | What it produces | Needs | Deterministic? |
|---|---|---|---|---|
| 0 | `image_meta` | Image header facts (`width`, `height`, format, mode, alpha, aspect ratio) plus humanised filename/path text | nothing — Pillow arrives with `fastembed` | ✅ pure function of bytes + filename |
| 1 | `clip_tags` | Rung 0's output **plus** zero-shot CLIP tags against a vocabulary you supply | nothing — `fastembed` is already core; ~0.6 GB of ONNX weights download lazily on first use | ✅ pure function of bytes + filename + vocabulary |
| 5 | `ross` | Description, summary and tags for **images, audio and video**, from the `ross` sibling binary | the `ross` binary on `$PATH`; `ffprobe`/`ffmpeg` for audio/video | ✅ keyless/offline · ❌ with `llm: true` |

### When to reach for each

**Rung 0 (`image_meta`)** — when the filesystem already carries the meaning.
A corpus laid out as `sprites/goblin/warrior-02.png` has its subject in the
path; rung 0 turns that into `warrior 02 goblin sprites` and stamps the header
facts as promotable metadata. It runs no model, opens no socket, and cannot
hallucinate. It is also **header-only**: it never decodes the raster, so a
640 KB palette PNG that expands to 139 megapixels costs the same as a 128×128
sprite.

**Rung 1 (`clip_tags`)** — when the pixels carry meaning the filename does
not. This is the keyless default for image search: no API key, no GPU, no
extra install. Its output is rung 0's text with the winning vocabulary labels
appended, so it is strictly additive.

> The vocabulary is the whole game. A generic vocabulary produces generic
> tags. `ClipTagsDeriver` refuses an empty one on purpose — choose the labels
> for your corpus, and bake them off rather than assuming.

**Rung 5 (`ross`)** — when you want natural-language descriptions, or when
the corpus is **not images**. `ross` sniffs the file type by magic bytes, so a
single `derive:` block covers image, audio and video from one rung. It is the
only audio path anywhere in chunkshop; there is no in-process audio tagger.
Optionally it will run an LLM pass against any OpenAI-compatible endpoint for
full descriptions — off by default.

Rungs 2 (local OCR), 3 (remote VLM captioning as a first-class rung) and 4
(late-interaction page-image retrieval) are named in the spec's ladder and are
**not implemented**. See `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md`.

> **Read this before you plan a PDF corpus:** every shipped rung takes images
> (and, for rung 5, audio and video). **None takes a PDF.** A scanned,
> image-only PDF still indexes as an empty document, and `on_empty` will not
> catch it — see [Limits of this release](#limits-of-this-release). The rung
> that would fix it is rung 2, and rung 2 is not built.

## A worked example, end to end

`docs/samples/images-keyless.yaml` is the shipped keyless recipe and is
runnable as printed from a repo clone.

```yaml
# docs/samples/images-keyless.yaml
cell_name: images_keyless
source:
  type: files
  glob: docs/samples/images/*.png
  id_from: stem
  derive:
    png|jpg|jpeg|webp:
      rung: clip_tags
      vocabulary:
        - a screenshot of a user interface
        - a photograph of a person
        - a chart or diagram
        - a game sprite or pixel art
        - a scanned document page
      top_k: 2
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
  database: ignored      # SQLite has no schema concept; required by config but unused
  table: images
  mode: overwrite
  hnsw: false            # sqlite-vec is brute-force KNN
```

The `derive:` key is a **pipe-separated extension list**, not a glob:
`png|jpg|jpeg|webp` routes those four extensions to that deriver. Extensions
not named in the block keep whatever parser they had — so one cell can ingest
Markdown, PDFs and images together, with only the images going through a
deriver.

Run it:

```bash
export SQLITE_PATH=/tmp/chunkshop-images.db
chunkshop ingest  --config docs/samples/images-keyless.yaml
chunkshop search  --config docs/samples/images-keyless.yaml \
  --query "pixel art game character" --k 3
```

```
1. [0.0164] sprite_knight#0  sprite knight docs samples images a game sprite or pixel art  path=docs/samples/images/sprite_knight.png
2. [0.0161] screenshot_settings#0  screenshot settings docs samples images a screenshot of a user interface a chart or diagram  path=docs/samples/images/screenshot_settings.png
3. [0.0159] chart_revenue#0  chart revenue docs samples images a chart or diagram  path=docs/samples/images/chart_revenue.png
```

`"pixel art"` appears nowhere in a filename or a path segment — it is only in
the vocabulary — so a correct top hit is evidence the CLIP rung actually ran.

### What lands in the row

The row shape is unchanged from any other cell. `original_content` and
`embedded_content` carry the derived text; `metadata` carries the deriver's
own keys plus provenance; and two extra **real columns** appear:

```sql
SELECT doc_id, content_origin, derived_rung, original_content FROM images;
```

```
chart_revenue        derived  clip_tags  chart revenue docs samples images a chart or diagram
screenshot_settings  derived  clip_tags  screenshot settings docs samples images a screenshot of a user interface a chart or diagram
sprite_knight        derived  clip_tags  sprite knight docs samples images a game sprite or pixel art
```

`content_origin` and `derived_rung` are promoted to real columns
**automatically** on any cell that configures `source.derive` — you do not
list them in `target.promote_metadata`, and a promotion you write yourself
wins over the default. That is deliberate: a model's guess and a verbatim
document must be distinguishable in the query a downstream consumer already
runs, without a jsonb reach-through and without opting in.

The full `metadata` blob for one row of the run above:

```json
{
  "width": 320,
  "height": 220,
  "image_format": "PNG",
  "image_mode": "RGB",
  "aspect_ratio": 1.4545,
  "has_alpha": false,
  "content_origin": "derived",
  "derived_rung": "clip_tags",
  "derived": {
    "rung": "clip_tags",
    "model": "Qdrant/clip-ViT-B-32-vision+Qdrant/clip-ViT-B-32-text",
    "model_version": "0.8.0",
    "engine": "python",
    "params_hash": "40667db93985609a",
    "ts": "2026-09-01T22:38:58.573509+00:00"
  },
  "clip_tags": ["a chart or diagram"],
  "clip_scores": [0.2161],
  "source_path": "docs/samples/images/chart_revenue.png",
  "parser": "ClipTagsDeriver",
  "framer": "identity",
  "frame_seq": 0,
  "strategy": "fixed_overlap",
  "start_word": 0,
  "n_words": 9
}
```

Any of the deriver's own keys can be lifted to an indexed column the ordinary
way — `target.promote_metadata: [width, height, image_format, derived.model]`
— using `ADD COLUMN IF NOT EXISTS` in `append` mode, so adding a column later
is not a destructive migration.

### Note on `path_segments`

Rung 0 defaults to `path_segments: true`, which is why `docs samples images`
appears in the derived text above. On a corpus whose directory names are
meaningful (`sprites/goblin/…`) that is free framing context; on a corpus
whose directories are UUIDs or build hashes it is dilution. Whether it helps
is **corpus-dependent and unmeasured** — set `path_segments: false` to turn it
off, and bake it off if it matters to you.

## Audio and video

Rung 5 covers them, and it needs no extra configuration beyond listing the
extensions. `docs/samples/media-ross.yaml` is the shipped recipe:

```yaml
# docs/samples/media-ross.yaml  (excerpt)
source:
  type: files
  glob: docs/samples/images/*.png
  id_from: stem
  derive:
    png|jpg|jpeg|webp|gif|bmp|tiff|wav|mp3|flac|ogg|m4a|mp4|mov|webm:
      rung: ross
```

Because `ross` dispatches on magic bytes rather than extension, one deriver
handles all three modalities: images and video go through CLIP, audio through
CLAP, and the metadata key set is identical either way.

**Prerequisites, and what happens without them.** `rung: ross` needs the
`ross` binary on `$PATH` (or a full path in `binary:`), and `ross` itself
needs `ffprobe`/`ffmpeg` to read audio and video (`exiftool` is optional for
images and degrades to a native PNG/WebP header fallback). None of this is
silent:

- **Missing / non-executable / wrong binary** → a `RuntimeError` at deriver
  **construction**, naming the binary and pointing at ross's repo. Construction
  happens before the sink's DDL runs, so a `mode: overwrite` cell with a bad
  `binary:` does **not** drop your table first.
- **Missing `ffprobe`** → ross exits non-zero on that file and chunkshop
  raises a `ParserError` carrying **ross's own stderr verbatim**, because
  ross's message names the missing binary and chunkshop's cannot.
- **A file ross does not recognise as media** → ross skips it and emits an
  empty array; chunkshop turns that into a named `ParserError` rather than an
  empty success.

Nothing here degrades to a silent skip.

## The empty-derivation policy: `on_empty`

`source.on_empty` governs what happens when a **deriver** produces no text at
all. A model that returns nothing is a failure, not a zero-length success, and
the point of the setting is that such a document must not silently disappear
from the index.

| Value | Behaviour |
|---|---|
| `error` (default) | Raise `EmptyDerivationError` naming the file and the deriver. |
| `skip` | Omit the document and continue the run. |
| `index` | Index the empty document. |

**It is scoped to derive-routed documents, and only those.** A file handled by
an ordinary parser — a plain `.md`, or a `.pdf` in a cell with no `derive:`
rung for it — that yields empty text is indexed as an empty document
regardless of this setting. That is the pre-`on_empty` behaviour, kept
unconditionally for the plain-parser path: applying a *derivation* policy to
it turned any corpus containing a zero-byte or whitespace-only file into an
aborted run.

Because the error is now unreachable from the plain-parser path, a text-only
cell cannot see a media concept in an error message at all.

Note the practical consequence for scanned PDFs: no shipped rung derives text
from a PDF, so a scanned, image-only PDF cannot be derive-routed today and
`on_empty` never fires for it. It is still indexed empty. Closing that needs
the OCR rung, which is not built.

## Provenance

Nothing derived is written without attribution. Every deriver stamps the same
six-field block into `metadata.derived`, plus the two flat keys that become
columns.

| Field | Meaning |
|---|---|
| `derived.rung` | Which rung produced the text (`image_meta`, `clip_tags`, `ross`). |
| `derived.model` | Model identity. `"deterministic"` for rung 0; `"<vision>+<text>"` for rung 1; `"ross"` for rung 5. |
| `derived.model_version` | Rung 0: `"1"`. Rung 1: the **installed `fastembed` version** — a fastembed upgrade can change the ONNX weights behind an unchanged model id. Rung 5: the output of `ross --version`, probed at construction, never hardcoded. |
| `derived.engine` | `"python"` or `"rust"` for rungs 0 and 1; `"ross"` for rung 5, because both ports drive the same binary and the derivation is genuinely ross's rather than either port's. |
| `derived.params_hash` | sha256 (first 16 hex) of the deriver's config, canonicalised as sorted-key JSON. |
| `derived.ts` | UTC ISO-8601 timestamp of the derivation. |
| `content_origin` | `"derived"`. Promoted to a column. |
| `derived_rung` | Same value as `derived.rung`. Promoted to a column. |

Rung 5 additionally records `ross_model` — ross's own report of *which* model
produced the text (`clip:clip-vit-base-patch32` for the local tagger, or the
LLM's model name when the endpoint path won). That is read off the response,
whereas `derived.model_version` is fixed at construction, so a row stays
attributable even if endpoint resolution moved in between. It is `null` when
an LLM call was attempted and failed — the honest record, with the failure
itself carried in `ross_warnings`.

## The derivation cache

Derivation is memoised on disk, keyed by
`sha256(content_hash ‖ derivation_hash ‖ cache_key_extra)` where

```
derivation_hash = sha256(CACHE_SCHEMA_VERSION ‖ rung ‖ model_id
                         ‖ model_version ‖ engine ‖ params_hash)
```

**The key is the provenance record.** That single design choice buys several
properties at once:

- Re-chunking, re-embedding, or pointing the cell at a different table
  re-derives **nothing** — none of that is in the key.
- Changing the model, the vocabulary, `top_k`, `min_score`, or any other
  parameter re-derives **everything** — there are no stale tags.
- Supersession is automatic: a different model is a different key, so the
  earlier derivation is not overwritten but co-exists.
- Crash-resume is "run it again". Cache hits skip completed work and the sink
  commits per document.

### Where it lives, and how to configure it

```yaml
source:
  type: files
  glob: ...
  derive: { ... }
  cache:
    enabled: true          # default
    root: /secure/vol/cs   # default: $CHUNKSHOP_CACHE_DIR, else
                           # ${XDG_CACHE_HOME:-~/.cache}/chunkshop/derived
```

Entries are one JSON file per key, sharded by the first two hex characters,
written atomically via `mkstemp` + `os.replace`. A missing, unreadable or
malformed entry is a **miss**, never an error — a damaged cache degrades to
recomputation.

The cache is built **only** when the cell has a `derive:` block, and only
derive-configured extensions are ever routed through it. A text-only cell
builds no cache and touches no cache directory.

Entries hold derived text, which for some corpora is sensitive material —
point `root` at an encrypted volume, or set `enabled: false` to keep derived
text only in the target database. There is **no encryption at rest** and no
TTL or eviction.

### The `chunkshop cache` commands

The cache has a CLI surface. All three subcommands are pure-Python and run on
a text-only install — no media or parser extra required.

| Command | Does |
|---|---|
| `chunkshop cache path` | Prints the resolved root and exits. Only the path goes to stdout, so `du -sh $(chunkshop cache path)` composes. |
| `chunkshop cache info` | Entry count and total size on disk. Cheap; run it before purging. |
| `chunkshop cache purge` | Deletes every entry under the root. Prompts first unless `--yes` is passed, and reports how many it removed. |

Each accepts `--config CELL.yaml` to resolve the root from that cell's
`source.cache.root` rather than the default — a user debugging one cell acts
on the directory that cell actually writes. Without `--config` they act on the
default root (`CHUNKSHOP_CACHE_DIR` > `XDG_CACHE_HOME` > `~/.cache`, then
`chunkshop/derived`). The resolved root is printed **before** the purge
confirmation, so it is never a surprise which directory is being emptied.

Purge removes only chunkshop's own `*.json` entries; unrelated files sharing
the root are left alone. A root that does not exist reports zero and exits 0 —
purging a cache you never built is not an error.

> **Purging is a decision, not housekeeping.** Rung-5 derivations are
> non-deterministic, so the cache is what keeps a corpus stable. A purge
> followed by a re-ingest can legitimately change derived text — and therefore
> the embeddings — with no config change at all. It is also the remedy for the
> two staleness gaps above: a ross built from source that edits
> `clip_labels.txt` without bumping its version, and installing `exiftool`
> mid-corpus.

### The soundness invariant, and why `cache_key_extra` exists

A deriver participating in the cache **must be a pure function of its file's
content plus everything it declares in the key**. Anything else it reads — the
filename, the parent directory, the mtime, an environment variable — is
invisible to the key, and two documents that agree on the key are served each
other's output.

This is not hypothetical. `ImageMetaDeriver` derives its text *from the
filename* while the key originally contained no path at all, so two
byte-identical images with different names collided and the second was served
the first's text — embedded, indexed, and searchable under the wrong document,
with no error. `ClipTagsDeriver` inherited it and served the first image's
tags too. The trigger did not even need duplicate files: renaming one file and
re-running the cell served the old name's text forever.

The fix is `Deriver.cache_key_extra(path) -> str | None`, a per-deriver
declaration of exactly what non-content material it consumes:

| Deriver | Contributes | Why |
|---|---|---|
| `image_meta` | Its own path-derived text prefix | Exactly the material consumed under the *current* config — with `path_segments: false` the parent directory is correctly absent from the key, and a rename that leaves the humanised text unchanged (`a-b.png` → `a_b.png`) still hits. |
| `clip_tags` | Delegates to its wrapped `image_meta` | It builds its text as `base.text + tags`, so it inherits rung 0's path dependence wholesale. |
| `ross` | The file's **basename** | When the local tagger is gated, ross falls back to a description humanised from the basename. The parent directories genuinely are not consumed. |
| any genuinely pure deriver | `None` (the protocol default) | Content-addressed sharing is preserved — two copies of the same file derive once, which is the property worth keeping. A third-party parser predating the method is treated as pure, which is what it was before. |

Only extensions named in the `derive:` block are cache-routed at all. The
built-in file parsers (text, PDF, DOCX, PPTX, XLSX, HTML) are not, deliberately:
keying them on the class name alone would collapse every `TextParser`
regardless of its configured `encoding` onto one entry.

`CACHE_SCHEMA_VERSION` went `"1"` → `"2"` when this landed, because entries
written under the old two-part key may hold data attributed to the wrong
document: they must be unreachable, not merely re-shaped.

### Two documented cache limitations (rung 5)

Both are properties of `ross`'s inputs that its CLI does not expose, so
chunkshop cannot fold them into the key.

1. **The vendored vocabulary is identified only by `ross --version`.** ross
   embeds a 50-label list (`src/clip_labels.txt`, `src/clap_labels.txt`) and
   its precomputed text features inside the binary. `--json` does not echo the
   label set and `--doctor` reports only external binaries, endpoint config
   and model cache paths, so the strongest available identifier is the
   package version. **A ross built from source that edits `clip_labels.txt`
   without bumping its version will silently serve stale cached derivations.**
   A user-supplied `labels_file` has no such gap — its *content* is hashed
   into `params["labels_sha256"]`, so editing the file re-derives.

2. **ross's `metadata` block varies with what is installed.** It comes from
   `exiftool` (images) and `ffprobe` (audio/video) when present, and from a
   native PNG/WebP header fallback when not — so installing `exiftool`
   part-way through a corpus changes `ross_metadata` for files already cached.

The mitigation for both is the same: `cache: {enabled: false}`, or purge the
cache after upgrading a source-built ross or changing the installed tool set.

### The LLM pass, and why it changes what the cache *is*

`llm:` defaults to `false`, and false means `--no-llm`: ross tags locally and
the rung is keyless and offline after its one-time model download.

With `llm: true`, ross calls an OpenAI-compatible endpoint. **chunkshop never
reads the endpoint, the model, or the key from YAML.** ross resolves them from
the **environment** by its own precedence — `ROSS_VISION_URL` > `ROSS_URL`,
`ROSS_VISION_MODEL` > `ROSS_MODEL`, and the same shape for `ROSS_API_KEY`, per
modality. That keeps credentials out of your config files and out of
provenance.

They are still in the hash. At construction, `llm: true` runs `ross --doctor`
and folds the per-modality **url + model + whether a key is set** into
`params["llm_endpoints"]`, so swapping `ROSS_MODEL` between two runs of the
same cell re-derives instead of serving the first model's text. The key's
*value* is never read, hashed, stamped or logged — only the boolean ross
itself prints (`key=set` / `key=none`), and it earns its place because adding
a key to a previously keyless endpoint turns a failed, degraded derivation
into a real one.

> **LLM output is not deterministic, and this is a materially different
> operational property.**
>
> Rungs 0 and 1 are pure functions of their input: purge the cache, re-ingest,
> and you get byte-identical text. Rung 5 with `llm: true` is not. The derived
> text comes from a remote model doing sampling, so two fresh derivations of
> the same image can differ even with the endpoint, the model and the prompt
> all identical — **the hash pins the inputs, not the output.**
>
> On this rung the cache is therefore not an optimisation. It is what makes a
> corpus stable: every row derived once and thereafter served from that one
> entry. The corollary is that **purging the cache and re-ingesting can
> legitimately change your derived text, and therefore your embeddings, with
> no config change at all.** Purge only when you mean to re-derive the corpus.

`ross --doctor --json` is accepted but ignored as of ross 0.1.0, so both ports
hand-parse its human-readable output and treat any shape they cannot read as a
hard construction-time error rather than falling back to a hash that omits the
model. A ross release that rewords `--doctor` breaks this rung loudly at
construction — the correct failure mode, but still one worth removing
upstream.

## Cost and latency

Measured on one developer machine over the three-image sample corpus, with
model weights already downloaded. Treat these as shape, not as a benchmark.

| Rung | Cold cache (3 images) | Warm cache (3 images) |
|---|---|---|
| `clip_tags` | 2.3 s | 0.5 s |
| `ross` (`llm: false`) | 6.1 s | 0.6 s |

Rung 0 runs no model at all and reads only file headers, so its per-file cost
is constant regardless of pixel count.

The first run of a rung-1 cell additionally downloads ~0.6 GB of CLIP weights
into the fastembed cache; the first run of a rung-5 cell downloads ross's own
CLIP/CLAP models (~89 MB / ~78 MB) into `~/.cache/ross-clap`. Both are lazy —
nothing downloads at install or at import, and a text-only cell triggers
neither. `timeout_s` on the ross rung defaults to `120` precisely to
accommodate that first download.

With `llm: true`, cost becomes whatever your endpoint charges, one call per
file. There is no budget ceiling or pre-run cost estimate in this release.

## Python and Rust

Both implementations accept the **same YAML**: `derive`, `on_empty` and
`cache` are modelled identically, the discriminator is `rung` (not `type`,
unlike every other union in the config), and all three rungs carry the same
field names and defaults.

**Build requirements differ.** In Rust, rungs 0 and 1 are behind Cargo
features — `image_meta` needs `media`, `clip_tags` needs `media` **and**
`embedder-hub` — and `media` is **not** part of the default `full` feature
set, so you need `cargo build --features media`. A YAML asking for a rung the
binary was not built with fails at config load with a message naming the
feature, not with a missing symbol. Rung 5 is behind no feature at all: it
builds under `--no-default-features`, because a subprocess has no
compile-time dependency.

**Parity is defined at two levels**, per SC-12 in
`spec/chunkshop-media-ingest/SPEC.md`:

*Bit-level, for embedding.* Text-tower embeddings match within `1e-6` per
component (verified for `Qdrant/clip-ViT-B-32-text` and
`Xenova/bge-base-en-v1.5-int8`). The CLIP **vision** tower does not reach that
bar, and its bar is two-tier and two-axis, set from a measured 28-image
corpus. Quoting the spec:

> **(a)** for images that need **no resize** (`target_size` returns the source
> dimensions), `1 − cosine ≤ 1e-9` **and** `max abs ≤ 1e-5` — the two runtimes
> agree essentially exactly (measured `1 − cosine ≈ 4e-13`, max abs 2.7e-7)
> because PIL's resampler never runs; **(b)** for every **resampled** image,
> `cosine ≥ 0.9995` **and** `max abs ≤ 1.2e-2`. The residual on resampled
> images is **content-dependent, not shape-dependent**: measured over the
> corpus it spans cosine 0.999775–0.999999 (0.075°–1.215°, median 0.775°) and
> max abs 2.1e-4–5.3e-3, with flat-shaded drawings at the easy end (median
> 0.241°) and photographs and greyscale at the hard end (median 0.824° and
> 1.072°). Its cause is **sub-LSB rounding inside PIL's fixed-point 8-bit
> resampler** versus `image`'s f32 one — *not* a filter difference
> (`FilterType::CatmullRom` **is** PIL's `BICUBIC`, the same Keys cubic with
> a = −0.5) and *not* an inference difference; tier (a) is the proof, since
> bypassing the resampler removes 100% of it.

Practically: for CLIP vision the "vectors from either implementation are
interchangeable" promise holds to within ~1.2°, for any image shape and
content. Candidate pairs separated by less than that could order differently
between implementations — rare, real, and worth knowing.

*Contract-level, for derivation.* Same YAML, same schema, same metadata keys,
same provenance fields. Rung 0 is deterministic, so both ports are asserted to
agree on **values** for all six of its fields — including `aspect_ratio`,
which is the only rung-0 field with arithmetic in it and which caught a real
half-to-even vs half-away-from-zero divergence.

**One consequence worth planning around:** `engine` participates in
`derivation_hash` by design. Python's `engine="python"` and Rust's
`engine="rust"` therefore produce different cache keys for byte-identical
config, so rungs 0 and 1 **maintain separate caches per implementation**. Rung
5 does not: its engine is `"ross"` in both ports, because both shell out to
the same binary, so a Rust `chunkshop-rs ingest` legitimately serves rows from
entries a prior Python run wrote.

## Upgrade impact

**Measured, not asserted.** An audit ran chunkshop's pre-existing *text-only*
sample configs against the pre-media baseline (`3df86cb`) and against this
branch, into separate SQLite targets, then diffed the results byte-for-byte:
doc ids, chunk text, metadata JSON keys *and* values, table schema, row counts
and the raw embedding blobs. What follows is that diff. Where something was
not measured, this section says so rather than reasoning from design intent.

**The headline: upgrading a text-only deployment requires no new installs and
changes no output.**

- **No new Python dependencies.** The base dependency list is byte-identical
  (the same 6 packages). Clean base-only virtualenvs built on each version
  resolve to the same 35 packages. The only `pyproject.toml` addition is an
  empty `vision = []` extra.
- **Pillow was already present at baseline.** It arrives transitively through
  `fastembed`, which is a core dependency — so rung 0's keyless image path
  costs zero additional install weight. That is why `vision` is empty.
- **Nothing media-shaped is imported on a text-only run.** A bare
  `import chunkshop` loads 246 modules on both versions, none of them from the
  media path.
- **No model downloads** on a text-only run.
- **No new files or directories.** With a clean `$XDG_CACHE_HOME` and `$HOME`,
  the directory tree after a text-only run is identical to baseline. Cache
  construction is gated on `cfg.derive`, so no derivation cache is created.
- **No schema change.** The media promoted columns (`content_origin`,
  `derived_rung`) are gated on `derive:` in both ports. A text-only cell's
  table was column-identical on both versions: `id`, `doc_id`, `seq_num`,
  `original_content`, `embedded_content`, `tags`, `metadata`, `source`,
  `created_at`.
- **Rust `preflight_derive()` is a no-op without a `derive:` block.** It
  returns early, performs no I/O, and cannot fail such a cell.
- **No shipped config regressed.** All 83 YAMLs present at the baseline load
  identically on both versions — Python 56 load as a `CellConfig` and 27 are
  not cell configs; Rust 48 and 35. Same verdict per file, both versions.
- **Row output was byte-identical** across three text-only configs × two
  ports, including the raw embedding blobs (matching sha256) — apart from the
  two Rust-only differences below.

### Two real differences, both Rust-only

1. **Rust rows gain a `parser` metadata key.** Existing Rust `files`-source
   deployments will see `"parser": "TextParser"` appear in `metadata` on newly
   written rows. Python has always written this key, so this is **Rust
   reaching parity with Python, not a new behaviour**. Low impact unless
   something on your side promotes or schema-validates the metadata object.
2. **Rust now rejects unknown fields under a `files` `source:` block.** A YAML
   carrying a stray or misspelled key that Rust previously ignored will now
   fail at config load. Python has always rejected these (pydantic
   `extra="forbid"`), so again Rust is catching up. This can only affect a
   config that already contained a key that was being silently dropped — which
   means it was not doing what it looked like it was doing. Run
   `chunkshop-rs validate` over your configs before rolling out.

`created_at` also differs run to run, but it is a wall-clock timestamp and
varies between two runs of the *baseline* too. Not version-induced.

### One regression, found and fixed before release

`on_empty` was briefly firing for plain-text files, not only for
derive-routed ones. A corpus containing a single zero-byte or whitespace-only
`.txt` or `.md` aborted the whole cell: a config that produced `docs=3
chunks=1` on baseline produced `docs=0` and exit code 1. Under
`target.mode: overwrite` the table had already been dropped before the source
drained, so it was left empty.

Fixed in `f6d8f55` (Python) and `be3aa05` (Rust) by scoping `on_empty` to
derive-routed documents only, which is what
[the policy always intended](#the-empty-derivation-policy-on_empty). Verified
restored: `docs=3 chunks=1` again, and a populated table survives a re-run
(1 row before, 1 row after). It is recorded here because it shipped in an
intermediate state of this branch, not because it is live.

### What was not measured

Only **SQLite sinks** and the **`files` source** were exercised end to end.
Postgres, MariaDB and ClickHouse sinks, and the `http` / `s3` / `pg_table`
sources, were not. The `on_empty` check lives only in `files.py` /
`files.rs`, so the other sources should be unaffected by it — but that was
not run, and is not being claimed.

## Limits of this release

Stated plainly, because the ladder is meant to be honest about what it does
not do:

- **No shipped rung derives text from a PDF, so the spec's motivating defect
  is not closed by this release.** Rungs 0 and 1 take images; rung 5 takes
  images, audio and video. None of them takes a PDF. A scanned, image-only PDF
  therefore still extracts to nothing and is still indexed as an empty
  document — and because `on_empty` is correctly scoped to derive-routed
  documents, it cannot catch that case either. This is exactly the F5 defect
  in `spec/chunkshop-media-ingest/SPEC.md` ("scanned PDF with no OCR
  configured → explicit error, never a silent 0-chunk success"): **it remains
  open.** Closing it needs the OCR rung (rung 2), which is not built.
- **No first-class remote VLM captioning rung.** Rung 5 with `llm: true` is
  the available path, and it goes through `ross`, not through a chunkshop-native
  deriver with a configurable JSON schema.
- **No CLIP visual plane.** There is no `embedder.type: clip`, no image-vector
  table, and no `search --similar-to ./ref.png`. Derived text is the only
  media path today; reference-image ("find more like *this file*") queries are
  not served.
- **No media arms in the bakeoff.** The bakeoff harness is text-only, so
  "which rung wins on your corpus" is not yet answerable with numbers from
  chunkshop itself.
- **No cost governance.** No budget ceiling, no pre-run estimate, no cost
  field in provenance for the LLM path.
- **No cache TTL, eviction, CLI surface, or encryption at rest.**

## See also

- [`reference/deriver-image-meta.md`](reference/deriver-image-meta.md) — rung 0 reference card.
- [`reference/deriver-clip-tags.md`](reference/deriver-clip-tags.md) — rung 1 reference card.
- [`reference/deriver-ross.md`](reference/deriver-ross.md) — rung 5 reference card.
- [`reference/parsers.md`](reference/parsers.md) — the parser/deriver dispatch layer `derive:` layers over.
- [`samples/images-keyless.yaml`](samples/images-keyless.yaml) — runnable rung-1 recipe.
- [`samples/media-ross.yaml`](samples/media-ross.yaml) — runnable rung-5 recipe.
- [`storage-model.md`](storage-model.md) — what chunkshop writes per row and how to query each payload.
- [`incremental.md`](incremental.md) — cursor-based sync for the `files` source.
- `spec/chunkshop-media-ingest/SPEC.md` — the binding spec (SC-12, NN-1…NN-6, the full ladder).
- `docs/superpowers/plans/MEDIA-INGEST-ROADMAP.md` — measured findings and what is still open.
