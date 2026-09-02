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


def _stub_ross(tmp_path, body: str, doctor: str | None = None) -> str:
    """A fake `ross` that answers `--version` (and optionally `--doctor`)
    and then runs `body`.

    `doctor` is shell, not a literal: the model-swap tests need a stub whose
    `--doctor` output tracks `$ROSS_MODEL` exactly the way the real binary's
    does, so the test can change ONE environment variable and nothing else.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "ross"
    arms = 'case "$1" in --version) echo "ross 9.9.9"; exit 0;;'
    if doctor is not None:
        arms += f' --doctor) {doctor}\nexit 0;;'
    arms += " esac"
    p.write_text(f"#!/bin/sh\n{arms}\n{body}\n")
    p.chmod(0o755)
    return str(p)


#: A `--doctor` arm that mirrors ross 0.1.0's real output, resolving from the
#: environment exactly as ross does for the single-endpoint case.
_DOCTOR_FROM_ENV = (
    "printf 'ffprobe    ok\\n'; "
    "printf 'exiftool   MISSING (optional)\\n'; "
    "for m in vision audio video; do "
    "  if [ -n \"$ROSS_URL\" ]; then "
    "    printf '%-10s %s model=%s key=%s\\n' \"$m\" \"$ROSS_URL\" "
    "\"$ROSS_MODEL\" \"$([ -n \"$ROSS_API_KEY\" ] && echo set || echo none)\"; "
    "  else printf '%-10s NOT SET\\n' \"$m\"; fi; "
    "done; "
    "printf '\\nclap       ok (/x)\\n'"
)


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


def test_llm_flag_changes_the_derivation_hash(tmp_path, monkeypatch):
    """`llm: true` needs a readable `--doctor`, because with the LLM on the
    endpoint and model are hash inputs — see the LLM section below."""
    monkeypatch.setenv("ROSS_URL", "http://h/v1")
    monkeypatch.setenv("ROSS_MODEL", "m")
    binary = _stub_ross(tmp_path, "exit 0", _DOCTOR_FROM_ENV)
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


# --- the LLM endpoint is an input, and it is in the hash -------------------
#
# The defect these close, measured before the fix: same cell, same images,
# only ROSS_MODEL changed between runs -> run 2 finished in 0.35s against
# run 1's 9.22s, served run 1's three cache entries verbatim, and the rows
# carried the first model's words with nothing recording which model wrote
# them.


#: The `llm: false` derivation hash, PINNED. Its whole point is that this
#: fix did not move it: `llm_endpoints` is absent from the params when the
#: LLM is off, so every cache entry written on the keyless path before the
#: fix is still reachable after it. A future edit that changes this literal
#: is invalidating real user caches and must say so out loud.
LLM_FALSE_HASH = "314091bc293d054ae41970523db6b580df4c3acf2e6af04a36293c526531d829"
LLM_FALSE_PARAMS = {"labels_sha256": None, "llm": False}


def test_the_llm_false_params_and_hash_did_not_move(tmp_path):
    """Pinned against the pre-fix values, measured on the same stub."""
    binary = _stub_ross(tmp_path, "exit 0")
    d = RossDeriver(binary=binary, llm=False)
    assert d._params() == LLM_FALSE_PARAMS
    assert d.derivation_hash() == LLM_FALSE_HASH


def test_llm_false_never_runs_doctor(tmp_path):
    """The keyless path must not pay a `--doctor` subprocess, and must not
    be able to fail because of one. This stub makes `--doctor` explode."""
    binary = _stub_ross(tmp_path, "exit 0", doctor='echo "boom" >&2; exit 9')
    d = RossDeriver(binary=binary, llm=False)
    assert d.derivation_hash() == LLM_FALSE_HASH
    assert d.llm_endpoints is None


def _llm_deriver(tmp_path, monkeypatch, model, url="http://endpoint:8082/v1", key=None):
    for var in ("ROSS_URL", "ROSS_MODEL", "ROSS_API_KEY",
                "ROSS_VISION_URL", "ROSS_VISION_MODEL", "ROSS_VISION_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ROSS_URL", url)
    monkeypatch.setenv("ROSS_MODEL", model)
    if key is not None:
        monkeypatch.setenv("ROSS_API_KEY", key)
    # The derive arm echoes back the model ross was told to use, the way the
    # real binary stamps `model` when the LLM path wins.
    body = (
        'printf \'{"type":"image","description":"a knight, per %s","model":"%s"}\''
        ' "$ROSS_MODEL" "$ROSS_MODEL"'
    )
    binary = _stub_ross(tmp_path, body, _DOCTOR_FROM_ENV)
    return RossDeriver(binary=binary, llm=True)


def test_a_model_swap_forces_a_re_derivation(tmp_path, monkeypatch):
    """THE regression test. Two derivations of the same file with different
    `ROSS_MODEL` values must produce different hashes AND must both really
    derive — a cache HIT here is the bug."""
    from chunkshop.sources.derivers.cache import DerivationCache, cached_derive
    from chunkshop.sources.parsers.base import cache_key_extra_for

    img = tmp_path / "sprite_knight.png"
    img.write_bytes(b"fake png bytes")
    content_hash = hashlib.sha256(img.read_bytes()).hexdigest()
    cache = DerivationCache(root=tmp_path / "cache")

    a = _llm_deriver(tmp_path / "a", monkeypatch, "mlx-community/gemma-4-e4b-it-4bit")
    ra, hit_a = cached_derive(
        cache, content_hash, a.derivation_hash(),
        lambda: a.derive(img), cache_key_extra_for(a, img),
    )
    assert not hit_a

    b = _llm_deriver(tmp_path / "b", monkeypatch, "deepreinforce-ai/Ornith-1.0-9B")
    assert a.derivation_hash() != b.derivation_hash(), (
        "the LLM model must be in the derivation hash"
    )
    rb, hit_b = cached_derive(
        cache, content_hash, b.derivation_hash(),
        lambda: b.derive(img), cache_key_extra_for(b, img),
    )
    assert not hit_b, "a model swap must re-derive, not serve the old model's text"
    assert ra.text != rb.text
    assert "gemma" in ra.text and "Ornith" in rb.text

    # ...and swapping BACK still hits, so this is a key change, not a
    # blanket cache defeat.
    c = _llm_deriver(tmp_path / "c", monkeypatch, "mlx-community/gemma-4-e4b-it-4bit")
    assert c.derivation_hash() == a.derivation_hash()
    _, hit_c = cached_derive(
        cache, content_hash, c.derivation_hash(),
        lambda: pytest.fail("should have been a cache hit"),
        cache_key_extra_for(c, img),
    )
    assert hit_c


def test_the_endpoint_url_is_in_the_hash_too(tmp_path, monkeypatch):
    """Same model name served by two different hosts is two different
    models in practice — a local mlx build and a hosted one do not agree."""
    a = _llm_deriver(tmp_path / "a", monkeypatch, "m", url="http://host-a:8082/v1")
    b = _llm_deriver(tmp_path / "b", monkeypatch, "m", url="http://host-b:8082/v1")
    assert a.derivation_hash() != b.derivation_hash()


def test_key_presence_but_never_its_value_is_in_the_hash(tmp_path, monkeypatch):
    """Adding a key to a previously keyless endpoint turns a 401 (and an
    `llm_error` degraded derivation) into a real one, so it must re-derive.
    Two DIFFERENT keys must not, because ross reports only `set`."""
    none = _llm_deriver(tmp_path / "n", monkeypatch, "m")
    k1 = _llm_deriver(tmp_path / "k1", monkeypatch, "m", key="sk-secret-one")
    k2 = _llm_deriver(tmp_path / "k2", monkeypatch, "m", key="sk-secret-two")
    assert none.derivation_hash() != k1.derivation_hash()
    assert k1.derivation_hash() == k2.derivation_hash()
    assert none._params()["llm_endpoints"]["vision"]["key_set"] is False
    assert k1._params()["llm_endpoints"]["vision"]["key_set"] is True


def test_no_api_key_reaches_params_provenance_metadata_or_an_error(
    tmp_path, monkeypatch
):
    """The explicit leak assertion. The stub reports `key=set`, the real key
    is in the environment, and it must appear in NOTHING chunkshop keeps."""
    secret = "sk-do-not-leak-9f3a2b"
    d = _llm_deriver(tmp_path / "k", monkeypatch, "m", key=secret)
    img = tmp_path / "x.png"
    img.write_bytes(b"fake")

    blobs = [
        json.dumps(d._params()),
        json.dumps(d.llm_endpoints),
        json.dumps(d.derive(img).metadata),
    ]
    # ...and the error paths, which is where a naive implementation echoes
    # `--doctor` output verbatim.
    from chunkshop.sources.derivers.ross import _parse_doctor, _redact_doctor

    leaky = (
        f"vision     http://h/v1 model=m key={secret}\n"
        f"audio      NOT SET\nvideo      NOT SET\n"
    )
    blobs.append(_redact_doctor(leaky))
    with pytest.raises(RuntimeError) as exc:
        _parse_doctor(leaky, "ross")
    blobs.append(str(exc.value))

    for blob in blobs:
        assert secret not in blob, blob
    assert "key_set" in blobs[0], "the BOOLEAN is kept; only the value is not"


def test_a_not_set_modality_is_null_and_configuring_one_re_derives(
    tmp_path, monkeypatch
):
    """`NOT SET` is the honest record — ross emits `llm_error: no endpoint
    configured` for that modality — and pointing it somewhere must
    re-derive rather than reuse the endpoint-less derivation."""
    for var in ("ROSS_URL", "ROSS_MODEL", "ROSS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    unset = RossDeriver(
        binary=_stub_ross(tmp_path / "u", "exit 0", _DOCTOR_FROM_ENV), llm=True
    )
    assert unset.llm_endpoints == {"vision": None, "audio": None, "video": None}

    setup = _llm_deriver(tmp_path / "s", monkeypatch, "m")
    assert unset.derivation_hash() != setup.derivation_hash()


@pytest.mark.parametrize(
    "doctor,needle",
    [
        # ross grew a format we cannot read.
        ("printf 'vision     http://h/v1 :: m\\n'; printf 'audio      NOT SET\\n';"
         " printf 'video      NOT SET\\n'", "could not parse"),
        # A modality vanished (or was renamed) — the silently-unhashed case.
        ("printf 'vision     NOT SET\\n'; printf 'audio      NOT SET\\n'",
         "did not report video"),
        # `--doctor` printed nothing at all.
        ("true", "did not report"),
        # An unexpected key state.
        ("printf 'vision     http://h/v1 model=m key=maybe\\n';"
         " printf 'audio      NOT SET\\n'; printf 'video      NOT SET\\n'",
         "unexpected key state"),
    ],
)
def test_an_unreadable_doctor_is_a_named_error_not_a_degraded_hash(
    tmp_path, monkeypatch, doctor, needle
):
    """A silent fallback to a hash that omits the model would reintroduce
    the exact bug this fix closes, so every unreadable shape is fatal — at
    CONSTRUCTION, before a `mode: overwrite` cell has dropped the table."""
    monkeypatch.setenv("ROSS_URL", "http://h/v1")
    monkeypatch.setenv("ROSS_MODEL", "m")
    binary = _stub_ross(tmp_path, "exit 0", doctor)
    with pytest.raises(RuntimeError) as exc:
        RossDeriver(binary=binary, llm=True)
    msg = str(exc.value)
    assert needle in msg, msg
    assert "ross --doctor" in msg, "the error must say how to see the real output"


def test_a_failing_doctor_is_a_named_error(tmp_path):
    binary = _stub_ross(tmp_path, "exit 0", doctor='echo "no config" >&2; exit 3')
    with pytest.raises(RuntimeError) as exc:
        RossDeriver(binary=binary, llm=True)
    assert "status 3" in str(exc.value)
    assert "no config" in str(exc.value), "ross's own stderr must be surfaced"


# --- Part B: the model actually used --------------------------------------


def test_ross_model_records_the_llm_that_actually_answered(tmp_path, monkeypatch):
    """The hash is computed before the run; the response says what really
    answered. `ross_model` is that, so a row is attributable even if
    resolution moved between `--doctor` and the request."""
    d = _llm_deriver(tmp_path, monkeypatch, "deepreinforce-ai/Ornith-1.0-9B")
    img = tmp_path / "x.png"
    img.write_bytes(b"fake")
    md = d.derive(img).metadata
    assert md["ross_model"] == "deepreinforce-ai/Ornith-1.0-9B"
    assert md["derived"]["model"] == "ross", "the TOOL is still ross"


def test_ross_model_never_claims_the_llm_when_the_llm_failed(tmp_path):
    """ross stamps `model` only when the LLM call SUCCEEDED (it stamps the
    local tagger's label only when no endpoint is configured at all), so a
    kept-local-result-after-llm_error entry has no `model` — and this
    deriver must not invent one. The `llm_error` rides in `ross_warnings`,
    so the null is explicable from the same row."""
    d = RossDeriver(binary=_stub_ross(tmp_path, "exit 0"))
    md = d._build_result(
        {
            "type": "image",
            "description": "a knight",
            "tags": ["knight"],
            "llm_error": "endpoint returned 401",
        }
    ).metadata
    assert md["ross_model"] is None
    assert md["ross_warnings"] == ["endpoint returned 401"]
    assert md["ross_description"] == "a knight"


# --- cross-port -----------------------------------------------------------


def test_llm_params_match_the_rust_port(tmp_path):
    """Both ports must build the SAME params from the SAME `--doctor` bytes.

    The fixture carries the literal `--doctor` output, so each port replays
    identical input through its own parser rather than through two hand-kept
    copies of a stub script. Params and key sets are compared, never the
    derivation hash — see the fixture's own note on why.
    """
    section = _fixture()["rung5_llm_params"]
    doctor_stdout = section["doctor_stdout"]
    (tmp_path / "doctor.txt").write_text(doctor_stdout)
    binary = _stub_ross(
        tmp_path, "exit 0", doctor=f'cat "{tmp_path}/doctor.txt"'
    )
    d = RossDeriver(binary=binary, llm=True)
    assert d._params() == section["params"]
    assert sorted(d._params()) == sorted(section["params"])
    assert d.llm_endpoints == section["params"]["llm_endpoints"]


def _fixture() -> dict:
    return json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "rust" / "chunkshop" / "tests" / "parity-fixtures"
            / "media_derivation_reference.json"
        ).read_text()
    )


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
