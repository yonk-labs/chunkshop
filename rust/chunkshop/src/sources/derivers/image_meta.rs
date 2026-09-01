//! Rung 0 for images: deterministic metadata, no model, no network. Mirrors
//! `python/src/chunkshop/sources/derivers/image_meta.py`.
//!
//! Rung 0 is deterministic, so unlike the CLIP rungs, Python and Rust are
//! expected to agree on VALUES here, not merely on metadata keys — see
//! `tests/media_derivation_parity.rs`'s doc comment for the schema-vs-value
//! split.
//!
//! # Why this reads HEADERS and never decodes
//!
//! Rung 0 needs exactly five facts: `width`, `height`, `format`, `mode`,
//! `has_alpha`. **Every one of them lives in the file header.** Pillow — the
//! reference implementation — never decodes to answer them: `Image.open` is
//! lazy, and `.size` / `.mode` / `.format` are read straight out of the
//! container's own header. This module does the same, and that single
//! property is what fixes three otherwise-unrelated measured defects at once:
//!
//! 1. **Large images stopped being ingestable at all.** The previous version
//!    called `image::open`, which fully decodes the raster. `image`
//!    0.25.10's `Limits::default()` caps `max_alloc` at 512 MiB
//!    (`src/io/limits.rs`), so a 14432x9664 palette PNG — **640 KB on disk**,
//!    139 Mpx decoded, 558 MB as RGBA — failed with "Memory limit exceeded"
//!    and, because a parser error propagates, aborted the whole cell with
//!    zero rows written. Python ingested the identical file in seconds.
//!    Palette PNGs compress ~200:1, so FILE SIZE DOES NOT PREDICT DECODE
//!    COST; that is why no synthetic fixture caught it.
//! 2. **Formats `image` cannot fully decode became readable.** Palette BMP
//!    ("failed to fill whole buffer"), palette TIFF ("Photometric
//!    interpretation RGBPalette ... unsupported") and 8-bit greyscale+alpha
//!    TIFF ("color type Unknown(16)") all carry perfectly well-formed
//!    headers. Reading the header answers rung 0 without ever asking the
//!    decoder to produce pixels it cannot produce.
//! 3. **`mode` stopped being a post-decode artifact.** `image` expands
//!    palettes, drops bit-depth distinctions and normalises colour models at
//!    decode time, so its `ColorType` cannot express PIL's `1`, `P`, `LA`,
//!    `CMYK` or `I;16` at all. Measured, the post-decode mapping was wrong
//!    on 5 of 13 BMP/TIFF fixtures and errored on 3 more.
//!
//! It is also dramatically faster: constant work per file regardless of pixel
//! count.
//!
//! `ClipTagsDeriver` genuinely needs a decoded image for the vision tower —
//! that path is unaffected and still decodes (see `clip_preprocess.rs`).
//!
//! # Where the mode strings come from
//!
//! Pillow's mode is a per-format function of header fields, and the mapping
//! is transcribed from Pillow's own source rather than inferred:
//!
//! | format | Pillow source | fields read here |
//! |---|---|---|
//! | PNG | `PngImagePlugin._MODES` | IHDR bit depth + colour type, `tRNS` presence |
//! | GIF | `GifImagePlugin` (always `P`) | logical screen size, Graphic Control Extension transparent index |
//! | JPEG | `JpegImagePlugin.SOF` | SOFn frame header: size + component count (1/3/4 -> L/RGB/CMYK) |
//! | WebP | `WebPImagePlugin` (`RGB`/`RGBA`) | RIFF/VP8X alpha flag, via `image`'s header-only `WebPDecoder` |
//! | BMP | `BmpImagePlugin.BIT2MODE` + its palette-greyscale test | DIB header bit count/compression/colour count + the palette bytes |
//! | TIFF | `TiffImagePlugin.OPEN_INFO` | IFD0 tags 256/257/258/262/277/338/339 |
//!
//! The fixtures under `tests/fixtures/images/` and the PIL-computed values in
//! their `expected.json` are what hold this honest — see that directory's
//! `generate.py`.

use std::fs::File;
use std::io::{BufReader, Read, Seek, SeekFrom};
use std::path::Path;

use image::{ImageDecoder, ImageFormat};
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
        let fmt = ImageFormat::from_path(path).unwrap_or(ImageFormat::Png);
        let fmt_name = pillow_style_format_name(fmt)?;
        // Same message shape as Python's `ParserError(f"failed to parse image
        // {path}: {exc}")`, so a user comparing the two ports' logs sees the
        // same line for the same bad file.
        let header = read_header(path, fmt)
            .map_err(|e| anyhow::anyhow!("failed to parse image {}: {e:#}", path.display()))?;

        let mut text = self.path_text(path);
        if text.trim().is_empty() {
            text = format!("{} image", fmt_name.to_lowercase());
        }

        let mut metadata = Map::new();
        metadata.insert("width".to_string(), json!(header.width));
        metadata.insert("height".to_string(), json!(header.height));
        metadata.insert(
            "image_format".to_string(),
            Value::String(fmt_name.to_string()),
        );
        metadata.insert("image_mode".to_string(), Value::String(header.mode.clone()));
        metadata.insert(
            "aspect_ratio".to_string(),
            json!(if header.height > 0 {
                round4(header.width as f64 / header.height as f64)
            } else {
                0.0
            }),
        );
        metadata.insert("has_alpha".to_string(), Value::Bool(header.has_alpha()));

        let result = ParseResult::with_metadata(text, metadata);
        let params =
            json!({"path_segments": self.path_segments, "segment_depth": self.segment_depth});
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

/// Python's `round(x, 4)`.
///
/// Python rounds half to **even** (banker's rounding) on the exact binary
/// value; the obvious `(x * 10000.0).round() / 10000.0` rounds half **away
/// from zero** and diverges on perfectly ordinary image sizes — measured:
/// 800x1024 and 1000x1280 both give 0.78125, which Python renders 0.7812 and
/// away-from-zero renders 0.7813; 720x512 gives 1.40625 -> 1.4062 vs 1.4063.
/// An 800x1024 screenshot is not an edge case.
///
/// Formatting is the shortest correct route: Rust's `{:.4}` is a
/// correctly-rounded, ties-to-even conversion of the exact binary value —
/// the same rule CPython's `round(float, ndigits)` applies. Doing the
/// arithmetic in f64 instead would reintroduce a scaling error of its own.
fn round4(x: f64) -> f64 {
    format!("{x:.4}").parse::<f64>().unwrap_or(x)
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
                // SLICE FIRST, THEN FILTER — the same order as Python's
                // `[p for p in path.parent.parts[-depth:] if p not in ("/", "")]`.
                // `Path::components()` yields a `RootDir` component whose
                // `as_os_str()` is "/", and taking the last `segment_depth`
                // components of a shallow path keeps it: `/corpus/x.png`
                // derived "x / corpus" in Rust against Python's "x corpus".
                // Filtering before the slice would be a different bug — it
                // would let a deeper segment take the root's place and change
                // the text for paths where the two currently agree. This is
                // cache-key material as well as output text, so a divergence
                // here means the two ports key the same file differently.
                for s in segs[start..].iter().filter(|s| **s != "/" && !s.is_empty()) {
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

// ---------------------------------------------------------------------------
// Header readers
// ---------------------------------------------------------------------------

/// Everything rung 0 needs, read from the container header.
///
/// `mode` is Pillow's `img.mode` string; `transparency` is whether Pillow
/// would put a `"transparency"` key in `img.info` (PNG `tRNS`, GIF Graphic
/// Control Extension). Together they reproduce Python's
/// `mode in ("RGBA", "LA", "PA") or "transparency" in img.info`.
#[derive(Debug, Clone, PartialEq, Eq)]
struct ImageHeader {
    width: u32,
    height: u32,
    mode: String,
    transparency: bool,
}

impl ImageHeader {
    fn has_alpha(&self) -> bool {
        matches!(self.mode.as_str(), "RGBA" | "LA" | "PA") || self.transparency
    }
}

fn read_header(path: &Path, fmt: ImageFormat) -> anyhow::Result<ImageHeader> {
    match fmt {
        ImageFormat::Png => png_header(path),
        ImageFormat::Gif => gif_header(path),
        ImageFormat::Jpeg => jpeg_header(path),
        ImageFormat::WebP => webp_header(path),
        ImageFormat::Bmp => bmp_header(path),
        ImageFormat::Tiff => tiff_header(path),
        // Unreachable: `pillow_style_format_name` rejects everything else
        // before this is called. Kept as an error rather than `unreachable!`
        // so a future format added there without a reader here fails loudly.
        other => Err(anyhow::anyhow!("no header reader for {other:?}")),
    }
}

fn open_buffered(path: &Path) -> std::io::Result<BufReader<File>> {
    Ok(BufReader::new(File::open(path)?))
}

/// PNG: IHDR bit depth + colour type, plus `tRNS` presence.
///
/// `png::Decoder::read_info` parses the header chunks and stops at the first
/// `IDAT` — it never inflates pixel data, so this is O(header), not O(pixels).
/// Mode mapping is `PngImagePlugin._MODES` keyed on `(bit_depth, color_type)`;
/// note two entries that look wrong but are Pillow's actual behaviour:
/// `(16, Grayscale)` is `I;16` (not `L`) and `(16, GrayscaleAlpha)` is `RGBA`
/// (not `LA` — Pillow's own comment says "LA;16B->LA not yet available").
fn png_header(path: &Path) -> anyhow::Result<ImageHeader> {
    let reader = png::Decoder::new(open_buffered(path)?).read_info()?;
    let info = reader.info();
    let depth = info.bit_depth as u8;
    let mode = match (depth, info.color_type) {
        (1, png::ColorType::Grayscale) => "1",
        (2 | 4 | 8, png::ColorType::Grayscale) => "L",
        (16, png::ColorType::Grayscale) => "I;16",
        (8 | 16, png::ColorType::Rgb) => "RGB",
        (1 | 2 | 4 | 8, png::ColorType::Indexed) => "P",
        (8, png::ColorType::GrayscaleAlpha) => "LA",
        (16, png::ColorType::GrayscaleAlpha) => "RGBA",
        (8 | 16, png::ColorType::Rgba) => "RGBA",
        (d, c) => anyhow::bail!("unsupported PNG bit depth / colour type ({d}, {c:?})"),
    };
    Ok(ImageHeader {
        width: info.width,
        height: info.height,
        mode: mode.to_string(),
        transparency: info.trns.is_some(),
    })
}

/// GIF: logical screen size, and whether frame 1's Graphic Control Extension
/// declares a transparent index. Pillow always opens a GIF as mode `P`.
///
/// `next_frame_info()` reads the frame's control + image descriptors WITHOUT
/// LZW-decoding its pixels (`read_next_frame()` would decode them), so a
/// large GIF costs the same as a small one.
fn gif_header(path: &Path) -> anyhow::Result<ImageHeader> {
    let mut options = gif::DecodeOptions::new();
    options.set_color_output(gif::ColorOutput::Indexed);
    let mut reader = options.read_info(open_buffered(path)?)?;
    let (width, height) = (u32::from(reader.width()), u32::from(reader.height()));
    let transparency = reader
        .next_frame_info()?
        .map(|f| f.transparent.is_some())
        .unwrap_or(false);
    Ok(ImageHeader {
        width,
        height,
        mode: "P".to_string(),
        transparency,
    })
}

/// JPEG: the SOFn frame header. `JpegImagePlugin.SOF` reads precision at
/// byte 0, height at 1..3, width at 3..5 (both big-endian) and the component
/// count at byte 5, then maps 1/3/4 components to `L`/`RGB`/`CMYK`. JPEG
/// never carries alpha.
fn jpeg_header(path: &Path) -> anyhow::Result<ImageHeader> {
    let mut f = open_buffered(path)?;
    let mut two = [0u8; 2];
    f.read_exact(&mut two)?;
    if two != [0xFF, 0xD8] {
        anyhow::bail!("not a JPEG: missing SOI marker");
    }
    let mut b = [0u8; 1];
    loop {
        f.read_exact(&mut b)?;
        if b[0] != 0xFF {
            continue; // fill byte or stray entropy-coded data
        }
        // Any run of 0xFF is padding before the marker byte itself.
        let mut marker = 0xFFu8;
        while marker == 0xFF {
            f.read_exact(&mut b)?;
            marker = b[0];
        }
        match marker {
            // End of image before any frame header.
            0xD9 => anyhow::bail!("JPEG ended before a start-of-frame marker"),
            // Standalone markers: no length, no payload.
            0x00 | 0x01 | 0xD0..=0xD8 => continue,
            // SOF0..SOF15 except DHT (C4), JPG (C8) and DAC (CC).
            0xC0..=0xCF if !matches!(marker, 0xC4 | 0xC8 | 0xCC) => {
                f.read_exact(&mut two)?;
                let n = u16::from_be_bytes(two) as usize;
                if n < 8 {
                    anyhow::bail!("truncated JPEG start-of-frame segment");
                }
                let mut s = vec![0u8; n - 2];
                f.read_exact(&mut s)?;
                let height = u32::from(u16::from_be_bytes([s[1], s[2]]));
                let width = u32::from(u16::from_be_bytes([s[3], s[4]]));
                let mode = match s[5] {
                    1 => "L",
                    3 => "RGB",
                    4 => "CMYK",
                    n => anyhow::bail!("cannot handle {n}-layer JPEG"),
                };
                return Ok(ImageHeader {
                    width,
                    height,
                    mode: mode.to_string(),
                    transparency: false,
                });
            }
            _ => {
                f.read_exact(&mut two)?;
                let n = u16::from_be_bytes(two);
                if n < 2 {
                    anyhow::bail!("bad JPEG segment length {n}");
                }
                f.seek(SeekFrom::Current(i64::from(n) - 2))?;
            }
        }
    }
}

/// WebP: `image`'s `WebPDecoder::new` parses only the RIFF container and the
/// VP8/VP8L/VP8X headers — no pixel decode — and its `color_type()` is
/// `Rgba8` exactly when the file declares alpha. Pillow reports `RGB` or
/// `RGBA` and nothing else for WebP (`WebPImagePlugin`: rawmode `RGBX` maps
/// to mode `RGB`).
fn webp_header(path: &Path) -> anyhow::Result<ImageHeader> {
    let decoder = image::codecs::webp::WebPDecoder::new(open_buffered(path)?)?;
    let (width, height) = decoder.dimensions();
    let mode = if decoder.color_type().has_alpha() {
        "RGBA"
    } else {
        "RGB"
    };
    Ok(ImageHeader {
        width,
        height,
        mode: mode.to_string(),
        transparency: false,
    })
}

fn u16_at(b: &[u8], o: usize, be: bool) -> anyhow::Result<u16> {
    let s: [u8; 2] = b
        .get(o..o + 2)
        .ok_or_else(|| anyhow::anyhow!("truncated header at byte {o}"))?
        .try_into()
        .unwrap();
    Ok(if be {
        u16::from_be_bytes(s)
    } else {
        u16::from_le_bytes(s)
    })
}

fn u32_at(b: &[u8], o: usize, be: bool) -> anyhow::Result<u32> {
    let s: [u8; 4] = b
        .get(o..o + 4)
        .ok_or_else(|| anyhow::anyhow!("truncated header at byte {o}"))?
        .try_into()
        .unwrap();
    Ok(if be {
        u32::from_be_bytes(s)
    } else {
        u32::from_le_bytes(s)
    })
}

/// BMP, following `BmpImagePlugin._bitmap` field for field.
///
/// The two rules that matter and that a post-decode `ColorType` cannot
/// express:
///
/// - `BIT2MODE` maps 1/4/8 bits to `P` and 16/24/32 to `RGB` — a 32-bit
///   BI_RGB BMP is `RGB`, not `RGBA`, because the fourth byte is reserved,
///   not alpha. Only BI_BITFIELDS with a non-zero alpha mask makes it `RGBA`.
/// - A paletted BMP whose palette is the identity greyscale ramp is reported
///   as `L` (or `1` for a 2-colour palette), NOT `P` — Pillow "ditches the
///   palette" in that case. This is why `image`, which knows only that the
///   file is indexed, cannot get greyscale BMPs right.
fn bmp_header(path: &Path) -> anyhow::Result<ImageHeader> {
    let mut f = open_buffered(path)?;
    let mut file_hdr = [0u8; 14];
    f.read_exact(&mut file_hdr)?;
    if &file_hdr[0..2] != b"BM" {
        anyhow::bail!("not a BMP: missing 'BM' signature");
    }
    let mut size_bytes = [0u8; 4];
    f.read_exact(&mut size_bytes)?;
    let header_size = u32::from_le_bytes(size_bytes) as usize;
    if !(12..=1024).contains(&header_size) {
        anyhow::bail!("unsupported BMP header type ({header_size})");
    }
    // Pillow's `header_data`: the DIB header minus its own size field.
    let mut hd = vec![0u8; header_size - 4];
    f.read_exact(&mut hd)?;

    let (width, height, bits, compression, mut colors, palette_padding) = if header_size == 12 {
        // BITMAPCOREHEADER.
        (
            u32::from(u16_at(&hd, 0, false)?),
            u32::from(u16_at(&hd, 2, false)?),
            u16_at(&hd, 6, false)?,
            0u32,
            0u32,
            3usize,
        )
    } else if matches!(header_size, 40 | 52 | 56 | 64 | 108 | 124) {
        let y_flip = hd[7] == 0xFF;
        let raw_h = u32_at(&hd, 4, false)?;
        (
            u32_at(&hd, 0, false)?,
            if y_flip {
                // Pillow: `2**32 - i32(header_data, 4)` on the UNSIGNED read.
                (1u64 << 32).wrapping_sub(u64::from(raw_h)) as u32
            } else {
                raw_h
            },
            u16_at(&hd, 10, false)?,
            u32_at(&hd, 12, false)?,
            u32_at(&hd, 28, false)?,
            4usize,
        )
    } else {
        anyhow::bail!("unsupported BMP header type ({header_size})");
    };

    if colors == 0 {
        colors = 1u32 << bits.min(31);
    }

    let mut mode = match bits {
        1 | 4 | 8 => "P",
        16 | 24 | 32 => "RGB",
        b => anyhow::bail!("unsupported BMP pixel depth ({b})"),
    };

    const BITFIELDS: u32 = 3;
    const RAW: u32 = 0;
    const RLE8: u32 = 1;
    const RLE4: u32 = 2;
    match compression {
        BITFIELDS => {
            // Masks live in the header from BITMAPV2HEADER (52) on; a bare
            // 40-byte header stores them in the three words that follow it,
            // and has no alpha mask at all.
            let (r, g, b, a) = if hd.len() >= 48 {
                (
                    u32_at(&hd, 36, false)?,
                    u32_at(&hd, 40, false)?,
                    u32_at(&hd, 44, false)?,
                    if hd.len() >= 52 {
                        u32_at(&hd, 48, false)?
                    } else {
                        0
                    },
                )
            } else {
                let mut m = [0u8; 12];
                f.read_exact(&mut m)?;
                (
                    u32_at(&m, 0, false)?,
                    u32_at(&m, 4, false)?,
                    u32_at(&m, 8, false)?,
                    0,
                )
            };
            // Pillow's SUPPORTED/MASK_MODES tables, reduced to the only
            // question that changes `mode`: does the layout carry alpha?
            const ALPHA_32: [(u32, u32, u32, u32); 4] = [
                (0xFF00_0000, 0x00FF_0000, 0x0000_FF00, 0x0000_00FF),
                (0x0000_00FF, 0x0000_FF00, 0x00FF_0000, 0xFF00_0000),
                (0x00FF_0000, 0x0000_FF00, 0x0000_00FF, 0xFF00_0000),
                (0xFF00_0000, 0x0000_FF00, 0x0000_00FF, 0x00FF_0000),
            ];
            const OPAQUE_32: [(u32, u32, u32, u32); 4] = [
                (0x00FF_0000, 0x0000_FF00, 0x0000_00FF, 0),
                (0xFF00_0000, 0x00FF_0000, 0x0000_FF00, 0),
                (0xFF00_0000, 0x0000_FF00, 0x0000_00FF, 0),
                (0, 0, 0, 0), // Pillow maps the all-zero mask to "BGRA"...
            ];
            const OPAQUE_24: [(u32, u32, u32); 1] = [(0x00FF_0000, 0x0000_FF00, 0x0000_00FF)];
            const OPAQUE_16: [(u32, u32, u32); 2] =
                [(0xF800, 0x07E0, 0x001F), (0x7C00, 0x03E0, 0x001F)];
            match bits {
                32 if ALPHA_32.contains(&(r, g, b, a)) => mode = "RGBA",
                // ...but "BGRA" contains an "A", so Pillow calls the all-zero
                // mask RGBA too. Keeping that quirk rather than "fixing" it is
                // the whole point of transcribing instead of reasoning.
                32 if (r, g, b, a) == (0, 0, 0, 0) => mode = "RGBA",
                32 if OPAQUE_32.contains(&(r, g, b, a)) => {}
                24 if OPAQUE_24.contains(&(r, g, b)) => {}
                16 if OPAQUE_16.contains(&(r, g, b)) => {}
                _ => anyhow::bail!("unsupported BMP bitfields layout"),
            }
        }
        // 32-bit BI_RGB is RGB, not RGBA: Pillow only flips it to RGBA for
        // a .cur (`header == 22`) or when the USE_RAW_ALPHA global is set.
        RAW => {}
        RLE8 | RLE4 => {}
        c => anyhow::bail!("unsupported BMP compression ({c})"),
    }

    if mode == "P" {
        if colors == 0 || colors > 65536 {
            anyhow::bail!("unsupported BMP palette size ({colors})");
        }
        // Pillow reads the palette straight after the header (and after the
        // bitfield masks, when those came from the stream).
        let mut palette = vec![0u8; palette_padding * colors as usize];
        f.read_exact(&mut palette)?;
        // "Is every entry `i` the colour (i, i, i)?" — with the 2-colour case
        // checked against (0, 255) rather than (0, 1).
        let grayscale = (0..colors as usize).all(|i| {
            let want = if colors == 2 {
                if i == 0 {
                    0u8
                } else {
                    255u8
                }
            } else {
                i as u8
            };
            let e = &palette[i * palette_padding..i * palette_padding + 3];
            e == [want, want, want]
        });
        if grayscale {
            mode = if colors == 2 { "1" } else { "L" };
        }
    }

    Ok(ImageHeader {
        width,
        height,
        mode: mode.to_string(),
        transparency: false,
    })
}

/// One row of `TiffImagePlugin.OPEN_INFO`, reduced to the fields that decide
/// the MODE. `big_endian: None` means both byte orders map to this mode.
struct TiffModeRow {
    photo: u16,
    sample_format: u16,
    bits: &'static [u16],
    extra: &'static [u16],
    big_endian: Option<bool>,
    mode: &'static str,
}

// Generated from `PIL.TiffImagePlugin.OPEN_INFO` (Pillow 12.3.0) by
// de-duplicating its 180 keys on the fields that actually select a mode.
// FillOrder is deliberately dropped: it appears in every OPEN_INFO key but
// never changes the mode column, so keying on it here would only make this
// table reject files Pillow accepts.
//
// One row per line, deliberately: this is a transcribed lookup table and
// it is read by comparing it against Pillow's, which is also one row per line.
#[rustfmt::skip]
static TIFF_MODES: &[TiffModeRow] = &[
    TiffModeRow { photo: 0, sample_format: 1, bits: &[1], extra: &[], big_endian: None, mode: "1" },
    TiffModeRow { photo: 0, sample_format: 1, bits: &[2], extra: &[], big_endian: None, mode: "L" },
    TiffModeRow { photo: 0, sample_format: 1, bits: &[4], extra: &[], big_endian: None, mode: "L" },
    TiffModeRow { photo: 0, sample_format: 1, bits: &[8], extra: &[], big_endian: None, mode: "L" },
    TiffModeRow { photo: 0, sample_format: 1, bits: &[16], extra: &[], big_endian: None, mode: "I;16" },
    TiffModeRow { photo: 0, sample_format: 3, bits: &[32], extra: &[], big_endian: None, mode: "F" },
    TiffModeRow { photo: 1, sample_format: 1, bits: &[1], extra: &[], big_endian: None, mode: "1" },
    TiffModeRow { photo: 1, sample_format: 1, bits: &[2], extra: &[], big_endian: None, mode: "L" },
    TiffModeRow { photo: 1, sample_format: 1, bits: &[4], extra: &[], big_endian: None, mode: "L" },
    TiffModeRow { photo: 1, sample_format: 1, bits: &[8], extra: &[], big_endian: None, mode: "L" },
    TiffModeRow { photo: 1, sample_format: 2, bits: &[8], extra: &[], big_endian: None, mode: "L" },
    TiffModeRow { photo: 1, sample_format: 1, bits: &[12], extra: &[], big_endian: None, mode: "I;16" },
    TiffModeRow { photo: 1, sample_format: 1, bits: &[16], extra: &[], big_endian: Some(false), mode: "I;16" },
    TiffModeRow { photo: 1, sample_format: 1, bits: &[16], extra: &[], big_endian: Some(true), mode: "I;16B" },
    TiffModeRow { photo: 1, sample_format: 2, bits: &[16], extra: &[], big_endian: None, mode: "I" },
    TiffModeRow { photo: 1, sample_format: 1, bits: &[32], extra: &[], big_endian: None, mode: "I" },
    TiffModeRow { photo: 1, sample_format: 2, bits: &[32], extra: &[], big_endian: None, mode: "I" },
    TiffModeRow { photo: 1, sample_format: 3, bits: &[32], extra: &[], big_endian: None, mode: "F" },
    TiffModeRow { photo: 1, sample_format: 1, bits: &[8, 8], extra: &[2], big_endian: None, mode: "LA" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8], extra: &[], big_endian: None, mode: "RGB" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[16, 16, 16], extra: &[], big_endian: None, mode: "RGB" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8, 8], extra: &[], big_endian: None, mode: "RGBA" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8, 8], extra: &[0], big_endian: None, mode: "RGB" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8, 8], extra: &[1], big_endian: None, mode: "RGBA" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8, 8], extra: &[2], big_endian: None, mode: "RGBA" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8, 8], extra: &[999], big_endian: None, mode: "RGBA" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[16, 16, 16, 16], extra: &[], big_endian: None, mode: "RGBA" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[16, 16, 16, 16], extra: &[0], big_endian: None, mode: "RGB" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[16, 16, 16, 16], extra: &[1], big_endian: None, mode: "RGBA" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[16, 16, 16, 16], extra: &[2], big_endian: None, mode: "RGBA" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8, 8, 8], extra: &[0, 0], big_endian: None, mode: "RGB" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8, 8, 8], extra: &[1, 0], big_endian: None, mode: "RGBA" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8, 8, 8], extra: &[2, 0], big_endian: None, mode: "RGBA" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8, 8, 8, 8], extra: &[0, 0, 0], big_endian: None, mode: "RGB" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8, 8, 8, 8], extra: &[1, 0, 0], big_endian: None, mode: "RGBA" },
    TiffModeRow { photo: 2, sample_format: 1, bits: &[8, 8, 8, 8, 8, 8], extra: &[2, 0, 0], big_endian: None, mode: "RGBA" },
    TiffModeRow { photo: 3, sample_format: 1, bits: &[1], extra: &[], big_endian: None, mode: "P" },
    TiffModeRow { photo: 3, sample_format: 1, bits: &[2], extra: &[], big_endian: None, mode: "P" },
    TiffModeRow { photo: 3, sample_format: 1, bits: &[4], extra: &[], big_endian: None, mode: "P" },
    TiffModeRow { photo: 3, sample_format: 1, bits: &[8], extra: &[], big_endian: None, mode: "P" },
    TiffModeRow { photo: 3, sample_format: 1, bits: &[8, 8], extra: &[0], big_endian: None, mode: "P" },
    TiffModeRow { photo: 3, sample_format: 1, bits: &[8, 8], extra: &[2], big_endian: None, mode: "PA" },
    TiffModeRow { photo: 5, sample_format: 1, bits: &[8, 8, 8, 8], extra: &[], big_endian: None, mode: "CMYK" },
    TiffModeRow { photo: 5, sample_format: 1, bits: &[16, 16, 16, 16], extra: &[], big_endian: None, mode: "CMYK" },
    TiffModeRow { photo: 5, sample_format: 1, bits: &[8, 8, 8, 8, 8], extra: &[0], big_endian: None, mode: "CMYK" },
    TiffModeRow { photo: 5, sample_format: 1, bits: &[8, 8, 8, 8, 8, 8], extra: &[0, 0], big_endian: None, mode: "CMYK" },
    TiffModeRow { photo: 6, sample_format: 1, bits: &[8], extra: &[], big_endian: None, mode: "L" },
    TiffModeRow { photo: 6, sample_format: 1, bits: &[8, 8, 8], extra: &[], big_endian: None, mode: "RGB" },
    TiffModeRow { photo: 8, sample_format: 1, bits: &[8, 8, 8], extra: &[], big_endian: None, mode: "LAB" },
];

/// TIFF: IFD0 tags, mapped through the table above.
///
/// Pillow's normalisation is reproduced first (`TiffImagePlugin._setup`):
/// `SampleFormat` collapses to one element when every band agrees;
/// `BitsPerSample` is truncated to, or repeated up to, `SamplesPerPixel`.
/// A combination Pillow has no entry for is an error here too — Pillow
/// raises `SyntaxError("unknown pixel mode")` and the file fails to open.
fn tiff_header(path: &Path) -> anyhow::Result<ImageHeader> {
    let mut f = open_buffered(path)?;
    let mut hdr = [0u8; 8];
    f.read_exact(&mut hdr)?;
    let be = match &hdr[0..2] {
        b"II" => false,
        b"MM" => true,
        other => anyhow::bail!("not a TIFF: byte-order marker {other:?}"),
    };
    let magic = u16_at(&hdr, 2, be)?;
    if magic != 42 {
        anyhow::bail!("unsupported TIFF variant (magic {magic}; BigTIFF is not supported)");
    }
    let ifd_offset = u64::from(u32_at(&hdr, 4, be)?);

    f.seek(SeekFrom::Start(ifd_offset))?;
    let mut count_bytes = [0u8; 2];
    f.read_exact(&mut count_bytes)?;
    let n_entries = u16_at(&count_bytes, 0, be)? as usize;
    let mut entries = vec![0u8; n_entries * 12];
    f.read_exact(&mut entries)?;

    let get = |f: &mut BufReader<File>, tag: u16| -> anyhow::Result<Option<Vec<u32>>> {
        tiff_tag_values(f, &entries, be, tag)
    };

    let photo = get(&mut f, 262)?
        .and_then(|v| v.first().copied())
        .unwrap_or(0) as u16;
    let width = get(&mut f, 256)?
        .and_then(|v| v.first().copied())
        .ok_or_else(|| anyhow::anyhow!("TIFF IFD has no ImageWidth tag"))?;
    let height = get(&mut f, 257)?
        .and_then(|v| v.first().copied())
        .ok_or_else(|| anyhow::anyhow!("TIFF IFD has no ImageLength tag"))?;
    let mut sample_format: Vec<u16> = get(&mut f, 339)?
        .map(|v| v.into_iter().map(|x| x as u16).collect())
        .unwrap_or_else(|| vec![1]);
    let mut bits: Vec<u16> = get(&mut f, 258)?
        .map(|v| v.into_iter().map(|x| x as u16).collect())
        .unwrap_or_else(|| vec![1]);
    let extra: Vec<u16> = get(&mut f, 338)?
        .map(|v| v.into_iter().map(|x| x as u16).collect())
        .unwrap_or_default();
    let samples_per_pixel = get(&mut f, 277)?
        .and_then(|v| v.first().copied())
        .unwrap_or(1) as usize;

    // `if len(sample_format) > 1 and max(sample_format) == min(sample_format)`
    if sample_format.len() > 1 && sample_format.iter().min() == sample_format.iter().max() {
        sample_format.truncate(1);
    }
    if samples_per_pixel < bits.len() {
        bits.truncate(samples_per_pixel);
    } else if samples_per_pixel > bits.len() && bits.len() == 1 {
        bits = std::iter::repeat_n(bits[0], samples_per_pixel).collect();
    }
    if bits.len() != samples_per_pixel {
        anyhow::bail!("unknown data organization (TIFF)");
    }
    if sample_format.len() != 1 {
        anyhow::bail!("unknown pixel mode (TIFF SampleFormat {sample_format:?})");
    }

    let mode = TIFF_MODES
        .iter()
        .find(|row| {
            row.photo == photo
                && row.sample_format == sample_format[0]
                && row.bits == bits.as_slice()
                && row.extra == extra.as_slice()
                && row.big_endian.map(|b| b == be).unwrap_or(true)
        })
        .map(|row| row.mode)
        .ok_or_else(|| {
            anyhow::anyhow!(
                "unknown pixel mode (TIFF photometric {photo}, sample_format {:?}, \
                 bits {bits:?}, extra_samples {extra:?})",
                sample_format[0]
            )
        })?;

    Ok(ImageHeader {
        width,
        height,
        mode: mode.to_string(),
        transparency: false,
    })
}

/// Read one IFD entry's values as `u32`s, following the offset when the
/// values don't fit in the entry's own 4-byte value field.
fn tiff_tag_values(
    f: &mut BufReader<File>,
    entries: &[u8],
    be: bool,
    tag: u16,
) -> anyhow::Result<Option<Vec<u32>>> {
    for e in entries.chunks_exact(12) {
        if u16_at(e, 0, be)? != tag {
            continue;
        }
        let ty = u16_at(e, 2, be)?;
        let count = u32_at(e, 4, be)? as usize;
        let width = match ty {
            1 | 2 | 6 | 7 => 1usize, // BYTE / ASCII / SBYTE / UNDEFINED
            3 | 8 => 2,              // SHORT / SSHORT
            4 | 9 => 4,              // LONG / SLONG
            other => anyhow::bail!("unsupported TIFF tag type {other} for tag {tag}"),
        };
        // Every tag this reader asks for is a short tuple. The cap keeps a
        // corrupt count from turning into a multi-gigabyte allocation — the
        // exact failure mode a header-only reader exists to avoid.
        if count > 4096 {
            anyhow::bail!("TIFF tag {tag} declares an implausible count ({count})");
        }
        let total = width * count;
        let raw = if total <= 4 {
            e[8..8 + total].to_vec()
        } else {
            let offset = u64::from(u32_at(e, 8, be)?);
            f.seek(SeekFrom::Start(offset))?;
            let mut buf = vec![0u8; total];
            f.read_exact(&mut buf)?;
            buf
        };
        let mut out = Vec::with_capacity(count);
        for i in 0..count {
            out.push(match width {
                1 => u32::from(raw[i]),
                2 => u32::from(u16_at(&raw, i * 2, be)?),
                _ => u32_at(&raw, i * 4, be)?,
            });
        }
        return Ok(Some(out));
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Repo-root `tests/fixtures/images/`. The images are Pillow-generated
    /// and `expected.json` holds Pillow's own answers — see that directory's
    /// `generate.py`.
    const FIXTURES: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../tests/fixtures/images");

    fn fixture(name: &str) -> std::path::PathBuf {
        std::path::Path::new(FIXTURES).join(name)
    }

    fn expected() -> serde_json::Map<String, Value> {
        let raw = std::fs::read_to_string(fixture("expected.json")).unwrap();
        serde_json::from_str::<Value>(&raw)
            .unwrap()
            .as_object()
            .unwrap()
            .clone()
    }

    #[test]
    fn humanise_splits_on_underscore_dash_dot() {
        assert_eq!(humanise("goblin_warrior-02"), "goblin warrior 02");
    }

    /// The whole fixture matrix, value for value, against Pillow.
    ///
    /// This is the test the hand-written per-format unit tests used to
    /// pretend to be. Those built a file with the `png`/`gif` crates, read it
    /// back with the same crates, and asserted a hand-written expectation —
    /// proving the Rust reader self-consistent and nothing about Pillow. Here
    /// the inputs are Pillow-written files and the expectations are Pillow's
    /// own output, so the only way to pass is to agree with the reference
    /// implementation.
    #[test]
    fn every_fixture_matches_the_pillow_reference_values() {
        let exp = expected();
        assert!(
            exp.len() >= 20,
            "expected.json looks truncated: {}",
            exp.len()
        );
        let mut failures = Vec::new();
        for (name, want) in &exp {
            let got = match ImageMetaDeriver::default().derive(&fixture(name)) {
                Ok(r) => r.metadata,
                Err(e) => {
                    failures.push(format!("{name}: ERROR {e:#}"));
                    continue;
                }
            };
            for key in [
                "width",
                "height",
                "image_format",
                "image_mode",
                "aspect_ratio",
                "has_alpha",
            ] {
                if got[key] != want[key] {
                    failures.push(format!(
                        "{name}.{key}: rust {} != pillow {}",
                        got[key], want[key]
                    ));
                }
            }
        }
        assert!(
            failures.is_empty(),
            "{} divergence(s):\n{}",
            failures.len(),
            failures.join("\n")
        );
    }

    /// Every extension the Python deriver claims to support has a fixture, so
    /// the matrix above cannot silently skip a format.
    #[test]
    fn the_fixture_set_covers_every_supported_extension() {
        let exp = expected();
        let covered: std::collections::HashSet<String> = exp
            .keys()
            .filter_map(|n| std::path::Path::new(n).extension()?.to_str())
            .map(|s| s.to_lowercase())
            .collect();
        for ext in ["png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif"] {
            assert!(covered.contains(ext), "no fixture with extension {ext:?}");
        }
    }

    /// A 640-KB-on-disk, 139-Mpx palette PNG. `image::open` refused this with
    /// "Memory limit exceeded" (558 MB as RGBA, past the crate's 512 MiB
    /// default `max_alloc`) and took the whole cell down with it; Pillow reads
    /// its header and returns. If this test ever gets slow or OOMs, something
    /// has put a full decode back on the rung-0 path.
    #[test]
    fn a_139_megapixel_palette_png_is_metadata_only_and_cheap() {
        let started = std::time::Instant::now();
        let md = ImageMetaDeriver::default()
            .derive(&fixture("png_big_palette.png"))
            .unwrap()
            .metadata;
        assert_eq!(md["width"], json!(14432));
        assert_eq!(md["height"], json!(9664));
        assert_eq!(md["image_mode"], json!("P"));
        assert!(
            started.elapsed() < std::time::Duration::from_secs(2),
            "reading a 139-Mpx header took {:?} — that is a decode, not a header read",
            started.elapsed()
        );
    }

    /// `round(w / h, 4)` is round-half-to-EVEN. `(x * 10000).round() / 10000`
    /// is round-half-away-from-zero, and the two disagree on ordinary
    /// screenshot dimensions. Values measured against CPython 3.12.
    #[test]
    fn aspect_ratio_rounds_half_to_even_like_python() {
        for (w, h, want) in [
            (800.0, 1024.0, 0.7812),
            (1000.0, 1280.0, 0.7812),
            (720.0, 512.0, 1.4062),
            (128.0, 128.0, 1.0),
            (320.0, 220.0, 1.4545),
            (360.0, 260.0, 1.3846),
        ] {
            assert_eq!(round4(w / h), want, "{w}x{h}");
        }
        // ...and the naive formula really does differ, or the assertions
        // above would pass for the wrong implementation too.
        let naive = |x: f64| (x * 10000.0).round() / 10000.0;
        assert_eq!(naive(800.0 / 1024.0), 0.7813);
    }

    #[test]
    fn text_includes_humanised_filename_and_path_segments() {
        let dir = tempfile::tempdir().unwrap();
        let sprites_dir = dir.path().join("sprites");
        std::fs::create_dir(&sprites_dir).unwrap();
        let p = sprites_dir.join("goblin_warrior.png");
        std::fs::copy(fixture("png_rgb.png"), &p).unwrap();
        let text = ImageMetaDeriver::default()
            .derive(&p)
            .unwrap()
            .text
            .to_lowercase();
        assert!(text.contains("goblin"), "{text}");
        assert!(text.contains("warrior"), "{text}");
        assert!(text.contains("sprites"), "{text}");
    }

    /// Python filters the filesystem root out of the parent segments
    /// (`p not in ("/", "")`). Rust's `Path::components()` yields a `RootDir`
    /// whose `as_os_str()` is "/", and it survived into the derived text —
    /// and therefore into the CACHE KEY, so the two ports keyed such a file
    /// differently. Only bites when the parent chain is within
    /// `segment_depth` of the root, which is why it went unnoticed.
    #[test]
    fn the_filesystem_root_is_not_a_word() {
        let d = ImageMetaDeriver::default();
        assert_eq!(
            d.path_text(std::path::Path::new("/corpus/x.png")),
            "x corpus"
        );
        assert_eq!(d.path_text(std::path::Path::new("/x.png")), "x");
        // Deep paths were always right; pinned so the fix can't regress them.
        assert_eq!(
            d.path_text(std::path::Path::new("/a/b/c/d/x.png")),
            "x b c d"
        );
    }

    #[test]
    fn never_returns_empty_text_even_for_a_bare_stem() {
        // Adversarial case per this plan's own rule 3: a filename that
        // humanises to nothing (all separator characters) must still fall
        // back to "<format> image", not blank text that on_empty=error
        // would then reject.
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("___.png");
        std::fs::copy(fixture("png_rgb.png"), &p).unwrap();
        let deriver = ImageMetaDeriver {
            path_segments: false,
            segment_depth: 3,
        };
        let text = deriver.derive(&p).unwrap().text;
        assert!(!text.trim().is_empty(), "must never be empty, got {text:?}");
        assert!(text.contains("image"));
    }

    #[test]
    fn provenance_is_stamped_with_rust_engine() {
        let md = ImageMetaDeriver::default()
            .derive(&fixture("png_rgb.png"))
            .unwrap()
            .metadata;
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
        let img: image::RgbaImage =
            image::ImageBuffer::from_pixel(1, 1, image::Rgba([1, 2, 3, 255]));
        img.save_with_format(&p, ImageFormat::Ico).unwrap();

        let err = ImageMetaDeriver::default().derive(&p).unwrap_err();
        assert!(format!("{err:#}").contains("does not support"), "{err:#}");
    }

    /// A file whose bytes are not the format its extension claims must fail
    /// with the same "failed to parse image" wording Python's `ParserError`
    /// uses, not panic and not silently return zeroed metadata.
    #[test]
    fn a_corrupt_file_fails_the_way_python_does() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("broken.png");
        std::fs::write(&p, b"this is not a PNG").unwrap();
        let err = ImageMetaDeriver::default().derive(&p).unwrap_err();
        assert!(
            format!("{err:#}").contains("failed to parse image"),
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

        let md = deriver.derive(&fixture("png_rgb.png")).unwrap().metadata;
        assert_eq!(md["derived"]["rung"], json!(rung));
        assert_eq!(md["derived"]["model"], json!(model));
        assert_eq!(md["derived"]["model_version"], json!(version));

        let expected_hash =
            super::super::provenance::derivation_hash(&rung, &model, &version, "rust", &params);
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
