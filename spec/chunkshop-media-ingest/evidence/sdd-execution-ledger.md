# SDD ledger — plan: docs/superpowers/plans/2026-09-01-media-ingest-v1a-seam-spine-images.md

Spec: spec/chunkshop-media-ingest/SPEC.md (read; binding authority)
Worktree: /home/yonk/yonk-tools/chunkshop-media-v1a  branch: feat/media-ingest-v1a
Base commit: 740607f (docs: media ingest spec + v1-A implementation plan)
Venv: python/.venv ready (uv sync, all CI extras) — use `uv run --no-sync`

## Pre-flight conflict scan

### Task-pair rows (shared file or interface)
| Pair | Shared | Produces → Consumes | Finding |
|---|---|---|---|
| T1→T2 | parsers/base.py → files.py | `call_deriver`, `ParseResult` | clean |
| T1→T3 | parsers/base.py | `EmptyDerivationError` | clean |
| T2↔T3 | files.py `_document_for` | T2 rewrites body; T3 inserts empty-check after `call_deriver` | clean, ORDER-CRITICAL (T2 before T3) |
| T2↔T8 | files.py `_document_for` | T8 replaces only the `result = call_deriver(...)` line | clean |
| T3↔T8 | files.py `_document_for` | T8's replaced line sits ABOVE T3's empty-check, so the check survives | clean, ORDER-CRITICAL (T3 before T8) |
| T4↔T5 | derivers/ | T5 takes derivation_hash as an opaque str; does NOT import T4's fn | clean (T5's "Consumes" line overstates; harmless) |
| T4→T6,T7 | provenance.py | `stamp_provenance(result,*,rung,model_id,model_version,params)` | signatures match all call sites — clean |
| T6→T7 | image_meta.py | `ImageMetaDeriver`, `supported_extensions` reused | clean |
| T6,T7↔T8 | both deriver files | T8 adds `derivation_hash()` method to each | clean |
| T4↔T8 | derivers/__init__.py | T4 creates (docstring only); T8 appends `from __future__ import annotations` | legal — a future-import may follow a module docstring |
| T8↔T9 | config.py | T8 adds `FilesSource.derive`; T9's validator reads it | clean, ORDER-CRITICAL (T8 before T9) |
| T9↔T11 | CellConfig w/ sqlite target | both construct a full cell config | BOTH HIT DEFECT D1 (below) |
| T10↔all | pyproject.toml | extras only | clean |

### Per-task self-agreement rows
| Task | Tests vs code it specifies | Finding |
|---|---|---|
| T1 | 8 tests vs result.py + base.py additions | clean |
| T2 | 2 tests vs `_document_for` rewrite | clean |
| T3 | 5 tests vs on_empty + pdf + Step 4b incremental fix | clean |
| T4 | 5 tests vs provenance.py | clean |
| T5 | 6 tests vs cache.py | clean |
| T6 | 5 tests vs image_meta.py | clean |
| T7 | 4 tests (3 slow) vs clip_tags.py | clean |
| T8 | 5+2 tests vs config + loader + files wiring | minor: full-resync path now reads bytes twice (hash + parser). Perf only, not a defect. NOT blocking. |
| T9 | 6 tests vs DEFAULT_MEDIA_PROMOTIONS + validator | DEFECT D1 |
| T10 | 4 tests vs pyproject extras | clean |
| T11 | 3 tests + CI workflow vs sample YAML | DEFECTS D1 + D2 |

### Defects found and RULINGS (made before dispatch)

**D1 — plan's sqlite target block is not the real config shape.**
Plan writes `target: {type: sqlite, path: ./images.db, table: t, mode: overwrite}`.
Actual `TargetConfig` (config.py:1168-1178) requires `database` (alias for
`database_name`) and takes the DB path via `dsn_env`, not `path`. Working
reference: `docs/samples/sample-sqlite.yaml`.
**Ruling:** every sqlite target block in Tasks 9 and 11 uses the real shape:
```yaml
target:
  type: sqlite
  dsn_env: SQLITE_PATH
  database: ignored     # SQLite has no schema concept; required but unused
  table: images
  mode: overwrite
  hnsw: false           # sqlite-vec is brute-force KNN
```
*Cost if wrong:* the quickstart and the T9 config tests fail to validate —
caught immediately by T11's own `test_sample_yaml_is_valid_config`.

**D2 — plan's CI step uses a flag that does not exist.**
Plan writes `chunkshop search ... --limit 1 --json`. Actual CLI (cli.py:690)
has `-k` for result count; `--json` is correct (`as_json`).
**Ruling:** use `-k 1 --json`.
*Cost if wrong:* the CI step errors on an unknown option; trivially fixable.

**D3 — `promote_metadata` default confirmed compatible.**
`TargetConfig.promote_metadata: list[PromoteColumn] = Field(default_factory=list)`
(config.py:1178), so T9's `assert not cfg.target.promote_metadata` holds for a
text-only cell. No change needed. (Checked, not a defect.)

Both rulings are carried into the Task 9 and Task 11 dispatches.

## Task log

Pre-flight complete. Rulings D1/D2 applied to the plan and committed (7e86f1b);
task-9 and task-11 briefs regenerated from the corrected plan.

NOTE on Task 1 review base: 7e86f1b (docs-only) was committed AFTER Task 1 was
dispatched, so the review-package BASE must be read from `git log` when Task 1
reports — not assumed to be 740607f — or the reviewer will see the docs commit
mixed into the code diff.

Task 1: dispatched (sonnet) — ParseResult + backward-compatible Deriver protocol.
  Brief: task-1-brief.md  Report: task-1-report.md
Task 1: implemented (commit 01ada1a; 918 passed / 256 skipped; test_custom_parser_injection
  passed UNMODIFIED — the no-breaking-change guarantee holds).
Task 1: review — Spec COMPLIANT, quality Approved, 1 Important + 2 Minor.
  Important: no test asserts call_deriver prefers .derive() over .parse() when an object
    has BOTH. Implementation is correct (base.py:121) but the guarantee is unenforced.
  Minor (deferred): test_parse_result.py:206 mid-file import, `Deriver` imported unused.
  Minor (deferred): base.py:1-4 module docstring stale — still says only "parse(path)".
  Reviewer confirmed: no ruff/flake8/lint step in CI, so the unused import has no CI effect.
Task 1: fix round 1/5 (3 addressed, 0 open — precedence test added, import cleaned,
  docstring updated; commits 01ada1a..2a16df3). Re-review: no new breakage;
  test_files_dispatch.py untouched (binding constraint preserved).
Task 1: complete (commits 7e86f1b..2a16df3, review clean)

Task 2: dispatched (haiku — mechanical, complete code in brief) — FilesSource consumes
  ParseResult and merges deriver metadata. BASE 2a16df3.
Task 2: implemented (commit 54132eb; 2 new + 16 regression tests pass).
Task 2: review — Spec COMPLIANT, quality Approved, 0 Critical, 0 Important.
  Reviewer independently verified (not just from the report): the legacy bare-`parse()`
  path still works via call_deriver -> normalize_parse_result; and iter_changes_since
  (files.py:94) calls the same _document_for, with cursor_from safe because source_path
  is unconditionally last in the dict literal.
  Minor (deferred): no test covers a deriver raising through the new merge path
    (unchanged exposure from before; Task 1 owns the deriver error contract).
  Minor (deferred): only `source_path` is adversarially tested as a shadowed key;
    `parser` uses the identical mechanism but is untested.
Task 2: complete (commits 2a16df3..54132eb, review clean)

Task 3: dispatched (sonnet — multi-file, changes a default) — empty derivation becomes
  loud. BASE 54132eb.
Task 3: implemented (commit a849a5d; full suite 929 passed / 253 skipped, +5 from this
  task; NO pre-existing test needed modification — nothing in the suite relied on empty
  documents being indexed. Implementer correctly corrected my stale 918/256 baseline
  to the true pre-task tip of 924/253).
Task 3: review — Spec COMPLIANT (all 8 steps incl. 4b), quality Approved, 0 Critical,
  0 Important. Reviewer independently verified: only two _document_for callers exist
  (files.py:63, :102) and both handle None; on_empty default "error" unweakened;
  test_files_dispatch.py untouched; the blank-PDF test uses a real pypdf-generated file.
  Minor (deferred): EmptyDerivationError message names only on_empty="skip" as the
    escape hatch, not "index" — a user wanting legacy behaviour must find it in docs.
  KNOWN COST (not a defect, worth documenting): under on_empty="skip", a permanently
    empty file is re-read, re-hashed and re-skipped on EVERY incremental run forever,
    because cursor_from can only stamp a cursor entry for a Document that exists. This
    is the only self-consistent behaviour given the signature. Flag for docs + final review.
Task 3: complete (commits 54132eb..a849a5d, review clean)

Task 4: dispatched (haiku — 2 new self-contained files, complete code in brief) —
  derivation hash + provenance stamping. BASE a849a5d.
Task 4: implemented (commit 5ff2854; 5/5 pass).
Task 4: review — Spec COMPLIANT but quality NEEDS FIXES. 1 Important (plan-mandated) + 3 Minor.
  Important: `_params_hash`'s `json.dumps(..., default=str)` (provenance.py:12-14) both
    COLLIDES and DRIFTS. Reviewer demonstrated with running code:
      (a) Path("/foo/bar") and "/foo/bar" -> same digest 7727093baf6da5cd
      (b) a set-valued param -> different digest across 3 separate python3 processes
          (set iteration order is PYTHONHASHSEED-salted)
    No shipped test catches either — all four hash tests use only str/int.

Task 4: Ruling: the finding WINS; `default=str` is removed. It came from MY plan text, but
  the SPEC is the binding authority and `default=str` violates two criteria outright:
    - SC-7 requires that an unchanged re-run re-derives NOTHING. A digest that drifts
      per-process makes every cache lookup miss, so SC-7 can never hold in production.
    - SC-8 / NN-3 require that nothing derived is written without correct attribution.
      Two different params sharing a digest is wrong attribution by construction.
  Fix chosen (the laziest correct one): DROP the fallback so a non-JSON-safe param raises
  a clear TypeError naming the offending key, rather than silently colliding or drifting.
  Rejected: type-tagging or canonicalising exotic types — every real caller in this plan
  (ClipTagsDeriver: list[str]/int/float; ImageMetaDeriver: bool/int) already passes
  JSON-native values, so that would be speculative machinery for no live caller.
  COST IF WRONG: a future deriver that legitimately wants a Path or set in its params
  gets a loud error at first call and must serialise it itself — a visible, immediate,
  one-line fix, versus silent cache thrash or mis-attribution that nobody would notice.
  This must land BEFORE Task 5 builds the cache on top of it.
Task 4: fix round 1/5 (6 addressed, 0 open — default=str dropped; per-key re-raise names
  the offending param; 3 regression tests added incl. a monkeypatched CACHE_SCHEMA_VERSION
  test; 2 docstrings; commits 5ff2854..2890e93). Re-review: no new breakage, the 5 original
  tests untouched and their logic unchanged.
Task 4: complete (commits a849a5d..2890e93, review clean)

Task 5: dispatched (haiku — one new self-contained file, complete code in brief) —
  provenance-keyed derivation cache. BASE 2890e93.
Task 5: implemented (commit 565f66f; 6/6 pass).
Task 5: review — Spec COMPLIANT but quality NEEDS FIXES. 1 Important (plan-mandated) + 3 Minor.
  Important: cache.get() catches (OSError, ValueError, KeyError) but NOT TypeError. A cache
    file corrupted to valid-but-non-dict JSON (null / 42 / "x" / []) makes payload["text"]
    raise TypeError uncaught -> crashes the ingest, violating the brief's own hard property
    "corrupt or missing entries are misses, never errors". Inherited verbatim from my brief.
  Reviewer verified as GENUINELY correct (not just claimed): temp file is created in
    path.parent so os.replace is same-filesystem atomic; key uses a \x00 separator so the two
    hashes cannot collide by concatenation; the corrupt-entry test's glob really does hit the
    file get() opens; test_cached_derive_computes_once really counts invocations.

Task 5: Ruling: the finding WINS; broaden the except to include TypeError. The brief states
  the property ("corrupt entries are misses, never errors") and the code fails to deliver it,
  so the brief contradicts itself and the stated property is what SC-6 depends on — a cache
  that can crash an ingest makes "restart the command to resume" false.
  Fix chosen: add TypeError to the except tuple + a regression test corrupting an entry to
  non-dict JSON. Rejected: a full schema validation layer for cache entries — over-built for
  a file only chunkshop writes, and the except-tuple already expresses "any damage is a miss".
  COST IF WRONG: an exotic corruption shape outside the tuple still crashes; the blast radius
  is one ingest run with a loud traceback, and the cache is safe to delete.

Task 5: CARRY FORWARD TO TASK 8 (reviewer's un-verifiable item): SC-6 needs Task 8 to call
  cached_derive PER DOCUMENT inside the ingest loop, not batched. Verify at Task 8 review.
Task 5: fix round 1/5 (2 addressed, 0 open — TypeError added to the except tuple; new
  test_non_dict_json_is_treated_as_a_miss covers null/42/"x"/[]; commits 565f66f..79da446).
  Re-review: no new breakage; the pre-existing ValueError-path test unchanged, and the new
  test genuinely exercises the TypeError path the old one never reached.
Task 5: complete (commits 2890e93..79da446, review clean)

Task 6: dispatched (haiku — one new self-contained deriver, complete code in brief) —
  rung 0 Pillow image metadata. BASE 79da446.
Task 6: implemented (commit 18aef64; 5/5 pass).
Task 6: review — Spec ISSUES + quality NEEDS FIXES. 1 Important (plan-mandated) + 2 Minor.
  Important: "never returns empty text" is NOT actually guaranteed. humanise() strips all of
    [_\-.]; a separator-only stem (_.png, -.png, __-__.png) humanises to "". With
    path_segments=False the join yields text == "", which Task 3's FilesSource treats as a
    derivation FAILURE and raises — turning an ingestable file into a hard error. The brief's
    own never-empty test uses goblin_warrior.png with path_segments=True: the easy case.
    The implementer's self-review overclaimed this gate as passing.
  Reviewer verified as correct: PIL imported inside derive() (not top-level), all attribute
    reads inside the `with Image.open` block, params JSON-safe, every return path stamped.

Task 6: Ruling: the finding WINS; add a real fallback so text can never be empty. My brief's
  reference implementation had the gap and its test hid it.
  Fix chosen: when the humanised stem AND any path segments all yield nothing, fall back to a
  token built from facts the deriver already holds — the image format — e.g. "png image".
  Rejected: (a) a bare constant like "image" — true but content-free, and this text is what
  gets embedded and searched; (b) relaxing Task 3's raise-on-empty — that raise IS the fix for
  the scanned-PDF defect and must not be weakened to accommodate a deriver bug.
  COST IF WRONG: a separator-named image indexes on "png image" instead of failing loudly;
  that is a strictly better outcome than a crashed ingest, and the metadata columns
  (width/height/format) still carry the real facts.

Task 6: CARRY FORWARD TO TASK 8 (reviewer Minor): Image.open() exceptions on a corrupt or
  non-image file propagate raw from this deriver. Confirm the Task 8 wiring policies that,
  or that ParserError/EmptyDerivationError handling covers it.
Task 6: fix round 1/5 (4 addressed, 0 open — format-token fallback "png image"; 3 regression
  tests incl. one asserting the fallback does NOT fire for goblin_warrior.png; commits
  18aef64..850936e). Re-review hunted for a REMAINING counterexample and found none: traced
  fmt="" -> " image" -> strip -> "image", so the never-empty guarantee is total.
  Minor (deferred): test 3 covers "segments provide content" rather than "stem AND segments
    both empty"; reviewer judged the fallback logic sound for that case.
Task 6: complete (commits 79da446..850936e, review clean)

Pre-check for Task 7: the `slow` pytest marker IS registered in pyproject.toml:100-102
  ("slow: tests that download models or run long benchmarks") and already used in
  test_chunker_semantic.py / test_two_kb_pattern.py. No unregistered-marker warning risk.

Task 7: dispatched (sonnet — numpy math + lazy model loading + slow tests) — rung 1 CLIP
  zero-shot image tagging. This is the SC-1 wedge. BASE 850936e.
Task 7: implemented (commit 2f8a0ee; fast 1/1, slow 3/3 with REAL ONNX inference in 3.82s,
  models already cached. red.png/blue.png ranked correctly; no assertion weakened).
Task 7: review — Spec COMPLIANT, quality Approved, 0 Critical, 0 Important.
  Reviewer verified by reading control flow rather than trusting the report: no top-level
  fastembed/numpy import (the hardest constraint); vocabulary embedded exactly ONCE per
  instance via the _ensure_models guard; BOTH vector sides L2-normalised so min_score is a
  real cosine threshold; zero-tags degrades to base.text (non-empty by Task 6's contract).
  Minor (deferred -> folded into Task 8 since it touches the same file): clip_tags.py:33
    imports `Optional` from typing, never used.
  Minor (deferred): _ensure_models sets self._vision before self._label_vectors and the
    re-entry guard checks only _vision — a second thread could see a half-initialised
    instance. Reviewer grepped for concurrent call sites and found none (ingest is
    subprocess-per-cell, not threaded), so no live trigger. Note if a caller ever shares
    a deriver across threads.
Task 7: complete (commits 850936e..2f8a0ee, review clean)

Task 8: dispatched (sonnet — multi-file integration, the largest task) — config wiring +
  cache application. BASE 2f8a0ee. Carries 3 forward items (SC-6 per-document caching,
  corrupt-image error policy, unused-import cleanup).
Task 8: implemented (commit 6525bcd; full suite 963 passed / 253 skipped, baseline 956/253
  verified by git-stash comparison — delta is exactly the 7 new tests, zero regressions).
  Status DONE_WITH_CONCERNS.
  SC-6 CONFIRMED: cached_derive is called per-file inside _document_for, not batched.
  Carried item 2 RESOLVED by implementer: PIL.UnidentifiedImageError now wrapped in
    ParserError, matching the pdf/docx/pptx/xlsx/html convention. Note: ParserError still
    isn't caught centrally, so a corrupt file aborts the run either way — only the exception
    shape changed. Pre-existing behaviour for every other parser; consistent, not a new gap.

Task 8: Ruling (concern 1, addressed BEFORE review since it is correctness/scope):
  DerivationCacheSettings.enabled defaults True, so EVERY FilesSource — text-only cells
  included — writes cache files to ~/.cache/chunkshop/derived/. Implementer measured 27 files
  written during its own test run. This violates NN-6 ("text-only users pay nothing: no new
  required dependency, no model download, no import cost, no behaviour change") — new disk
  state appearing for a user who indexes only markdown is paying something.
  Fix chosen: gate cache CONSTRUCTION on a derive block being configured —
  `if cfg.derive and cfg.cache.enabled:`. One condition. Text-only cells build no cache and
  touch no disk; media cells still get caching with zero config; `cache.enabled: false` still
  disables it for a media cell that wants that.
  Rejected: (a) flipping the default to False — the cache is what makes media ingest usable
  at all, so media users would have to opt into the thing that makes the feature work;
  (b) a tri-state auto/on/off `enabled` — more config surface for a distinction the derive
  block already expresses.
  Justification that this loses nothing: without a derive block every parser is cheap local
  text extraction (pypdf, python-docx, read_text). There is no expensive derivation to memoise,
  so caching a text-only cell was never buying anything in the first place.
  COST IF WRONG: someone with a genuinely slow custom text parser and no derive block gets no
  caching; they set `cache.enabled: true`... which under this fix would ALSO not build one.
  That is the real cost — accepted, because no such caller exists in the plan and the honest
  fix if one appears is to let a derive block route their extension at that point.
Task 8: NN-6 cache gating applied pre-review (commit 4819dd1). Implementer verified by direct
  measurement: 0 cache files after a full run, vs 27 before. Full suite 965/253.
Task 8: review (of both commits) — Spec COMPLIANT, quality NEEDS FIXES. 1 Important + 3 Minor.
  Reviewer verified named risk 1 CLEAN: both derivers pass an identical
    (rung, model_id, model_version, params) tuple to derivation_hash() and to
    stamp_provenance(), so the cache key and the recorded provenance cannot diverge.
  Reviewer verified the NN-6 test is load-bearing: it asserts cache_root.exists() is False
    (filesystem), not merely src._cache is None — traced DerivationCache.put()'s mkdir to
    confirm removing the gate would actually fail the test.
  Important (named risk 2, CONFIRMED REACHABLE): once ANY derive block exists the cache is
    built, and _document_for routes EVERY parser dispatch through cached_derive — not just
    deriver-owned extensions. TextParser takes a config-dependent `encoding` but has no
    derivation_hash(), so its key falls back to the class name "TextParser", identical for
    every encoding. Two cells sharing the default machine-wide cache root and touching the
    same file silently serve the first cell's decoded text to the second. Same class of bug
    for any injected third-party FileParser with config-dependent output.

Task 8: Ruling: fix via the reviewer's option (a) — route ONLY extensions populated from
  cfg.derive through cached_derive; leave the default parser table's dispatch untouched.
  Why (a) over (b) (folding an encoding/config digest into the class-name fallback):
    (a) is narrower and matches the stated intent already written in the code's own comment
    ("memoise EXPENSIVE derivation"). It also deletes the entire bug CLASS rather than
    patching the one instance — no parser lacking derivation_hash() can reach the cache at
    all, so no future config-dependent parser can reintroduce this. (b) fixes TextParser and
    leaves the next one to be discovered.
  COST IF WRONG: an expensive third-party parser injected via FilesSource(parsers=...) gets
  no caching. Accepted — there is no such caller today, and the honest fix when one appears
  is to route it through a derive block (Plan B's F9 subprocess rung does exactly that).
Task 8: fix round 1/5 (4 addressed, 1 new item raised — commits 4819dd1..4738fb1).
  All 4 findings ADDRESSED with committed regression coverage. Implementer MUTATION-TESTED
  its own regression test unprompted (reverted the gate to `if True:`, confirmed the test
  failed with 2 cache files, restored) — re-reviewer independently confirmed that claim is
  consistent with what DerivationCache._path() would actually produce.
  Re-reviewer also verified the derived-extension set uses the SAME .strip().lower().lstrip(".")
  normalisation as the parser table, so the cache genuinely engages for media (the mirror-image
  failure would have been an invisible perf bug).

Task 8: Ruling on the re-review's "New breakage: Document.fingerprint is None for non-derived
  extensions on iter_documents()": NOT A REGRESSION. The reviewer baselined against mid-task
  4819dd1 instead of shipped main. Verified directly against main @ 3df86cb (v1.0.0-rc6):
      iter_documents -> self._document_for(Path(p))    # no content_hash passed
      Document(..., fingerprint=content_hash)          # therefore None
  fingerprint has ALWAYS been None on the full-resync path. Task 8's first commit (6525bcd)
  accidentally CHANGED that by hashing unconditionally; this fix round restored main's
  behaviour for text files and IMPROVED it for derived files, which now carry a fingerprint
  they never had. Rejected fixing it: (a) hashing every file again reintroduces the
  double-read my previous ruling removed AND re-changes shipped behaviour; (b) forcing
  fingerprint to None for media too would discard information we now get for free.
  Safety confirmed: cursor_from reads Document.fingerprint, but is reachable only in
  incremental mode, which always routes through iter_changes_since — and that path always
  computes and passes content_hash. Re-reviewer verified this independently in runner.py.
  COST IF WRONG: a library embedder calling iter_documents() directly sees fingerprint set
  for derived extensions and None for others. Asymmetric, but strictly more information than
  main shipped, and now to be locked by a test.
  ACTION: the reviewer's underlying point — nothing tests fingerprint from iter_documents()
  and three commits just churned this line — is valid. Dispatching a small round to LOCK the
  contract with a test rather than to change behaviour.
Task 8: fix round 2/5 (1 addressed, 0 open — test-only commit 792b174 pinning BOTH halves of
  the fingerprint asymmetry with a docstring explaining WHY, so a future reader cannot
  "helpfully" make it uniform). Re-review confirmed the diff touches no file under
  python/src/, and that the implementer correctly checked the existing incremental test
  first rather than adding a redundant assertion.
Task 8: complete (commits 2f8a0ee..792b174, review clean)

Task 9: dispatched (sonnet — pydantic model validator on CellConfig) — content_origin and
  derived_rung as promoted columns. BASE 792b174. Brief already carries pre-flight ruling D1
  (correct sqlite target shape).
Task 9: implemented (commit b5b0d28). Status DONE_WITH_CONCERNS; BOTH concerns resolved by me:

  Concern A (test-count delta) — RESOLVED, no defect. I measured the suite directly at both
    commits rather than accepting either number: 968 passed/253 skipped at Task 8 tip
    (792b174) -> 974/253 at Task 9 head (b5b0d28) = +6, exactly the 6 new tests, zero
    regressions. MY stated baseline of 969 was stale; the implementer's "972" came from a
    partial stash. Nothing wrong with the code. (Suite also carries 4 pre-existing warnings
    at BOTH commits — not introduced by this branch. Note for the final review.)

  Concern B — REAL PLAN DEFECT, and it would have hit Task 11.
Task 9: Ruling D4: FixedOverlapChunker is defined with `window_words`/`step_words`
  (config.py:361-364), NOT `max_words`/`overlap`. My plan used the wrong names in 4 places,
  including the Task 11 sample YAML that ships to docs/samples/ and gets asserted by
  test_sample_yaml_is_valid_config. Found by the Task 9 implementer while fixing its own
  inline test YAML — the same class of defect as pre-flight D1 (TargetConfig shape).
  Fixed in the plan (commit 72b1972); task-11 brief regenerated from the corrected plan.
  COST IF WRONG: Task 11's own config-validation test catches it — but only after a wasted
  round, which is exactly what fixing it here avoids.
  PROCESS NOTE: two of my four plan defects were wrong pydantic field names copied from
  memory. The pre-flight scan verified TargetConfig and the CLI flags but did not verify
  every config model the plan's YAML touches. That check belongs in the pre-flight scan.
Task 9: review — Spec COMPLIANT, quality Approved, 0 Critical, 0 Important. 3 Minor.
  Reviewer verified by code inspection (not from the report): dedupe uses
  PromoteColumn.column_name rather than a re-derived dots->__ mapping; reassignment is
  `list(...) + additions` so the module-level DEFAULT_MEDIA_PROMOTIONS is never mutated; the
  no-derive branch is an unconditional early return BEFORE any read of self.target, so NN-6
  holds structurally rather than empirically; and the validator is a fixed point (existing is
  computed from live state each call), so re-validation cannot accumulate duplicates.
  Minor (deferred): test_text_only_cell_gets_no_media_columns asserts only `not
    promote_metadata` on a cell that never set any — it does not cover a no-derive cell with
    its OWN unrelated promotions coming back byte-identical. Plan-mandated (the brief's own
    test code). Code path makes it moot, but the test is the weaker of the two.
  Minor (deferred): idempotency has NO committed regression test — verified only ad hoc in
    the implementer's report. Structurally safe, but named risk #1 deserves permanent cover.
  Minor (deferred): every derive-enabled cell's promote_metadata holds the SAME
    PromoteColumn instances from the module-level list (references, not copies).
    PromoteColumn is not frozen=True, so the safety is incidental rather than enforced.
    Nothing mutates them today; flag if a future task ever needs in-place mutation.
  NOTE FOR FINAL REVIEW: the first two Minors are cheap, valuable tests that lock behaviour
  this task explicitly cared about. Deliberately NOT looped (minors never extend the loop);
  triage them in the final review's single fix wave.
Task 9: complete (commits 792b174..b5b0d28, review clean)

Task 10: dispatched (haiku — pyproject extras + one test file) — extras packaging and the
  SC-13 base-install guarantee. BASE b5b0d28.
Task 10: implemented (commit 1f22259; 978 passed / 253 skipped, +4 tests. uv.lock UNTOUCHED
  — verified by me, since the task edits pyproject.toml and a stray sync would rewrite it).
  NOTE: my plan-fix commit 72b1972 (docs-only) sat between b5b0d28 and this task's commit, so
  I re-based the review package on 72b1972 — the same trap I flagged at Task 1. Caught it.
Task 10: review — Spec COMPLIANT, "Approved" verdict BUT 1 Important (plan-mandated) + 1 Minor.
  The Important governs, so the loop runs regardless of the verdict word.
  Reviewer verified the two things that mattered, independently rather than from the report:
    - The subprocess test CANNOT pass vacuously: subprocess.run(..., check=True) turns the
      child's AssertionError into CalledProcessError in the parent.
    - The assertion is REACHABLE: traced the whole import chain (chunkshop/__init__ lazy
      __getattr__, config, sources/__init__, session_staging -> backends.postgres, parsers/*)
      and confirmed nothing eagerly imports fastembed anywhere on the path.
    - `vision = []` is not a novel pattern — `summarize = []` already exists in the same file.
  Important: the banned-substring guard `"av>="` is spelling-specific. `av==13.0`, `av~=13`,
    or a bare unpinned `"av"` would slip past, converting "no media library in core deps"
    into a partially-enforced promise for that one library.

Task 10: Ruling: the finding WINS. A test whose entire job is to enforce a packaging promise
  must not have a spelling-shaped hole. Fix: extract each requirement's PACKAGE NAME (text
  before the first version specifier / bracket / whitespace), PEP-503-normalise it (lowercase,
  `_`->`-`), and exact-match against a banned NAME set — rather than substring-matching
  version-pinned fragments.
  Rejected: adding more substring spellings ("av==", "av~=", ...) — that is the same defect
  with more rows, and the next operator PEP adds reopens it.
  COST IF WRONG: name extraction mishandles an exotic requirement form (a URL requirement, or
  an environment marker) and the test either false-positives loudly on a legitimate dep — the
  safe direction — or misses one. Bounded, and the test is one file to correct.
Task 10: fix round 1/5 (3 addressed, 0 open — PEP-503 package-NAME extraction replaces
  substring matching; helper extracted and unit-tested; unused pytest import dropped;
  commits 1f22259..e537ea2). Re-review ran the adversarial pass I asked for and could not
  construct a banned requirement that slips past: URL direct-reference (`av @ https://...`),
  environment marker (`av>=13; python_version>="3.12"`), extras bracket (`av[extras]>=13`),
  case variation (`AV>=13`), underscores (`faster_whisper`) — all correctly rejected; and
  `av-something` correctly NOT flagged. pyproject [project] dependencies and uv.lock unchanged.
Task 10: complete (commits b5b0d28..e537ea2, review clean)

Task 11: Ruling D5 (made BEFORE dispatch, from a pre-check): the sqlite target loads
  sqlite_vec (backends/sqlite.py:36), which ships in the `chunkshop[sqlite]` extra — so the
  plan's CI step `pip install ./python` (base only) would have failed at ingest time, and
  SC-1's "NO EXTRAS" wording was not achievable as written.
  Ruling: the quickstart installs `chunkshop[sqlite]`, and SC-1's claim is narrowed to its
  real substance — **no API key, no GPU, no MEDIA extra, no model choice**. A vector store's
  own driver is not a media dependency, and nobody evaluating a RAG library is surprised that
  a storage backend needs its driver.
  Rejected: (a) a postgres target — needs a running server, far worse for a 5-minute
  quickstart; (b) inventing a store-less path — does not exist and would not demo search.
  The CI job still asserts NO media package (rapidocr/faster-whisper/torch/av) is importable,
  which is the part of the promise that actually differentiates chunkshop.
  Applied to SPEC.md SC-1 and the plan; task-11 brief regenerated (commit d82066e).
  COST IF WRONG: the headline claim is one extra token longer to state. The alternative —
  shipping a README quickstart that fails on the reader's first command — is far worse.

Task 11: dispatched (sonnet — sample YAML + fixtures + README + CI workflow + a real run).
  BASE e537ea2. Brief carries rulings D1 (TargetConfig shape), D2 (search -k), D4
  (window_words/step_words) and D5 (sqlite extra).
Task 11: implemented (commit 8eb1954; 984 passed / 253 skipped, +5 tests, 0 regressions).
  Status DONE_WITH_CONCERNS. REAL end-to-end run performed in BOTH the dev venv AND a fresh
  clean `chunkshop[sqlite]`-only venv mirroring CI: sprite_knight.png ranked FIRST for
  "a game sprite". The wedge is verified by execution, not by assertion.

  *** SHIPPED BUG FOUND BY ACTUALLY RUNNING THE QUICKSTART ***
  `chunkshop search` was hardcoded to Postgres and completely broken against sqlite targets.
  This is a defect in shipped v1.0.0-rc6 that 979 passing tests never caught, because nothing
  in the suite ever exercised the CLI search path against the sqlite backend. The quickstart
  could not work at all until it was fixed. Fixed additively via a `hybrid_search_fn` dispatch
  (search_common.py + cli.py) with its own regression test (test_cli_search_sqlite.py).

  Two MORE plan defects of mine, corrected by the implementer in the CI script:
    D6: the CI step passed the query positionally; `chunkshop search` needs `--query`.
    D7: it parsed the --json payload as a bare list; the real shape is data["chunks"].
  Both would have made the SC-1 gate fail for the wrong reason. That is now FOUR plan defects
  of the wrong-API-shape class (D1 TargetConfig, D4 chunker fields, D6 flag, D7 JSON shape) —
  every one of them a chunkshop API detail I wrote from memory instead of verifying.

Task 11: Ruling D8 — ACCEPT the scope expansion into search_common.py and cli.py.
  Task 11's brief did not authorise touching the search path. But SC-1 is "a text query
  returns the correct image", and that criterion is unsatisfiable on the documented quickstart
  while search is Postgres-only. The alternatives were worse: a postgres quickstart needs a
  running server (killing the 5-minute claim), and a quickstart that ingests without searching
  does not demonstrate the feature at all.
  CONDITIONS on the acceptance, to be verified at review: the change must be strictly ADDITIVE
  and must not alter the Postgres path's behaviour, and it must carry its own regression test.
  COST IF WRONG: a regression in the Postgres search path — chunkshop's primary backend and
  its most-used surface. This is the single riskiest diff on the branch and the review is
  scoped accordingly.

  Known limitation to document (implementer's concern 2): sqlite's `where` builder does not
  support the default fact-row exclusion filter, so the quickstart passes `--include-facts`
  (a no-op for an image corpus) with an inline README explanation. Carry to the final review.
Task 11: review (OPUS — escalated; the only top-tier review on this branch, because this is
  the one diff touching chunkshop's primary search path). Spec COMPLIANT, quality Approved,
  0 Critical, 2 Important, 6 Minor.

  *** D8's CONDITIONS ARE MET — the Postgres path is provably unchanged. *** Six lines of
  traced evidence: _hybrid_search_fn_for returns a callable ONLY for target_type=="sqlite"
  and otherwise returns None without importing anything (cli.py:642-649); with None,
  search_common.py:409-412 executes a byte-identical statement to pre-diff, preserving its
  laziness; the call site (:413-425) is unmodified, same positional dsn + same 11 kwargs in
  the same order; hybrid_search_fn is appended last with a None default behind a `*`, so no
  caller's argument binding shifts; the only two call sites are cli.py:736 (updated) and
  cli.py:1357 (untouched); return shape unchanged. No regression.
  Reviewer also independently verified the README/sample byte-identity by extracting and
  diffing (not by trusting the test), and confirmed the CI gate fails at every stage
  (bash -e, explicit `|| exit 1`, sys.exit on a string arg, and loud KeyError on a shape change).

  Important #1 — THE GATE CANNOT DETECT THE THING IT EXISTS TO GUARD. Indexed text for the
    sprite includes filename/path tokens ("sprite knight docs samples images ..."), the query
    is "a game sprite", and the assertion checks `"sprite" in source_path`. Neither other
    fixture has "sprite" in its path, so the FTS leg ALONE pins the answer with the CLIP
    contribution stripped to zero. Meanwhile README:216-219 sells this job as what stops the
    zero-shot-tagging claim from rotting. Fix: query "pixel art" — present only via the CLIP
    vocabulary, absent from every filename.
  Important #2 — the README quickstart is not runnable as printed for the install it prints.
    It says `pip install chunkshop[sqlite]` (PyPI) then references docs/samples/... which does
    not ship in a wheel. README:216-218 explicitly claims it is "runnable as printed"; false
    for that audience. CI misses it because it installs from a checkout.
Task 11: fix round 1/5 (3 addressed, 0 open — commits 8eb1954..2aac8ce). Query changed to
  "pixel art"; README now clones the repo first (matching CI's `pip install './python[sqlite]'`);
  fixture generator committed (docs/samples/images/generate_fixtures.py, deterministic).
  REAL run: sprite_knight 0.01639 > screenshot_settings 0.01613 > chart_revenue 0.01587,
  legs=["semantic"] — the ranking is now driven by CLIP-derived tag text, not filenames.
  Re-reviewer verified CLIP-only concretely rather than by assumption: humanise() emits
  whole-word tokens (sprite/knight/chart/revenue/screenshot/settings/docs/samples/images),
  none equal "pixel" or "art". Noted the near-miss that `chart` CONTAINS `art`, then proved
  it is not a leak: search_sqlite.py:33-35,124 uses FTS5 unicode61 whole-token MATCH, so a
  query token "art" cannot match the token "chart". chart_revenue also scored LOWEST.
  No python/src/ change this round; uv.lock and [project] dependencies untouched.
Task 11: complete (commits e537ea2..2aac8ce, review clean)

*** ALL 11 TASKS COMPLETE ***

CARRY INTO THE FINAL REVIEW'S SINGLE FIX WAVE (deliberately NOT looped — the re-reviewer
flagged it as non-blocking and out of scope for its round, and the process says such items go
to the final review's triage rather than extending a task loop):
  [HARDEN-GATE] The SC-1 CI gate asserts bare rank-1 at a ~1.6% margin (0.01639 vs 0.01613)
  on a semantic-only leg. A fastembed/CLIP version bump, a different ONNX Runtime build, or
  fixtures regenerated on another platform could flip ranks 1 and 2 — turning the gate red for
  a reason unrelated to the SC-1 claim. A flaky gate gets disabled by the first person it
  inconveniences, which defeats Task 11 entirely. Recommended hardening, strictly additive:
  also assert the sprite's CLIP tag ("a game sprite or pixel art") is present in the top hit's
  clip_tags metadata. That check is leg-independent and proves the deriver ran and chose the
  right label, without weakening the rank assertion. Verdict is mine at the final review.

=== FINAL WHOLE-BRANCH REVIEW (opus) — VERDICT: MERGE AFTER FIXES, 0 Critical ===
Confirmed the Postgres search path unchanged INDEPENDENTLY (6 traced points, without
reference to the prior review). Checked all 8 controller rulings + 3 in-task rulings and
found NONE wrong. Triaged all 15 deferred Minors.

CORRECTION TO MY OWN REASONING (recorded because it was wrong and I acted on it):
  I described the CI gate's margin as "~1.6% (0.01639 vs 0.01613)". Those are not scores.
  search_common.py:66 computes contrib = 1.0/(rrf_k + rank) with rrf_k=60, so with a single
  leg the fused score is IDENTICALLY 1/(60+rank): 1/61=0.016393, 1/62=0.016129, 1/63=0.015873.
  The "margin" is the mechanical spacing between RRF ranks and carries ZERO information about
  the underlying cosine similarities. My fragility assessment rested on a misread number.
  Consequence: the flakiness risk is REAL but UNQUANTIFIED, and the hardening I proposed does
  not reduce it (if ranks flip, the top hit is screenshot_settings and the tag assertion fails
  in the same run). It is still worth adopting, for a different reason: it proves the CLIP
  deriver ran and chose the right label, which the rank-only gate does not.

FINAL RULINGS (feeding the single fix wave):
  F-R1 [HARDEN-GATE] ADOPT, with the reviewer's two additions: also assert the sprite's
    clip_tags contains the pixel-art label; raise --k to 3 and print all three hits with their
    clip_tags on failure (at -k 1 a red gate is undiagnosable); and measure the real semantic
    margin once and record it as a workflow comment.
  F-R2 [I-1] FIX. EmptyDerivationError's message violates NN-6 verbatim ("A text-only user
    must never see a media concept in an error message") — it names an image, a scanned PDF
    and an OCR rung. Verified by execution on a text-only cell. Compounds with deferred T3-a.
    Also: the default change is unannounced, and runner.py:95 calls create_table() (which
    DROPs under mode: overwrite) BEFORE the document loop at :100, with no handler anywhere —
    so a text-only user upgrading with one empty file goes from a complete table to a dropped
    and partially-refilled one. Needs a docs/upgrading.md entry.
  F-R3 [I-2] FIX. No CHANGELOG entry for 24 commits adding a public protocol, three config
    keys, four extras, a changed default — and fixing a SHIPPED rc6 bug (search hardcoded to
    Postgres). rc6 users on sqlite need to know.
  F-R4 [extras] REMOVE `ocr`, `audio`, `media`; KEEP `vision = []`. The reviewer is right and
    this is the one thing on the branch I would regret: those extras install real weight
    (rapidocr, faster-whisper, av, soundfile) and deliver NOTHING — no OCR or ASR rung exists
    here — and test_base_install_unaffected.py:57-65 now ENFORCES their presence, making an
    empty promise a test invariant. Extras are a published contract; adding one later is not
    breaking, retracting one is. `vision = []` stays because it is honest: it names a real,
    working capability that costs nothing.
    COST IF WRONG: v1.1 must re-add two lines of pyproject when the rungs land. Trivial.
  F-R5 [Minor #3, promoted] FIX. model_version is hardcoded "1" in both derivers and is part
    of the cache key, so a fastembed upgrade changing the ONNX weights behind
    Qdrant/clip-ViT-B-32-vision would serve stale cached tags forever. This is precisely the
    failure class the spine exists to prevent (cf. the T4 default=str drift), so I am
    promoting it out of Minor. Fold importlib.metadata.version("fastembed") into
    model_version for model-backed rungs only; image_meta stays "1" (it is deterministic).

  NOT fixed, filed for the user: Minors 1,2,4,5,6,7,8,9,10,11,12 + the structural note that
  hybrid_search_fn should be marked provisional in its docstring.

PROCESS LESSON (reviewer's, and it is correct): NN-6 and SC-13 were assigned to ONE task
  (10), which interpreted them as a packaging concern, while the constraint they express is
  cross-cutting. Task 3 owned the on_empty default and checked the suite; Task 10 owned NN-6
  and checked packaging; nobody crossed them. Next time, give the non-negotiables their own
  reviewer at the end rather than an owner in the middle.

=== FIX WAVE RE-REVIEW: all 6 ADDRESSED, no new breakage ===
  Verified the two invariants I named: the media hint is gated on `self.cfg.derive`
  (Optional[dict]=None, falsy for text-only cells) and the test proves it by configuring a
  derive block for .png while the failure is on .blank — checked against the ACTUAL literal
  strings on both branches, not merely that a conditional exists. And derivation_hash() and
  stamp_provenance() both call the same self._model_version() with no intervening mutation;
  the new test monkeypatches it, then RECOMPUTES the hash from the recorded metadata value and
  asserts equality — a genuine agreement proof, not two coincidentally-equal assertions.
  _model_version() catches only PackageNotFoundError, returns "unknown", and imports
  importlib.metadata inside the method, so lazy loading is undisturbed.
  Measured margin confirmed to be against embedded_content (derived text), not filenames.
  uv.lock and [project] dependencies untouched; the PEP-503 guard byte-identical, not watered down.

ADJUDICATION OF RESIDUALS (no second fix wave; these are rulings, not fixes):
  R-1: PARKED — the base EmptyDerivationError message names `skip` and `index` but not the
    literal word `error` as a third mode. MY REQUIREMENT WAS WRONG, not the implementation:
    a user seeing this message is BY DEFINITION already in error mode, so naming it tells them
    nothing actionable. The two actionable escapes are both named. The code stands.
    Sub-note (real, minor): the accompanying test asserts `"error" in message or "on_empty" in
    message` — an or-clause that cannot distinguish the two, so it would pass either way.
    Weak test, harmless assertion; filed, not fixed.
  R-2: PARKED — spec/chunkshop-media-ingest/{SPEC,PLAN}.md still reference chunkshop[ocr]/
    [audio]/[media]. These are PLANNING artifacts describing the full escalation ladder
    including v1.1 audio, so the references are forward-looking design rather than stale
    packaging claims. The shipped contract (pyproject.toml) is now correct, which is the one
    that binds users. Worth one clarifying line in the SPEC saying extras ship WITH their
    rungs — filed as a follow-up for the user, not a merge blocker.
    [CORRECTION 2026-09-01, post-hoc review — the ruling stands, its premise was overstated:
    grep at HEAD finds NO reference to `chunkshop[media]` anywhere in the repo, and PLAN.md
    references none of the three extras. Only SPEC.md still names `chunkshop[ocr]` and
    `chunkshop[audio]` (F4, the rung ladder at :570, the deriver diagram at :919). So the
    scope is "SPEC.md, ocr/audio", not "SPEC/PLAN, ocr/audio/media". The follow-up itself is
    unchanged and still open.]

FINAL STATE: 989 passed / 253 skipped. 27 commits. 0 Critical, 0 Important open.
