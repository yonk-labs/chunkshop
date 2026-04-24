"""Speed gate (SC-003): semantic chunking wall time <= 2 * main embed wall time.

Uses the shipped `docs/samples/*-*.md` concatenated to ~5000 words. Runs the
chunker with its default dedicated boundary model (MiniLM int8). Measures the
main-cell embed time as what a real cell pipeline actually does: embedding N
chunks of ~max_chars size (what `hierarchy` + `sentence_aware` emit), NOT a
single-item `embed([full_text])` call — which would silently truncate to 512
tokens and yield a near-zero baseline that makes the ratio meaningless.

The brief's "FastembedProvider.embed() wall time for the same document with
the cell's default batch_size" is read as the pipeline comparison: how long
does the embedder spend on this doc in a normal chunker's output shape?
"""
from __future__ import annotations

import glob
import os
import time

import pytest


def _load_5k_word_text() -> str:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    # Sample corpus glob is *-*.md, NOT *.md — see CLAUDE.md. Avoids picking up
    # docs/samples/README.md and other non-corpus files.
    files = sorted(glob.glob(os.path.join(repo_root, "docs", "samples", "*-*.md")))
    combined = "\n\n".join(open(f).read() for f in files)
    words = combined.split()
    while len(words) < 5000:
        words += combined.split()
    return " ".join(words[:5000])


@pytest.mark.slow
def test_semantic_chunking_speed_gate():
    from fastembed import TextEmbedding

    import chunkshop.embedders  # registers int8 variants  # noqa: F401
    from chunkshop.chunkers.semantic import SemanticChunker
    from chunkshop.config import SemanticChunker as Cfg
    from chunkshop.sources.base import Document

    text = _load_5k_word_text()
    doc = Document(id="bench", content=text, title="bench", metadata={})

    # Main-cell embed time: simulate what a real cell does. A cell emits N
    # chunks of ~max_chars each (the default 2000), NOT one 30-KB string. A
    # single-item embed([full_text]) would silently truncate to 512 tokens so
    # most of the doc is never embedded — the baseline would be ~one forward
    # pass, the semantic chunker runs ~300 forward passes, and the ratio
    # becomes meaningless (~16x) even though the pipeline impact is fine.
    # We chunk the text into 2000-char blocks on word boundaries and embed
    # them in one batched call — that's what `hierarchy`/`sentence_aware`
    # followed by FastembedProvider.embed() actually costs at cell-run time.
    CHUNK_CHARS = 2000
    words = text.split()
    chunks_text: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for w in words:
        if cur_len + len(w) + 1 > CHUNK_CHARS and cur:
            chunks_text.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
        else:
            cur.append(w)
            cur_len += len(w) + 1
    if cur:
        chunks_text.append(" ".join(cur))

    main_model = TextEmbedding(model_name="Xenova/bge-base-en-v1.5-int8", threads=2)
    _ = list(main_model.embed(["warmup"]))
    t0 = time.perf_counter()
    _ = list(main_model.embed(chunks_text))
    main_embed_time = time.perf_counter() - t0

    # Warm the MiniLM boundary model so the benchmark measures steady-state chunk time.
    boundary = TextEmbedding(
        model_name="sentence-transformers/all-MiniLM-L6-v2-int8", threads=2
    )
    _ = list(boundary.embed(["warmup"]))

    chunker = SemanticChunker(Cfg(type="semantic"))
    t0 = time.perf_counter()
    chunks = chunker.chunk(doc)
    semantic_time = time.perf_counter() - t0

    ratio = semantic_time / main_embed_time if main_embed_time > 0 else float("inf")
    print(
        f"\n[SC-003 speed gate] main embed (bge-base-int8): {main_embed_time:.2f}s, "
        f"semantic chunk (MiniLM-int8): {semantic_time:.2f}s, ratio: {ratio:.2f}x"
    )
    assert len(chunks) > 1, "benchmark doc should produce more than one chunk"
    assert semantic_time <= 2 * main_embed_time, (
        f"SC-003 speed gate FAILED: semantic chunking took {semantic_time:.2f}s "
        f"vs ceiling of {2 * main_embed_time:.2f}s (2x main embed). "
        f"ratio={ratio:.2f}x"
    )
