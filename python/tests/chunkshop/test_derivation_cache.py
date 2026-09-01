from chunkshop.sources.derivers.cache import DerivationCache, cached_derive
from chunkshop.sources.parsers.result import ParseResult


def test_miss_then_hit(tmp_path):
    cache = DerivationCache(root=tmp_path)
    assert cache.get("c1", "d1") is None
    cache.put("c1", "d1", ParseResult(text="cat", metadata={"k": 1}))
    got = cache.get("c1", "d1")
    assert got is not None
    assert got.text == "cat"
    assert got.metadata == {"k": 1}


def test_different_derivation_hash_is_a_miss(tmp_path):
    """Changing the prompt/model/params must re-derive, never serve stale."""
    cache = DerivationCache(root=tmp_path)
    cache.put("c1", "d1", ParseResult(text="old"))
    assert cache.get("c1", "d2") is None


def test_different_content_hash_is_a_miss(tmp_path):
    cache = DerivationCache(root=tmp_path)
    cache.put("c1", "d1", ParseResult(text="old"))
    assert cache.get("c2", "d1") is None


def test_cached_derive_computes_once(tmp_path):
    cache = DerivationCache(root=tmp_path)
    calls = []

    def compute():
        calls.append(1)
        return ParseResult(text="expensive")

    r1, hit1 = cached_derive(cache, "c1", "d1", compute)
    r2, hit2 = cached_derive(cache, "c1", "d1", compute)

    assert r1.text == r2.text == "expensive"
    assert hit1 is False and hit2 is True
    assert len(calls) == 1, "second call must not recompute"


def test_purge_empties_the_cache(tmp_path):
    cache = DerivationCache(root=tmp_path)
    cache.put("c1", "d1", ParseResult(text="x"))
    cache.put("c2", "d1", ParseResult(text="y"))
    assert cache.purge() == 2
    assert cache.get("c1", "d1") is None


def test_corrupt_entry_is_treated_as_a_miss(tmp_path):
    cache = DerivationCache(root=tmp_path)
    cache.put("c1", "d1", ParseResult(text="x"))
    for f in tmp_path.rglob("*.json"):
        f.write_text("{not json", encoding="utf-8")
    assert cache.get("c1", "d1") is None
