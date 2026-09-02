"""Rung 5: the `ross` subprocess deriver.

TWO TIERS, deliberately.

The NN-5 tests (missing binary, unreadable labels file, config validation)
run ALWAYS — they are the "never a dead end" contract, and a suite that
skipped them on a machine without ross would be silent about exactly the
case a machine without ross hits.

Everything that actually shells out is gated on the binary being present,
mirroring how this repo's DB tests skip on an unreachable DSN.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from chunkshop.sources.derivers.ross import RossDeriver, ross_available

requires_ross = pytest.mark.skipif(
    not ross_available(),
    reason="ross binary not on $PATH (https://github.com/yonk-labs/ross)",
)

SAMPLES = Path(__file__).resolve().parents[3] / "docs" / "samples" / "images"
KNIGHT = SAMPLES / "sprite_knight.png"
CHART = SAMPLES / "chart_revenue.png"

#: Every key this deriver promises, present on every document regardless of
#: what ross chose to emit. Pinned as a literal so an accidental addition or
#: removal is a test failure, not a silent contract change — the Rust port
#: asserts the same set.
ROSS_METADATA_KEYS = {
    "ross_type",
    "ross_mime",
    "ross_description",
    "ross_summary",
    "ross_tags",
    "ross_tag_confidence",
    "ross_model",
    "ross_metadata",
    "ross_warnings",
    "content_origin",
    "derived_rung",
    "derived",
}

DERIVED_KEYS = {"rung", "model", "model_version", "engine", "params_hash", "ts"}


# --- NN-5: never a dead end. These run with or without ross installed. ----


def test_missing_binary_names_the_binary_and_what_to_do(tmp_path):
    missing = str(tmp_path / "definitely-not-ross")
    with pytest.raises(RuntimeError) as exc:
        RossDeriver(binary=missing)
    msg = str(exc.value)
    assert missing in msg, msg
    assert "github.com/yonk-labs/ross" in msg, msg
    assert "`binary:`" in msg, msg


def test_a_non_executable_binary_is_a_named_error_not_a_traceback(tmp_path):
    fake = tmp_path / "ross"
    fake.write_text("#!/bin/sh\nexit 0\n")  # deliberately NOT chmod +x
    with pytest.raises(RuntimeError) as exc:
        RossDeriver(binary=str(fake))
    assert str(fake) in str(exc.value)
    assert "github.com/yonk-labs/ross" in str(exc.value)


def test_a_binary_that_is_not_ross_is_rejected_at_construction(tmp_path):
    """`--version` printing nothing parseable must not become a silent
    empty `model_version` folded into the cache key."""
    fake = tmp_path / "ross"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    with pytest.raises(RuntimeError) as exc:
        RossDeriver(binary=str(fake))
    assert "parseable" in str(exc.value)


def test_a_nonzero_version_exit_is_a_named_error(tmp_path):
    fake = tmp_path / "ross"
    fake.write_text("#!/bin/sh\necho 'boom' >&2\nexit 7\n")
    fake.chmod(0o755)
    with pytest.raises(RuntimeError) as exc:
        RossDeriver(binary=str(fake))
    assert "status 7" in str(exc.value)
    assert "boom" in str(exc.value), "ross's own stderr must be surfaced"


def test_unreadable_labels_file_fails_at_construction(tmp_path):
    with pytest.raises(RuntimeError) as exc:
        RossDeriver(binary="ross", labels_file=str(tmp_path / "nope.txt"))
    assert "labels_file" in str(exc.value)


def _stub_ross(tmp_path, body: str) -> str:
    """A fake `ross` that answers `--version` and then runs `body`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "ross"
    p.write_text(
        '#!/bin/sh\n'
        'case "$1" in --version) echo "ross 9.9.9"; exit 0;; esac\n'
        f"{body}\n"
    )
    p.chmod(0o755)
    return str(p)


def test_a_nonzero_derive_exit_surfaces_ross_stderr_with_a_hint(tmp_path):
    """Exit 5 means ross could not load its local models. The user must see
    ross's own message — when a run fails for a missing ffprobe/exiftool,
    only ross's stderr names the missing binary."""
    binary = _stub_ross(tmp_path, 'echo "ffprobe not found" >&2\nexit 5')
    d = RossDeriver(binary=binary)
    from chunkshop.sources.parsers.base import ParserError

    with pytest.raises(ParserError) as exc:
        d.derive(KNIGHT)
    msg = str(exc.value)
    assert "status 5" in msg, msg
    assert "CLIP/CLAP" in msg, msg
    assert "ffprobe not found" in msg, msg


def test_a_timeout_is_a_named_error_naming_the_knob(tmp_path):
    binary = _stub_ross(tmp_path, "sleep 5")
    d = RossDeriver(binary=binary, timeout_s=1)
    from chunkshop.sources.parsers.base import ParserError

    with pytest.raises(ParserError) as exc:
        d.derive(KNIGHT)
    assert "timeout_s" in str(exc.value)


def test_unparseable_json_is_a_named_error_not_a_stacktrace(tmp_path):
    binary = _stub_ross(tmp_path, 'echo "not json at all"')
    d = RossDeriver(binary=binary)
    from chunkshop.sources.parsers.base import ParserError

    with pytest.raises(ParserError) as exc:
        d.derive(KNIGHT)
    assert "not json" in str(exc.value)


def test_a_ross_reported_per_file_error_is_never_an_empty_derivation(tmp_path):
    binary = _stub_ross(
        tmp_path, """echo '{"path":"x","error":"image too large"}'"""
    )
    d = RossDeriver(binary=binary)
    from chunkshop.sources.parsers.base import ParserError

    with pytest.raises(ParserError) as exc:
        d.derive(KNIGHT)
    assert "image too large" in str(exc.value)


# --- config ---------------------------------------------------------------


def test_config_accepts_the_rung_and_rejects_a_typo():
    from pydantic import ValidationError

    from chunkshop.config import FilesSource

    cfg = FilesSource(
        type="files", glob="*.png", derive={"png": {"rung": "ross"}}
    )
    assert cfg.derive["png"].binary == "ross"
    assert cfg.derive["png"].llm is False
    assert cfg.derive["png"].timeout_s == 120
    assert cfg.derive["png"].labels_file is None

    with pytest.raises(ValidationError):
        FilesSource(
            type="files",
            glob="*.png",
            derive={"png": {"rung": "ross", "binry": "/x"}},
        )


def test_model_version_is_probed_not_hardcoded(tmp_path):
    """`clip_tags` hardcodes its model version because it has no runtime
    option; this rung does, so a hardcoded constant would be a regression."""
    binary = _stub_ross(tmp_path, "exit 0")
    assert RossDeriver(binary=binary).version == "9.9.9"


def test_the_labels_file_content_is_in_the_params_not_its_path(tmp_path):
    """Vocabulary identity. Editing the labels file MUST re-derive; moving
    it must not."""
    a = tmp_path / "a" / "labels.txt"
    b = tmp_path / "b" / "labels.txt"
    for p in (a, b):
        p.parent.mkdir(parents=True)
        p.write_text("a cat\na dog\n")
    binary = _stub_ross(tmp_path, "exit 0")

    da = RossDeriver(binary=binary, labels_file=str(a))
    db = RossDeriver(binary=binary, labels_file=str(b))
    assert da.derivation_hash() == db.derivation_hash(), "path must not matter"
    assert da.labels_sha256 == hashlib.sha256(b"a cat\na dog\n").hexdigest()

    b.write_text("a cat\na dog\na bird\n")
    dc = RossDeriver(binary=binary, labels_file=str(b))
    assert dc.derivation_hash() != da.derivation_hash(), "content must matter"


def test_the_vendored_vocabulary_is_identified_by_the_binary_version(tmp_path):
    """With no `labels_file`, the vocabulary lives inside the binary and is
    identified ONLY by its version — so a version bump must invalidate."""
    old = _stub_ross(tmp_path / "old", "exit 0")
    new_dir = tmp_path / "new"
    new_dir.mkdir()
    p = new_dir / "ross"
    p.write_text(
        '#!/bin/sh\ncase "$1" in --version) echo "ross 9.9.10"; exit 0;; esac\n'
    )
    p.chmod(0o755)

    assert (
        RossDeriver(binary=old).derivation_hash()
        != RossDeriver(binary=str(p)).derivation_hash()
    )


def test_llm_flag_changes_the_derivation_hash(tmp_path):
    binary = _stub_ross(tmp_path, "exit 0")
    assert (
        RossDeriver(binary=binary, llm=False).derivation_hash()
        != RossDeriver(binary=binary, llm=True).derivation_hash()
    )


def test_timeout_and_binary_path_do_not_change_the_derivation_hash(tmp_path):
    """Neither changes what ross COMPUTES, so neither may fragment the
    cache. The version, which does change it, is already `model_version`."""
    a = _stub_ross(tmp_path / "a", "exit 0")
    b = _stub_ross(tmp_path / "b", "exit 0")
    assert (
        RossDeriver(binary=a, timeout_s=10).derivation_hash()
        == RossDeriver(binary=b, timeout_s=999).derivation_hash()
    )


def test_cache_key_extra_is_the_basename_only(tmp_path):
    """ross's gated-tagger fallback humanises the BASENAME (it rsplits the
    path on '/' first), so the parent directories are genuinely not
    consumed and keying on them would needlessly fragment the cache."""
    binary = _stub_ross(tmp_path, "exit 0")
    d = RossDeriver(binary=binary)
    assert d.cache_key_extra(Path("/one/two/knight.png")) == "knight.png"
    assert d.cache_key_extra(Path("/other/knight.png")) == "knight.png"
    assert d.cache_key_extra(Path("/one/two/other.png")) == "other.png"


# --- the real binary ------------------------------------------------------


@requires_ross
def test_derives_an_image_with_the_promised_key_set():
    r = RossDeriver().derive(KNIGHT)
    assert set(r.metadata) == ROSS_METADATA_KEYS
    assert set(r.metadata["derived"]) == DERIVED_KEYS
    assert r.text.strip()
    assert r.metadata["ross_type"] == "image"
    assert r.metadata["ross_mime"] == "image/png"


@requires_ross
def test_provenance_records_ross_as_the_engine_and_the_real_version():
    d = RossDeriver()
    md = d.derive(KNIGHT).metadata
    assert md["derived"]["engine"] == "ross"
    assert md["derived"]["rung"] == "ross"
    assert md["derived"]["model"] == "ross"
    assert md["derived"]["model_version"] == d.version
    # The recorded version really is what the binary reports.
    out = subprocess.run(
        ["ross", "--version"], capture_output=True, text=True, check=True
    ).stdout
    assert d.version == out.split()[-1]


@requires_ross
def test_stamped_provenance_reproduces_the_derivation_hash():
    """The stamped record, standing alone, must be enough to rebuild the
    cache key — otherwise provenance and the key can drift apart."""
    from chunkshop.sources.derivers.provenance import _hash_from_parts

    d = RossDeriver()
    md = d.derive(KNIGHT).metadata
    rebuilt = _hash_from_parts(
        md["derived"]["rung"],
        md["derived"]["model"],
        md["derived"]["model_version"],
        md["derived"]["engine"],
        md["derived"]["params_hash"],
    )
    assert rebuilt == d.derivation_hash()


@requires_ross
def test_no_nondeterministic_field_reaches_the_metadata():
    """ross emits `duration_ms` and `mtime`; neither is keyed, so caching
    either would make a hit differ from a fresh derivation."""
    md = RossDeriver().derive(KNIGHT).metadata
    blob = json.dumps(md)
    assert "duration_ms" not in blob
    assert "mtime" not in blob


@requires_ross
def test_a_tagged_image_puts_tags_in_both_text_and_metadata():
    r = RossDeriver().derive(CHART)
    assert r.metadata["ross_tags"], "chart_revenue.png is expected to tag"
    for tag in r.metadata["ross_tags"]:
        assert tag in r.text
    assert isinstance(r.metadata["ross_tag_confidence"], float)


@requires_ross
def test_a_non_media_file_is_a_named_error_not_an_empty_document(tmp_path):
    from chunkshop.sources.parsers.base import ParserError

    p = tmp_path / "notes.png"  # .png extension, but text bytes
    p.write_text("this is not an image")
    with pytest.raises(ParserError) as exc:
        RossDeriver().derive(p)
    assert "magic bytes" in str(exc.value)


@requires_ross
def test_audio_derives_through_the_same_rung_with_the_same_key_set(tmp_path):
    """ross sniffs by magic bytes, so audio needs no extra machinery at
    all — the same deriver, the same config block, the same key set.
    chunkshop has no in-process audio tagger in any plan; this is it."""
    wav = tmp_path / "beep_tone.wav"
    _write_wav(wav)
    r = RossDeriver().derive(wav)
    assert set(r.metadata) == ROSS_METADATA_KEYS
    assert r.metadata["ross_type"] == "audio"
    assert r.metadata["ross_metadata"]["duration_s"] == pytest.approx(1.0, abs=0.2)
    assert r.text.strip()


@requires_ross
def test_byte_identical_files_under_two_names_do_not_share_a_cache_entry(
    tmp_path,
):
    """The pairing test for `cache_key_extra`. ross's gated-tagger fallback
    derives text from the basename, so two copies of ONE file under two
    names must derive differently AND must not collide in the cache."""
    from chunkshop.sources.derivers.cache import DerivationCache, cached_derive
    from chunkshop.sources.parsers.base import cache_key_extra_for

    a = tmp_path / "sprite_knight.png"
    b = tmp_path / "haunted_castle.png"
    a.write_bytes(KNIGHT.read_bytes())
    b.write_bytes(KNIGHT.read_bytes())

    d = RossDeriver()
    content_hash = hashlib.sha256(a.read_bytes()).hexdigest()
    cache = DerivationCache(root=tmp_path / "cache")

    ra, _ = cached_derive(
        cache, content_hash, d.derivation_hash(),
        lambda: d.derive(a), cache_key_extra_for(d, a),
    )
    rb, hit = cached_derive(
        cache, content_hash, d.derivation_hash(),
        lambda: d.derive(b), cache_key_extra_for(d, b),
    )
    assert not hit, "the second name must MISS, not be served the first's text"
    assert "knight" in ra.text.lower()
    assert "castle" in rb.text.lower()

    # ...and the same name must hit.
    _, hit_again = cached_derive(
        cache, content_hash, d.derivation_hash(),
        lambda: pytest.fail("should have been a cache hit"),
        cache_key_extra_for(d, a),
    )
    assert hit_again


def _write_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> None:
    """A 1s 440Hz tone, written with the stdlib so the test needs no ffmpeg."""
    import math
    import struct
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(20000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(rate * seconds))
        )
        w.writeframes(frames)
