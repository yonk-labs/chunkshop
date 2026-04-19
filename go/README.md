# chunkshop-go

Planned Go implementation. Not yet started.

Goal: bit-exact parity with the Python reference implementation — same YAML config, same
chunkers, same ONNX models (via [`onnxruntime_go`](https://github.com/yalue/onnxruntime_go))
+ HF tokenizer bindings, same pgvector target table.

Will be published as a Go module once the Python MVP has been verified end-to-end against
the scotus factorial experiment.
