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

    # --- provenance -------------------------------------------------------

    def _params(self) -> dict:
        """Everything that changes ross's OUTPUT, and nothing that doesn't.

        `binary` and `timeout_s` are deliberately absent: the path a
        binary is reached by does not change what it computes (its version
        does, and that is `model_version`), and a timeout only bounds how
        long we wait. `llm` and the vocabulary identity DO change output.
        """
        return {"llm": self.llm, "labels_sha256": self.labels_sha256}

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
            "ross_model": entry.get("model"),
            "ross_metadata": entry.get("metadata") or {},
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


def ross_available(binary: str = "ross") -> bool:
    """Is a ross binary reachable? For test gating, mirroring how the DB
    tests skip on an unreachable DSN."""
    return shutil.which(binary) is not None
