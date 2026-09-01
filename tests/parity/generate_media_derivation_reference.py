"""Generate the SC-12 contract-level parity fixture for media derivation.

Writes `rust/chunkshop/tests/parity-fixtures/media_derivation_reference.json`,
which `rust/chunkshop/tests/media_derivation_parity.rs` reads.

Run from the repo root:

    cd python && uv run --no-sync python ../tests/parity/generate_media_derivation_reference.py

This script exists because the fixture previously had NO committed generator:
its `_comment` pointed at a Python heredoc embedded in a plan document, and
the file had visibly drifted from anything that snippet emitted. Every other
parity fixture in this repo is produced by a script under `tests/parity/` or
`scripts/`; this one now is too.

WHAT THE FIXTURE RECORDS: key sets, never values that legitimately differ
between the two implementations. `derived.ts` is a wall-clock timestamp,
`derived.model_version` is the host `fastembed` version (Python's package vs
the Rust crate), and `derived.engine` — plus therefore the whole derivation
hash — differ BY DESIGN so both languages can share one cache directory
without masquerading as each other. None of those may ever be asserted equal.

TWO LEVELS, because they are two different contracts:

  * `rung0` / `rung1` — what the DERIVER returns. This is the provenance
    shape.
  * `rung0_document` / `rung1_document` — what a user actually reads off a
    row, i.e. what `FilesSource` produces after merging in its own keys
    (`source_path`, `parser`). The deriver-level set alone cannot see a
    missing `parser` key, and for a while it did not: Rust stamped no
    `parser` at all while Python stamped `parser="ClipTagsDeriver"` on every
    row, and the deriver-level fixture was green throughout.

Rung 1 loads the two CLIP towers (~0.6GB). They are reused from the cache the
Rust tests populate, so this is a no-download run once those have been fetched
at least once. Pass `--skip-rung1` to regenerate only the rung-0 sections,
preserving whatever rung-1 sections the existing fixture already holds.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Single thread everywhere, matching `generate_clip_full_vectors.py` and the
# Rust tests. Rung 0 is deterministic regardless; rung 1's tag SELECTION could
# in principle flip on a near-tie, and the key set is what we record anyway.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    REPO_ROOT
    / "rust"
    / "chunkshop"
    / "tests"
    / "parity-fixtures"
    / "media_derivation_reference.json"
)

# Relative to the repo root — recorded in the fixture and read by the Rust
# test, so both sides derive the SAME image.
IMAGE_FIXTURE = "docs/samples/images/sprite_knight.png"

# The rung-1 config the fixture is generated with. The Rust test reads these
# back out of the fixture rather than restating them, so a regenerated
# vocabulary can never silently be compared against a differently-configured
# Rust deriver.
VOCABULARY = ["a knight sprite", "a landscape photo", "a chart"]
TOP_K = 3
MIN_SCORE = 0.2

COMMENT = (
    "Golden fixture for rust/chunkshop/tests/media_derivation_parity.rs. "
    "GENERATED — do not hand-edit; run "
    "tests/parity/generate_media_derivation_reference.py. "
    "KEY SETS ONLY, never values that legitimately differ between "
    "implementations: derived.ts is a timestamp, derived.model_version is the "
    "host fastembed version (python vs the rust crate), and derived.engine / "
    "the derivation hash differ BY DESIGN (engine participates in the hash so "
    "both languages can share one cache directory without masquerading as "
    "each other). The *_document sections are the same rungs seen through "
    "FilesSource, i.e. what a user actually reads off a row — they carry "
    "source_path and parser, which the deriver-level sections cannot see."
)


def _keys(metadata: dict) -> list[str]:
    return sorted(metadata)


def _derived_keys(metadata: dict) -> list[str]:
    return sorted(metadata["derived"])


def _document_section(derive_cfg: dict, image: Path) -> dict:
    """Drive the REAL `FilesSource` over a one-image glob.

    Not a hand-assembled dict: `parser` and `source_path` are merged in by
    `files.py`'s metadata-assembly block, and the point of this section is to
    record what that block actually produces.
    """
    from chunkshop.config import FilesSource as FilesSourceConfig
    from chunkshop.sources.files import FilesSource

    cfg = FilesSourceConfig(
        type="files",
        glob=str(image),
        id_from="stem",
        derive={image.suffix.lstrip("."): derive_cfg},
        cache={"enabled": False},
    )
    docs = list(FilesSource(cfg).iter_documents())
    if len(docs) != 1:
        raise SystemExit(f"expected exactly one document from {image}, got {len(docs)}")
    md = docs[0].metadata
    return {
        "metadata_keys": _keys(md),
        # Pinned as a VALUE, not just a key: an empty string or a Rust type
        # name in this slot is exactly the defect the key exists to catch,
        # and a key-set assertion cannot see it.
        "parser": md["parser"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--skip-rung1",
        action="store_true",
        help="regenerate only rung 0; keep the existing rung-1 sections "
        "(avoids loading ~0.6GB of CLIP weights)",
    )
    args = ap.parse_args()

    image = REPO_ROOT / IMAGE_FIXTURE
    if not image.exists():
        print(f"ERROR: missing image fixture {image}", file=sys.stderr)
        return 2

    from chunkshop.sources.derivers.image_meta import ImageMetaDeriver

    out: dict = {
        "_comment": COMMENT,
        "image_fixture": IMAGE_FIXTURE,
    }

    rung0 = ImageMetaDeriver().derive(image)
    out["rung0"] = {
        "deriver": ImageMetaDeriver.__name__,
        "metadata_keys": _keys(rung0.metadata),
        "derived_keys": _derived_keys(rung0.metadata),
    }
    out["rung0_document"] = _document_section({"rung": "image_meta"}, image)

    if args.skip_rung1:
        if not FIXTURE.exists():
            print(
                "ERROR: --skip-rung1 needs an existing fixture to carry the "
                "rung-1 sections over from",
                file=sys.stderr,
            )
            return 2
        prior = json.loads(FIXTURE.read_text())
        for key in ("rung1", "rung1_document"):
            if key not in prior:
                print(f"ERROR: existing fixture has no {key} to carry over", file=sys.stderr)
                return 2
            out[key] = prior[key]
        print("carried rung1 + rung1_document over from the existing fixture")
    else:
        from chunkshop.sources.derivers.clip_tags import ClipTagsDeriver

        # No `cache_dir` argument: `ClipTagsDeriver` deliberately takes none,
        # so the shipped code path is exercised exactly as a user would get
        # it, resolving models through fastembed's own default cache.
        d = ClipTagsDeriver(
            vocabulary=VOCABULARY,
            top_k=TOP_K,
            min_score=MIN_SCORE,
        )
        rung1 = d.derive(image)
        out["rung1"] = {
            "deriver": ClipTagsDeriver.__name__,
            "vocabulary": VOCABULARY,
            "top_k": TOP_K,
            "min_score": MIN_SCORE,
            "metadata_keys": _keys(rung1.metadata),
            "derived_keys": _derived_keys(rung1.metadata),
        }
        out["rung1_document"] = _document_section(
            {
                "rung": "clip_tags",
                "vocabulary": VOCABULARY,
                "top_k": TOP_K,
                "min_score": MIN_SCORE,
            },
            image,
        )
        # The fixture is committed, so no absolute host path may reach it.
        assert str(REPO_ROOT) not in json.dumps(out), "host path leaked into the fixture"

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {FIXTURE}")
    for section in ("rung0", "rung0_document", "rung1", "rung1_document"):
        if section in out:
            body = out[section]
            print(f"  {section}: {len(body['metadata_keys'])} metadata keys", end="")
            print(f", parser={body['parser']!r}" if "parser" in body else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
