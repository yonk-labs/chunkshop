"""Unit tests for spacy_entities extractor.

Skips cleanly if `[spacy]` extra isn't installed. SC-002: ≥3 fixture inputs
with known entities, whitelist filtering enforced.
"""
from __future__ import annotations

import pytest

pytest.importorskip("spacy")

from chunkshop.config import SpacyEntitiesExtractor
from chunkshop.extractors import load_extractor


APPLE_TEXT = (
    "Apple Inc. acquired Beats Electronics in 2014. "
    "Tim Cook announced the deal in Cupertino."
)

POLITICS_TEXT = (
    "Barack Obama served two terms as President of the United States. "
    "He was succeeded by Donald Trump in January 2017."
)

TECH_TEXT = (
    "Microsoft announced a new partnership with OpenAI in Redmond. "
    "Satya Nadella framed the deal as strategic for Azure customers."
)


@pytest.fixture(scope="module")
def extractor():
    # Module-scoped so the spaCy model loads once — auto-download on first run.
    return load_extractor(SpacyEntitiesExtractor(type="spacy_entities"))


def test_spacy_extracts_org_and_person_apple(extractor):
    r = extractor.extract(APPLE_TEXT)
    assert r.tags == []
    entities = r.metadata["entities"]
    assert "ORG" in entities
    assert isinstance(entities["ORG"], list)
    assert any("Apple" in o for o in entities["ORG"])
    assert "PERSON" in entities
    assert any("Tim" in p or "Cook" in p for p in entities["PERSON"])


def test_spacy_extracts_person_politics(extractor):
    r = extractor.extract(POLITICS_TEXT)
    entities = r.metadata["entities"]
    assert "PERSON" in entities
    persons = " ".join(entities["PERSON"]).lower()
    assert "obama" in persons or "trump" in persons


def test_spacy_extracts_org_tech(extractor):
    r = extractor.extract(TECH_TEXT)
    entities = r.metadata["entities"]
    assert "ORG" in entities
    orgs = " ".join(entities["ORG"]).lower()
    # At least one of Microsoft / OpenAI / Azure should land as ORG
    assert any(o in orgs for o in ("microsoft", "openai", "azure"))


def test_spacy_whitelist_filters_labels():
    ex = load_extractor(
        SpacyEntitiesExtractor(type="spacy_entities", label_whitelist=["PERSON"])
    )
    r = ex.extract("Apple Inc. was founded by Steve Jobs in 1976.")
    entities = r.metadata["entities"]
    # PERSON should be retained; ORG and DATE must be filtered out.
    assert "PERSON" in entities
    assert "ORG" not in entities
    assert "DATE" not in entities


def test_spacy_empty_input_returns_empty_entities(extractor):
    r = extractor.extract("")
    assert r.tags == []
    assert r.metadata["entities"] == {}


def test_spacy_dedupes_mentions(extractor):
    r = extractor.extract("Apple Inc. grew. Apple Inc. reported earnings. Apple Inc. paid dividends.")
    orgs = r.metadata["entities"].get("ORG", [])
    # 'Apple Inc.' appears 3 times in input but dedupe should leave 1 occurrence.
    apple_mentions = [o for o in orgs if "Apple" in o]
    assert len(apple_mentions) == 1
