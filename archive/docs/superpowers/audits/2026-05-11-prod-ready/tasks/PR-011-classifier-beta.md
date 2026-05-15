# PR-011 — Bump Python classifier from "3 - Alpha" to "4 - Beta"

**Priority:** P3
**Effort:** XS (~5 min)
**Dependencies:** none
**GAP-IDs:** GAP-017

## Problem

`python/pyproject.toml` still lists `Development Status :: 3 - Alpha`. By PyPI maturity stages (3=Alpha, 4=Beta, 5=Production/Stable), chunkshop v0.4.0 — with 349 tests, 4-backend matrix, dual-language parity, cross-language vector round-trips — is meaningfully beyond Alpha.

## Solution

Edit `python/pyproject.toml`:

```diff
 classifiers = [
-    "Development Status :: 3 - Alpha",
+    "Development Status :: 4 - Beta",
     ...
 ]
```

Also update the README's status badge:

```diff
-[![status: alpha](https://img.shields.io/badge/status-alpha-orange)](python/pyproject.toml)
+[![status: beta](https://img.shields.io/badge/status-beta-yellow)](python/pyproject.toml)
```

## Acceptance Criteria

- [ ] `pyproject.toml` classifier updated.
- [ ] README badge updated.
- [ ] On next PyPI release, chunkshop shows the Beta classifier.

## Risk if Skipped

User expectations stay miscalibrated. Some users skip Alpha-classified packages on principle.
