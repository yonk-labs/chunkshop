"""Cross-language derivation-hash parity, enforced by CI.

Mirror of ``rust/chunkshop/tests/hash_parity.rs``. Both files assert the
SAME constants against their own port's shipped code, so a drift in either
turns exactly one suite red instead of both staying green.

``scripts/hash_parity_probe.py`` and ``examples/hash_parity_probe.rs`` print
these values, but a probe with no assertions that nothing invokes is not a
check: the ``min_score`` f32/f64 divergence shipped while both probes ran
green, because the Rust probe hand-wrote an f64 JSON literal the production
path never produced.

``derivation_hash``'s ``engine`` component legitimately differs across ports
("python" vs "rust"), so every case pins a fixed synthetic
(rung, model, version, engine) tuple: any difference can then only come from
params canonicalization, which must NOT differ.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chunkshop.config import ClipTagsDeriverConfig
from chunkshop.sources.derivers.cache import DerivationCache
from chunkshop.sources.derivers.provenance import derivation_hash

RUNG = "clip_tags"
MODEL = "Qdrant/clip-ViT-B-32"
VERSION = "1.2.3"
ENGINE = "fastembed-rs"

_PROBE_ROOT = Path("/probe-root-unused")


def _h(params: dict) -> str:
    return derivation_hash(RUNG, MODEL, VERSION, ENGINE, params)


def _relative_key_path(content_hash: str, deriv_hash: str, extra: str | None = None) -> str:
    full = DerivationCache(root=_PROBE_ROOT)._path(content_hash, deriv_hash, extra)
    return str(full.relative_to(_PROBE_ROOT))


@pytest.mark.parametrize(
    "label,params,expected",
    [
        (
            "clip",
            {"top_k": 3, "min_score": 0.2, "vocabulary": ["a cat", "a dog"]},
            "eba57f98976d4411953d28abf83fea07474a458a14f434006e849bdf11850333",
        ),
        (
            # key-order independence: same content, different literal order
            "reord",
            {"vocabulary": ["a cat", "a dog"], "min_score": 0.2, "top_k": 3},
            "eba57f98976d4411953d28abf83fea07474a458a14f434006e849bdf11850333",
        ),
        (
            # json.dumps defaults to ensure_ascii=True; the shipped
            # canonicalizer must pass ensure_ascii=False to agree with
            # serde_json's raw UTF-8.
            "nonascii",
            {"top_k": 3, "vocabulary": ["café", "猫"]},
            "db2ad85f884587c546d0e93a5929ec60bd3aef1c0adde1c039ad916427dfb90f",
        ),
        (
            "quote",
            {"text": 'she said "hello"'},
            "f529dc6cc68239f2662589b5e07813db5fdce79596d82d4576b689be8e89ae59",
        ),
        (
            "backslash",
            {"path": "C:\\Users\\test"},
            "1beb4c9d5f4dc489677edaa9581b2942049cba490f2c4af16c8dca22008d3dd6",
        ),
        (
            "control",
            {"text": "line one\nline two\ttabbed"},
            "fa7eb09d9b698f1b2f4f50ffaf1fd4d69be4f0ded56ab79560329ff50afe964f",
        ),
        (
            "emoji",
            {"text": "launch \U0001F680 party \U0001F389"},
            "96575b79196cba8800c208d044092ac1b55a25ea4daa67975da69cdf463cbb22",
        ),
    ],
)
def test_params_canonicalization_matches_rust(label, params, expected):
    assert _h(params) == expected, f"params case {label} diverged"


def test_empty_params_hash_matches_rust():
    assert (
        derivation_hash("image_meta", "deterministic", "1", "rust", {})
        == "cb43b14c693ffdd721e0b974d86387717658461bcf31e970890d86cf13305d91"
    )


@pytest.mark.parametrize(
    "label,content,deriv,extra,expected",
    [
        (
            "simple", "c1", "d1", None,
            "c0/c025b0ee86847c86d25f74d8016c44c3db6d52378e26b5679ffeda0c9f98090a.json",
        ),
        (
            "empty", "", "", None,
            "96/96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7.json",
        ),
        (
            "nonascii", "café", "猫", None,
            "47/4710d2edf99c3298915df9374ddb41b07de179742d37f9a836669d4041375796.json",
        ),
        (
            "shalike",
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
            "e159c4e35c8cf84439ecaf417477d67316c5607a367f773753ae76c54cdf0d1d",
            None,
            "af/af799e370d018d0108143371ee19f309d4f674599ddaf2236baddf5674ddf08d.json",
        ),
        (
            # A deriver that IS path-dependent contributes a third component.
            "extra", "c1", "d1", "a chart revenue",
            "03/033455ba3ef8e3338f24e29cb3a1a2bd3f2d52aa4543ff7c6d1067d64cc219e1.json",
        ),
    ],
)
def test_cache_key_paths_match_rust(label, content, deriv, extra, expected):
    assert _relative_key_path(content, deriv, extra) == expected, f"cache-key case {label} diverged"


def test_shipped_clip_tags_params_canonicalize_like_rust():
    """The params object the SHIPPED ClipTagsDeriver actually produces.

    Every default is read off the constructed deriver rather than retyped as
    a literal, so this is the case that catches a numeric-width divergence:
    Rust held ``min_score`` as ``f32``, which canonicalizes the shipped
    default ``0.20`` as ``0.20000000298023224`` once serde widens it to f64.
    """
    from chunkshop.sources.derivers import load_deriver

    deriver = load_deriver(
        ClipTagsDeriverConfig(rung="clip_tags", vocabulary=["a cat", "a dog"])
    )
    params = {
        "vocabulary": deriver.vocabulary,
        "top_k": deriver.top_k,
        "min_score": deriver.min_score,
    }
    assert (
        json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        == '{"min_score":0.2,"top_k":3,"vocabulary":["a cat","a dog"]}'
    )
    assert (
        derivation_hash("clip_tags", MODEL, VERSION, ENGINE, params)
        == "eba57f98976d4411953d28abf83fea07474a458a14f434006e849bdf11850333"
    )
