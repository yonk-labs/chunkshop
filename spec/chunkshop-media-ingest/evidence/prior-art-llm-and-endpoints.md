# Prior art — ../llm, and what model endpoints actually exist on this box

## ../llm = simonw/llm v0.33 (upstream OSS, not a Yonk tool)
- `git remote`: git@github.com:simonw/llm.git; HEAD a463c63 2026-08-22 "Release 0.33"
- User ran /reverse-engineer on it TODAY 05:42-05:47 (skill-output/reverse-engineer/), minutes before this spec session.
- **Attachments** (`docs/usage.md:65-87`): `-a path|url`, images/audio/video, auto content-type
  detect, `--at` to force type, multiple per prompt, pipe with `-`. Gemini models take audio+video.
- **Schemas** (`docs/schemas.md`): `--schema 'caption, art_style, tags multi'` concise DSL →
  deterministic JSON out. `--schema-multi` for batches. Supported on OpenAI/Anthropic/Gemini + plugins.
- Logs every prompt/response to SQLite.
- **NOT installed**: no `llm` on PATH, no venv binary. `uv tool install llm` would fix.

## Local model endpoints — NONE for vision/ASR
- No ollama, no llama-server/llama.cpp, no whisper/faster-whisper/whisper.cpp.
- Listening ports are Postgres (5432/5434/5435/5437/55432/55433), Unity, and misc Python app servers.
- Only reachable model hosts are REMOTE and GENERATIVE, not understanding:
  yoap-studio `192.168.1.193:8600`, yonk-image local Qwen endpoint.
- gamelib's strong tier expects an OpenAI-compatible vision endpoint via env var; verified
  historically against `google/gemma-4-E4B-it` on vLLM (not currently running).
