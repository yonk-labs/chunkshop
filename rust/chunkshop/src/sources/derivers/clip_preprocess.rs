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
//! **Do not change the filter.** The ~6.8e-4 residual that survives this fix is
//! sub-LSB rounding inside PIL's fixed-point 8-bit resampler versus `image`'s
//! f32 one, and it is the floor of what is achievable without reimplementing
//! Pillow's integer kernel.
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

use image::{imageops::FilterType, DynamicImage, GenericImageView};
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
pub fn open_for_clip(path: &Path) -> anyhow::Result<DynamicImage> {
    let img = image::ImageReader::open(path)
        .map_err(|e| anyhow::anyhow!("open {}: {e}", path.display()))?
        .decode()
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
