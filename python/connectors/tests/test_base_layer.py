def test_interfaces_import():
    from chunkshop_connectors._base import interfaces as I

    # The Onyx interface hierarchy — names confirmed against the lifted file at exec.
    for name in ("BaseConnector", "CheckpointedConnector", "LoadConnector"):
        assert hasattr(I, name), f"missing {name}"


def test_no_ragflow_internal_imports():
    import pathlib
    import re

    base = pathlib.Path(__file__).parents[1] / "src/chunkshop_connectors/_base"
    bad = re.compile(r"^\s*from\s+(api|rag|common)\.|^\s*import\s+(api|rag)\b", re.M)
    offenders = [p.name for p in base.glob("*.py") if bad.search(p.read_text())]
    assert not offenders, f"unrewritten RAGFlow imports in: {offenders}"


def test_no_anthropic_basemodel_bug():
    import pathlib

    txt = (
        pathlib.Path(__file__).parents[1]
        / "src/chunkshop_connectors/_base/interfaces.py"
    ).read_text()
    assert "from anthropic import BaseModel" not in txt
