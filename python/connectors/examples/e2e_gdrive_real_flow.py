#!/usr/bin/env python3
"""# Demo: Google Drive URL → real OAuth → ingest → summarize → pgvector

End-to-end. Point at a Drive folder URL or a single-doc URL, do the real
Google OAuth dance via a loopback redirect, fetch the content, print an
extractive summary, and persist the full pipeline (Source → Chunker →
Embedder → pgvector sink) to Postgres.

## Prerequisites

1. **Google OAuth client** (one-time setup):
   - Visit https://console.cloud.google.com/apis/credentials
   - Create an OAuth client ID, type = **Desktop app**
   - Add this redirect URI exactly: `http://localhost:8765/callback`
   - Export the credentials:
     ```
     export GDRIVE_CLIENT_ID='<your-client-id>.apps.googleusercontent.com'
     export GDRIVE_CLIENT_SECRET='<your-client-secret>'
     ```

2. **Postgres test stack** (for the pgvector sink):
   ```
   docker compose -f docker-compose.test.yaml up -d
   ```

## Usage

    # Folder
    python e2e_gdrive_real_flow.py 'https://drive.google.com/drive/folders/0BabcXYZ...'

    # Single Google Doc
    python e2e_gdrive_real_flow.py 'https://docs.google.com/document/d/1abcXYZ.../edit'

    # Single Drive file (text-ish)
    python e2e_gdrive_real_flow.py 'https://drive.google.com/file/d/1abcXYZ.../view'

    # Force re-auth (drop cached tokens)
    python e2e_gdrive_real_flow.py --auth ...

    # Skip the pgvector sink (only print the summary)
    python e2e_gdrive_real_flow.py --no-pgvector ...

Tokens are cached at `~/.chunkshop/gdrive-tokens.json` and proactively
refreshed when they're near expiry on subsequent runs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from queue import Empty, Queue

DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5434/chunkshop_test"
TOKEN_CACHE = Path.home() / ".chunkshop" / "gdrive-tokens.json"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
DEFAULT_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Drive URL → (kind, id) — order matters: docs.google.com matches "doc" first.
_DRIVE_PATTERNS = [
    (re.compile(r"docs\.google\.com/document/d/([A-Za-z0-9_-]+)"), "doc"),
    (re.compile(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)"), "doc"),
    (re.compile(r"docs\.google\.com/presentation/d/([A-Za-z0-9_-]+)"), "doc"),
    (re.compile(r"drive\.google\.com/drive/folders/([A-Za-z0-9_-]+)"), "folder"),
    (re.compile(r"drive\.google\.com/drive/u/\d+/folders/([A-Za-z0-9_-]+)"), "folder"),
    (re.compile(r"drive\.google\.com/file/d/([A-Za-z0-9_-]+)"), "file"),
    (re.compile(r"drive\.google\.com/open\?id=([A-Za-z0-9_-]+)"), "file"),
]


def parse_drive_url(url: str) -> tuple[str, str]:
    for pat, kind in _DRIVE_PATTERNS:
        m = pat.search(url)
        if m:
            return kind, m.group(1)
    raise ValueError(
        f"unrecognized Drive URL: {url!r}\n"
        "  Expected one of:\n"
        "    https://drive.google.com/drive/folders/<ID>\n"
        "    https://docs.google.com/document/d/<ID>/edit\n"
        "    https://drive.google.com/file/d/<ID>/view"
    )


# ---------- OAuth loopback flow ----------


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server contract
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        error = params.get("error", [None])[0]
        self.server.code_queue.put((code, error))  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = (
            b"<html><body style='font-family:sans-serif;text-align:center;padding:3rem'>"
            b"<h2>Authentication complete.</h2>"
            b"<p>You may close this tab and return to the terminal.</p>"
            b"</body></html>"
        )
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):  # noqa: D401 — silence stdlib http.server
        pass


def _run_oauth_flow(client_id: str, client_secret: str, scopes: list[str]):
    from chunkshop_connectors.oauth.google import GoogleOAuthProvider

    provider = GoogleOAuthProvider(client_id=client_id, client_secret=client_secret)
    state = f"chunkshop-demo-{int(time.time())}"
    auth_url = provider.authorization_url(
        state=state, redirect_uri=REDIRECT_URI, scopes=scopes
    )

    httpd = HTTPServer(("localhost", REDIRECT_PORT), _OAuthCallbackHandler)
    httpd.code_queue = Queue()  # type: ignore[attr-defined]
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    print("\n  Open this URL in your browser to grant access:\n")
    print(f"    {auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
    print(f"  Waiting for callback on {REDIRECT_URI} (5 min timeout)...")

    try:
        try:
            code, error = httpd.code_queue.get(timeout=300)  # type: ignore[attr-defined]
        except Empty:
            raise RuntimeError("OAuth timeout — no callback received within 5 minutes")
    finally:
        httpd.shutdown()
        server_thread.join(timeout=2)

    if error:
        raise RuntimeError(f"OAuth error from Google: {error}")
    if not code:
        raise RuntimeError("OAuth callback received without a code")

    print("  Exchanging authorization code for tokens...")
    return provider.exchange_code(code=code, redirect_uri=REDIRECT_URI)


def _tokens_to_dict(tokens) -> dict:
    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at.isoformat(),
        "scopes": list(tokens.scopes),
        "provider": tokens.provider,
        "provider_extras": dict(tokens.provider_extras),
    }


def _dict_to_tokens(d: dict):
    from chunkshop.oauth import OAuthTokens

    return OAuthTokens(
        access_token=d["access_token"],
        refresh_token=d.get("refresh_token"),
        expires_at=datetime.fromisoformat(d["expires_at"]),
        scopes=d.get("scopes", []),
        provider=d.get("provider", "google"),
        provider_extras=d.get("provider_extras", {}),
    )


def _save_tokens(tokens) -> None:
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps(_tokens_to_dict(tokens), indent=2))
    # Tighten perms so the refresh token isn't world-readable.
    os.chmod(TOKEN_CACHE, 0o600)
    print(f"  tokens saved to {TOKEN_CACHE}")


def authenticate(client_id: str, client_secret: str, scopes: list[str], force: bool = False):
    """Load cached tokens (refreshing if near expiry) or run the loopback OAuth flow."""
    if not force and TOKEN_CACHE.exists():
        try:
            tokens = _dict_to_tokens(json.loads(TOKEN_CACHE.read_text()))
            print(f"  using cached tokens from {TOKEN_CACHE}")
            from chunkshop.oauth import proactive_refresh
            from chunkshop_connectors.oauth.google import GoogleOAuthProvider

            provider = GoogleOAuthProvider(
                client_id=client_id, client_secret=client_secret
            )
            refreshed = proactive_refresh(tokens, provider=provider, leeway_minutes=5)
            if refreshed:
                print("  refreshed access token (was near expiry)")
                tokens = refreshed
                _save_tokens(tokens)
            return tokens
        except Exception as exc:
            print(f"  cached tokens unusable ({exc}); re-authenticating")

    tokens = _run_oauth_flow(client_id, client_secret, scopes)
    _save_tokens(tokens)
    return tokens


# ---------- Ingest ----------


def _fetch_single_doc(file_id: str, tokens):
    """Single-doc URLs bypass the connector and go straight to the Drive REST API."""
    import httpx

    from chunkshop.sources.base import Document

    base = "https://www.googleapis.com/drive/v3"
    headers = {"Authorization": f"Bearer {tokens.access_token}"}

    meta_resp = httpx.get(
        f"{base}/files/{file_id}",
        headers=headers,
        params={"fields": "id,name,mimeType,modifiedTime"},
        timeout=30.0,
    )
    if meta_resp.status_code == 401:
        raise RuntimeError(
            "Drive returned 401 — token may be expired or the file isn't shared with you."
        )
    meta_resp.raise_for_status()
    meta = meta_resp.json()

    mime = meta["mimeType"]
    print(f"  doc: {meta['name']!r}  mime={mime}")

    if mime == "application/vnd.google-apps.document":
        body_resp = httpx.get(
            f"{base}/files/{file_id}/export",
            headers=headers,
            params={"mimeType": "text/plain"},
            timeout=60.0,
        )
    elif mime == "application/vnd.google-apps.spreadsheet":
        body_resp = httpx.get(
            f"{base}/files/{file_id}/export",
            headers=headers,
            params={"mimeType": "text/csv"},
            timeout=60.0,
        )
    elif mime == "application/vnd.google-apps.presentation":
        body_resp = httpx.get(
            f"{base}/files/{file_id}/export",
            headers=headers,
            params={"mimeType": "text/plain"},
            timeout=60.0,
        )
    elif mime.startswith("text/") or mime in (
        "application/json",
        "application/xml",
        "application/x-yaml",
    ):
        body_resp = httpx.get(
            f"{base}/files/{file_id}",
            headers=headers,
            params={"alt": "media"},
            timeout=60.0,
        )
    else:
        raise RuntimeError(
            f"unsupported MIME for direct ingest: {mime}. "
            f"Try a Google Doc / Sheet / Slides URL, or a text file."
        )
    body_resp.raise_for_status()
    content = body_resp.text

    return Document(
        id=meta["id"],
        content=content,
        title=meta["name"],
        metadata={
            "drive_id": meta["id"],
            "mime_type": mime,
            "modified_time": meta.get("modifiedTime"),
        },
    )


def _fetch_folder(folder_id: str, tokens):
    """Folder URLs go through the gdrive connector (yields chunkshop Documents)."""
    from chunkshop.config import ConnectorSource
    from chunkshop.sources import load_source

    cfg = ConnectorSource(
        type="connector",
        connector="gdrive",
        config={
            "folder_id": folder_id,
            "oauth_tokens": _tokens_to_dict(tokens),
        },
    )
    src = load_source(cfg)
    return list(src.iter_documents())


# ---------- Summarize ----------


def summarize(text: str, max_length: int = 500) -> str:
    """Lede extractive summary if available, otherwise first-N-sentences fallback."""
    try:
        from chunkshop.summarizers.lede import summarize as lede_summarize
    except ImportError:
        return _fallback_summary(text, max_length)
    try:
        return lede_summarize(text, max_length=max_length)
    except Exception:
        return _fallback_summary(text, max_length)


def _fallback_summary(text: str, max_length: int) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out, length = [], 0
    for s in sentences:
        if not s:
            continue
        if length + len(s) > max_length and out:
            break
        out.append(s)
        length += len(s) + 1
    return " ".join(out)


# ---------- Persist via the full chunkshop pipeline ----------


CHUNKER_CHOICES = {
    "sentence_aware": lambda: __import__("chunkshop.config", fromlist=["SentenceAwareChunker"]).SentenceAwareChunker(
        type="sentence_aware", min_chars=200, max_chars=1200
    ),
    "fixed_overlap": lambda: __import__("chunkshop.config", fromlist=["FixedOverlapChunker"]).FixedOverlapChunker(
        type="fixed_overlap", window_words=200, step_words=100
    ),
    "hierarchy": lambda: __import__("chunkshop.config", fromlist=["HierarchyChunker"]).HierarchyChunker(
        type="hierarchy", max_chars=1200
    ),
    "neighbor_expand": lambda: __import__(
        "chunkshop.config", fromlist=["NeighborExpandChunker"]
    ).NeighborExpandChunker(type="neighbor_expand", max_chars=1200),
    "code_aware": lambda: __import__("chunkshop.config", fromlist=["CodeAwareChunker"]).CodeAwareChunker(
        type="code_aware", max_chars=4000, include_imports=True
    ),
}

EXTRACTOR_CHOICES = {
    "none": lambda: __import__("chunkshop.config", fromlist=["NoneExtractor"]).NoneExtractor(type="none"),
    "rake_keywords": lambda: __import__("chunkshop.config", fromlist=["RakeKeywordsExtractor"]).RakeKeywordsExtractor(
        type="rake_keywords", top_k=10, min_chars=4
    ),
    "lang_detect": lambda: __import__("chunkshop.config", fromlist=["LangDetectExtractor"]).LangDetectExtractor(
        type="lang_detect"
    ),
    "lede_top_terms": lambda: __import__(
        "chunkshop.config", fromlist=["LedeTopTermsExtractor"]
    ).LedeTopTermsExtractor(type="lede_top_terms"),
    "composite_keywords_lang": lambda: __import__(
        "chunkshop.config", fromlist=["CompositeExtractor", "RakeKeywordsExtractor", "LangDetectExtractor"]
    ).CompositeExtractor(
        type="composite",
        extractors=[
            __import__("chunkshop.config", fromlist=["RakeKeywordsExtractor"]).RakeKeywordsExtractor(
                type="rake_keywords", top_k=10, min_chars=4
            ),
            __import__("chunkshop.config", fromlist=["LangDetectExtractor"]).LangDetectExtractor(
                type="lang_detect"
            ),
        ],
    ),
}


def persist_to_pgvector(
    docs,
    *,
    dsn: str,
    cell_name: str = "gdrive_demo",
    chunker_name: str = "sentence_aware",
    extractor_name: str = "none",
):
    """Write docs to a temp dir as .txt files, then drive the full
    Source → Chunker → Extractor → Embedder → pgvector pipeline via run_cell.

    `chunker_name` picks one of CHUNKER_CHOICES; `extractor_name` picks one of
    EXTRACTOR_CHOICES so callers can mix metadata-extraction strategies.

    Returns (docs_processed, chunks_written, schema, table).
    """
    from chunkshop.config import (
        CellConfig,
        FastembedEmbedder,
        FilesSource,
        RuntimeConfig,
        TargetConfig,
    )
    from chunkshop.runner import run_cell

    if chunker_name not in CHUNKER_CHOICES:
        raise ValueError(
            f"unknown chunker {chunker_name!r}; choose from {sorted(CHUNKER_CHOICES)}"
        )
    if extractor_name not in EXTRACTOR_CHOICES:
        raise ValueError(
            f"unknown extractor {extractor_name!r}; choose from {sorted(EXTRACTOR_CHOICES)}"
        )

    os.environ["CHUNKSHOP_GDRIVE_DEMO_DSN"] = dsn
    schema = "chunkshop_gdrive_demo"
    table = "ingested_docs"

    with tempfile.TemporaryDirectory(prefix="chunkshop-gdrive-") as tmp:
        for i, doc in enumerate(docs):
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", doc.title or doc.id) or f"doc_{i}"
            (Path(tmp) / f"{i:03d}_{safe}.txt").write_text(doc.content or "")

        cfg = CellConfig(
            cell_name=cell_name,
            source=FilesSource(type="files", glob=f"{tmp}/*.txt", id_from="stem"),
            chunker=CHUNKER_CHOICES[chunker_name](),
            embedder=FastembedEmbedder(
                type="fastembed",
                model_name="Xenova/bge-small-en-v1.5-int8",
                dim=384,
                batch_size=64,
                threads=2,
            ),
            extractor=EXTRACTOR_CHOICES[extractor_name](),
            target=TargetConfig(
                type="postgres",
                dsn_env="CHUNKSHOP_GDRIVE_DEMO_DSN",
                database=schema,
                table=table,
                mode="overwrite",
                hnsw=False,
                promote_metadata=(
                    [{"path": "language", "type": "text"}]
                    if extractor_name in ("lang_detect", "composite_keywords_lang")
                    else []
                ),
            ),
            runtime=RuntimeConfig(omp_num_threads=2, heartbeat_every=5),
        )
        result = run_cell(cfg)
        if result.error:
            raise RuntimeError(f"run_cell errored: {result.error}")
        return result.docs_processed, result.chunks_written, schema, table


def _postgres_reachable(dsn: str) -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            return True
    except Exception:
        return False


# ---------- Main ----------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="Google Drive folder URL or doc/file URL")
    parser.add_argument(
        "--auth", action="store_true", help="Force re-authentication (clears cached tokens)"
    )
    parser.add_argument(
        "--summary-length", type=int, default=500, help="Max summary characters per doc"
    )
    parser.add_argument(
        "--chunker",
        default="sentence_aware",
        choices=sorted(CHUNKER_CHOICES),
        help="Chunking strategy used by the pgvector sink (default: sentence_aware)",
    )
    parser.add_argument(
        "--extractor",
        default="none",
        choices=sorted(EXTRACTOR_CHOICES),
        help="Metadata extractor applied per chunk (default: none). "
        "rake_keywords / lang_detect / lede_top_terms / composite_keywords_lang "
        "exercise chunkshop's existing metadata-extraction extras.",
    )
    parser.add_argument(
        "--no-pgvector", action="store_true", help="Skip the pgvector sink (summary only)"
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("CHUNKSHOP_TEST_DSN", DEFAULT_DSN),
        help=f"Postgres DSN (default: {DEFAULT_DSN})",
    )
    args = parser.parse_args(argv)

    print("=" * 72)
    print("# Demo: Google Drive URL -> OAuth -> ingest -> summarize -> pgvector")
    print("=" * 72)

    client_id = os.environ.get("GDRIVE_CLIENT_ID")
    client_secret = os.environ.get("GDRIVE_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "\n  ERROR: set GDRIVE_CLIENT_ID and GDRIVE_CLIENT_SECRET (see module docstring).\n"
            "  https://console.cloud.google.com/apis/credentials → OAuth client (Desktop)\n"
            f"  Add redirect URI exactly: {REDIRECT_URI}",
            file=sys.stderr,
        )
        return 2

    try:
        kind, drive_id = parse_drive_url(args.url)
    except ValueError as exc:
        print(f"\n  {exc}", file=sys.stderr)
        return 2
    print(f"  Parsed: kind={kind!r}  id={drive_id!r}")

    tokens = authenticate(client_id, client_secret, DEFAULT_SCOPES, force=args.auth)

    print(f"\n  Ingesting from Drive ({kind})...")
    if kind == "folder":
        docs = _fetch_folder(drive_id, tokens)
    else:
        docs = [_fetch_single_doc(drive_id, tokens)]
    print(f"  Fetched {len(docs)} document(s) totaling "
          f"{sum(len(d.content or '') for d in docs):,} chars")

    if not docs:
        print("\n  (no docs ingested)")
        return 0

    print("\n  --- Summaries ---")
    for doc in docs:
        text = doc.content or ""
        summary = summarize(text, max_length=args.summary_length)
        print(f"\n  [{doc.title!r}] ({len(text):,} chars)")
        for line in summary.split("\n"):
            print(f"    {line}")

    if args.no_pgvector:
        print("\n  --no-pgvector set; skipping sink.")
        print("\n  done.")
        return 0

    if not _postgres_reachable(args.dsn):
        print(
            f"\n  WARN: Postgres at {args.dsn} unreachable. "
            "Start the test stack with `docker compose -f docker-compose.test.yaml up -d`.",
            file=sys.stderr,
        )
        print("  Summary printed above; skipping pgvector sink.")
        return 0

    print(
        f"\n  Persisting via Source -> Chunker({args.chunker}) -> "
        f"Extractor({args.extractor}) -> Embedder -> pgvector ({args.dsn})..."
    )
    print("  loading fastembed model on first run (Xenova/bge-small-en-v1.5-int8, ~30 MB)...")
    t0 = time.time()
    n_docs, n_chunks, schema, table = persist_to_pgvector(
        docs,
        dsn=args.dsn,
        chunker_name=args.chunker,
        extractor_name=args.extractor,
    )
    print(
        f"  run_cell wall {time.time() - t0:.1f}s — wrote {n_chunks} chunk(s) "
        f"from {n_docs} doc(s) into {schema}.{table} "
        f"[chunker={args.chunker}, extractor={args.extractor}]"
    )

    print("\n  Verifying with a quick SELECT...")
    import psycopg

    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT count(*), count(distinct doc_id) FROM "{schema}"."{table}"'
        )
        nchunks_db, ndocs_db = cur.fetchone()
        print(f"  SELECT: {nchunks_db} chunk row(s), {ndocs_db} doc(s)")
        cur.execute(
            f'SELECT doc_id, seq_num, length(original_content) FROM "{schema}"."{table}" '
            "ORDER BY doc_id, seq_num LIMIT 5"
        )
        for doc_id, seq, n in cur.fetchall():
            print(f"    {doc_id!r:40}  seq={seq:<3}  len={n}")

    print(
        f"\n  Schema {schema}.{table} retained for inspection. Drop with:\n"
        f"    psql {args.dsn} -c 'DROP SCHEMA IF EXISTS {schema} CASCADE'"
    )
    print("\n  done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
