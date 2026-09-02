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

Rung 5 (`ross`) needs the `ross` binary on `$PATH`. When it is absent the
rung-5 sections are carried over from the existing fixture and a note is
printed — the same shape as `--skip-rung1`, because a machine without ross
must not be able to silently blank a committed contract.
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

# Rung 5 is generated with ross's own vendored vocabulary and no LLM — the
# keyless, offline default. The Rust test reads this config back out of the
# fixture rather than restating it.
ROSS_CONFIG = {"rung": "ross"}

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


def _carry_over(out: dict, sections: tuple[str, ...], why: str) -> None:
    """Copy sections from the committed fixture rather than regenerating.

    A machine that cannot produce a section must not blank it: the fixture
    is a cross-language contract, and an absent generator dependency is a
    reason to preserve, never to delete.
    """
    if not FIXTURE.exists():
        raise SystemExit(
            f"ERROR: {why}, and there is no existing fixture to carry "
            f"{', '.join(sections)} over from"
        )
    prior = json.loads(FIXTURE.read_text())
    for key in sections:
        if key not in prior:
            raise SystemExit(f"ERROR: existing fixture has no {key} to carry over")
        out[key] = prior[key]
    print(f"carried {', '.join(sections)} over ({why})")


def _rung5(out: dict, image: Path, skip: bool) -> None:
    """The `ross` subprocess rung. Its metadata key set is FIXED by the
    deriver (every key present on every document regardless of what ross
    emitted), which is what makes it assertable across ports at all."""
    from chunkshop.sources.derivers.ross import RossDeriver, ross_available

    sections = ("rung5", "rung5_document")
    if skip:
        _carry_over(out, sections, "--skip-rung5")
        return
    if not ross_available():
        _carry_over(out, sections, "ross is not on $PATH")
        return

    r = RossDeriver().derive(image)
    out["rung5"] = {
        "deriver": RossDeriver.__name__,
        "config": ROSS_CONFIG,
        "metadata_keys": _keys(r.metadata),
        "derived_keys": _derived_keys(r.metadata),
        # Pinned as VALUES, unlike model/version/hashes: both ports shell
        # out to the SAME binary, so `engine` and `rung` are genuinely
        # identical rather than legitimately divergent. `model_version` is
        # not pinned — it is whatever ross the host has.
        "engine": r.metadata["derived"]["engine"],
        "rung": r.metadata["derived"]["rung"],
        "model": r.metadata["derived"]["model"],
    }
    out["rung5_document"] = _document_section(ROSS_CONFIG, image)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--skip-rung5",
        action="store_true",
        help="carry the existing rung-5 sections over instead of running the "
        "ross binary (implied when ross is not on $PATH)",
    )
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

    _rung5(out, image, args.skip_rung5)

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
    for section in (
        "rung0", "rung0_document", "rung1", "rung1_document",
        "rung5", "rung5_document",
    ):
        if section in out:
            body = out[section]
            print(f"  {section}: {len(body['metadata_keys'])} metadata keys", end="")
            print(f", parser={body['parser']!r}" if "parser" in body else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
