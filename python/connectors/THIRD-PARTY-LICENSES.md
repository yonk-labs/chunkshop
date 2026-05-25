# Third-party licences

`chunkshop-connectors` bundles code adapted from upstream open-source
projects. This file documents each upstream and reproduces its licence
text in full.

## 1. Onyx (formerly Danswer) — MIT Expat

Upstream: https://github.com/onyx-dot-app/onyx

The connector implementations under `src/chunkshop_connectors/` (excluding
`_adapt.py`, `_tier.py`, and chunkshop-authored `__init__.py` scaffolds)
were originally written by the Onyx project and lifted into RAGFlow's
`common/data_source/` tree under the same MIT Expat licence terms that
Onyx ships with. We preserve the upstream attribution headers verbatim in
each lifted file.

```
MIT License (Expat)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 2. RAGFlow — Apache-2.0 (carrier of the Onyx-MIT data_source subtree)

Upstream: https://github.com/infiniflow/ragflow

RAGFlow's overall licence is Apache-2.0, but its `common/data_source/`
directory carries a *file-scoped* MIT Expat licence with Onyx attribution
preserved in its `__init__.py`. **We lift from that subtree only.** No
Apache-licensed RAGFlow code is included here.

The RAGFlow Apache-2.0 licence text is available in the upstream
repository at `LICENSE`. We do not redistribute it because we do not
distribute any Apache-2.0-licensed RAGFlow code; we lift only the
MIT-headered files documented above.

For exact upstream commit SHA and audit date see
`src/chunkshop_connectors/_PROVENANCE.md`.
