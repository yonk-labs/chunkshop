import importlib.util

import pytest

from chunkshop.config import LedeReportExtractor as Cfg
from chunkshop.extractors import load_extractor
from chunkshop.extractors.lede_report import LedeReportExtractor


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _has_spacy_model(name: str = "en_core_web_sm") -> bool:
    if not _has("spacy"):
        return False
    import spacy

    try:
        spacy.load(name)
        return True
    except Exception:
        return False


TEXT = """# Snyder v. United States

**Docket Number:** 23-108
**Citation:** 603 U.S. ___ (2024)
**Term:** 2023
**Petitioner:** James E. Snyder
**Respondent:** United States of America

## Decision

Federal law, 18 U.S.C. §666, proscribes bribes to state and local officials
but does not make it a crime for those officials to accept gratuities for their
past acts. Justice Ketanji Brown Jackson authored a dissenting opinion, in
which Justices Sonia Sotomayor and Elena Kagan joined.
"""


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_lede_report_extracts_structured_facts():
    result = LedeReportExtractor(
        Cfg(type="lede_report", max_chars=1200, max_facts=10)
    ).extract(TEXT)

    report = result.metadata["lede_report"]
    assert report["key_facts"]
    assert report["attributes"]["term"]["value"] == "2023"
    assert report["attributes"]["docket_number"]["value"] == "23-108"
    assert any(pc["path"] == "lede_report.attributes.term.value" for pc in report["promotion_candidates"])
    assert "Ketanji Brown Jackson" in report["search_text"]
    assert "2023" in report["metadata"]["dates"]
    assert any("2023" == tag for tag in result.tags)
    assert any("23-108" == tag for tag in result.tags)
    assert any("Docket Number" in fact for fact in report["key_facts"])


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
@pytest.mark.skipif(not _has_spacy_model(), reason="no spaCy model installed")
def test_lede_report_spacy_backend_adds_entities():
    result = LedeReportExtractor(
        Cfg(type="lede_report", backend="spacy", max_chars=1200, max_facts=10)
    ).extract(TEXT)

    report = result.metadata["lede_report"]
    assert "Ketanji Brown Jackson" in result.tags
    assert "Ketanji Brown Jackson" in report["spacy_metadata"]["entities"]


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_lede_report_is_loadable_from_config():
    extractor = load_extractor(Cfg(type="lede_report"))
    assert isinstance(extractor, LedeReportExtractor)
    assert extractor.cfg.max_chars == 4000


@pytest.mark.skipif(not _has("lede"), reason="lede not installed")
def test_lede_report_tag_sources_can_be_limited():
    result = LedeReportExtractor(
        Cfg(type="lede_report", tag_sources=("dates",), max_facts=10)
    ).extract(TEXT)

    assert "2023" in result.tags
    assert "23-108" not in result.tags
    assert all("Ketanji" not in tag for tag in result.tags)
