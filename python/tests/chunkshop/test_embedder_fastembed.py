import numpy as np
from chunkshop.config import FastembedEmbedder
from chunkshop.embedders import load_embedder
from chunkshop.embedders.fastembed_provider import _purge_incomplete_model_cache


def test_purge_incomplete_model_cache_wipes_only_poisoned_dirs(tmp_path, monkeypatch):
    # #80: an interrupted download leaves a *.incomplete blob; the self-heal must
    # delete that model dir (so it re-downloads) and leave healthy dirs alone.
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    poisoned = tmp_path / "models--qdrant--bge-base-en-v1.5-onnx-q" / "blobs"
    poisoned.mkdir(parents=True)
    (poisoned / "4e55.incomplete").write_bytes(b"")
    healthy = tmp_path / "models--BAAI--bge-small-en-v1.5" / "blobs"
    healthy.mkdir(parents=True)
    (healthy / "abcd").write_bytes(b"real weights")

    assert _purge_incomplete_model_cache() is True
    assert not (tmp_path / "models--qdrant--bge-base-en-v1.5-onnx-q").exists()
    assert (tmp_path / "models--BAAI--bge-small-en-v1.5").exists()
    # Idempotent: nothing poisoned left, so a second call is a no-op.
    assert _purge_incomplete_model_cache() is False


def test_purge_incomplete_model_cache_missing_root_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "does-not-exist"))
    assert _purge_incomplete_model_cache() is False


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


def test_threads_parameter_caps_ort_session():
    # threads=1 must be plumbed into fastembed so ORT's intra_op_num_threads
    # is capped. Without this, 4 concurrent workers on a shared box end up
    # running 4 × num_cpus threads each and thrashing.
    cfg = FastembedEmbedder(
        type="fastembed", model_name="BAAI/bge-small-en-v1.5", dim=384, threads=1
    )
    emb = load_embedder(cfg)
    arr = emb.embed(["hello"])  # smoke
    assert arr.shape == (1, 384)

    # Walk fastembed's internals to find the ORT session and confirm the cap.
    # Layout varies by fastembed version; tolerate missing attrs and fall
    # back to the smoke assertion above if we can't introspect.
    def _find_intra_op(obj, depth: int = 0):
        if depth > 4 or obj is None:
            return None
        for attr in ("_sess_options", "sess_options", "session_options"):
            opts = getattr(obj, attr, None)
            if opts is not None and hasattr(opts, "intra_op_num_threads"):
                return opts.intra_op_num_threads
        for attr in ("model", "_model", "session", "_session", "ort_session"):
            nxt = getattr(obj, attr, None)
            if nxt is not None:
                v = _find_intra_op(nxt, depth + 1)
                if v is not None:
                    return v
        return None

    found = _find_intra_op(emb._model)
    if found is not None:
        assert found == 1, f"expected intra_op_num_threads=1, got {found}"
