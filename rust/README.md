# chunkshop-rs

Planned Rust implementation. Not yet started.

Goal: bit-exact parity with the Python reference implementation — same YAML config, same
chunkers, same ONNX models (via the [`ort`](https://crates.io/crates/ort) crate) + HF
[`tokenizers`](https://crates.io/crates/tokenizers) crate, same pgvector target table.

Will be published to crates.io once the Python MVP has been verified end-to-end against
the scotus factorial experiment.
