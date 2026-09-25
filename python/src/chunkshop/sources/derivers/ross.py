"""Rung 5 for media: the `ross` binary, run as a subprocess.

`ross` (https://github.com/yonk-labs/ross, Apache-2.0) is a single Rust
binary that takes media in and emits metadata, tags, a description and
optionally an LLM summary. It sniffs type by MAGIC BYTES rather than by
extension, so one deriver covers image, audio and video — which is how
chunkshop gets an audio tagger it has no in-process rung for.

WHY A SUBPROCESS AND NOT A LINKED LIBRARY. Recorded in `DECISIONS.md`
(2026-09-01) and not revisited here: CLAUDE.md's standing rule is that
sibling repos are user-wired via config and never imported into core; ross
is v0.1.0 and unpublished, so linking would make chunkshop unbuildable
without a local checkout; NN-4 defines rung 5 as exactly this escape hatch.
The welcome consequence is that this rung costs a text-only user literally
nothing — no Python extra, no Cargo feature, no import at module load.

WHAT IS ALWAYS PASSED. `--json --quiet --clip --clap`. The two tagger flags
are unconditional rather than configurable: without them and with
`--no-llm`, ross emits deterministic facts and NO `description` at all, so
every document would derive to empty text and trip `on_empty`. ross's flags
are per-modality, so passing both is how one deriver covers images (CLIP)
and audio (CLAP) from a single config block.

THE CACHE-SOUNDNESS INVARIANT (see `parsers.base.Deriver`). Two inputs of
ross's are NOT this file's bytes:

* **The filename.** When the local tagger is gated (which the README
  reports is the common case on specialised corpora), ross falls back to a
  description humanised from the file's BASENAME
  (`clap_tag::humanize`, which rsplits the path on '/' first). So this
  deriver is not a pure content function and `cache_key_extra` returns the
  basename — exactly the material consumed, no more.
* **The vendored label vocabulary.** ross embeds a 50-label list
  (`src/clip_labels.txt`, `src/clap_labels.txt`) and its precomputed text
  features INSIDE the binary. Nothing on ross's CLI surface reports it:
  `--json` does not echo the label set, and `--doctor` prints only external
  binaries, endpoint config and model cache paths. The strongest available
  identifier is therefore the binary's own version, which this deriver
  obtains at construction by RUNNING `ross --version` (never hardcoded —
  unlike `ClipTagsDeriver`'s `FASTEMBED_VERSION`, which has no runtime
  option and needs a Cargo.lock tripwire test instead) and records as
  `model_version`.

  **LIMITATION, stated plainly:** if a future ross build changes
  `clip_labels.txt` WITHOUT bumping its package version, cached derivations
  from the older vocabulary stay reachable and go stale. A user-supplied
  `labels_file` has no such gap — its CONTENT is hashed into
  `params["labels_sha256"]`, so editing the file re-derives. Mitigation for
  the vendored case is `cache: {enabled: false}` or
  `chunkshop cache purge` after upgrading a ross built from source.

A THIRD, SMALLER GAP worth knowing: ross's `metadata` block comes from
`exiftool` (images) and `ffprobe` (audio/video) when those are installed,
and from a native PNG/WebP header fallback when they are not — so
installing exiftool mid-corpus changes `ross_metadata` for files already
cached. Same mitigation.

THE LLM ENDPOINT IS AN INPUT, AND IT IS IN THE HASH. With `llm: true`,
ross calls an OpenAI-compatible endpoint resolved from the ENVIRONMENT
(`ROSS_VISION_URL` > `ROSS_URL`, `ROSS_VISION_MODEL` > `ROSS_MODEL`, and
the same shape for the key), per modality. None of that appears in the
config block, so for a while none of it reached `params()` — and swapping
`ROSS_MODEL` between two runs of the same cell silently served the first
model's text out of the cache. It now does: at construction, `llm: true`
runs `ross --doctor` and folds the per-modality **url + model + whether a
key is set** into `params["llm_endpoints"]`.

Resolution is delegated to ross rather than reimplemented here on purpose.
ross's precedence (`--<mod>-url > ROSS_<MOD>_URL > --url > ROSS_URL`) is
its own; re-deriving it in two host languages is the drift class this
rung already has enough of. The cost is that `--doctor` prints for humans
(`--doctor --json` is not supported as of ross 0.1.0), so this file parses
human-readable output — deliberately strictly: anything it cannot read is
a construction-time ERROR, never a fallback to a hash that omits the
model, because a silent fallback IS the bug.

The API key's VALUE is never read, hashed, stamped or printed. Only the
boolean ross itself reports (`key=set` / `key=none`) enters the params,
and it earns its place: adding a key to a previously keyless endpoint
turns a 401-and-`llm_error` degraded derivation into a real one, which
must re-derive rather than serve the degraded text forever.

WHAT IS STILL NOT DETERMINISTIC, AND CANNOT BE. With `llm: true` the
derived text comes from a remote model doing sampling. **Two fresh
derivations of the same image can differ** even with the endpoint, the
model and the prompt all identical — the hash pins the INPUTS, not the
output. So on this rung the cache is not an optimisation, it is what
makes a corpus stable: every row derived once and thereafter served from
that one entry. The corollary is worth stating plainly because it is a
materially different property from rungs 0 and 1, which are pure
functions of the bytes: **`chunkshop cache purge` followed by a re-ingest
can legitimately change the derived text, and therefore the embeddings,
with no config change at all.** Purge this cache only when you mean to
re-derive the corpus.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from chunkshop.sources.derivers.provenance import stamp_provenance
from chunkshop.sources.parsers.base import ParserError
from chunkshop.sources.parsers.result import ParseResult

#: The rung name, the `engine` recorded in provenance, and the `model` id.
#: `engine` is "ross" (not "python"/"rust") because BOTH ports shell out to
#: the same binary — the derivation is genuinely ross's, not either port's,
#: and stamping the host language would claim otherwise. It is also what
#: lets a ross-derived cache entry coexist with a fastembed-derived one
#: without either masquerading as the other.
ENGINE = "ross"
RUNG = "ross"
MODEL_ID = "ross"

#: Exit codes ross documents in its README, mapped to what the user should
#: actually do about them. Anything else falls through to the bare status.
_EXIT_HINTS = {
    2: "ross ran with --strict and at least one file errored",
    3: "no LLM endpoint is configured; set ROSS_URL + ROSS_MODEL, or leave "
       "`llm: false` in the derive block to stay offline",
    4: "ross rejected the input or the arguments",
    5: "ross could not load its local CLIP/CLAP models (first run downloads "
       "~89MB/78MB into ~/.cache/ross-clap; check network and disk)",
}

_INSTALL_HINT = (
    "Install ross (https://github.com/yonk-labs/ross), or set `binary:` in "
    "the `rung: ross` derive block to its full path."
)

#: The modalities `ross --doctor` reports, in ross's own print order. Fixed
#: rather than discovered: a ross that grows a fourth modality must be a
#: loud parse failure here, not a silently unhashed new endpoint.
_MODALITIES = ("vision", "audio", "video")

_DOCTOR_HINT = (
    "With `llm: true` this rung resolves its endpoint from the environment "
    "(ROSS_VISION_URL > ROSS_URL, ROSS_VISION_MODEL > ROSS_MODEL, ...) by "
    "running `ross --doctor`, and folds the url + model into the derivation "
    "hash so that swapping models re-derives instead of serving the previous "
    "model's text. Run `ross --doctor` yourself to see what could not be "
    "read. If ross's output format has changed, this deriver needs updating "
    "-- do not work around it by disabling the cache, which would put the "
    "model back outside the key."
)


class RossDeriver:
    # ross sniffs by magic bytes, so this list is documentation of what the
    # tool covers rather than a gate — `FilesSource` routes purely on the
    # extensions named in the user's `derive:` key.
    supported_extensions = [
        # image
        "png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif",
        # audio
        "wav", "mp3", "flac", "ogg", "m4a", "aac",
        # video
        "mp4", "mov", "webm", "mkv", "avi",
    ]

    def __init__(
        self,
        binary: str = "ross",
        labels_file: Optional[str] = None,
        llm: bool = False,
        timeout_s: int = 120,
    ):
        self.binary = binary
        self.labels_file = labels_file
        self.llm = llm
        self.timeout_s = timeout_s
        # Both resolved EAGERLY, at construction. `FilesSource.__init__`
        # builds its derivers before `runner.run_cell` reaches
        # `sink.create_table()`, so a missing binary or an unreadable
        # labels file fails before a `mode: overwrite` cell has dropped the
        # user's table — the same property `preflight_derive` buys the Rust
        # port. It also makes `derivation_hash()` infallible, which it must
        # be: `files.py` calls it with no error path.
        self.labels_sha256 = self._hash_labels_file()
        self.version = self._probe_version()
        # Third eager probe, and only when it can matter: `llm: false` must
        # not pay a `--doctor` subprocess, and — more importantly — must
        # produce params BYTE-IDENTICAL to the ones it produced before this
        # probe existed, so every cache entry on the keyless path stays
        # reachable. `None` here is what keeps `llm_endpoints` out of the
        # params dict entirely rather than present-and-null.
        self.llm_endpoints = self._probe_llm_endpoints()

    # --- construction-time probes ----------------------------------------

    def _hash_labels_file(self) -> Optional[str]:
        """sha256 of the resolved labels file's CONTENT, or None.

        Content, not path: moving the file must not re-derive, and editing
        it in place MUST. This is the vocabulary identity for the
        `labels_file` case — the case where ross's own vendored list is
        replaced and the version number therefore says nothing about which
        labels ran.
        """
        if self.labels_file is None:
            return None
        p = Path(self.labels_file)
        try:
            return hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError as exc:
            raise RuntimeError(
                f"ross deriver: cannot read labels_file {str(p)!r}: {exc}. "
                f"Fix the path in the `rung: ross` derive block, or drop "
                f"`labels_file` to use ross's built-in vocabulary."
            ) from exc

    def _probe_version(self) -> str:
        """`ross --version` -> "0.1.0". Never hardcoded.

        Doubles as the NN-5 existence check: a missing, non-executable or
        wrong binary produces the actionable error here rather than a
        confusing failure on the first image.
        """
        try:
            proc = subprocess.run(
                [self.binary, "--version"],
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"ross deriver: cannot run the ross binary {self.binary!r}: "
                f"{exc.strerror or exc}. {_INSTALL_HINT}"
            ) from exc
        except PermissionError as exc:
            raise RuntimeError(
                f"ross deriver: the ross binary {self.binary!r} is not "
                f"executable: {exc.strerror or exc}. {_INSTALL_HINT}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"ross deriver: cannot run the ross binary {self.binary!r}: "
                f"{exc}. {_INSTALL_HINT}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"ross deriver: {self.binary!r} --version did not finish "
                f"within {self.timeout_s}s. {_INSTALL_HINT}"
            ) from exc

        if proc.returncode != 0:
            raise RuntimeError(
                f"ross deriver: {self.binary!r} --version exited with status "
                f"{proc.returncode}. {_INSTALL_HINT} "
                f"ross stderr: {proc.stderr.strip()}"
            )
        # clap's `#[command(version)]` prints "<name> <semver>".
        line = (proc.stdout or "").strip().splitlines()
        token = line[0].split()[-1] if line and line[0].split() else ""
        if not token:
            raise RuntimeError(
                f"ross deriver: {self.binary!r} --version printed nothing "
                f"parseable ({proc.stdout!r}); is this really the ross "
                f"binary? {_INSTALL_HINT}"
            )
        return token

    def _probe_llm_endpoints(self) -> Optional[dict]:
        """`ross --doctor` -> the per-modality endpoint identity, or None.

        None when `llm` is false: ross is then run with `--no-llm` and no
        endpoint is consulted at all, so there is nothing to key on and no
        reason to spend a subprocess finding that out.

        Otherwise this asks ROSS to resolve its own precedence rather than
        reimplementing `--<mod>-url > ROSS_<MOD>_URL > --url > ROSS_URL` in
        two host languages. The deriver passes no `--url`/`--model` flags
        of its own, so a bare `--doctor` resolves exactly what the eventual
        derive call will use.

        Eager, alongside the other two probes, for the same reason: it must
        fail before `sink.create_table()` has dropped a `mode: overwrite`
        table, and `derivation_hash()` must stay infallible.
        """
        if not self.llm:
            return None
        try:
            proc = subprocess.run(
                [self.binary, "--doctor"],
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"ross deriver: {self.binary!r} --doctor did not finish "
                f"within {self.timeout_s}s. {_DOCTOR_HINT}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"ross deriver: cannot run the ross binary {self.binary!r}: "
                f"{exc}. {_INSTALL_HINT}"
            ) from exc

        if proc.returncode != 0:
            raise RuntimeError(
                f"ross deriver: {self.binary!r} --doctor exited with status "
                f"{proc.returncode}. {_DOCTOR_HINT} "
                f"ross stderr: {proc.stderr.strip() or '(empty)'}"
            )
        return _parse_doctor(proc.stdout, self.binary)

    # --- provenance -------------------------------------------------------

    def _params(self) -> dict:
        """Everything that changes ross's OUTPUT, and nothing that doesn't.

        `binary` and `timeout_s` are deliberately absent: the path a
        binary is reached by does not change what it computes (its version
        does, and that is `model_version`), and a timeout only bounds how
        long we wait. `llm` and the vocabulary identity DO change output.

        `llm_endpoints` is present ONLY when `llm` is true. That is not
        tidiness — it is what makes the keyless path's hash byte-identical
        to what it was before this key existed, so no existing cache entry
        was invalidated to fix a bug that could not affect it.
        """
        params = {"llm": self.llm, "labels_sha256": self.labels_sha256}
        if self.llm_endpoints is not None:
            params["llm_endpoints"] = self.llm_endpoints
        return params

    def derivation_hash(self) -> str:
        from chunkshop.sources.derivers.provenance import derivation_hash

        return derivation_hash(RUNG, MODEL_ID, self.version, ENGINE, self._params())

    def cache_key_extra(self, path: Path) -> str | None:
        """The file's BASENAME — ross's only per-document non-content input.

        When the local tagger clears its confidence bar the description is
        a pure function of the pixels/samples, but when it is GATED ross
        falls back to `humanize(basename)`. `humanize` rsplits the path on
        '/' and strips the extension, so the parent directories are
        genuinely not consumed and must not be keyed on; the basename is.

        Returning the basename rather than the full path keeps the key as
        narrow as correctness allows: the same file ingested by relative
        and absolute glob still shares one entry, while two byte-identical
        files under DIFFERENT names correctly do not.
        """
        return path.name

    # --- derivation -------------------------------------------------------

    def _argv(self, path: Path) -> list[str]:
        argv = [self.binary, str(path), "--json", "--quiet", "--clip", "--clap"]
        if not self.llm:
            argv.append("--no-llm")
        if self.labels_file is not None:
            argv += ["--labels-file", self.labels_file]
        return argv

    def derive(self, path: Path) -> ParseResult:
        argv = self._argv(path)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout_s
            )
        except subprocess.TimeoutExpired as exc:
            raise ParserError(
                f"ross deriver: {self.binary!r} did not finish within "
                f"{self.timeout_s}s for {path}. Raise `timeout_s` in the "
                f"`rung: ross` derive block, or check that ross is not "
                f"blocked downloading its CLIP/CLAP models on first run."
            ) from exc
        except OSError as exc:  # binary vanished between construction and use
            raise RuntimeError(
                f"ross deriver: cannot run the ross binary {self.binary!r}: "
                f"{exc}. {_INSTALL_HINT}"
            ) from exc

        if proc.returncode != 0:
            hint = _EXIT_HINTS.get(proc.returncode)
            hint = f" ({hint})" if hint else ""
            # ross's own stderr is surfaced verbatim, never swallowed: when
            # a run fails because ffprobe/exiftool is absent, ross's message
            # names the missing binary and ours cannot.
            raise ParserError(
                f"ross deriver: {self.binary!r} exited with status "
                f"{proc.returncode}{hint} for {path}. "
                f"ross stderr: {proc.stderr.strip() or '(empty)'}"
            )

        entry = self._parse_entry(proc.stdout, path)
        if entry.get("error"):
            raise ParserError(
                f"ross deriver: ross reported an error for {path}: "
                f"{entry['error']}"
            )
        return self._build_result(entry)

    def _parse_entry(self, stdout: str, path: Path) -> dict:
        """ross prints ONE object for a single input, an array for many."""
        try:
            payload = json.loads(stdout)
        except ValueError as exc:
            raise ParserError(
                f"ross deriver: could not parse {self.binary!r} --json output "
                f"for {path}: {exc}. First 200 bytes: {stdout[:200]!r}"
            ) from exc
        if isinstance(payload, list):
            if not payload:
                # ross SKIPS non-media files (magic-byte sniff) rather than
                # erroring — an empty array is that, and it is a real
                # configuration mistake worth naming, not an empty success.
                raise ParserError(
                    f"ross deriver: {self.binary!r} produced no entry for "
                    f"{path}; ross sniffs by magic bytes and skips files it "
                    f"does not recognise as image/audio/video."
                )
            payload = payload[0]
        if not isinstance(payload, dict):
            raise ParserError(
                f"ross deriver: unexpected {self.binary!r} --json shape for "
                f"{path}: {type(payload).__name__}"
            )
        return payload

    def _build_result(self, entry: dict) -> ParseResult:
        """Map ross's JSON onto `ParseResult`.

        The metadata key set is FIXED — every key below is always present,
        holding null/[]/{} when ross omitted the field. ross's own output
        keys are conditional (`tags` only appears when a tagger cleared its
        bar), and a key set that varies by input cannot be asserted for
        cross-port parity, nor promoted to a column.

        Deliberately NOT mapped:
          * `path`   — `FilesSource` stamps `source_path` itself.
          * `sha256`, `bytes` — chunkshop already carries the content hash
            as the document `fingerprint`.
          * `mtime`  — an input that is invisible to the cache key; caching
            it would serve a stale value the moment a file is touched.
          * `duration_ms` — wall-clock timing. Storing it would make a
            cache HIT differ from a fresh derivation on every row.
        """
        ross_metadata = dict(entry.get("metadata") or {})
        exif = ross_metadata.get("exif")
        if isinstance(exif, dict):
            # `cache_key_extra` is the BASENAME only (see its docstring), so
            # a byte-identical same-basename file in a DIFFERENT directory
            # is a deliberate cache hit. exiftool's filesystem-derived exif
            # fields — `Directory`, `SourceFile`, and every `File*`-prefixed
            # key (`FileModifyDate`, `FileAccessDate`, `FileInodeChangeDate`,
            # `FilePermissions`, `FileName`, ...) — are per-PATH, not
            # per-(content, basename), so a cache hit would otherwise
            # silently serve the FIRST file's directory and mtimes to the
            # second. Stripped here, before this ever reaches the cache, so
            # a fresh derivation and one rebuilt from a cache hit are
            # equally clean — the cache stores exactly what this returns.
            ross_metadata["exif"] = {
                k: v for k, v in exif.items()
                if k != "Directory" and k != "SourceFile" and not k.startswith("File")
            }

        description = _as_str(entry.get("description"))
        summary = _as_str(entry.get("summary"))
        tags = [t for t in (entry.get("tags") or []) if isinstance(t, str)]
        warnings = [
            _as_str(entry.get(k))
            for k in ("clip_error", "clap_error", "llm_error")
            if _as_str(entry.get(k))
        ]

        # description, then summary, then the tags. `clip_tags` builds its
        # text the same shape (base text + tags), so a reader moving
        # between rungs sees one convention. No de-duplication: when the
        # local tagger fires, ross's own `description` restates the tags,
        # which is harmless for embedding and keeps the two ports trivially
        # identical.
        text = " ".join(p for p in [description, summary, *tags] if p)

        metadata = {
            "ross_type": entry.get("type"),
            "ross_mime": entry.get("mime"),
            "ross_description": description or None,
            "ross_summary": summary or None,
            "ross_tags": tags,
            "ross_tag_confidence": entry.get("tag_confidence"),
            # ross's own report of WHICH model produced the text:
            # "clip:clip-vit-base-patch32" for the local tagger, or the LLM
            # model name when the endpoint path won. Distinct from
            # `derived.model` (always "ross"), which identifies the tool.
            #
            # This is the second half of the endpoint-in-the-hash fix, and
            # the half that stays true when the first cannot: the hash is
            # computed at CONSTRUCTION from `--doctor`, while this is read
            # off the RESPONSE, so a row is attributable even if resolution
            # moved in between (or the endpoint served a different model
            # than it advertised).
            #
            # NOT defaulted, on purpose. ross stamps the local tagger's
            # label only when no endpoint is configured at all, and stamps
            # the LLM's name only when the call SUCCEEDED — so when the LLM
            # was attempted and failed, `model` is absent and this lands
            # None. That is the honest record: the kept text came from the
            # local tagger, ross does not say which build of it, and
            # inventing either the LLM's name (a lie) or a tagger label
            # ross did not emit (a guess) would be worse than null. The
            # `llm_error` itself is carried in `ross_warnings`, so a null
            # here is always explicable from the same row.
            "ross_model": entry.get("model"),
            "ross_metadata": ross_metadata,
            "ross_warnings": warnings,
        }
        return stamp_provenance(
            ParseResult(text=text, metadata=metadata),
            rung=RUNG,
            model_id=MODEL_ID,
            model_version=self.version,
            engine=ENGINE,
            params=self._params(),
        )


def _as_str(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _redact_doctor(stdout: str) -> str:
    """A rendering of `--doctor` output that is safe to put in an error.

    ross 0.1.0 prints `key=none` / `key=set` and never the key itself, so
    echoing its output verbatim would be safe TODAY. But the only path that
    reaches an error message is the path where ross's format is not what we
    expect, which is exactly when that assumption stops being checkable.
    So: modality lines are truncated at ` key=`, and every other line is
    reduced to its leading label. The hint tells the user to run
    `ross --doctor` themselves, where the real output is theirs to see.
    """
    out = []
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        label = parts[0]
        if label in _MODALITIES:
            body = parts[1] if len(parts) > 1 else ""
            body = body.split(" key=", 1)[0].rstrip()
            out.append(f"{label} {body} key=<hidden>".strip())
        else:
            out.append(f"{label} ...")
    return "\n".join(out) or "(no output)"


def _parse_doctor(stdout: str, binary: str) -> dict:
    """Parse `ross --doctor` into `{modality: {url, model, key_set} | None}`.

    ross prints one line per modality, either::

        vision     http://host:8082/v1 model=some/model key=none
        audio      NOT SET

    Parsed strictly and from the RIGHT: the model id is whatever sits
    between ` model=` and ` key=`, so a url or a model containing spaces
    would be a named failure rather than a quietly truncated hash input.

    A modality reported `NOT SET` maps to `None`, which is the honest
    record: ross will emit `llm_error: no endpoint configured` for it, and
    configuring one later must re-derive.

    Every failure here is a hard error. There is no degraded mode: falling
    back to a hash that omits the model is precisely the defect this
    function exists to close.
    """
    found: dict = {}
    for raw in (stdout or "").splitlines():
        parts = raw.strip().split(None, 1)
        if not parts or parts[0] not in _MODALITIES:
            continue
        name = parts[0]
        rest = (parts[1] if len(parts) > 1 else "").strip()

        if rest == "NOT SET":
            found[name] = None
            continue

        head, sep, key = rest.rpartition(" key=")
        url, sep2, model = head.rpartition(" model=")
        if not sep or not sep2 or not url.strip() or not model.strip():
            raise RuntimeError(
                f"ross deriver: could not parse the {name!r} line of "
                f"{binary!r} --doctor; expected "
                f"'{name} <url> model=<model> key=<none|set>' or "
                f"'{name} NOT SET'. {_DOCTOR_HINT} "
                f"Output (key redacted):\n{_redact_doctor(stdout)}"
            )
        key = key.strip()
        if key not in ("none", "set"):
            raise RuntimeError(
                f"ross deriver: {binary!r} --doctor reported an unexpected "
                f"key state for {name!r}; expected 'none' or 'set'. "
                f"{_DOCTOR_HINT} "
                f"Output (key redacted):\n{_redact_doctor(stdout)}"
            )
        # The key's VALUE is never read. Its PRESENCE is: adding a key to a
        # previously keyless endpoint turns a 401 (and an `llm_error`
        # degraded derivation) into a real one, and that must re-derive.
        found[name] = {
            "url": url.strip(),
            "model": model.strip(),
            "key_set": key == "set",
        }

    missing = [m for m in _MODALITIES if m not in found]
    if missing:
        raise RuntimeError(
            f"ross deriver: {binary!r} --doctor did not report "
            f"{', '.join(missing)}. {_DOCTOR_HINT} "
            f"Output (key redacted):\n{_redact_doctor(stdout)}"
        )
    # Rebuilt in ross's own order so the params dict is stable regardless of
    # the order the lines happened to arrive in.
    return {m: found[m] for m in _MODALITIES}


def ross_available(binary: str = "ross") -> bool:
    """Is a ross binary reachable? For test gating, mirroring how the DB
    tests skip on an unreachable DSN."""
    return shutil.which(binary) is not None
