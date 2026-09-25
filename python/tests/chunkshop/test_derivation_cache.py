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


def test_purge_and_stats_ignore_non_entry_shaped_files(tmp_path):
    """GAP-002: `rglob('*.json')` matched ANY .json anywhere under root,
    so a mis-set cache root (e.g. pointed at a project checkout) would
    delete files like `package.json` on `purge -y`. Only the real cache
    layout — `<root>/<2 hex>/<64 hex>.json` — is chunkshop's; everything
    else sharing the root, at any depth, must survive both stats() and
    purge()."""
    cache = DerivationCache(root=tmp_path)
    cache.put("c1", "d1", ParseResult(text="real entry"))

    foreign_top = tmp_path / "package.json"
    foreign_top.write_text("{}")
    nested = tmp_path / "nested" / "foo.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("{}")
    # Same directory depth as a real entry, but not hash-shaped.
    wrong_shape = tmp_path / "ab" / "not-a-hash.json"
    wrong_shape.parent.mkdir(parents=True, exist_ok=True)
    wrong_shape.write_text("{}")

    count, _total = cache.stats()
    assert count == 1

    removed = cache.purge()
    assert removed == 1
    assert cache.get("c1", "d1") is None
    assert foreign_top.exists()
    assert nested.exists()
    assert wrong_shape.exists()


def test_purge_sweeps_orphaned_tmp_files_in_entry_shaped_dirs(tmp_path):
    """A crash between `mkstemp` and `os.replace` in `put()` leaves a
    `*.tmp` sibling in the same `<2 hex>/` subdirectory as a real entry.
    `purge()` should sweep those too, without counting them as entries."""
    cache = DerivationCache(root=tmp_path)
    cache.put("c1", "d1", ParseResult(text="real entry"))

    entry_dir = next((tmp_path).glob("*/*.json")).parent
    orphan_tmp = entry_dir / "tmpabc123.tmp"
    orphan_tmp.write_text("half-written")

    count, _total = cache.stats()
    assert count == 1, "orphan .tmp must not be counted as an entry"

    removed = cache.purge()
    assert removed == 1
    assert not orphan_tmp.exists()


def test_corrupt_entry_is_treated_as_a_miss(tmp_path):
    cache = DerivationCache(root=tmp_path)
    cache.put("c1", "d1", ParseResult(text="x"))
    for f in tmp_path.rglob("*.json"):
        f.write_text("{not json", encoding="utf-8")
    assert cache.get("c1", "d1") is None


def test_non_dict_json_is_treated_as_a_miss(tmp_path):
    """Valid JSON that isn't a dict (null, number, string, array) must be a miss."""
    cache = DerivationCache(root=tmp_path)

    # Store a valid entry first to establish the file
    cache.put("c1", "d1", ParseResult(text="original"))

    # Corrupt with various non-dict JSON payloads
    for invalid_payload in ["null", "42", '"x"', "[]"]:
        cache.put("c1", "d1", ParseResult(text="placeholder"))
        for f in tmp_path.rglob("*.json"):
            f.write_text(invalid_payload, encoding="utf-8")
        # Should degrade to miss, not raise TypeError
        assert cache.get("c1", "d1") is None
