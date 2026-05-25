"""Attribution CI guard.

All lifted-from-RAGFlow files under ``_base/`` exist because
chunkshop is honouring the Onyx MIT licence. These tests fail loudly
if the attribution scaffolding is accidentally removed during a
refactor — better to break the build than to ship a package that
quietly strips its upstream credit.
"""
from __future__ import annotations

from pathlib import Path

BASE = Path(__file__).parents[1] / "src/chunkshop_connectors/_base"


def test_notice_file_exists():
    """`NOTICE` must exist at the package root and credit upstream."""
    notice = BASE.parents[2] / "NOTICE"
    assert notice.exists(), "NOTICE file missing from package root"
    txt = notice.read_text()
    assert "Onyx" in txt or "MIT" in txt
    assert "RAGFlow" in txt or "infiniflow" in txt


def test_third_party_licenses_exists():
    """`THIRD-PARTY-LICENSES.md` must exist and reference MIT."""
    f = BASE.parents[2] / "THIRD-PARTY-LICENSES.md"
    assert f.exists(), "THIRD-PARTY-LICENSES.md missing from package root"
    assert "MIT" in f.read_text()


def test_provenance_records_upstream_sha():
    """`_PROVENANCE.md` must pin the upstream RAGFlow commit SHA."""
    f = BASE.parent / "_PROVENANCE.md"
    assert f.exists(), "_PROVENANCE.md missing from chunkshop_connectors/"
    assert "ed179ce" in f.read_text(), "upstream RAGFlow SHA missing or changed"
