# KB Template Samples

These samples describe recommended KB shapes for evaluation and production
planning. They are intentionally separate because a KB/namespace is a retrieval
contract: it bundles corpus shape, metadata, indexes, vector metric, and default
context profile.

Phase D showed why this matters:

- SCOTUS-like legal documents favored document-level summaries and facts.
- MHR-like news/multi-hop corpora favored chunk summaries plus raw evidence.
- Code/doc corpora need their own path/symbol/language metadata and should not
  inherit prose defaults blindly.

The profile fields shown here are the intended first-class product surface.
Until profile support lands in the CLI, treat these as target configs for the
evaluation harness and implementation plan, not guaranteed runnable ingest
YAML.

| Sample | Use when |
|---|---|
| [`scotus-legal-doc.yaml`](scotus-legal-doc.yaml) | Documents are self-contained legal/opinion/policy records. |
| [`mhr-news-multihop.yaml`](mhr-news-multihop.yaml) | Answers often require evidence from multiple news/source documents. |
| [`codebase.yaml`](codebase.yaml) | Corpus is source code, APIs, docs, READMEs, or generated references. |
| [`generic-balanced.yaml`](generic-balanced.yaml) | Mixed or unknown prose corpus; good starting point, not a final default. |

## Rule of Thumb

Create a separate KB when any of these differ:

- metadata worth promoting;
- common question type;
- chunking needs;
- search/filter strategy;
- context-packing winner;
- latency or token budget;
- required evaluation workload.

Mixed KBs are allowed, but the profile should be marked `auto` or
`generic-balanced`, and the evaluation report must break out results by corpus
family. Aggregate accuracy alone can hide a regression in one family.

