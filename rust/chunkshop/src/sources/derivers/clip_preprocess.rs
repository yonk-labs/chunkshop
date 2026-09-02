//! Aspect-ratio-preserving CLIP image preprocessing — the geometry half of
//! `Qdrant/clip-ViT-B-32-vision`'s `preprocessor_config.json`, done by
//! chunkshop instead of by `fastembed-rs`.
//!
//! # Why this exists (upstream bug, measured not guessed)
//!
//! `preprocessor_config.json` for the CLIP vision tower says
//! `size: {"shortest_edge": 224}` + `crop_size: 224x224`. HuggingFace's
//! `CLIPImageProcessor` semantics for `shortest_edge` are: scale the image so
//! its SHORTEST edge becomes 224 **preserving aspect ratio**, then center-crop
//! 224x224. Python `fastembed` does exactly that
//! (`fastembed/image/transform/functional.py::resize`).
//!
//! `fastembed-rs` 5.13.3 does not. `src/image_embedding/utils.rs:215` turns
//! `shortest_edge` into a square target `(224, 224)`, and `:51` applies it with
//! `DynamicImage::resize_exact`, which does **not** preserve aspect ratio. Any
//! non-square image is squashed to 224x224 and the following center-crop
//! becomes a no-op. The two implementations then see a different field of view,
//! not merely a different resampling of the same view.
//!
//! Measured through the real ONNX vision tower, identical filter on both sides,
//! only the geometry differing:
//!
//! | image | source | `resize_exact` squash | aspect-correct (this module) |
//! |---|---|---|---|
//! | `sprite_knight.png` | 128x128 | 0.999989 (0.26°) | 0.999989 (0.26°) |
//! | `chart_revenue.png` | 320x220 | 0.973479 (13.2°) | 0.999991 (0.24°) |
//! | `screenshot_settings.png` | 360x260 | 0.897174 (26.2°) | 0.999991 (0.24°) |
//!
//! The bug is **aspect-ratio-dependent, not downscale-dependent**: the square
//! 128x128 fixture is upscaled and was always fine, because for a square image
//! `resize_exact(224,224)` happens to BE the aspect-preserving answer.
//!
//! # What is NOT the problem: the filter
//!
//! `FilterType::CatmullRom` is the correct match for PIL's `BICUBIC` — both are
//! the Keys cubic with `a = -0.5`. At matched dimensions the two agree on
//! 96.6–98.8% of pixels bit-for-bit (mean abs error 0.013–0.044 out of 255).
//! **Do not change the filter.** The residual that survives this fix is sub-LSB
//! rounding inside PIL's fixed-point 8-bit resampler versus `image`'s f32 one,
//! and it is the floor of what is achievable without reimplementing Pillow's
//! integer kernel. It is CONTENT-dependent: measured over the 28-image corpus
//! in `tests/parity-fixtures/clip-vision-corpus/` it runs from 2.1e-4 max abs
//! (0.075 deg, a checkerboard) to 5.3e-3 (0.92 deg, an upscaled photograph),
//! median 2.1e-3 (0.78 deg). The 6.8e-4 once quoted here was the figure for
//! `sprite_knight.png` — a flat-shaded drawing at the easy end of that range,
//! and not representative. Images needing NO resize agree exactly
//! (`1 - cosine ~ 4e-13`), which is what pins the cause on the resampler.
//!
//! # How the fix is applied
//!
//! `ClipTagsDeriver` calls [`open_for_clip`] and hands the resulting, already
//! exactly-224x224 image to `ImageEmbedding::embed_images` instead of handing a
//! path to `ImageEmbedding::embed`. `fastembed-rs`'s own pipeline then runs on
//! a 224x224 input, where both of its geometry steps are provably identity:
//!
//! - `Resize` → `resize_exact(224, 224)` → `image::imageops::resize`, which
//!   short-circuits to a plain buffer copy when the target dimensions equal the
//!   source dimensions (`image` 0.25.10, `src/imageops/sample.rs:985`). The
//!   resampler never runs, so this is exact identity — not "nearly identity",
//!   which is precisely what this fix exists to eliminate.
//! - `CenterCrop` → `crop_imm(0, 0, 224, 224)` on a 224x224 image.
//!
//! Everything downstream of geometry (`PILToNDarray`, `Rescale`, `Normalize`)
//! is left to `fastembed-rs`; it already matches Python componentwise.
//!
//! # Bit depth: SATURATE, do not scale
//!
//! `convert_to_rgb_like_pil` clamps 16-bit greyscale samples to 255 instead of
//! rescaling them into 0..255, and that looks wrong until you know why.
//!
//! PIL opens a 16-bit greyscale PNG or TIFF as mode `I;16` — 16-bit integers,
//! not a wider colour channel — and `convert("RGB")` on an integer image
//! CLIPS: sample 30000 becomes 255, and so does 500. `image` decodes the same
//! file as `Luma16` and `into_rgb8()` RESCALES: 30000 becomes 117.
//! Measured through the real ONNX vision tower (the same session on both
//! sides, so preprocessing is the only variable) on a 256x192 synthetic
//! depth map spanning the full u16 range, plus the identical picture
//! pre-reduced to 8 bits as a control:
//!
//! | image | cosine (Rust vs Python) | angle | mean abs pixel error | pixels differing |
//! |---|---|---|---|---|
//! | 16-bit greyscale, rescaling (before) | 0.865687703 | 30.04° | 164 / 255 | 100.0% |
//! | 16-bit greyscale, saturating (after) | 1.000000000 | 0.00° | 0 / 255 | 0.0% |
//! | 8-bit greyscale control (unchanged) | 0.998849662 | 2.75° | 0 / 255 | 4.6% |
//!
//! EVERY pixel differed before the fix — a larger divergence than the
//! aspect-ratio bug this module was originally written to fix. After it, the
//! two pipelines produce byte-identical 224x224 tensors for this image.
//!
//! The control's residual 2.75° is NOT this bug: its worst pixel is off by a
//! single least-significant bit, which is the known PIL-integer-resampler vs
//! `image`-f32-resampler floor described under "What is NOT the problem"
//! below. It is unchanged by this fix, which is the point of having it.
//!
//! This is not bug-compatibility with PIL for its own sake. HuggingFace's
//! `CLIPImageProcessor` is PIL-based, so the model's training data and its
//! reference preprocessing both saw PIL's saturating conversion. Matching PIL
//! is matching what the vision tower was trained to expect; "correcting" it to
//! a linear rescale would feed the model an image it has never seen.
//!
//! Only the greyscale-without-alpha case is special-cased. PIL loads 16-bit
//! RGB and 16-bit greyscale+alpha through raw modes that take the HIGH BYTE
//! (`RGB;16B`, `LA;16B`), which is a rescale, and `into_rgb8()` already agrees
//! with that to within a least-significant bit.
//!
//! # Truncation, not rounding
//!
//! Python computes the long edge as `int(size * long / short)` — f64 division
//! then truncation toward zero. Rust's `as u32` cast on an f64 truncates the
//! same way, so [`target_size`] reproduces it bit-for-bit. This matters: the
//! sibling tool `ross` rounds instead, and on `chart_revenue.png` that one-pixel
//! difference (326 vs 325) costs 4.06 degrees.
//!
//! # Very large images: PIL's policy, not `image`'s allocator cap
//!
//! [`open_for_clip`] used to be `ImageReader::open(path)?.decode()`, which
//! inherits `image`'s `Limits::default()` — a 512 MiB `max_alloc`. A 14432x9664
//! palette PNG with `tRNS` is 136 KB on disk and 139,470,848 pixels; palette +
//! `tRNS` expands to `Rgba8`, so the decode wants 558 MB and the crate refuses
//! it with `Memory limit exceeded`. Python ingests the same file with nothing
//! worse than a `DecompressionBombWarning`, and because a parser error aborts
//! the whole cell in both ports, one such file in a corpus cost every row in
//! the batch — measured end to end, the Rust cell exited 1 having written 0
//! rows while the Python cell wrote all 4.
//!
//! The decode is now guarded by [`decompression_bomb_check`], a transcription
//! of `PIL.Image._decompression_bomb_check`, and the allocator cap is lifted so
//! that the policy — not an incidental byte budget — is what decides. See that
//! function for the bands and [`open_for_clip`] for why the cap is lifted
//! rather than merely raised.

use image::{imageops::FilterType, DynamicImage, GenericImageView, ImageDecoder};
use std::path::Path;

/// `size.shortest_edge` and `crop_size.{height,width}` from
/// `Qdrant/clip-ViT-B-32-vision`'s `preprocessor_config.json`.
///
/// Hardcoded rather than read from the JSON because `ClipTagsDeriver` pins the
/// model pair — `check_model_supported` rejects any override — so there is
/// exactly one preprocessor config this path can ever see. If that pin is ever
/// widened to a model with a different `shortest_edge`, this constant must be
/// read from the config file in the same change, or the pre-resize below and
/// `fastembed-rs`'s own resize would disagree and the geometry would silently
/// change again.
pub const CLIP_SHORTEST_EDGE: u32 = 224;

/// PIL's `BICUBIC` is the Keys cubic with `a = -0.5`; so is `CatmullRom`.
/// Same filter `fastembed-rs` uses, deliberately — see the module doc.
const CLIP_RESAMPLE: FilterType = FilterType::CatmullRom;

/// `PIL.Image.MAX_IMAGE_PIXELS`.
///
/// **This is a parity contract, not a tuning knob.** Pillow computes it as
/// `int(1024 * 1024 * 1024 // 4 // 3)`, and the installed Pillow (12.3.0)
/// reports exactly this value — both checked by `the_constants_match_pil`.
/// Python is the reference implementation here, so changing this number
/// without changing it there is a parity regression, not a tuning decision.
/// Above it Pillow warns and decodes anyway; above `2 *` it raises. See
/// [`decompression_bomb_check`].
pub const MAX_IMAGE_PIXELS: u64 = 89_478_485;

/// Pillow's hard ceiling: `2 * MAX_IMAGE_PIXELS`, the point at which
/// `_decompression_bomb_check` raises `DecompressionBombError` instead of
/// warning. Named rather than written inline so the `2 *` is visibly the same
/// `2 *` that appears in Pillow's source.
pub const MAX_IMAGE_PIXELS_HARD: u64 = 2 * MAX_IMAGE_PIXELS;

/// What Pillow's decompression-bomb policy says about an image's dimensions.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BombVerdict {
    /// `pixels <= MAX_IMAGE_PIXELS`. Decode, silently.
    Allow,
    /// `MAX_IMAGE_PIXELS < pixels <= 2 * MAX_IMAGE_PIXELS`. Pillow emits a
    /// `DecompressionBombWarning` and **decodes anyway**; so do we.
    Warn,
    /// `pixels > 2 * MAX_IMAGE_PIXELS`. Pillow raises
    /// `DecompressionBombError`; so do we.
    Reject,
}

/// `PIL.Image._decompression_bomb_check`, transcribed.
///
/// Pillow's body, for comparison:
///
/// ```python
/// pixels = max(1, size[0]) * max(1, size[1])
/// if pixels > 2 * MAX_IMAGE_PIXELS:
///     raise DecompressionBombError(...)
/// if pixels > MAX_IMAGE_PIXELS:
///     warnings.warn(..., DecompressionBombWarning)
/// ```
///
/// Three details are load-bearing and are reproduced rather than tidied up:
///
/// - Both comparisons are strict `>`, so a pixel count landing *exactly* on
///   either constant takes the gentler branch. Exactly `MAX_IMAGE_PIXELS` is
///   silent; exactly `2 * MAX_IMAGE_PIXELS` warns rather than rejecting.
/// - `max(1, ...)` per axis, so a zero-sized dimension contributes 1 and a
///   0x0 image counts as 1 pixel, not 0. It never reaches the bomb branches
///   either way, but the clamp is Pillow's and is kept so the two ports can
///   be diffed line for line. (`resize_and_center_crop` is what actually
///   rejects a zero dimension, in both ports.)
/// - The product is computed in `u64`. `u32 * u32` overflows for anything past
///   about 65535 square, and a wrapped product could land back inside the
///   allow band — which is precisely the input a decompression bomb wants.
pub fn decompression_bomb_check(width: u32, height: u32) -> BombVerdict {
    let pixels = u64::from(width.max(1)) * u64::from(height.max(1));
    if pixels > MAX_IMAGE_PIXELS_HARD {
        BombVerdict::Reject
    } else if pixels > MAX_IMAGE_PIXELS {
        BombVerdict::Warn
    } else {
        BombVerdict::Allow
    }
}

/// The `(width, height)` an image is scaled to before the center crop.
///
/// Mirrors `fastembed/image/transform/functional.py::resize` exactly,
/// including its `int()` truncation:
///
/// ```text
/// short, long   = (width, height) if width <= height else (height, width)
/// new_short     = size
/// new_long      = int(size * long / short)
/// new_size      = (new_short, new_long) if width <= height else (new_long, new_short)
/// ```
///
/// Returns `None` for a degenerate (zero-dimension) image, which cannot be
/// scaled to a 224-shortest-edge anything.
pub fn target_size(width: u32, height: u32, shortest_edge: u32) -> Option<(u32, u32)> {
    if width == 0 || height == 0 {
        return None;
    }
    let (short, long) = if width <= height {
        (width, height)
    } else {
        (height, width)
    };
    // f64 arithmetic in the same order Python evaluates it, then truncate
    // toward zero exactly as `int()` does.
    let new_long = (shortest_edge as f64 * long as f64 / short as f64) as u32;
    // A `long` edge that truncates below `shortest_edge` is impossible for
    // long >= short, but clamp anyway so the crop below can never underflow.
    let new_long = new_long.max(shortest_edge);
    Some(if width <= height {
        (shortest_edge, new_long)
    } else {
        (new_long, shortest_edge)
    })
}

/// Aspect-preserving resize to a 224 shortest edge, then a 224x224 center crop.
///
/// The caller must have already converted to RGB — the ordering matters,
/// because both implementations run `convert_to_rgb` BEFORE the resize and
/// dropping an alpha channel after resampling is not the same operation as
/// dropping it before. [`open_for_clip`] does this; call it rather than this
/// function unless you have your own already-RGB `DynamicImage`.
///
/// Center-crop offsets use integer division, matching Python's
/// `(orig - crop) // 2` and `fastembed-rs`'s own `(origin - crop) / 2`.
pub fn resize_and_center_crop(
    img: DynamicImage,
    shortest_edge: u32,
) -> anyhow::Result<DynamicImage> {
    let (w, h) = img.dimensions();
    let (new_w, new_h) = target_size(w, h, shortest_edge).ok_or_else(|| {
        anyhow::anyhow!("cannot CLIP-preprocess a {w}x{h} image: zero-sized dimension")
    })?;
    // `resize_exact` at matching dimensions is a buffer copy, not a resample
    // (image 0.25.10 short-circuits), so a square 224 input passes through
    // untouched instead of being re-filtered.
    let resized = img.resize_exact(new_w, new_h, CLIP_RESAMPLE);
    let x = (new_w - shortest_edge) / 2;
    let y = (new_h - shortest_edge) / 2;
    Ok(resized.crop_imm(x, y, shortest_edge, shortest_edge))
}

/// Decode an image file and apply CLIP's geometry: RGB, aspect-preserving
/// resize to a 224 shortest edge, 224x224 center crop.
///
/// The result is exactly `CLIP_SHORTEST_EDGE` square, so
/// `ImageEmbedding::embed_images` can run its remaining transforms over it with
/// its own resize and crop reduced to identity.
///
/// # Size policy
///
/// The vision tower genuinely needs decoded pixels, so the header-only trick
/// `ImageMetaDeriver` uses at rung 0 is not available here. What *is* available
/// is reading the dimensions from the header first, applying
/// [`decompression_bomb_check`] to them, and only then decoding. That is what
/// Pillow does, and it is what this function does.
///
/// ## Why the allocator cap is lifted rather than raised
///
/// `image`'s `Limits::default()` caps `max_alloc` at 512 MiB. That cap has no
/// relationship to Pillow's policy: a 139 Mpx palette PNG is comfortably inside
/// Pillow's warn band but wants 558 MB as `Rgba8`, so the default cap vetoes a
/// decode the policy explicitly permits — which is the bug this guard exists to
/// fix. Raising the cap to some larger number only moves the disagreement:
/// bytes-per-pixel is not known until the decoder picks an output type, and it
/// ranges from 1 (`Luma8`) to 16 (`Rgba32F`, via OpenEXR/HDR). Any byte budget
/// therefore has to guess a bytes-per-pixel to be safe at the top of the warn
/// band, and whatever it guesses, some legal format exceeds it and gets vetoed
/// incidentally — reintroducing exactly this bug for a narrower set of files.
///
/// So the byte cap is removed and the **pixel count is the guard**: nothing can
/// reach the decoder unless [`decompression_bomb_check`] returned `Allow` or
/// `Warn`, which bounds the image at `MAX_IMAGE_PIXELS_HARD` pixels regardless
/// of what it decodes to. Band 3 is the rejecting branch and the only rejecting
/// branch, which is the whole point: the guard is the policy, stated once, and
/// not an allocator side effect that happens to fire first.
///
/// The other `Limits` fields (`max_image_width` / `max_image_height`) are
/// `None` in `Limits::default()` already, so `no_limits()` gives up nothing
/// that was previously protecting us except the byte cap.
pub fn open_for_clip(path: &Path) -> anyhow::Result<DynamicImage> {
    let mut reader = image::ImageReader::open(path)
        .map_err(|e| anyhow::anyhow!("open {}: {e}", path.display()))?;
    // Set before `into_decoder`, which is where `image` hands the limits to the
    // format decoder. Format detection is unchanged: `ImageReader::open` infers
    // it from the file extension, exactly as the previous `.decode()` did.
    reader.limits(image::Limits::no_limits());
    let decoder = reader
        .into_decoder()
        .map_err(|e| anyhow::anyhow!("decode {}: {e}", path.display()))?;

    // Header-only. `ImageDecoder::dimensions()` reads the size the format
    // header already parsed — no pixel data is inflated to answer it, which is
    // what makes rejecting a bomb cheap instead of self-defeating.
    //
    // `image_meta.rs`'s `read_header` was the other candidate for this. It is
    // deliberately not reused: it is keyed to the six formats rung 0 supports
    // and returns Pillow mode/`tRNS` facts this path has no use for, so reusing
    // it would both narrow which images CLIP can see and require exposing rung
    // 0's internals. The decoder is already open here and already knows the
    // dimensions, so asking it costs nothing extra.
    let (width, height) = decoder.dimensions();
    match decompression_bomb_check(width, height) {
        BombVerdict::Allow => {}
        BombVerdict::Warn => {
            // Pillow's `DecompressionBombWarning` text, verbatim, so a user
            // diffing the two ports' logs sees the same line for the same file.
            tracing::warn!(
                target: "chunkshop::media",
                "{}: Image size ({} pixels) exceeds limit of {MAX_IMAGE_PIXELS} pixels, \
                 could be decompression bomb DOS attack.",
                path.display(),
                u64::from(width) * u64::from(height),
            );
        }
        BombVerdict::Reject => {
            // Pillow's `DecompressionBombError` text, likewise.
            anyhow::bail!(
                "decode {}: Image size ({} pixels) exceeds limit of \
                 {MAX_IMAGE_PIXELS_HARD} pixels, could be decompression bomb DOS attack.",
                path.display(),
                u64::from(width) * u64::from(height),
            );
        }
    }

    let img = DynamicImage::from_decoder(decoder)
        .map_err(|e| anyhow::anyhow!("decode {}: {e}", path.display()))?;
    // `ConvertToRGB` first, same order as both reference pipelines.
    let rgb = convert_to_rgb_like_pil(img);
    resize_and_center_crop(rgb, CLIP_SHORTEST_EDGE)
}

/// PIL's `convert("RGB")`.
///
/// Alpha: PIL and `into_rgb8()` both DROP it rather than compositing over a
/// background, so every alpha-carrying variant is left to `into_rgb8()`.
///
/// Bit depth: 16-bit GREYSCALE is the one case where they disagree, and they
/// disagree loudly. PIL calls such an image `I;16` and converts it by CLIPPING
/// each sample to 0..=255; `into_rgb8()` rescales 0..=65535 onto 0..=255.
/// Saturating is the behaviour the CLIP vision tower was trained against — see
/// this module's "Bit depth" section for the measurement and the reasoning.
fn convert_to_rgb_like_pil(img: DynamicImage) -> DynamicImage {
    match img {
        DynamicImage::ImageLuma16(buf) => {
            let (w, h) = buf.dimensions();
            let mut out = image::RgbImage::new(w, h);
            for (dst, src) in out.pixels_mut().zip(buf.pixels()) {
                let v = src.0[0].min(u16::from(u8::MAX)) as u8;
                *dst = image::Rgb([v, v, v]);
            }
            DynamicImage::ImageRgb8(out)
        }
        other => DynamicImage::ImageRgb8(other.into_rgb8()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// PIL clips `I;16` to 0..=255; `into_rgb8()` rescales. Both 500 and
    /// 30000 are >= 255 and must come out 255, not 2 and 117.
    #[test]
    fn sixteen_bit_greyscale_saturates_like_pil_instead_of_rescaling() {
        let mut buf = image::ImageBuffer::<image::Luma<u16>, Vec<u16>>::new(2, 2);
        buf.put_pixel(0, 0, image::Luma([0]));
        buf.put_pixel(1, 0, image::Luma([200]));
        buf.put_pixel(0, 1, image::Luma([500]));
        buf.put_pixel(1, 1, image::Luma([30000]));
        let out = convert_to_rgb_like_pil(DynamicImage::ImageLuma16(buf.clone())).to_rgb8();
        assert_eq!(out.get_pixel(0, 0).0, [0, 0, 0]);
        assert_eq!(out.get_pixel(1, 0).0, [200, 200, 200]);
        assert_eq!(out.get_pixel(0, 1).0, [255, 255, 255]);
        assert_eq!(out.get_pixel(1, 1).0, [255, 255, 255]);
        // ...and the rescaling the crate would have done really is different,
        // or the assertions above would pass for either implementation.
        let rescaled = DynamicImage::ImageLuma16(buf).into_rgb8();
        assert_eq!(rescaled.get_pixel(1, 1).0, [117, 117, 117]);
    }

    /// 8-bit paths are untouched — the special case must not leak into them.
    #[test]
    fn eight_bit_images_convert_exactly_as_before() {
        let mut rgba = image::RgbaImage::new(1, 1);
        rgba.put_pixel(0, 0, image::Rgba([10, 20, 30, 0]));
        let out = convert_to_rgb_like_pil(DynamicImage::ImageRgba8(rgba)).to_rgb8();
        // Alpha DROPPED, not composited — the colour survives a zero alpha.
        assert_eq!(out.get_pixel(0, 0).0, [10, 20, 30]);

        let mut l = image::GrayImage::new(1, 1);
        l.put_pixel(0, 0, image::Luma([77]));
        let out = convert_to_rgb_like_pil(DynamicImage::ImageLuma8(l)).to_rgb8();
        assert_eq!(out.get_pixel(0, 0).0, [77, 77, 77]);
    }

    #[test]
    fn a_square_image_scales_to_exactly_the_shortest_edge() {
        assert_eq!(target_size(128, 128, 224), Some((224, 224)));
        assert_eq!(target_size(512, 512, 224), Some((224, 224)));
    }

    #[test]
    fn a_landscape_image_keeps_its_aspect_ratio() {
        // chart_revenue.png. short = height = 220, long = width = 320.
        // int(224 * 320 / 220) = int(325.8181...) = 325.
        assert_eq!(target_size(320, 220, 224), Some((325, 224)));
        // screenshot_settings.png. int(224 * 360 / 260) = int(310.153...) = 310.
        assert_eq!(target_size(360, 260, 224), Some((310, 224)));
    }

    #[test]
    fn a_portrait_image_keeps_its_aspect_ratio() {
        assert_eq!(target_size(220, 320, 224), Some((224, 325)));
    }

    #[test]
    fn the_long_edge_truncates_rather_than_rounds() {
        // THE one-pixel bug class. `ross` rounds here and lands on 326;
        // Python truncates and lands on 325, and that single pixel is worth
        // 4.06 degrees of embedding angle. 325.818... must floor, not round.
        assert_eq!(target_size(320, 220, 224).unwrap().0, 325);
        // A ratio whose product rounds UP but truncates DOWN: 224*101/100 =
        // 226.24 -> 226 either way; pick one that actually differs.
        // 224 * 199 / 100 = 445.76 -> trunc 445, round 446.
        assert_eq!(target_size(199, 100, 224).unwrap().0, 445);
    }

    #[test]
    fn an_exact_ratio_is_not_pushed_off_by_float_error() {
        // 224 * 2 / 1 must be exactly 448, not 447.
        assert_eq!(target_size(400, 200, 224), Some((448, 224)));
        assert_eq!(target_size(672, 224, 224), Some((672, 224)));
    }

    #[test]
    fn a_zero_dimension_image_is_rejected_rather_than_panicking() {
        assert_eq!(target_size(0, 100, 224), None);
        assert_eq!(target_size(100, 0, 224), None);
        let img = DynamicImage::ImageRgb8(image::RgbImage::new(0, 10));
        assert!(resize_and_center_crop(img, 224).is_err());
    }

    #[test]
    fn the_output_is_always_exactly_the_crop_size() {
        // Extreme-but-not-pathological aspect ratios included: 20x500 scales
        // to 224x5600 and must still crop to 224x224.
        for (w, h) in [
            (128u32, 128u32),
            (320, 220),
            (360, 260),
            (20, 500),
            (500, 20),
            (224, 224),
        ] {
            let img = DynamicImage::ImageRgb8(image::RgbImage::new(w, h));
            let out = resize_and_center_crop(img, 224).unwrap();
            assert_eq!(
                out.dimensions(),
                (224, 224),
                "{w}x{h} did not land on 224x224"
            );
        }
    }

    #[test]
    fn an_already_224_square_image_passes_through_byte_identical() {
        // The load-bearing claim behind doing the geometry ourselves and then
        // letting fastembed-rs's `resize_exact(224,224)` run anyway: at
        // matching dimensions `image` copies instead of resampling, so the
        // double pass is exact identity, not "nearly identity".
        let mut src = image::RgbImage::new(224, 224);
        for (x, y, p) in src.enumerate_pixels_mut() {
            *p = image::Rgb([(x % 256) as u8, (y % 256) as u8, ((x + y) % 256) as u8]);
        }
        let original = DynamicImage::ImageRgb8(src);
        let once = resize_and_center_crop(original.clone(), 224).unwrap();
        assert_eq!(once.to_rgb8().into_raw(), original.to_rgb8().into_raw());
        // ...and again, standing in for fastembed-rs's own resize step.
        let twice = once.resize_exact(224, 224, CLIP_RESAMPLE);
        assert_eq!(twice.to_rgb8().into_raw(), original.to_rgb8().into_raw());
    }

    // -----------------------------------------------------------------------
    // Pillow decompression-bomb parity
    // -----------------------------------------------------------------------

    /// Repo-root `tests/fixtures/images/`, same fixtures rung 0 uses.
    fn fixture(name: &str) -> std::path::PathBuf {
        std::path::PathBuf::from(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../tests/fixtures/images"
        ))
        .join(name)
    }

    /// The constants are a parity contract with `PIL.Image.MAX_IMAGE_PIXELS`.
    /// Pinned literally so a typo cannot silently widen or narrow the bands.
    #[test]
    fn the_constants_match_pil() {
        assert_eq!(MAX_IMAGE_PIXELS, 89_478_485);
        assert_eq!(MAX_IMAGE_PIXELS_HARD, 178_956_970);
        // Pillow derives it as `int(1024 * 1024 * 1024 // 4 // 3)`.
        assert_eq!(MAX_IMAGE_PIXELS, 1024 * 1024 * 1024 / 4 / 3);
    }

    /// The three bands, at and around both boundaries.
    ///
    /// Cross-checked against the installed Pillow — see
    /// `tests/fixtures/images/README.md` for the table and the command that
    /// produced it. Both of Pillow's comparisons are strict `>`, so landing
    /// exactly on a constant takes the gentler branch; those two rows are the
    /// off-by-one this test exists to pin.
    #[test]
    fn the_bands_match_pils_decompression_bomb_check() {
        // Dimensions chosen so `w * h` hits the interesting counts exactly.
        // 89_478_485 = 5 * 17_895_697 (17_895_697 is prime).
        let cases: &[(u32, u32, BombVerdict)] = &[
            (1, 1, BombVerdict::Allow),
            (9_458, 9_458, BombVerdict::Allow), // 89_453_764, just under
            (5, 17_895_697, BombVerdict::Allow), // exactly MAX — strict `>`
            (10, 8_947_849, BombVerdict::Warn), // MAX + 5
            (14_432, 9_664, BombVerdict::Warn), // png_big_palette.png
            (10, 17_895_697, BombVerdict::Warn), // exactly 2*MAX — strict `>`
            (20, 8_947_849, BombVerdict::Reject), // 2*MAX + 10
            (100_000, 100_000, BombVerdict::Reject),
            // u32*u32 would wrap here; u64 must not let it back into Allow.
            (65_536, 65_536, BombVerdict::Reject),
            (u32::MAX, u32::MAX, BombVerdict::Reject),
        ];
        for &(w, h, want) in cases {
            assert_eq!(
                decompression_bomb_check(w, h),
                want,
                "{w}x{h} ({} px)",
                u64::from(w) * u64::from(h)
            );
        }
    }

    /// Pillow's `max(1, ...)` per axis. A zero dimension counts as 1, so a
    /// degenerate image is never routed through the bomb branches — it is
    /// `resize_and_center_crop` that rejects it, in both ports.
    #[test]
    fn a_zero_dimension_is_clamped_to_one_like_pil() {
        assert_eq!(decompression_bomb_check(0, 0), BombVerdict::Allow);
        assert_eq!(decompression_bomb_check(0, 100_000_000), BombVerdict::Warn);
    }

    /// THE regression. 14432x9664 palette PNG with `tRNS`, 136 KB on disk,
    /// 139,470,848 pixels — inside Pillow's warn band, and 558 MB as `Rgba8`,
    /// which is over `image`'s 512 MiB `Limits::default()` `max_alloc`.
    /// Before the fix this returned `decode ...: Memory limit exceeded` and,
    /// because a parser error aborts the cell, took every other document in
    /// the run down with it. Python ingests the same file with only a
    /// `DecompressionBombWarning`.
    ///
    /// End-to-end on purpose: the arithmetic is unit-tested above, but the
    /// bug was in the plumbing — the limits handed to the decoder — and only
    /// a real decode exercises that.
    #[test]
    #[cfg_attr(miri, ignore)]
    fn a_139_megapixel_png_inside_pils_warn_band_decodes() {
        let path = fixture("png_big_palette.png");
        assert_eq!(
            decompression_bomb_check(14_432, 9_664),
            BombVerdict::Warn,
            "fixture must sit in the warn band or it is not testing the bug"
        );
        let out = open_for_clip(&path).expect("139 Mpx PNG must decode, as it does in Python");
        assert_eq!(out.dimensions(), (224, 224));
    }

    /// An ordinary image is untouched by the guard — the fix must not have
    /// bought the big file's decode at the cost of the common path.
    #[test]
    #[cfg_attr(miri, ignore)]
    fn an_ordinary_image_still_decodes_to_224_square() {
        let out = open_for_clip(&fixture("png_rgba.png")).unwrap();
        assert_eq!(out.dimensions(), (224, 224));
    }

    /// Band 3 must still reject, and it must be the *policy* rejecting rather
    /// than an allocator limit — `no_limits()` removed the only other thing
    /// that could have. Tested through the policy function with dimensions as
    /// inputs: materializing a genuinely >178 Mpx file to prove arithmetic
    /// would cost hundreds of MB for no extra coverage.
    #[test]
    fn band_three_is_rejected_and_is_the_only_rejecting_branch() {
        assert_eq!(
            decompression_bomb_check(20_000, 20_000),
            BombVerdict::Reject
        );
        // Nothing below the hard ceiling rejects, however many bytes it would
        // decode to. 178_956_970 px is 2.9 GB as `Rgba32F` — far past any byte
        // cap — and must still be allowed through, because the pixel count is
        // the guard.
        assert_ne!(
            decompression_bomb_check(10, 17_895_697),
            BombVerdict::Reject
        );
    }

    #[test]
    fn the_center_crop_takes_the_middle_not_the_corner() {
        // 448x224 -> already 224 on the short edge, crop x offset 112.
        let mut src = image::RgbImage::new(448, 224);
        for (x, _y, p) in src.enumerate_pixels_mut() {
            // Column-encoded so the crop window is identifiable.
            *p = image::Rgb([(x / 2) as u8, 0, 0]);
        }
        let out = resize_and_center_crop(DynamicImage::ImageRgb8(src), 224).unwrap();
        let rgb = out.to_rgb8();
        // First kept column is source column 112 -> value 56.
        assert_eq!(rgb.get_pixel(0, 0)[0], 56);
    }
}
