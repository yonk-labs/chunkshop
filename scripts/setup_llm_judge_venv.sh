#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${1:-"$ROOT/.venv-llm-judge"}"
SPEC="${LLM_JUDGE_SPEC:-llm-judge @ git+https://github.com/yonk-labs/llm-judge.git}"

if command -v uv >/dev/null 2>&1; then
  if [[ ! -x "$VENV/bin/python" ]]; then
    uv venv "$VENV" --python python3
  fi
  uv pip install --python "$VENV/bin/python" "$SPEC"
else
  if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
  fi
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install "$SPEC"
fi

echo "$VENV/bin/llm-judge"
