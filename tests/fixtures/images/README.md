# Adversarial image-metadata fixtures

Tiny Pillow-generated images that exercise the `image_meta` rung-0 deriver's
awkward cases — the ones where `image`-crate decoding and Pillow disagree
unless the decoder reads the container's own colour metadata:

| file | what it covers |
|---|---|
| `swatch_palette.png` | indexed PNG (`mode="P"`), no alpha |
| `swatch_palette_trns.png` | indexed PNG carrying a `tRNS` chunk (`has_alpha=True` without an alpha channel) |
| `swatch_grayscale.png` | 8-bit greyscale (`mode="L"`) |
| `swatch_rgba.png` | true RGBA (`mode="RGBA"`) |
| `swatch_rgb.gif` | GIF — `image` always reports alpha; only the Graphic Control Extension says whether it's real |
| `swatch_rgb.jpg` | baseline JPEG (`mode="RGB"`, never alpha) |
| `swatch_rgb.webp` | WebP (`mode="RGB"`) |

Regenerate with (from the repo root):

    cd python && uv run --no-sync python ../docs/samples/images/generate_fixtures.py

They deliberately live **here** and not in `docs/samples/images/`: that
directory is the SC-1 quickstart corpus, globbed as `*.png` by
`docs/samples/images-keyless.yaml` and asserted on by
`.github/workflows/sc1-quickstart-clock.yml`. Adding images there dilutes the
quickstart's ranking margin down to bare RRF rank spacing — which is exactly
what happened while these files sat in that directory. Keep test fixtures out
of the sample corpus.
