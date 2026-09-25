//! SC-12 bit-level parity for BOTH CLIP towers, against Python `fastembed`
//! reference vectors.
//!
//! The two towers get DIFFERENT bars, per SC-12 in
//! `spec/chunkshop-media-ingest/SPEC.md`:
//!
//! - **Text tower: `1e-6` per-component absolute.** Text tokenization and the
//!   text encoder are deterministic across the two ORT builds; the measured
//!   divergence is ~1.8e-7, so this bar has ~5x headroom and is a real
//!   regression detector, not a rubber stamp.
//! - **Vision tower: `cosine >= 0.9995` AND `max_abs <= 1.2e-2`, for every
//!   resampled image; `1 - cosine <= 1e-9` AND `max_abs <= 1e-5` for the
//!   no-resize control rows.** Two axes, and two tiers. See below.
//!
//! # The vision bar, and why it is looser than it looks
//!
//! This bar has been set wrong twice. First as `cosine >= 0.9999` with
//! per-image exemptions; then, after the aspect-ratio fix, as a uniform
//! `cosine >= 0.99998` described as "ONE bar for every image, square and
//! non-square alike". Both were fitted to the same THREE fixtures
//! (`sprite_knight`, `chart_revenue`, `screenshot_settings`) — flat-shaded
//! low-frequency Pillow drawings that sit at the easy end of the residual
//! distribution. An independent audit generated 13 ordinary photographs and 6
//! of 13 failed `0.99998`.
//!
//! The residual is CONTENT-dependent, not shape-dependent. It is now measured
//! over a 28-image corpus that spans the content space — real photographs
//! (scikit-image's bundled set) at a range of resolutions and aspect ratios
//! from 1:4 to 4:1, greyscale, high-frequency text and texture, flat-shaded
//! synthetic, and no-resize controls. Built by
//! `tests/parity/generate_clip_vision_corpus.py`; described row by row in
//! `parity-fixtures/clip_vision_corpus_manifest.json`.
//!
//! ## Measured distribution (2026-09-01, threads pinned to 1 on both sides)
//!
//! No-resize controls — `target_size` returns the source dimensions, so PIL's
//! resampler never runs and neither does `image`'s:
//!
//! | image | cosine | angle | max abs |
//! |---|---|---|---|
//! | `ctrl_astronaut_224x224.png` | 1.000000000000 | 0.000052 deg | 2.68e-7 |
//! | `aspect_coffee_225x224.png` | 1.000000000000 | 0.000050 deg | 1.71e-7 |
//!
//! The two runtimes agree EXACTLY when the resampler is bypassed. 100% of the
//! residual below is PIL's fixed-point 8-bit resampler versus `image`'s f32
//! one. Closing it would mean reimplementing Pillow's integer kernel.
//!
//! Resampled rows (n = 26): cosine min **0.999775132**, median 0.999908283,
//! max 0.999999140. Angle min 0.075 deg, median 0.775 deg, max **1.215 deg**.
//! Max-abs min 2.13e-4, median 2.13e-3, max **5.31e-3**.
//!
//! | worst 6 | content | cosine | angle | max abs |
//! |---|---|---|---|---|
//! | `aspect_rocket_400x1600.png` | photo | 0.999775132 | 1.2151 deg | 4.49e-3 |
//! | `grey_moon_512x512.png` | greyscale | 0.999781929 | 1.1966 deg | 2.81e-3 |
//! | `grey_camera_512x512.png` | greyscale | 0.999825056 | 1.0717 deg | 2.96e-3 |
//! | `photo_cat_451x300.png` | photo | 0.999845576 | 1.0069 deg | 3.67e-3 |
//! | `grey_coins_384x303.png` | greyscale | 0.999846668 | 1.0034 deg | 3.39e-3 |
//! | `scale_cat_1024x682.png` | photo | 0.999871105 | 0.9199 deg | **5.31e-3** |
//!
//! | best 5 (resampled) | content | cosine | angle |
//! |---|---|---|---|
//! | `freq_checker_200x200.png` | synthetic | 0.999999140 | 0.0751 deg |
//! | `freq_gravel_512x512.png` | texture | 0.999992875 | 0.2163 deg |
//! | `flat_screenshot_settings.png` | flat | 0.999991266 | 0.2395 deg |
//! | `flat_chart_revenue.png` | flat | 0.999991164 | 0.2409 deg |
//! | `flat_sprite_knight.png` | flat | 0.999989313 | 0.2649 deg |
//!
//! Median angle by content class: flat 0.241 deg (n=3), synthetic 0.075
//! (n=1), texture 0.526 (n=2), text 0.599 (n=2), photo 0.824 (n=15),
//! greyscale 1.072 (n=3). The three fixtures the old bar was calibrated on
//! are the three `flat` rows — the easiest resampled rows in the whole
//! corpus, 4-5x better than the median photograph. That is the whole error.
//!
//! Against this corpus the old bars fail on the SHIPPED, CORRECT code:
//! `cosine >= 0.99998` fails 21 of 28 rows, `max_abs <= 2e-3` fails 15 of 28.
//!
//! ## What the bar catches
//!
//! Each geometry regression was injected and measured across all 28 rows —
//! not reasoned about. "rows failing" is out of 28; the two control rows can
//! never fail a geometry regression, because on them the resize is identity.
//!
//! | injected regression | worst cosine | worst angle | rows failing `0.9995` |
//! |---|---|---|---|
//! | *(none — shipped code)* | 0.999775132 | 1.22 deg | **0** |
//! | revert to `ImageEmbedding::embed(path)` (the squash) | 0.752454455 | 41.20 deg | 17 |
//! | `.round()` instead of truncation in `target_size` | 0.997352100 | 4.17 deg | 6 |
//! | center-crop offset off by one pixel | 0.987134231 | 9.20 deg | 17 |
//! | `FilterType::Triangle` | 0.987964035 | 8.90 deg | 25 |
//! | `FilterType::Lanczos3` | 0.994753266 | 5.87 deg | 19 |
//! | `FilterType::Nearest` | 0.883638335 | 27.92 deg | 26 |
//!
//! ## What the bar lets through
//!
//! Stated plainly, because a loose bar that is true beats a tight one that is
//! false. `MIN_VISION_COSINE = 0.9995` permits a cosine distance of 5.0e-4
//! against a worst measurement of 2.2487e-4 — 2.22x headroom. So it would let
//! through a DOUBLING of the resampler floor (an `image` or Pillow precision
//! change, say) on the worst image in the corpus. It would not let through a
//! tripling. It also cannot see a change that moves every image by less than
//! ~1.2x its current residual.
//!
//! It does not let through any geometry regression anyone has managed to
//! construct: the weakest of the six above (`round` vs truncate) still misses
//! by 5.3x in cosine distance on six separate images.
//!
//! Deliberately NOT tighter. `0.99998` is FALSE for this codebase — it fails
//! 21 of 28 rows of correct output. A bar that only holds on flat-shaded
//! drawings is not a bar, it is a fixture-selection artifact.
//!
//! Deliberately NOT looser than `0.9995` either. `0.999` would still catch
//! every regression in the table, but it permits 1.0e-3 of cosine distance —
//! 4.4x the measured worst — which is enough to absorb a genuine quadrupling
//! of the floor while reading green.
//!
//! ## Why two axes, and two tiers
//!
//! Cosine over 512 dimensions is insensitive to a handful of large
//! per-component errors: perturbing a single f32 by 1e-4 moves cosine to
//! 0.999999995 and is invisible to any bar here. `MAX_VISION_ABS_DIFF` is the
//! independent per-component axis, sized with the same 2.22x headroom over
//! its own worst measurement (5.312e-3).
//!
//! The TIERS matter more. The corpus bar has to be loose enough to hold on a
//! 1280x720 photograph, which makes it too loose to notice a changed weight
//! file. The no-resize control rows have no such constraint — they measure
//! everything EXCEPT the resampler (decode, RGB conversion, rescale,
//! normalization, the ONNX weights, the runtime) and they agree to 4e-13. So
//! they carry a bar six orders of magnitude tighter, and it is those rows,
//! not the loose corpus bar, that will catch a `fastembed`/`ort`/`image` bump
//! that changes the model or the arithmetic. A test with only the loose bar
//! would have lost that sensitivity entirely.
//!
//! Which rows are controls is DERIVED, by asking the shipped
//! `clip_preprocess::target_size` whether the image needs a resize at all —
//! not by a hand-maintained list that could silently stop matching the code.
//!
//! **Do not change the filter** (`CatmullRom` IS PIL's `BICUBIC` — the same
//! Keys cubic with a = -0.5) and do not "tighten" the corpus bar back toward
//! `0.99998` without re-measuring: it is not achievable on photographic
//! content with these two resamplers.
//!
//! # What makes each of these tests go red
//!
//! Neither test compares a vector against itself, and neither fixture is
//! generated by the code path it checks:
//! `tests/parity/generate_clip_full_vectors.py` and
//! `tests/parity/generate_clip_vision_corpus.py` run PYTHON's `fastembed`,
//! the tests below run Rust's `fastembed-rs`.
//!
//! Beyond the injected regressions tabulated above: a `fastembed`/`ort`/
//! `image` bump that changes the CLIP ONNX weights or the resampler behind
//! the stock model ids (caught by the control rows), and any edit that swaps
//! the towers or the fixtures. The text test goes red on a tokenizer,
//! normalization, or weight change.
//!
//! Each test also carries a NEGATIVE CONTROL: each fixture holds multiple
//! vectors, and the test asserts the subject matches its own reference AND
//! does *not* match a different one. A degenerate comparison (both sides
//! reading the same buffer, a tolerance so wide everything passes, a
//! silently-empty vector) fails the control even when it would sail through
//! the positive assertion. Without that, "passes" and "cannot fail" look
//! identical from the outside.
//!
//! The two live tests are `#[ignore]`d because they load ~0.6GB of ONNX
//! weights — same discipline as `clip_tags.rs`'s own live test. Run them
//! explicitly:
//!
//! ```text
//! cargo test --features media,embedder-hub --test clip_embedding_parity -- --ignored --nocapture
//! ```
//!
//! `fixtures_are_wellformed_and_the_negative_control_is_real` is NOT ignored:
//! it needs no model and guards the fixtures themselves in every CI run.
#![cfg(all(feature = "media", feature = "embedder-hub"))]

use std::path::PathBuf;

use chunkshop::sources::derivers::clip_preprocess::open_for_clip;
use fastembed::{
    EmbeddingModel, ImageEmbedding, ImageEmbeddingModel, ImageInitOptions, TextEmbedding,
    TextInitOptions,
};

/// Index 0 is the parity subject; index 1 is the negative control. Must stay
/// in the same order as `tests/parity/generate_clip_full_vectors.py::TEXTS`.
const TEXTS: [&str; 2] = [
    "a game sprite or pixel art",
    "quarterly revenue chart, bar graph",
];

/// The corpus bar for every RESAMPLED row. See the module doc for the full
/// measured distribution and the injected-regression table.
///
/// Worst measured residual over the 28-image corpus is cosine 0.999775132
/// (`aspect_rocket_400x1600.png`, 1.2151 deg), i.e. a cosine DISTANCE of
/// 2.2487e-4. This bar permits 5.0e-4 — 2.22x that.
///
/// PROTECTS AGAINST: every geometry regression that has been constructed and
/// measured — reverting to `fastembed-rs`'s aspect-squashing `embed(path)`
/// (worst 41.20 deg), rounding instead of truncating the long edge (4.17
/// deg), a one-pixel center-crop offset (9.20 deg), and three wrong resample
/// filters (5.87-27.92 deg). The weakest of those still misses by 5.3x in
/// cosine distance, on six separate images.
///
/// LETS THROUGH: a doubling of the PIL-vs-`image` resampler floor on the
/// worst image in the corpus (a Pillow or `image` precision change would do
/// this). Not a tripling. It is also blind to any change smaller than ~1.2x
/// the current per-image residual — which is exactly why the no-resize
/// control rows below carry a separate, six-orders-tighter bar.
///
/// Deliberately NOT `0.99998`, the previous value: that is FALSE for this
/// codebase — it fails 21 of these 28 rows on correct output, because it was
/// fitted to three flat-shaded drawings that are the easiest rows in the
/// corpus. A loose bar that is true beats a tight one that is false.
const MIN_VISION_COSINE: f64 = 0.9995;

/// Second, independent axis for resampled rows. Cosine over 512 dimensions is
/// insensitive to a handful of large per-component errors — perturbing one
/// f32 by 1e-4 leaves cosine at 0.999999995 — so cap the per-component
/// extremum too. Worst measured is 5.312e-3 (`scale_cat_1024x682.png`); this
/// is 2.26x that, the same headroom `MIN_VISION_COSINE` carries.
const MAX_VISION_ABS_DIFF: f32 = 1.2e-2;

/// The tight tier, for rows `clip_preprocess::target_size` says need no
/// resize at all. On those the resampler — the entire cause of the residual
/// above — never runs, and the two runtimes agree to a measured
/// `1 - cosine` of ~4e-13 and a max abs of 2.7e-7.
///
/// These rows are what actually guards the model and the runtime: decode, RGB
/// conversion, rescale, normalization, the ONNX weights and the ORT build all
/// participate, and none of them is allowed the latitude the resampler gets.
/// A weight or arithmetic change hides easily under `MIN_VISION_COSINE` and
/// cannot hide under this.
///
/// ~2400x headroom over the measurement; deliberately not pinned at it.
const MAX_CONTROL_COSINE_DISTANCE: f64 = 1e-9;

/// Per-component companion to `MAX_CONTROL_COSINE_DISTANCE`. 37x the measured
/// 2.682e-7, and 1200x tighter than the resampled rows' bar.
const MAX_CONTROL_ABS_DIFF: f32 = 1e-5;

/// One row of `clip_vision_corpus_manifest.json`.
struct CorpusRow {
    name: String,
    width: u32,
    height: u32,
    content: String,
}

/// Read the corpus manifest, in the same row order as the reference `.bin`.
fn corpus_rows() -> Vec<CorpusRow> {
    let p = fixtures_dir().join("clip_vision_corpus_manifest.json");
    let v: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("read {}: {e}", p.display())),
    )
    .unwrap();
    v["images"]
        .as_array()
        .expect("manifest must have an `images` array")
        .iter()
        .map(|r| CorpusRow {
            name: r["name"].as_str().unwrap().to_string(),
            width: r["width"].as_u64().unwrap() as u32,
            height: r["height"].as_u64().unwrap() as u32,
            content: r["content"].as_str().unwrap().to_string(),
        })
        .collect()
}

impl CorpusRow {
    /// Whether this image bypasses the resampler entirely.
    ///
    /// DERIVED from the shipped `target_size`, not from a hand-maintained
    /// list of names: `resize_exact` at matching dimensions short-circuits to
    /// a buffer copy (`image` 0.25.10), so a row whose target size equals its
    /// source size is never resampled and must meet the tight bar. Asking the
    /// real function means this classification cannot drift away from what
    /// the code does — if `target_size` ever changed, rows would move tiers
    /// rather than silently keep the wrong one.
    fn is_no_resize_control(&self) -> bool {
        chunkshop::sources::derivers::clip_preprocess::target_size(
            self.width,
            self.height,
            chunkshop::sources::derivers::clip_preprocess::CLIP_SHORTEST_EDGE,
        ) == Some((self.width, self.height))
    }
}

fn fixtures_dir() -> PathBuf {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("tests");
    p.push("parity-fixtures");
    p
}

fn corpus_image_path(name: &str) -> PathBuf {
    fixtures_dir().join("clip-vision-corpus").join(name)
}

/// Model cache. Anchored to the package dir rather than the process cwd so
/// the test reuses one cache no matter where `cargo test` was invoked from.
/// `FASTEMBED_CACHE_DIR` (fastembed's own env var) still wins if set.
fn cache_dir() -> PathBuf {
    match std::env::var("FASTEMBED_CACHE_DIR") {
        Ok(v) => PathBuf::from(v),
        Err(_) => {
            let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
            p.push(".fastembed_cache");
            p
        }
    }
}

/// Byte layout is the one `embedding_parity.rs::read_reference` already
/// reads and `scripts/produce_rust_parity_reference.py` already writes:
/// `u32 n`, `u32 dim`, then `n*dim` little-endian f32s.
fn read_reference(name: &str) -> (usize, usize, Vec<f32>) {
    let p = fixtures_dir().join(name);
    let bytes = std::fs::read(&p).unwrap_or_else(|e| panic!("read {}: {}", p.display(), e));
    assert!(
        bytes.len() >= 8,
        "{}: reference file too small",
        p.display()
    );
    let n = u32::from_le_bytes(bytes[0..4].try_into().unwrap()) as usize;
    let dim = u32::from_le_bytes(bytes[4..8].try_into().unwrap()) as usize;
    let expected_size = 8 + n * dim * 4;
    assert_eq!(
        bytes.len(),
        expected_size,
        "{}: size {} != expected {} for n={n} dim={dim}",
        p.display(),
        bytes.len(),
        expected_size,
    );
    let floats = (0..n * dim)
        .map(|i| {
            let off = 8 + i * 4;
            f32::from_le_bytes(bytes[off..off + 4].try_into().unwrap())
        })
        .collect();
    (n, dim, floats)
}

fn row(flat: &[f32], dim: usize, i: usize) -> &[f32] {
    &flat[i * dim..(i + 1) * dim]
}

fn cosine_similarity(a: &[f32], b: &[f32]) -> f64 {
    let dot: f64 = a.iter().zip(b).map(|(x, y)| *x as f64 * *y as f64).sum();
    let na: f64 = a.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
    let nb: f64 = b.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
    dot / (na * nb)
}

fn max_abs_diff(a: &[f32], b: &[f32]) -> f32 {
    a.iter()
        .zip(b)
        .map(|(x, y)| (x - y).abs())
        .fold(0.0, f32::max)
}

/// Deterministic ORT reduction order, matching the generator's
/// `OMP_NUM_THREADS=1` and `embedding_parity.rs`'s own preamble.
fn pin_threads() {
    std::env::set_var("OMP_NUM_THREADS", "1");
    std::env::set_var("ORT_INTRA_OP_NUM_THREADS", "1");
    std::env::set_var("MKL_NUM_THREADS", "1");
}

/// Runs in every CI pass — no model, no network. Guards the fixtures
/// themselves: a truncated, re-headered, or accidentally duplicated fixture
/// makes this red, and so does a negative control that stopped being a
/// control (rows 0 and 1 collapsing to the same vector would make the live
/// tests' discrimination assertions vacuous).
#[test]
fn fixtures_are_wellformed_and_the_negative_control_is_real() {
    for name in [
        "clip_text_full_reference.bin",
        "clip_vision_corpus_reference.bin",
    ] {
        let (n, dim, flat) = read_reference(name);
        assert!(
            n >= 2,
            "{name}: needs at least a subject and a negative control, got {n}"
        );
        assert_eq!(dim, 512, "{name}: CLIP ViT-B-32 is 512-dim");

        for i in 0..n {
            let v = row(&flat, dim, i);
            let norm: f64 = v.iter().map(|x| (*x as f64).powi(2)).sum::<f64>().sqrt();
            assert!(
                (norm - 1.0).abs() <= 1e-5,
                "{name}[{i}]: |v| = {norm} — CLIP references are L2-normalised"
            );
        }

        // A usable negative control must EXIST somewhere in the fixture.
        // Deliberately not "rows 0 and 1 are dissimilar": the vision corpus is
        // ordered by content class, and its first two rows are two crops of
        // the same photograph (cosine 0.9992) — a perfectly good corpus, but a
        // useless adjacent pair. The live tests below pick their control by
        // measurement rather than by index, and this guards that such a pick
        // is available at all.
        let mut min_pair = f64::MAX;
        for i in 0..n {
            for j in (i + 1)..n {
                let c = cosine_similarity(row(&flat, dim, i), row(&flat, dim, j));
                assert!(
                    row(&flat, dim, i) != row(&flat, dim, j),
                    "{name}: rows {i} and {j} are bit-identical — a duplicate fixture \
                     inflates the row count without adding a measurement. \
                     (`skimage.data.cat()` and `.chelsea()` are the same image; that \
                     is exactly how one got in.)"
                );
                min_pair = min_pair.min(c);
            }
        }
        assert!(
            min_pair < 0.99,
            "{name}: the most dissimilar pair in this fixture is {min_pair} similar — \
             no row can serve as a negative control, so the live parity tests below \
             would pass even on a degenerate comparison."
        );
    }

    // The vision corpus is three artefacts that must agree: the manifest's
    // row order, the reference `.bin`'s row count, and the PNGs on disk. A
    // regeneration that wrote one and not the others would otherwise surface
    // as a confusing tolerance failure in the live test rather than as
    // "your fixtures disagree".
    let rows = corpus_rows();
    let (n, _dim, _) = read_reference("clip_vision_corpus_reference.bin");
    assert_eq!(
        n,
        rows.len(),
        "clip_vision_corpus_reference.bin has {n} rows but the manifest lists \
         {} — regenerate both with tests/parity/generate_clip_vision_corpus.py",
        rows.len()
    );
    assert!(
        rows.len() >= 20,
        "the corpus exists to characterise a distribution; {} rows cannot",
        rows.len()
    );
    for r in &rows {
        let p = corpus_image_path(&r.name);
        assert!(p.exists(), "missing corpus image {}", p.display());
    }

    // Both tiers must be non-empty, or one of the two bars below is never
    // exercised and could rot to any value without a test noticing.
    let controls = rows.iter().filter(|r| r.is_no_resize_control()).count();
    assert!(
        controls >= 1,
        "no no-resize control row — the tight bar would never run"
    );
    assert!(
        rows.len() - controls >= 15,
        "only {} resampled rows; the corpus bar would be fitted to a handful \
         of images again",
        rows.len() - controls
    );

    // ...and the content classes must actually span the space. The whole
    // reason the bar was wrong twice is that the fixture set was three
    // flat-shaded drawings.
    let classes: std::collections::HashSet<&str> =
        rows.iter().map(|r| r.content.as_str()).collect();
    for needed in ["photo", "greyscale", "text", "flat"] {
        assert!(
            classes.contains(needed),
            "corpus has no {needed:?} rows; it cannot characterise the residual"
        );
    }
}

#[test]
#[ignore = "downloads/loads ~0.3GB of CLIP text weights; run manually with --ignored"]
fn clip_text_tower_matches_python_within_1e6() {
    pin_threads();
    let (n, dim, expected) = read_reference("clip_text_full_reference.bin");
    assert_eq!(n, TEXTS.len(), "fixture row count must match TEXTS");

    let opts = TextInitOptions::new(EmbeddingModel::ClipVitB32)
        .with_cache_dir(cache_dir())
        .with_show_download_progress(false);
    let mut model = TextEmbedding::try_new(opts).expect("load CLIP text tower");
    let actual = model
        .embed(TEXTS, Some(1))
        .expect("CLIP text tower must embed");
    assert_eq!(actual.len(), n);

    // Positive: every row matches its own Python reference tightly.
    for (i, got) in actual.iter().enumerate() {
        let exp = row(&expected, dim, i);
        assert_eq!(got.len(), dim, "row {i} dim mismatch");
        let mad = max_abs_diff(got, exp);
        let cos = cosine_similarity(got, exp);
        eprintln!("clip text[{i}]: max_abs_diff={mad:.6e} cosine_sim={cos:.12}");
        assert!(
            mad <= 1e-6,
            "clip text[{i}]: max abs diff {mad:.6e} exceeds 1e-6. The CLIP text \
             tower is expected near-exact across the two ORT builds (~1e-8 when \
             this bar was set). Do NOT loosen this — investigate the model \
             files, tokenizer, or a fastembed weight change."
        );
        assert!(
            1.0 - cos <= 1e-6,
            "clip text[{i}]: cosine distance {:.6e} exceeds 1e-6",
            1.0 - cos
        );
    }

    // Negative control: the subject must NOT match the other reference.
    // Without this, a comparison that had quietly become self-vs-self (or a
    // tolerance wide enough to swallow anything) would still pass above.
    let control_mad = max_abs_diff(&actual[0], row(&expected, dim, 1));
    let control_cos = cosine_similarity(&actual[0], row(&expected, dim, 1));
    eprintln!(
        "clip text negative control: max_abs_diff={control_mad:.6e} cosine_sim={control_cos:.12}"
    );
    assert!(
        control_mad > 1e-3,
        "negative control: embedding of {:?} is within {control_mad:.3e} of the \
         reference for {:?}. Two different sentences cannot produce the same \
         vector — the comparison is degenerate, not passing.",
        TEXTS[0],
        TEXTS[1]
    );
}

/// Two tiers, two axes. See this file's module doc for the measured
/// distribution the bars come from and the injected regressions they catch.
///
/// Prints every row's cosine, angle and max-abs on `--nocapture`, so a
/// re-measurement of the whole distribution is one command, not a bespoke
/// harness:
///
/// ```text
/// cargo test --features media,embedder-hub --test clip_embedding_parity -- --ignored --nocapture
/// ```
#[test]
#[ignore = "downloads/loads ~0.3GB of CLIP vision weights; run manually with --ignored"]
fn clip_vision_tower_matches_python_across_the_content_distribution() {
    pin_threads();
    let (n, dim, expected) = read_reference("clip_vision_corpus_reference.bin");
    let rows = corpus_rows();
    assert_eq!(n, rows.len(), "fixture rows must match the manifest");

    let paths: Vec<PathBuf> = rows.iter().map(|r| corpus_image_path(&r.name)).collect();
    for p in &paths {
        assert!(p.exists(), "missing corpus image {}", p.display());
    }

    let opts = ImageInitOptions::new(ImageEmbeddingModel::ClipVitB32)
        .with_cache_dir(cache_dir())
        .with_show_download_progress(false);
    let mut model = ImageEmbedding::try_new(opts).expect("load CLIP vision tower");

    // The load-bearing line. `model.embed(paths)` — fastembed-rs's own path —
    // would squash the aspect ratio (utils.rs:51/:215); measured across this
    // corpus that costs up to 41.2 deg and fails 17 of 28 rows.
    // `open_for_clip` is exactly what `ClipTagsDeriver::score_image` calls, so
    // this test exercises the shipped path rather than a test-only
    // reimplementation of it.
    let imgs = paths
        .iter()
        .map(|p| open_for_clip(p).expect("CLIP preprocessing must succeed"))
        .collect::<Vec<_>>();
    let actual = model
        .embed_images(imgs)
        .expect("CLIP vision tower must embed");
    assert_eq!(actual.len(), n);

    let mut worst = (f64::MAX, String::new());
    let mut worst_mad = (0.0f32, String::new());
    let mut n_controls = 0usize;

    for (i, r) in rows.iter().enumerate() {
        let exp = row(&expected, dim, i);
        assert_eq!(actual[i].len(), dim, "row {i} dim mismatch");
        let cos = cosine_similarity(&actual[i], exp);
        let mad = max_abs_diff(&actual[i], exp);
        let deg = cos.clamp(-1.0, 1.0).acos().to_degrees();
        let control = r.is_no_resize_control();
        eprintln!(
            "clip vision[{i:2}] {:<32} {:>4}x{:<4} {:<10} {}cosine={cos:.12} angle={deg:.4}deg max_abs={mad:.6e}",
            r.name,
            r.width,
            r.height,
            r.content,
            if control { "CONTROL " } else { "        " },
        );

        if control {
            n_controls += 1;
            // The tight tier. Nothing here is allowed the resampler's
            // latitude, because the resampler does not run: `target_size`
            // returns the source dimensions and `resize_exact` short-circuits
            // to a buffer copy. What IS measured is decode, RGB conversion,
            // rescale, normalization, the ONNX weights and the ORT build —
            // and those agreed to 1-cosine ~4e-13 when this bar was set.
            assert!(
                1.0 - cos <= MAX_CONTROL_COSINE_DISTANCE,
                "clip vision[{i}] ({}): this is a NO-RESIZE row — PIL's resampler \
                 never runs on it, so the two runtimes must agree essentially \
                 exactly (measured 1-cosine ~4e-13). Got 1-cosine {:.3e}, over the \
                 {MAX_CONTROL_COSINE_DISTANCE:.0e} bar. This is NOT the resampler \
                 residual the corpus bar tolerates: something changed in the model \
                 weights, the ORT build, the RGB conversion, or the rescale/normalize \
                 arithmetic. Do not loosen this to match the corpus bar — that would \
                 discard the only rows sensitive enough to see such a change.",
                r.name,
                1.0 - cos
            );
            assert!(
                mad <= MAX_CONTROL_ABS_DIFF,
                "clip vision[{i}] ({}): no-resize row max abs diff {mad:.6e} exceeds \
                 {MAX_CONTROL_ABS_DIFF:.0e} — same investigation as above.",
                r.name
            );
        } else {
            if cos < worst.0 {
                worst = (cos, r.name.clone());
            }
            if mad > worst_mad.0 {
                worst_mad = (mad, r.name.clone());
            }
            assert!(
                cos >= MIN_VISION_COSINE,
                "clip vision[{i}] ({}, {}x{}, {}): cosine {cos} ({deg:.4} deg) is below \
                 the SC-12 corpus bar {MIN_VISION_COSINE}. That bar already allows 2.2x \
                 the worst residual measured across this corpus (1.2151 deg), so this is \
                 not ordinary content variation — it is a GEOMETRY divergence. Check that \
                 `clip_preprocess` still scales the shortest edge to 224 preserving aspect \
                 ratio, TRUNCATES (never rounds) the long edge, center-crops with integer \
                 division, and converts to RGB before resizing; that the filter is still \
                 `CatmullRom`; and that this test still goes through \
                 `embed_images(open_for_clip(..))` rather than `embed(path)`. Every one of \
                 those, injected and measured, lands 4-41 deg out. Investigate, do not loosen.",
                r.name,
                r.width,
                r.height,
                r.content
            );
            assert!(
                mad <= MAX_VISION_ABS_DIFF,
                "clip vision[{i}] ({}): max abs diff {mad:.6e} exceeds \
                 {MAX_VISION_ABS_DIFF:.6e}. Cosine over 512 dims is insensitive to a few \
                 large per-component errors, which is why this axis exists — same \
                 investigation as above.",
                r.name
            );
        }
    }

    eprintln!(
        "clip vision: {n} rows, {n_controls} no-resize control(s); worst resampled \
         cosine {:.12} ({}), worst resampled max_abs {:.6e} ({})",
        worst.0, worst.1, worst_mad.0, worst_mad.1
    );
    assert!(
        n_controls >= 1,
        "no row exercised the tight control bar — it is dead code"
    );

    // Negative control. Without it, a comparison that had quietly become
    // self-vs-self — or a bar wide enough to swallow anything — would sail
    // through every assertion above.
    //
    // The control row is CHOSEN BY MEASUREMENT, not by index: pick whichever
    // reference row is least like row 0's. Index 1 was the obvious choice and
    // the wrong one — this corpus is ordered by content class, so rows 0 and 1
    // are two crops of the same photograph and sit at cosine 0.9992. Picking
    // by measurement also means a reordered or regenerated corpus can never
    // silently turn the control into a near-duplicate.
    let (ctrl_idx, control_cos) = (1..n)
        .map(|j| (j, cosine_similarity(&actual[0], row(&expected, dim, j))))
        .min_by(|a, b| a.1.partial_cmp(&b.1).unwrap())
        .expect("corpus must have more than one row");
    eprintln!(
        "clip vision negative control: {} vs the reference for {} -> cosine_sim={control_cos:.12}",
        rows[0].name, rows[ctrl_idx].name
    );
    assert!(
        control_cos < 0.99,
        "negative control: {} embeds to within cosine {control_cos} of the \
         reference for {}, the LEAST similar row in the corpus — the comparison \
         cannot tell any two images apart, so the {MIN_VISION_COSINE} floor above \
         proves nothing.",
        rows[0].name,
        rows[ctrl_idx].name
    );
    // ...and row 0 must still be closer to its OWN reference than to that one,
    // or "matches its reference" and "matches anything" are the same claim.
    let own = cosine_similarity(&actual[0], row(&expected, dim, 0));
    assert!(
        own > control_cos,
        "{} is closer to {}'s reference ({control_cos}) than to its own ({own})",
        rows[0].name,
        rows[ctrl_idx].name
    );
}
