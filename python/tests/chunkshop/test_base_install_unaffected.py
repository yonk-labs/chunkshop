import importlib
import subprocess
import sys

import pytest


def test_importing_chunkshop_downloads_no_model():
    """NN-6: nothing may download at import time."""
    code = (
        "import sys; import chunkshop.sources.derivers.clip_tags as m; "
        "assert 'fastembed' not in sys.modules, 'fastembed imported at module import'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_deriver_module_imports_without_touching_models():
    m = importlib.import_module("chunkshop.sources.derivers.clip_tags")
    d = m.ClipTagsDeriver(vocabulary=["a cat"])
    assert d._vision is None, "models must be lazy until derive() is called"


def test_pyproject_declares_media_extras():
    import tomllib
    from pathlib import Path

    root = Path(__file__).parents[2] / "pyproject.toml"
    data = tomllib.loads(root.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert "ocr" in extras and "audio" in extras and "media" in extras
    assert extras["vision"] == [], "CLIP needs no extra — fastembed is already core"


def test_no_media_library_entered_core_dependencies():
    import tomllib
    from pathlib import Path

    root = Path(__file__).parents[2] / "pyproject.toml"
    data = tomllib.loads(root.read_text(encoding="utf-8"))
    core = " ".join(data["project"]["dependencies"]).lower()
    for banned in ("rapidocr", "faster-whisper", "torch", "transformers", "av>="):
        assert banned not in core, f"{banned} must live in an extra, not core"
