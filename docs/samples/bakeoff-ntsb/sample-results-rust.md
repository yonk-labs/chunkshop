# Bakeoff report: ntsb_bakeoff (Rust, sample run, committed)

> **Note:** This is the actual leaderboard from one verified run of
> `bakeoff-ntsb-rust.yaml` against the NTSB corpus, executed by
> `chunkshop-rs bakeoff`. It's committed so you can see what the Rust
> bakeoff produces without running it yourself.
>
> The Rust matrix is **8 combos** (2 BGE int8 embedders × 4 chunkers) —
> nomic isn't in the Rust embedder registry yet, so the Rust YAML drops
> it. `sample-results-python.md` (next to this file) shows the canonical
> 12-combo Python leaderboard. `scripts/parity_check_bakeoff.py` verifies
> that both implementations rank the 8 overlapping combos within ~2.5pp
> MRR and agree on ordering for distinct-MRR pairs.

- Run: 2026-04-29 18:54:52
- Corpus: /home/yonk/yonk-tools/pg-raggraph/benchmarks/kg-rag-eval/extracted/ntsb/*.md
- Queries: 12
- Combos: 8

## Leaderboard (sorted by MRR)

| # | Chunker | Embedder | r@1 | r@3 | r@5 | MRR |
|---|---|---|---|---|---|---|
| 1 | `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | 0.917 | 0.917 | 1.000 | 0.933 |
| 2 | `sentence_aware` | `Xenova/bge-base-en-v1.5-int8` | 0.917 | 0.917 | 1.000 | 0.933 |
| 3 | `sentence_aware` | `Xenova/bge-small-en-v1.5-int8` | 0.833 | 1.000 | 1.000 | 0.903 |
| 4 | `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | 0.833 | 0.917 | 0.917 | 0.875 |
| 5 | `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | 0.833 | 0.917 | 0.917 | 0.861 |
| 6 | `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | 0.833 | 0.833 | 0.917 | 0.850 |
| 7 | `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-small-en-v1.5-int8` | 0.750 | 0.750 | 0.833 | 0.771 |
| 8 | `fixed_overlap(window_words=300, step_words=150)` | `Xenova/bge-base-en-v1.5-int8` | 0.667 | 0.667 | 0.833 | 0.700 |

## Per-query detail (top-1 hit per combo)

| Chunker | Embedder | Query | Gold | Top-1 | MRR |
|---|---|---|---|---|---|
| `hierarchy` | `Xenova/bge-base-en-v1.5-int8` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20080111X00041` | 0.200 |
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
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080102X00005` | 0.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `hierarchy` | `Xenova/bge-small-en-v1.5-int8` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-base-en-v1.5-int8` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20080111X00041` | 0.000 |
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
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | elderly pilot accident on private grass airstrip with trees and utility wires | `20071229X02007` | `20080116X00055` | 0.200 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Piper PA-22 final approach short turf runway tree strike | `20071229X02007` | `20071229X02007` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Beech A23 hard landing porpoise nose gear collapse Death Valley | `20071231X02009` | `20071231X02009` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | nosewheel landing gear failed after bounced touchdown on runway 5 | `20071231X02009` | `20071231X02009` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | loss of directional control on landing rollway runway 21 Belen New Mexico | `20080102X00005` | `20080102X00005` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Cessna 172 Skyhawk landing on icy snow-covered runway in Wisconsin | `20080104X00020` | `20080104X00020` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | pilot completed three takeoffs and full stop landings before runway switch | `20080104X00020` | `20080117X00072` | 0.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Cessna 210 fatal crash into mountains southeast of Arteaga Mexico | `20080108X00029` | `20080108X00029` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | private pilot fatal accident XB-GTS Mesa de las Tablas Coahuila | `20080108X00029` | `20080108X00029` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Panama-registered Cessna 172 fatal mountain crash near Boquete Chiriqui | `20080108X00031` | `20080108X00031` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | Islas Secas departing flight three fatalities one serious injury | `20080108X00031` | `20080108X00031` | 1.000 |
| `neighbor_expand(window=1, base=hierarchy)` | `Xenova/bge-small-en-v1.5-int8` | amateur built Vans RV-8 left wing strike after going around at Latrobe | `20080109X00035` | `20080109X00035` | 1.000 |
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
