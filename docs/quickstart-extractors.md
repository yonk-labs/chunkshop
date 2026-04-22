# Quickstart: metadata extractor recipes

Pick an extractor from the decision tree, paste the YAML, run `chunkshop ingest`.
For the full reference (config fields, behavior, edge cases), see
[`extractors.md`](extractors.md). For the narrative walkthrough with a real
Postgres and SQL queries, see [`tutorial-metadata.md`](tutorial-metadata.md).

## Decision tree

| Your goal                                                      | Pick                                              |
|-----------------------------------------------------------------|---------------------------------------------------|
| Cheap keyword tags on every chunk                               | `rake_keywords`                                   |
| High-quality topic labels for a search UI                       | `keybert_phrases`                                 |
| Filter retrievals by ORG / PERSON / GPE                         | `spacy_entities` + promote `entities.ORG`         |
| Multilingual corpus — route queries by language                 | `lang_detect` + promote `language`                |
| All of the above in one ingest pass                             | `composite` chaining them                         |

## Recipes

Each recipe is copy-paste runnable against `docs/samples/handbook-*.md` (the
sample corpus that ships with chunkshop). Swap `source.glob` for your own
corpus.

### `rake_keywords` — cheap keyword tags

**Use when** you want a flat `tags[]` column of keyword phrases with zero model
download.
**What this produces** — `tags: ["section heading", "civil rights", ...]`;
no structured metadata.

```yaml
# rake.yaml
cell_name: rake_demo
source: { type: files, glob: "docs/samples/handbook-*.md", id_from: stem }
chunker: { type: hierarchy }
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
extractor:
  type: rake_keywords
  top_k: 8
target:
  dsn_env: CHUNKSHOP_DSN
  schema: mydata
  table: rake_chunks
  mode: create_if_missing
  source_tag: rake_demo
```

```bash
uv sync --extra extractors
chunkshop ingest --config rake.yaml
```

---

### `keybert_phrases` — embedding-quality topic labels

**Use when** phrase quality matters (public search UI, analyst facets).
**What this produces** — `tags: ["pgvector", "vector databases", ...]`; no
structured metadata. Needs ~90 MB MiniLM download on first run.

```yaml
# keybert.yaml
cell_name: keybert_demo
source: { type: files, glob: "docs/samples/handbook-*.md", id_from: stem }
chunker: { type: hierarchy }
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
extractor:
  type: keybert_phrases
  top_k: 8
  keyphrase_ngram_range: [1, 3]    # unigrams → trigrams
target:
  dsn_env: CHUNKSHOP_DSN
  schema: mydata
  table: keybert_chunks
  mode: create_if_missing
  source_tag: keybert_demo
```

```bash
uv sync --extra keybert
chunkshop ingest --config keybert.yaml
```

---

### `spacy_entities` — filter by ORG / PERSON / GPE

**Use when** you want to answer "all chunks mentioning Apple" via SQL before
vector search.
**What this produces** — `metadata.entities = {"ORG": [...], "PERSON": [...]}`,
promoted to `entities__org text[]`, `entities__person text[]` for indexing.

```yaml
# spacy.yaml
cell_name: spacy_demo
source: { type: files, glob: "docs/samples/handbook-*.md", id_from: stem }
chunker: { type: hierarchy }
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
extractor:
  type: spacy_entities
  label_whitelist: [ORG, PERSON, GPE]
target:
  dsn_env: CHUNKSHOP_DSN
  schema: mydata
  table: spacy_chunks
  mode: create_if_missing
  source_tag: spacy_demo
  promote_metadata:
    - { path: entities.ORG, type: "text[]" }
    - { path: entities.PERSON, type: "text[]" }
```

```bash
uv sync --extra spacy
chunkshop ingest --config spacy.yaml

# Example query:
psql "$CHUNKSHOP_DSN" -c \
  "SELECT doc_id, \"entities__org\" FROM mydata.spacy_chunks
   WHERE \"entities__org\" IS NOT NULL LIMIT 5;"
```

---

### `lang_detect` — language code + confidence

**Use when** your corpus has more than one language, or you need a
data-quality signal.
**What this produces** — `metadata.language = "en" | "fr" | ...`, confidence
float, promoted to `language text` column.

```yaml
# lang.yaml
cell_name: lang_demo
source: { type: files, glob: "docs/samples/handbook-*.md", id_from: stem }
chunker: { type: hierarchy }
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
extractor:
  type: lang_detect
target:
  dsn_env: CHUNKSHOP_DSN
  schema: mydata
  table: lang_chunks
  mode: create_if_missing
  source_tag: lang_demo
  promote_metadata:
    - { path: language, type: text }
```

```bash
uv sync --extra lang
chunkshop ingest --config lang.yaml

# Example query:
psql "$CHUNKSHOP_DSN" -c \
  "SELECT language, COUNT(*) FROM mydata.lang_chunks GROUP BY language;"
```

---

### `composite` — chain them all

**Use when** you want entities, language, and keyphrases on every row in one
ingest pass.
**What this produces** — merged metadata dict from every child; concatenated
tags; promoted columns drawn from each child's output.

```yaml
# composite.yaml
cell_name: composite_demo
source: { type: files, glob: "docs/samples/handbook-*.md", id_from: stem }
chunker: { type: hierarchy }
embedder:
  type: fastembed
  model_name: Xenova/bge-small-en-v1.5-int8
  dim: 384
  threads: 4
extractor:
  type: composite
  extractors:
    - type: spacy_entities
      label_whitelist: [ORG, PERSON, GPE]
    - type: lang_detect
    - type: keybert_phrases
      top_k: 5
target:
  dsn_env: CHUNKSHOP_DSN
  schema: mydata
  table: composite_chunks
  mode: create_if_missing
  source_tag: composite_demo
  promote_metadata:
    - { path: entities.ORG, type: "text[]" }
    - { path: entities.PERSON, type: "text[]" }
    - { path: language, type: text }
```

```bash
uv sync --extra nlp       # umbrella: keybert + spacy + langdetect
chunkshop ingest --config composite.yaml
```

## What this replaces

Without chunkshop, the composite recipe above replaces roughly **30 lines of
custom NLP glue**:

```python
# The long way — what chunkshop's composite extractor replaces.
import spacy, langdetect
from keybert import KeyBERT
from langdetect import DetectorFactory

DetectorFactory.seed = 0
nlp = spacy.load("en_core_web_sm")
kb = KeyBERT("all-MiniLM-L6-v2")

def extract(text):
    # NER
    doc = nlp(text)
    entities = {}
    for ent in doc.ents:
        if ent.label_ not in {"ORG", "PERSON", "GPE"}:
            continue
        entities.setdefault(ent.label_, []).append(ent.text)
    entities = {k: list(dict.fromkeys(v)) for k, v in entities.items()}

    # Language
    try:
        candidates = langdetect.detect_langs(text)
        lang = candidates[0].lang if candidates else None
        conf = float(candidates[0].prob) if candidates else 0.0
    except Exception:
        lang, conf = None, 0.0

    # Keyphrases
    pairs = kb.extract_keywords(text, keyphrase_ngram_range=(1, 2), top_n=5)
    tags = [p for p, _ in pairs]

    return {
        "tags": tags,
        "metadata": {
            "entities": entities, "language": lang, "language_confidence": conf,
        },
    }
# ...and then wiring that into your chunking loop, your INSERT statement,
# your jsonb promotion, your column migration, your HNSW index...
```

The YAML above is 18 lines. That's the value prop.

## Cheatsheet

| Want to…                                            | Set                                                                     |
|-----------------------------------------------------|-------------------------------------------------------------------------|
| Filter by an ORG name at SQL                        | `spacy_entities` + `promote_metadata: [{path: entities.ORG, type: "text[]"}]` |
| Cluster by language                                 | `lang_detect` + `promote_metadata: [{path: language, type: text}]`       |
| Show a tag cloud in a UI                            | `keybert_phrases` (tags) — read `tags[]` column directly                |
| Populate all three at once                          | `composite` with `spacy_entities` + `lang_detect` + `keybert_phrases`   |
| GIN-index entity arrays                             | `CREATE INDEX ... USING gin ("entities__org");` after ingest            |

## Full reference + walkthrough

- Config fields, failure modes, and when-not-to-pick per extractor:
  [`extractors.md`](extractors.md).
- End-to-end narrative with Postgres, SQL verification, and realistic queries:
  [`tutorial-metadata.md`](tutorial-metadata.md).
