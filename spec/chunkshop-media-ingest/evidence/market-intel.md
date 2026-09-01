# Competitive landscape — multimodal RAG ingest (researched 2026-09-01)

## Tier 1

| Tool | Image handling | Key/GPU needed for images | Audio | Assembled quickstart? |
|---|---|---|---|---|
| **LlamaIndex** | (d) `ClipEmbedding`/`MultiModalEmbedding` **and** (c) GPT-4V captioning — two disconnected paths, user wires both | CLIP path: no key, GPU optional. Caption path: OpenAI key | Whisper reader documented as calling the **OpenAI Whisper API** (cloud). Local path UNVERIFIED | ✗ must wire 2 vector stores + MultiModalVectorIndex |
| **Unstructured.io** | (b) OCR (Tesseract) default; (c) VLM captioning opt-in, docs example = GPT-4o. **No CLIP option at all** | OCR free/local; VLM path needs key (local provider UNVERIFIED) | none found — document/image-centric | ✗ it's an ETL/partitioning layer, hands off downstream |
| **IBM Docling** | (c) VLM captioning only (local Granite Vision / SmolVLM-256M, or remote). **No CLIP anywhere** | free local path exists; disabled by default (cost) | **strong** — real local Whisper (PyTorch/MLX/CTranslate2), 6 sizes, no key, CPU ok | ✗ converter only, no indexing/search |
| **LangChain** | split: `UnstructuredImageLoader` → OCR; `langchain_experimental.open_clip.OpenCLIPEmbeddings` → (d) local CLIP | OpenCLIP: no key, GPU optional | external loaders (Whisper API/AssemblyAI); local path UNVERIFIED | ✗ hand-assemble loader + embedder + store |
| **Haystack (deepset)** | (d) **first-class**: `SentenceTransformersDocumentImageEmbedder`, default `sentence-transformers/clip-ViT-B-32`, + `ImageFileToDocument`, `DocumentTypeRouter` | no key for public HF models, CPU ok — **but** needs separate `sentence-transformers-haystack` install | not addressed in the multimodal tutorial | ✗ headline tutorial defaults to **GPT-4o-mini + OpenAI key**, burying the free CLIP path |

## Tier 2 (selected)
- **txtai** — ⭐ **closest existing thing to chunkshop's wedge**: local CLIP image+text index, no key, CPU-capable, with an actual short "similarity search with images" tutorial.
- **ColPali / byaldi** — (e) genuine local late-interaction page-image retrieval, no key (`pip install byaldi`), but needs poppler/pdf2image and a real GPU for practical throughput. Scoped to page images, not photo folders.
- **Morphik** — ColPali-style, self-hostable, but docs push the managed cloud free tier as the quick path.
- **Ragie** — SaaS-only, no local/OSS path. Multimodal + timestamped audio answers.
- **Marker** — PDF→Markdown (Surya OCR); extracts images as files, only captions them in explicit "LLM mode".
- **Cognee / Pathway / R2R** — VLM-captioning lineage (Pathway templates default to GPT-4o). R2R's mechanism UNVERIFIED.
- **llmware** — OCR only. **embedchain** — archived, folded into Mem0. **Chonkie** — text chunking, no image embedding.
- **RAG-Anything (HKUDS)** — OSS, integrates CLIP/BLIP *and* VLM captioning; git-clone install, no pypi quickstart.

## Synthesis

**A. Is "no key, no GPU, images searchable in 5 min" a gap?**
**A gap in PACKAGING, not in components.** The building blocks are documented in ≥4 places
(Haystack's image embedder, LangChain OpenCLIP, LlamaIndex ClipEmbedding, txtai). txtai is closest.
Haystack has the most production-grade component but its headline tutorial leads with an OpenAI key.
**Nobody markets "point at a folder → 5-minute cross-modal search, zero config" as the pitch.**

**B. CLIP-direct vs caption-then-embed — which is common, which wins?**
Caption-then-embed is the more common *default* (Unstructured, Docling, Cognee, Pathway, R2R all
lead with VLM captioning). CLIP-direct is the minority path. On raw retrieval, CLIP/SigLIP-family
dual-encoders **beat** VLM embedding models — SigLIP-2 400M scores 85.7/94.9 T→I/I→T on Flickr30K
vs VLM2Vec-7B at 79.8/91.6. Bigger VLM ≠ better retrieval.
⭐ **NO published head-to-head of "CLIP-embed-raw-image" vs "VLM-caption-then-text-embed" in a
RAG-specific setting exists.** chunkshop's bakeoff would produce novel, citable data — the same
move that produced its `hierarchy` chunker finding.

**C. Diagram-heavy PDFs — late-interaction or captioning?**
Late-interaction (ColPali/ColQwen/Nemotron ColEmbed) leads on ViDoRe but the field is NOT solved:
ViDoRe V3 (Feb 2026) top model **Nemotron ColEmbed V2 (8B) = 63.42 NDCG@10**; top late-interaction
models all stay **under 65%**, weakest on multi-hop/open-ended/non-textual queries. Avoids OCR and
handles layout natively, but is compute/storage-heavy (multi-vector per page) and **needs a GPU** —
which cuts directly against a no-GPU wedge if adopted wholesale.

**D. Provenance — CONFIRMED FIELD-WIDE GAP.**
None of Docling's or Unstructured's enrichment docs state that output metadata records which VLM
model/version produced a caption or embedding. Broader literature treats re-embedding on model
upgrade and staleness invalidation as aspirational, not shipped. **Nobody in Tier 1 or Tier 2
visibly does this.** Precedent inside chunkshop: it already treats provenance as a schema-level
concern for code edges (`code_edges.provenance` CHECK constraint separating `'ast'`/`'heuristic'`/
future `'scip'`; see `docs/superpowers/plans/2026-05-29-p2-python-heuristic-provenance-tag.md`).

**E. Strongest objection to "we CLIP your images, no key needed" — TAKE THIS SERIOUSLY:**
> *CLIP is bad at exactly the images that appear in real corpora — dense text-in-image,
> screenshots, and diagrams/charts — which is precisely why late-interaction and VLM-captioning
> exist as separate lineages.*
- CLIP compresses a whole image to ONE vector; it cannot do "find the paragraph in this screenshot"
  the way ColPali's per-patch MaxSim can.
- CLIP cannot read text in an image. A scanned bill retrieves on visual gestalt, not on its words.
- CLIP's training distribution is natural photos + web alt-text; diagrams/charts/screenshots are
  its weakest domain — per the ViDoRe findings above.
- Silently re-embedding with a new CLIP checkpoint changes search behaviour with no audit trail
  (which is objection D turned against us — and is answered only if provenance ships).

## Unverified / open
- LangChain + LlamaIndex local-offline audio paths (both documented as cloud API).
- Whether Unstructured's VLM enrichment supports a fully local provider.
- R2R's actual image pipeline (CLIP vs VLM).
