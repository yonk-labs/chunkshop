# Bakeoff report: ntsb_bakeoff (sample run, committed)

> **Note:** This is the actual leaderboard from one verified run of
> `bakeoff-ntsb.yaml` against the NTSB corpus. It's committed so you can
> see what the bakeoff produces without running it yourself. Re-running on
> your machine should give the same ordering (the embedders are
> deterministic) but cosine values can drift by ~1e-3 from ORT-binary
> noise. The sibling `sample-recommended.yaml` is the runnable cell for
> the top combo.
>
> The CLI also writes a non-committed copy to
> `skill-output/bakeoff/ntsb_bakeoff/report.md` on every run.

- Run: 2026-04-29 12:29:15
- Corpus: /home/yonk/yonk-tools/pg-raggraph/benchmarks/kg-rag-eval/extracted/ntsb/*.md
- Queries: 12
- Combos: 12

## Leaderboard (sorted by MRR)

| # | Chunker | Embedder | r@1 | r@3 | r@5 | MRR |
|---|---|---|---|---|---|---|
| 1 | `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 1.000 | 1.000 | 0.958 |
| 2 | `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 1.000 | 1.000 | 0.958 |
| 3 | `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | 0.917 | 1.000 | 1.000 | 0.944 |
| 4 | `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.917 | 0.917 | 1.000 | 0.938 |
| 5 | `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | 0.917 | 0.917 | 1.000 | 0.933 |
| 6 | `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | 0.833 | 1.000 | 1.000 | 0.903 |
| 7 | `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | 0.833 | 0.917 | 1.000 | 0.896 |
| 8 | `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | 0.833 | 0.917 | 0.917 | 0.861 |
| 9 | `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | 0.833 | 0.917 | 0.917 | 0.861 |
| 10 | `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | 0.750 | 0.917 | 1.000 | 0.836 |
| 11 | `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | 0.750 | 0.750 | 0.833 | 0.771 |
| 12 | `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | 0.667 | 0.667 | 0.833 | 0.700 |

## Per-query detail (top-1 hit per combo)

| Chunker | Embedder | Query | Gold | Top-1 | MRR |
|---|---|---|---|---|---|
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20071229X02007` | 1.000 |
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20071231X02009` | 1.000 |
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080102X00005` | 0.500 |
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `hierarchy` | `nomic-ai/nomic-embed-text-v1.5-Q` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20071229X02007` | 1.000 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20071231X02009` | 1.000 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080102X00005` | 0.500 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `sentence_aware` | `nomic-ai/nomic-embed-text-v1.5-Q` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20080111X00041` | 0.333 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20071231X02009` | 1.000 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080104X00020` | 1.000 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20071229X02007` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20071231X02009` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080109X00035` | 0.250 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `nomic-ai/nomic-embed-text-v1.5-Q` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20080111X00041` | 0.200 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20071231X02009` | 1.000 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080104X00020` | 1.000 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20080116X00055` | 0.500 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20071231X02009` | 1.000 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080102X00005` | 0.333 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20080116X00055` | 0.500 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20071231X02009` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080102X00005` | 0.250 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20080116X00055` | 0.333 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20071231X02009` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20071231X02009` | 0.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20080116X00061` | 0.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20071231X02009` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080104X00020` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080117X00064` | 0.333 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20071229X02007` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20080111X00041` | 0.500 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20080111X00041` | 0.333 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080109X00035` | 0.200 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `nomic-ai/nomic-embed-text-v1.5-Q` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20080116X00061` | 0.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20080111X00041` | 0.250 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080115X00053` | 0.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20080115X00053` | 0.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20080111X00041` | 0.200 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080109X00035` | 0.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080115X00053` | 0.200 |
| `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |

## Statistical power

12 queries means one query flipping moves aggregate recall by 0.083. Combos within ~0.17 of the leader are not reliably distinguishable. Re-run with more queries or a larger corpus before treating the leaderboard as a tournament result.
