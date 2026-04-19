import numpy as np
from chunkshop.config import FastembedEmbedder
from chunkshop.embedders import load_embedder


def test_bge_small_embeds_to_384_dim():
    cfg = FastembedEmbedder(type="fastembed", model_name="BAAI/bge-small-en-v1.5", dim=384, batch_size=2)
    emb = load_embedder(cfg)
    assert emb.dim == 384
    arr = emb.embed(["hello", "world"])
    assert arr.shape == (2, 384)
    assert arr.dtype == np.float32
    # fastembed normalizes by default; vectors should be close to unit norm
    norms = np.linalg.norm(arr, axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=0.05)


def test_embed_empty_list_returns_zero_rows():
    cfg = FastembedEmbedder(type="fastembed", model_name="BAAI/bge-small-en-v1.5", dim=384)
    emb = load_embedder(cfg)
    arr = emb.embed([])
    assert arr.shape == (0, 384)
    assert arr.dtype == np.float32


def test_dim_mismatch_raises():
    # Lying about dim should be caught at embed time
    cfg = FastembedEmbedder(type="fastembed", model_name="BAAI/bge-small-en-v1.5", dim=999)
    emb = load_embedder(cfg)
    import pytest
    with pytest.raises(ValueError, match="dim"):
        emb.embed(["probe"])
