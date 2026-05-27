# Bundled fact-extractor consolidators (`lede`, `lede_spacy`)

**Chunker**: `consolidation` (`chunkshop.chunkers.consolidation.ConsolidationChunker`)
**Modes**: `chunkshop.config.LedeConsolidator`, `chunkshop.config.LedeSpacyConsolidator`
**Impls**: `chunkshop.consolidators.lede_facts`, `chunkshop.consolidators.lede_spacy_facts`
**Ship status**: verified (0.7.0)
**Optional extras**: `chunkshop[lede]` (`lede` mode) / `chunkshop[lede-spacy]` + a spaCy model (`lede_spacy` mode)

## Purpose

The `consolidation` chunker turns an episode (the concatenation of a
document's base chunks) into **one episode chunk** plus **N atomic fact
chunks**. Each fact is a `kind='fact'` row carrying
`subject` / `predicate` / `object` / `support_span` / `confidence`, linked
back to its episode via `metadata.source_chunk_seq`.

The consolidator slot decides *how* facts get extracted. 0.7.0 adds two
first-class **bundled fact-extractor modes** alongside the existing
`mode: callable` (bring-your-own) and `mode: passthrough` (baseline,
zero facts):

- **`mode: lede`** — salient-sentence propositions. Each lede-selected
  sentence becomes one fact's `support_span`; `subject/predicate/object`
  are left null. Sparse but cheap and dependency-light.
- **`mode: lede_spacy`** — dependency-parsed subject/predicate/object
  triples from those salient sentences.

Both decouple the **fact extractor** from the **episode summary** (the
`summarizer:` slot) — that separation is the design point. You can extract
SVO triples while summarizing with `lede`, the new `caveman` reducer, an
external field, or nothing at all.

## What each mode emits

### `mode: lede`

`chunkshop.consolidators.lede_facts.extract_facts` runs lede over the
episode, splits the returned summary into sentences, and emits one fact per
sentence (up to `max_facts`):

| Fact field      | Value |
|-----------------|-------|
| `support_span`  | The lede-selected sentence. |
| `subject`       | `None`. |
| `predicate`     | `None`. |
| `object`        | `None`. |
| `confidence`    | **Rank-decay** in `[0, 1]`: `round(1.0 - i/n, 3)` where `i` is the sentence's rank and `n` the count. First sentence is most confident. |

These are propositions, not triples — useful when you want salient spans
back without committing to a parse.

### `mode: lede_spacy`

`chunkshop.consolidators.lede_spacy_facts.extract_facts` runs lede first,
then parses each salient sentence with spaCy and pulls one triple per
sentence:

| Fact field      | Value |
|-----------------|-------|
| `support_span`  | The salient sentence. |
| `predicate`     | The root-verb **lemma** (root must be a `VERB`/`AUX`; sentences with no verb root are skipped). |
| `subject`       | First `nsubj`/`nsubjpass` child of the root, else `None`. |
| `object`        | First direct/copular object (`dobj`/`obj`/`attr`/`acomp`) child of the root; failing that, a one-hop prepositional object (ROOT → `prep` → `pobj`); else `None`. |
| `confidence`    | **Heuristic**: `1.0` for a full SVO triple (subject *and* object present), `0.6` for a partial (subject-only or object-only). Sentences with neither subject nor object are skipped. |

These triples are **noisy candidates, not clean typed edges**. spaCy's
parse is statistical; predicates are raw verb lemmas, not a controlled
vocabulary, and the object heuristic deliberately over-captures
prepositional and copular complements. Treat them as a recall-oriented
extraction layer to filter downstream, not as a knowledge graph.

## Config schema

Both modes are pydantic v2 models with `extra="forbid"`, dispatched on the
`mode` discriminator inside `consolidator:`.

### `LedeConsolidator` (`mode: lede`)

| Field             | Type      | Default | Notes |
|-------------------|-----------|---------|-------|
| `mode`            | `Literal["lede"]` | **Required** | Discriminator. |
| `summarizer`      | `SummarizerConfig?` | `None` | Optional summary slot — see below. |
| `confidence_floor`| `float`   | `0.0`   | `[0, 1]`. Drops facts below the floor before embedding. |
| `max_facts`       | `int`     | `10`    | `>= 1`. Cap on facts emitted per episode. |

### `LedeSpacyConsolidator` (`mode: lede_spacy`)

| Field             | Type      | Default | Notes |
|-------------------|-----------|---------|-------|
| `mode`            | `Literal["lede_spacy"]` | **Required** | Discriminator. |
| `summarizer`      | `SummarizerConfig?` | `None` | Optional summary slot — see below. |
| `confidence_floor`| `float`   | `0.0`   | `[0, 1]`. Drops facts below the floor before embedding. |
| `max_facts`       | `int`     | `20`    | `>= 1`. Cap on facts emitted per episode. |
| `model`           | `str`     | `"en_core_web_sm"` | spaCy model name passed to `spacy.load(...)`. |

The enclosing `ConsolidationChunker` adds `fact_max_chars` (default `1200`,
`>= 1`): fact `support_span`s longer than this are **truncated** (stamping
`metadata.truncated: true`), never split — splitting would break the
proposition.

## The summarizer slot

`summarizer:` is an optional `SummarizerConfig` that fills the **episode
summary** completely independently of the fact extractor. It accepts the
same three modes documented in [`summaries.md`](../summaries.md):

- `mode: callable` — e.g. `module: chunkshop.summarizers.lede`, or the new
  `chunkshop.summarizers.caveman` reducer, or your own function.
- `mode: external` — pull a precomputed summary off the document metadata.
- `mode: passthrough` — summary equals the episode text.

When `summarizer:` is **omitted**, the bundled extractor returns an empty
summary string, and the chunker falls back to embedding the raw episode
text (`embedded_content = result["summary"] or episode_text`). So you can
run a fact extractor with no summary cost at all.

This decoupling is deliberate: *what* gets summarized and *how* facts get
extracted are separate knobs.

## `confidence_floor` — a storage lever

Facts whose `confidence` is below `confidence_floor` are **dropped before
embedding**, in the consolidator itself — fewer fact rows written, fewer
vectors stored. A null confidence coerces to `0.0`, so any floor `> 0`
drops unscored facts (the bundled extractors never emit null; this only
matters for BYO callable consolidators).

Note this is the **write-time** floor and differs from
`chunkshop fact-search --confidence-floor` (read-time), which *keeps*
null-confidence rows. Use `confidence_floor` here to control how many
fact vectors land in the table; use the read-time flag to filter what a
query returns.

## Confidence is per-extractor, NOT cross-calibrated

The two extractors produce confidence on **different scales**:

- `lede` — rank-decay (`1.0` for the first sentence, decaying toward `0`).
- `lede_spacy` — a two-value heuristic (`1.0` full SVO, `0.6` partial).

These numbers are **not comparable across modes**. A `0.6` from
`lede_spacy` does not mean the same thing as a `0.6` from `lede`. Pick one
extractor per corpus and calibrate `confidence_floor` against that
extractor's distribution; don't mix-and-match thresholds.

## Extras gating

| Mode          | Required install | Also needs |
|---------------|------------------|------------|
| `lede`        | `chunkshop[lede]` | — |
| `lede_spacy`  | `chunkshop[lede-spacy]` | A downloaded spaCy model: `python -m spacy download en_core_web_sm` |

```bash
cd chunkshop/python
# lede mode
uv sync --extra lede
# lede_spacy mode (extra + model)
uv sync --extra lede-spacy
uv run --no-sync python -m spacy download en_core_web_sm
```

Imports are lazy — chunkshop core never imports lede or spaCy unless your
YAML selects the mode. A missing extra raises an actionable
`RuntimeError` ("Install it and the model and retry.") rather than failing
silently.

## Example: `lede` propositions, no summary

```yaml
chunker:
  type: consolidation
  base:
    type: sentence_aware
    doc_type: prose
  consolidator:
    mode: lede
    max_facts: 8
    confidence_floor: 0.3
```

This emits one episode chunk (embedding the raw episode text, since no
`summarizer:` is set) plus up to 8 fact rows whose rank-decay confidence
is at least `0.3`.

## Example: `lede_spacy` triples + caveman-reduced summary

```yaml
chunker:
  type: consolidation
  base:
    type: sentence_aware
    doc_type: prose
  consolidator:
    mode: lede_spacy
    model: en_core_web_sm
    max_facts: 20
    confidence_floor: 0.6        # keep only full SVO triples
    summarizer:
      mode: callable
      module: chunkshop.summarizers.caveman
      function: summarize
```

Here the episode summary is the caveman-reduced episode text, while facts
are dependency-parsed SVO triples — and the `0.6` floor keeps only full
triples (drops subject-only / object-only partials).

## How fact rows land in the table

The chunker writes both `original_content` and `embedded_content` of each
fact row as the (possibly truncated) `support_span`. Per-fact metadata:

| `metadata` key      | Value |
|---------------------|-------|
| `kind`              | `"fact"`. |
| `subject` / `predicate` / `object` | The extracted triple parts (may be null). |
| `support_span`      | The sentence (also the row's content). |
| `confidence`        | Per-extractor confidence (see above). |
| `truncated`         | `true` if `support_span` exceeded `fact_max_chars`. |
| `source_chunk_seq`  | The episode chunk's `seq_num` — links fact → episode. |
| `consolidator` / `extractor` | The mode string (`"lede"` / `"lede_spacy"`). |

The episode chunk itself carries `metadata.kind = "episode"`. (On a
consolidator failure the chunker degrades to a single passthrough episode
chunk with `metadata.consolidation_error` set — it never raises, so one
poisoned document can't abort a nightly cell.)

## Reading facts back

Fact rows are excluded from `chunkshop search` by default (pass
`--include-facts` to mix them in). To query facts directly — by
`subject`/`predicate`/`object` and a read-time confidence floor — use the
dedicated subcommand:

```bash
chunkshop fact-search --config cell.yaml --query "<query>"
```

See the `fact-search` command reference for its full flag set
(`--confidence-floor`, `--summary`, `--k`, `--json`). The key thing to keep
in mind from this page: the write-time `confidence_floor` decides which
facts ever get stored; `fact-search --confidence-floor` then filters what
you read back from whatever was stored.

## Tests proving the contract

- `tests/chunkshop/test_consolidator_lede_facts.py` — rank-decay
  confidence, `max_facts` cap, null SVO fields.
- `tests/chunkshop/test_consolidator_lede_spacy_facts.py` — SVO triple
  extraction, `1.0`/`0.6` heuristic, prepositional/copular object capture,
  verbless-sentence skip.
- Chunker-level tests for `kind='fact'` rows, `source_chunk_seq` linkage,
  `fact_max_chars` truncation, and O4 degrade-on-failure.

## See also

- [`summaries.md`](../summaries.md) — the summarizer contract and the
  `caveman` reducer.
- `docs/reference/cli-fact-search.md` — reading facts back (if present).
- The `consolidation` chunker and the agent-memory SP-A design spec for
  the episode/fact data model.
