# CLIP is already inside chunkshop — verified 2026-09-01

## The finding
`fastembed` is a CORE, non-optional dependency (`python/pyproject.toml:24`, `fastembed>=0.3`;
0.8.0 installed). It already ships BOTH CLIP towers plus late-interaction multimodal models.

`ImageEmbedding.list_supported_models()`:
| model | dim | GB |
|---|---|---|
| Qdrant/clip-ViT-B-32-vision | 512 | 0.34 |
| Qdrant/resnet50-onnx | 2048 | 0.10 |
| Qdrant/Unicom-ViT-B-16 | 768 | 0.82 |
| Qdrant/Unicom-ViT-B-32 | 512 | 0.48 |
| jinaai/jina-clip-v1 | 768 | 0.34 |

`TextEmbedding.list_supported_models()` — the QUERY-side towers, same vector space:
| model | dim | GB |
|---|---|---|
| **Qdrant/clip-ViT-B-32-text** | 512 | 0.25 |
| jinaai/jina-clip-v1 | 768 | 0.55 |

`LateInteractionMultimodalEmbedding.list_supported_models()` — page-image retrieval, no OCR:
| model | dim | GB |
|---|---|---|
| Qdrant/colpali-v1.3-fp16 | 128 | 6.5 |
| Qdrant/colmodernvbert | 128 | 1.0 |

## Rust parity: ALREADY ENABLED
`rust/chunkshop/Cargo.toml:104`
```toml
fastembed = { version = "5.13.1", default-features = false,
              features = ["ort-download-binaries-native-tls", "image-models"], optional = true }
```
`image-models` is already on. No new crate, no new feature flag needed for the Rust port.

## Live verification (CPU, this box)
```
vision load 4.6s (one-time) | 2 imgs 0.06s  -> ~30ms/image
text load   3.4s (one-time) | 2 queries 0.01s -> ~5ms/query
image dim (512,)  text dim (512,)

                   red.png  blue.png
a red square        0.2772    0.2523   <- correct
a blue square       0.2305    0.3031   <- correct
CROSS-MODAL RETRIEVAL WORKS: True
```
**Caveat (do not overclaim):** flat colour squares are near-OOD for CLIP; margins of 0.02-0.07
prove the PLUMBING works, NOT retrieval quality on real sprites. A real-corpus NDCG/recall test
is required — this becomes a phase-4 Success Criterion, not an assumption.

## Why this matters to the spec
It dismantles the phase-1 consult's strongest argument against multimodal embedders. Fable's case
was: "text->image retrieval needs the CLIP text tower inside `chunkshop search`, in every
implementation, forever — that drags a multimodal runtime into three languages." False here: the
text tower is 250MB of ONNX loading through the SAME ORT runtime chunkshop already uses for
bge-int8, and the Rust crate already has the flag on. No torch, no transformers, no new dep.

## Throughput comparison (for 113,973 assets, gamelib's real corpus size)
| Path | per image | full corpus | key/GPU needed |
|---|---|---|---|
| CLIP via fastembed | ~30 ms | **~57 min single-threaded** | none |
| BLIP local CPU (gamelib actual, batch=16) | 450 ms | ~14 h (its 9-h run crashed at 52%) | none, but torch |
| Remote VLM (192.168.1.133:8000, no-think) | ~1.2 s | ~13 h @ concurrency 6 | endpoint |

## The capability split (answers "BLIP/CLIP or 3rd-party tool?")
| | CLIP: image -> vector | BLIP-style: image -> text |
|---|---|---|
| Dependency | ALREADY INSTALLED (core) | torch+transformers ~2GB, or remote VLM |
| Cost/image | ~30ms CPU, free, offline | 450ms local / ~1.2s remote / cloud $ |
| API key | **no** | yes (unless heavy local dep) |
| Rust/Go parity | already flagged on | HTTP ports; torch does not |
| Gives you | semantic + cross-modal search | grep-able content, FTS, metadata, graph/agent input |
| Cannot | be read, quoted, or fed to an LLM | be cheap |

=> CLIP: use it, no third party. Captioning: opt-in, remote/third-party.
