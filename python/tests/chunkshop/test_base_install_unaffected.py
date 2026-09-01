import importlib
import re
import subprocess
import sys


def _extract_package_name(requirement_string: str) -> str:
    """Extract and normalize package name from a requirement string.

    PEP 503 normalization: lowercase, replace _ with -.

    Examples:
        "av>=13" → "av"
        "av==13.0" → "av"
        "av~=13.0" → "av"
        "av" → "av"
        "psycopg[binary]>=3.2" → "psycopg"
        "chunkshop[pdf]" → "chunkshop"
    """
    # Match up to the first version specifier, bracket, space, or marker
    match = re.match(r'^([a-zA-Z0-9._-]+)', requirement_string.strip())
    if not match:
        return ""
    name = match.group(1)
    # Normalize: lowercase, _ → -
    return name.lower().replace('_', '-')


def test_extract_package_name_handles_version_specs():
    """Helper extracts names correctly across version-specifier styles."""
    assert _extract_package_name("av>=13") == "av"
    assert _extract_package_name("av==13.0") == "av"
    assert _extract_package_name("av~=13.0") == "av"
    assert _extract_package_name("av") == "av"
    assert _extract_package_name("psycopg[binary]>=3.2") == "psycopg"
    assert _extract_package_name("chunkshop[pdf]") == "chunkshop"
    assert _extract_package_name("numpy>=1.26") == "numpy"
    # Legitimate packages that contain "av" must NOT trip the check
    assert _extract_package_name("av-something") == "av-something"


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
    core_deps = data["project"]["dependencies"]

    # Banned package names (PEP 503 normalized: lowercase, _ → -)
    banned = {"rapidocr", "faster-whisper", "torch", "transformers", "av", "pillow-simd"}

    for req_string in core_deps:
        pkg_name = _extract_package_name(req_string)
        assert pkg_name not in banned, (
            f"{pkg_name} (from {req_string!r}) must live in an extra, not core"
        )
