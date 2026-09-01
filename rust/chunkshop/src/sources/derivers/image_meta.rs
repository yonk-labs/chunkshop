//! Rung 0 for images: deterministic metadata, no model, no network. Mirrors
//! `python/src/chunkshop/sources/derivers/image_meta.py`.
//!
//! Rung 0 is deterministic, so unlike the CLIP rungs, Python and Rust are
//! expected to agree on VALUES here, not merely on metadata keys — see
//! `tests/media_derivation_parity.rs`'s doc comment for the schema-vs-value
//! split, and Task 9 Correction 1 for how `image_mode`/`has_alpha` parity
//! was measured and closed for PNG and GIF specifically.

use std::fs::File;
use std::io::BufReader;
use std::path::Path;

use image::{ColorType, GenericImageView, ImageFormat};
use serde_json::{json, Map, Value};

use super::provenance::stamp_provenance;
use crate::sources::parsers::{CacheableDeriver, Deriver, ParseResult};

/// `goblin_warrior-02` -> `goblin warrior 02`. Splits on `_`, `-`, `.`.
pub fn humanise(stem: &str) -> String {
    stem.split(|c| c == '_' || c == '-' || c == '.')
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Matches Pillow's `img.format` string exactly for the formats this
/// function handles — NOT `ImageFormat::extensions_str()[0]`, which would
/// give "jpg" (not Pillow's "JPEG") for .jpg files. Verified via docs.rs:
/// `image` 0.25's `ImageFormat::extensions_str()` for `Jpeg` returns
/// `["jpg", "jpeg"]` (jpg first) — using it naively would silently diverge
/// from Python's metadata value on every JPEG.
///
/// Returns an error, rather than leaking, for any other format. The
/// original plan draft assumed this arm was unreachable because Task 2's
/// registry only routes 8 extensions here — that assumption does NOT hold:
/// checked against `sources/parsers/registry.rs` as it now actually ships
/// (Task 9 Correction 3), that registry never routes ANY image extension
/// to `ImageMetaDeriver` at all — image routing is entirely driven by the
/// user's own `derive:` YAML block (Task 8's `ImageMetaDeriverConfig`
/// places no extension restriction), so a config like
/// `derive: {"ico": {rung: image_meta}}` reaches this function with
/// `ImageFormat::Ico` for any format the `image` crate recognizes by
/// extension. That IS user-reachable, so this returns a named error
/// instead of `Box::leak`ing.
fn pillow_style_format_name(fmt: ImageFormat) -> anyhow::Result<&'static str> {
    Ok(match fmt {
        ImageFormat::Png => "PNG",
        ImageFormat::Jpeg => "JPEG",
        ImageFormat::WebP => "WEBP",
        ImageFormat::Gif => "GIF",
        ImageFormat::Bmp => "BMP",
        ImageFormat::Tiff => "TIFF",
        other => {
            return Err(anyhow::anyhow!(
                "image_meta deriver does not support the {other:?} format; \
                 supported formats are png, jpg/jpeg, webp, gif, bmp, tiff/tif"
            ));
        }
    })
}

pub struct ImageMetaDeriver {
    pub path_segments: bool,
    pub segment_depth: usize,
}

impl Default for ImageMetaDeriver {
    fn default() -> Self {
        Self {
            path_segments: true,
            segment_depth: 3,
        }
    }
}

impl Deriver for ImageMetaDeriver {
    fn derive(&self, path: &Path) -> anyhow::Result<ParseResult> {
        let img = image::open(path)
            .map_err(|e| anyhow::anyhow!("failed to parse image {}: {e}", path.display()))?;
        let (width, height) = img.dimensions();
        let decoded_color = img.color();
        let fmt = ImageFormat::from_path(path).unwrap_or(ImageFormat::Png);
        let fmt_name = pillow_style_format_name(fmt)?;
        let (mode, has_alpha) = pillow_mode_and_alpha(path, fmt, decoded_color);

        let mut text = self.path_text(path);
        if text.trim().is_empty() {
            text = format!("{} image", fmt_name.to_lowercase());
        }

        let mut metadata = Map::new();
        metadata.insert("width".to_string(), json!(width));
        metadata.insert("height".to_string(), json!(height));
        metadata.insert(
            "image_format".to_string(),
            Value::String(fmt_name.to_string()),
        );
        metadata.insert("image_mode".to_string(), Value::String(mode));
        metadata.insert(
            "aspect_ratio".to_string(),
            json!(
                if height > 0 {
                    (width as f64 / height as f64 * 10000.0).round() / 10000.0
                } else {
                    0.0
                }
            ),
        );
        metadata.insert("has_alpha".to_string(), Value::Bool(has_alpha));

        let result = ParseResult::with_metadata(text, metadata);
        let params = json!({"path_segments": self.path_segments, "segment_depth": self.segment_depth});
        Ok(stamp_provenance(
            result,
            "image_meta",
            "deterministic",
            "1",
            "rust",
            &params,
        ))
    }
}

impl ImageMetaDeriver {
    /// The path-derived prefix of this deriver's output text.
    ///
    /// The single place path -> text material is computed. `derive()`
    /// builds its output from it and `cache_key_extra()` returns it
    /// verbatim, so the two can never drift into disagreeing about which
    /// path material was consumed — a second, key-only re-derivation of
    /// the same logic is exactly the duplication class that has already
    /// produced defects on this branch.
    ///
    /// May be empty (a stem of all separators humanises to nothing);
    /// `derive()` owns the non-empty fallback, which depends on the image
    /// format rather than the path and so is not key material.
    pub fn path_text(&self, path: &Path) -> String {
        let mut words = vec![humanise(
            path.file_stem().and_then(|s| s.to_str()).unwrap_or(""),
        )];
        if self.path_segments {
            if let Some(parent) = path.parent() {
                let segs: Vec<&str> = parent
                    .components()
                    .filter_map(|c| c.as_os_str().to_str())
                    .collect();
                let start = segs.len().saturating_sub(self.segment_depth);
                for s in &segs[start..] {
                    words.push(humanise(s));
                }
            }
        }
        words
            .into_iter()
            .filter(|w| !w.is_empty())
            .collect::<Vec<_>>()
            .join(" ")
    }
}

impl CacheableDeriver for ImageMetaDeriver {
    fn cache_key_parts(&self) -> (String, String, String, Value) {
        (
            "image_meta".to_string(),
            "deterministic".to_string(),
            "1".to_string(),
            json!({"path_segments": self.path_segments, "segment_depth": self.segment_depth}),
        )
    }

    /// This deriver is NOT a pure function of its bytes.
    ///
    /// Its output text comes from the filename (always) and the parent
    /// segments (only when `path_segments` is on), so byte-identical
    /// images with different names must not share a cache entry. Returning
    /// `path_text` — the exact material consumed under the CURRENT config,
    /// not the path unconditionally — keeps the key as narrow as
    /// correctness allows: with `path_segments` off the parent directory
    /// is correctly absent from the key, and a rename that leaves the
    /// humanised text unchanged (`a-b.png` -> `a_b.png`) still hits,
    /// because the derived text is genuinely identical.
    fn cache_key_extra(&self, path: &Path) -> Option<String> {
        Some(self.path_text(path))
    }
}

/// Pillow-exact `(mode, has_alpha)` where the `image` crate's post-decode
/// `ColorType` would diverge from PIL's `img.mode` — measured empirically
/// (Task 9 Correction 1) across a fixture set covering palette PNG,
/// palette+tRNS PNG, grayscale PNG, RGBA PNG, JPEG, WebP, and GIF:
///
/// - PNG: `image` EXPANDS palette images to `Rgb8`/`Rgba8` at decode time,
///   losing PIL's "P" mode entirely, and only sets `has_alpha` from the
///   post-expansion color type — which happens to still come out right for
///   a palette PNG with a tRNS chunk (the crate bakes the palette's alpha
///   into the expanded RGBA buffer) but gives the wrong MODE string either
///   way. Fixed by reading the IHDR color-type byte and tRNS-chunk presence
///   directly via the `png` crate's low-level reader (`png_mode_and_alpha`
///   below), bypassing `image`'s expansion.
/// - GIF: `image`'s GIF decoder unconditionally expands every frame to
///   `Rgba8` regardless of whether the file declares a transparent color,
///   so `ColorType::has_alpha()` is `true` for every GIF — including ones
///   with no transparency at all. Pillow always opens a GIF as mode "P"
///   (GIF is inherently palette-based) with `has_alpha` reflecting whether
///   the Graphic Control Extension actually sets a transparent index.
///   Fixed by reading that flag directly via the `gif` crate
///   (`gif_mode_and_alpha` below).
/// - JPEG, WebP (opaque), BMP, TIFF: not observed to diverge in the fixture
///   set exercised here (PIL reports "RGB" and `image`'s post-decode
///   `ColorType` agrees) — these fall through to the plain `ColorType`
///   mapping. BMP and TIFF specifically were NOT covered by Correction 1's
///   fixture set (only PNG variants, JPEG, WebP, and GIF were); a
///   palette-mode BMP or TIFF could in principle hit the same class of
///   divergence PNG did, unverified.
fn pillow_mode_and_alpha(path: &Path, fmt: ImageFormat, decoded: ColorType) -> (String, bool) {
    match fmt {
        ImageFormat::Png => {
            png_mode_and_alpha(path).unwrap_or_else(|| fallback_mode_and_alpha(decoded))
        }
        ImageFormat::Gif => {
            gif_mode_and_alpha(path).unwrap_or_else(|| fallback_mode_and_alpha(decoded))
        }
        _ => fallback_mode_and_alpha(decoded),
    }
}

fn fallback_mode_and_alpha(c: ColorType) -> (String, bool) {
    (color_type_mode(c), c.has_alpha())
}

/// Best-effort Pillow-style mode string from `image`'s POST-DECODE
/// `ColorType` — only used as a fallback when the format-specific direct
/// readers below don't apply or fail to open the file a second time.
fn color_type_mode(c: ColorType) -> String {
    match c {
        ColorType::L8 | ColorType::L16 => "L",
        ColorType::La8 | ColorType::La16 => "LA",
        ColorType::Rgb8 | ColorType::Rgb16 | ColorType::Rgb32F => "RGB",
        ColorType::Rgba8 | ColorType::Rgba16 | ColorType::Rgba32F => "RGBA",
        _ => "UNKNOWN",
    }
    .to_string()
}

/// Reads the PNG IHDR color-type byte and tRNS-chunk presence directly via
/// `png::Decoder` (`Transformations::IDENTITY` by default — no expansion),
/// reproducing Pillow's exact `mode`/`has_alpha` for grayscale, RGB,
/// palette, and palette+tRNS PNGs alike. `has_alpha` mirrors Python's own
/// `mode in ("RGBA", "LA", "PA") or "transparency" in img.info`: PNG has no
/// "PA" IHDR color type (PIL only produces "PA" via an explicit
/// `.convert("PA")`, never from a bare `Image.open`), so that arm never
/// applies here.
///
/// Returns `None` (falls back to `image`'s decoded `ColorType`) if the file
/// can't be opened/parsed a second time via `png::Decoder` for any reason —
/// a value-fidelity enhancement, not a correctness-critical path, since
/// `image::open` already decoded this same file successfully.
fn png_mode_and_alpha(path: &Path) -> Option<(String, bool)> {
    let file = File::open(path).ok()?;
    let decoder = png::Decoder::new(BufReader::new(file));
    let reader = decoder.read_info().ok()?;
    let info = reader.info();
    let mode = match info.color_type {
        png::ColorType::Grayscale => "L",
        png::ColorType::GrayscaleAlpha => "LA",
        png::ColorType::Rgb => "RGB",
        png::ColorType::Rgba => "RGBA",
        png::ColorType::Indexed => "P",
    };
    let has_alpha = matches!(mode, "RGBA" | "LA") || info.trns.is_some();
    Some((mode.to_string(), has_alpha))
}

/// Reads the GIF's first frame's Graphic Control Extension directly via
/// `gif::Decoder` to recover whether the file declares a transparent color
/// index. Pillow always opens a GIF as mode "P" (GIF is inherently
/// palette-indexed), so only `has_alpha` needs probing here.
///
/// `ColorOutput::Indexed` skips the (unneeded) RGBA pixel expansion this
/// deriver never uses — only `frame.transparent` is read.
///
/// Returns `None` (falls back to `image`'s decoded `ColorType`, i.e.
/// `has_alpha = true` unconditionally) if the file can't be opened/parsed a
/// second time via `gif::Decoder`, or has no frames at all.
fn gif_mode_and_alpha(path: &Path) -> Option<(String, bool)> {
    let file = File::open(path).ok()?;
    let mut options = gif::DecodeOptions::new();
    options.set_color_output(gif::ColorOutput::Indexed);
    let mut reader = options.read_info(file).ok()?;
    let has_alpha = reader
        .read_next_frame()
        .ok()?
        .map(|f| f.transparent.is_some())
        .unwrap_or(false);
    Some(("P".to_string(), has_alpha))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn humanise_splits_on_underscore_dash_dot() {
        assert_eq!(humanise("goblin_warrior-02"), "goblin warrior 02");
    }

    fn write_rgb_png(dir: &Path, name: &str, w: u32, h: u32) -> std::path::PathBuf {
        let p = dir.join(name);
        let img: image::RgbImage = image::ImageBuffer::from_pixel(w, h, image::Rgb([200, 30, 30]));
        img.save(&p).unwrap();
        p
    }

    fn write_rgba_png(dir: &Path, name: &str, w: u32, h: u32) -> std::path::PathBuf {
        let p = dir.join(name);
        let img: image::RgbaImage =
            image::ImageBuffer::from_pixel(w, h, image::Rgba([200, 30, 30, 255]));
        img.save(&p).unwrap();
        p
    }

    #[test]
    fn emits_dimensions_and_format() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_rgb_png(dir.path(), "sprite.png", 64, 32);
        let result = ImageMetaDeriver::default().derive(&p).unwrap();
        assert_eq!(result.metadata["width"], json!(64));
        assert_eq!(result.metadata["height"], json!(32));
        assert_eq!(result.metadata["image_format"], json!("PNG"));
        assert_eq!(result.metadata["aspect_ratio"], json!(2.0));
    }

    #[test]
    fn has_alpha_true_for_rgba() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_rgba_png(dir.path(), "sprite.png", 8, 8);
        let result = ImageMetaDeriver::default().derive(&p).unwrap();
        assert_eq!(result.metadata["has_alpha"], json!(true));
        assert_eq!(result.metadata["image_mode"], json!("RGBA"));
    }

    #[test]
    fn has_alpha_false_for_rgb() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_rgb_png(dir.path(), "sprite.png", 8, 8);
        let result = ImageMetaDeriver::default().derive(&p).unwrap();
        assert_eq!(result.metadata["has_alpha"], json!(false));
        assert_eq!(result.metadata["image_mode"], json!("RGB"));
    }

    #[test]
    fn palette_png_reports_p_mode_matching_pillow() {
        // Task 9 Correction 1's core adversarial case: `image` expands
        // palette PNGs to Rgb8 at decode time, but Pillow reports "P".
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("swatch.png");
        let mut img = image::RgbaImage::new(4, 4);
        for px in img.pixels_mut() {
            *px = image::Rgba([255, 0, 0, 255]);
        }
        // Quantize down to an indexed palette PNG via the `image` crate's
        // own encoder options isn't exposed simply here, so this test
        // instead writes a real indexed PNG by hand using the `png` crate
        // directly (same crate the deriver reads back with).
        let file = std::fs::File::create(&p).unwrap();
        let w = std::io::BufWriter::new(file);
        let mut encoder = png::Encoder::new(w, 4, 4);
        encoder.set_color(png::ColorType::Indexed);
        encoder.set_depth(png::BitDepth::Eight);
        encoder.set_palette(vec![255u8, 0, 0, 0, 255, 0]);
        let mut writer = encoder.write_header().unwrap();
        writer.write_image_data(&[0u8; 16]).unwrap();
        drop(writer);

        let result = ImageMetaDeriver::default().derive(&p).unwrap();
        assert_eq!(result.metadata["image_mode"], json!("P"));
        assert_eq!(result.metadata["has_alpha"], json!(false));
    }

    #[test]
    fn palette_png_with_trns_reports_p_mode_and_has_alpha_true() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("swatch_trns.png");
        let file = std::fs::File::create(&p).unwrap();
        let w = std::io::BufWriter::new(file);
        let mut encoder = png::Encoder::new(w, 4, 4);
        encoder.set_color(png::ColorType::Indexed);
        encoder.set_depth(png::BitDepth::Eight);
        encoder.set_palette(vec![255u8, 0, 0, 0, 255, 0]);
        encoder.set_trns(vec![0u8]); // palette index 0 is transparent
        let mut writer = encoder.write_header().unwrap();
        writer.write_image_data(&[0u8; 16]).unwrap();
        drop(writer);

        let result = ImageMetaDeriver::default().derive(&p).unwrap();
        assert_eq!(result.metadata["image_mode"], json!("P"));
        assert_eq!(result.metadata["has_alpha"], json!(true));
    }

    #[test]
    fn gif_reports_p_mode_and_has_alpha_false_without_transparency() {
        // Task 9 Correction 1's other core adversarial case: `image`'s GIF
        // decoder always expands to Rgba8 (has_alpha=true unconditionally),
        // but a GIF with no Graphic Control Extension transparency flag
        // must report has_alpha=false to match Pillow.
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("swatch.gif");
        let file = std::fs::File::create(&p).unwrap();
        let mut encoder = gif::Encoder::new(file, 4, 4, &[255, 0, 0, 0, 255, 0]).unwrap();
        let frame = gif::Frame::from_indexed_pixels(4, 4, vec![0u8; 16], None);
        encoder.write_frame(&frame).unwrap();
        drop(encoder);

        let result = ImageMetaDeriver::default().derive(&p).unwrap();
        assert_eq!(result.metadata["image_mode"], json!("P"));
        assert_eq!(result.metadata["has_alpha"], json!(false));
    }

    #[test]
    fn text_includes_humanised_filename_and_path_segments() {
        let dir = tempfile::tempdir().unwrap();
        let sprites_dir = dir.path().join("sprites");
        std::fs::create_dir(&sprites_dir).unwrap();
        let p = write_rgb_png(&sprites_dir, "goblin_warrior.png", 8, 8);
        let text = ImageMetaDeriver::default().derive(&p).unwrap().text.to_lowercase();
        assert!(text.contains("goblin"), "{text}");
        assert!(text.contains("warrior"), "{text}");
        assert!(text.contains("sprites"), "{text}");
    }

    #[test]
    fn never_returns_empty_text_even_for_a_bare_stem() {
        // Adversarial case per this plan's own rule 3: a filename that
        // humanises to nothing (all separator characters) must still fall
        // back to "<format> image", not blank text that on_empty=error
        // would then reject.
        let dir = tempfile::tempdir().unwrap();
        let p = write_rgb_png(dir.path(), "___.png", 8, 8);
        let deriver = ImageMetaDeriver { path_segments: false, segment_depth: 3 };
        let text = deriver.derive(&p).unwrap().text;
        assert!(!text.trim().is_empty(), "must never be empty, got {text:?}");
        assert!(text.contains("image"));
    }

    #[test]
    fn provenance_is_stamped_with_rust_engine() {
        let dir = tempfile::tempdir().unwrap();
        let p = write_rgb_png(dir.path(), "sprite.png", 8, 8);
        let md = ImageMetaDeriver::default().derive(&p).unwrap().metadata;
        assert_eq!(md["content_origin"], json!("derived"));
        assert_eq!(md["derived_rung"], json!("image_meta"));
        assert_eq!(md["derived"]["engine"], json!("rust"));
        assert_eq!(md["derived"]["model"], json!("deterministic"));
    }

    #[test]
    fn unsupported_format_returns_a_named_error_instead_of_leaking() {
        // Task 9 Correction 3: `pillow_style_format_name`'s fallback arm IS
        // reachable (any extension can be routed to `image_meta` via
        // `derive:` YAML), so an unsupported-but-`image`-crate-decodable
        // format must return a normal error, not `Box::leak`.
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("icon.ico");
        // A minimal valid 1x1 ICO the `image` crate can decode.
        let img: image::RgbaImage = image::ImageBuffer::from_pixel(1, 1, image::Rgba([1, 2, 3, 255]));
        img.save_with_format(&p, ImageFormat::Ico).unwrap();

        let err = ImageMetaDeriver::default().derive(&p).unwrap_err();
        assert!(
            format!("{err:#}").contains("does not support"),
            "{err:#}"
        );
    }

    #[test]
    fn cache_key_parts_match_the_stamped_provenance_tuple() {
        // Task 9 Step 4b: `cache_key_parts()` must be the SAME tuple
        // `derive()` stamps via `stamp_provenance`, so the cache key and
        // the recorded provenance can never silently disagree.
        let deriver = ImageMetaDeriver {
            path_segments: true,
            segment_depth: 3,
        };
        let (rung, model, version, params) = deriver.cache_key_parts();
        assert_eq!(rung, "image_meta");
        assert_eq!(model, "deterministic");
        assert_eq!(version, "1");
        assert_eq!(params, json!({"path_segments": true, "segment_depth": 3}));

        let dir = tempfile::tempdir().unwrap();
        let p = write_rgb_png(dir.path(), "sprite.png", 8, 8);
        let md = deriver.derive(&p).unwrap().metadata;
        assert_eq!(md["derived"]["rung"], json!(rung));
        assert_eq!(md["derived"]["model"], json!(model));
        assert_eq!(md["derived"]["model_version"], json!(version));

        let expected_hash = super::super::provenance::derivation_hash(
            &rung, &model, &version, "rust", &params,
        );
        let recomputed = super::super::provenance::derivation_hash(
            md["derived"]["rung"].as_str().unwrap(),
            md["derived"]["model"].as_str().unwrap(),
            md["derived"]["model_version"].as_str().unwrap(),
            md["derived"]["engine"].as_str().unwrap(),
            &params,
        );
        assert_eq!(expected_hash, recomputed);
    }
}
