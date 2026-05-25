def test_package_imports():
    import chunkshop_connectors
    assert chunkshop_connectors.__doc__  # attribution block present


def test_attribution_present():
    import chunkshop_connectors
    import pathlib

    init = pathlib.Path(chunkshop_connectors.__file__).read_text()
    assert "Onyx" in init and "MIT" in init
