#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-all}"
UV_BIN="$ROOT_DIR/.runtime/bin/uv"
export UV_CACHE_DIR="$ROOT_DIR/cache/uv"
export UV_PYTHON_INSTALL_DIR="$ROOT_DIR/.runtime/python"
export PIP_CACHE_DIR="$ROOT_DIR/cache/pip"
export HF_HUB_OFFLINE=0
export TRANSFORMERS_OFFLINE=0
mkdir -p "$ROOT_DIR/.runtime/bin" "$UV_CACHE_DIR" "$PIP_CACHE_DIR"

case "$TARGET" in all|api|asr|tts) ;; *) echo "Usage: $0 [all|api|asr|tts]" >&2; exit 2 ;; esac

retry() {
  local attempts="$1"; shift
  local attempt
  for ((attempt=1; attempt<=attempts; attempt++)); do
    "$@" && return 0
    [[ "$attempt" == "$attempts" ]] && return 1
    echo "[setup] Command failed; retrying ($((attempt + 1))/$attempts)..." >&2
    sleep $((attempt * 2))
  done
}

if [[ ! -x "$UV_BIN" ]]; then
  echo "[setup] Downloading the local uv installer..."
  archive="$ROOT_DIR/.runtime/uv.tar.gz"
  curl -fL --retry 3 -o "$archive" "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz"
  tar -xzf "$archive" -C "$ROOT_DIR/.runtime"
  install -m 755 "$ROOT_DIR/.runtime/uv-x86_64-unknown-linux-gnu/uv" "$UV_BIN"
  rm -f "$archive"
fi

create_env() {
  local name="$1"
  local requirements="$2"
  local env_dir="$ROOT_DIR/.runtime/$name"
  [[ -x "$env_dir/bin/python" ]] || "$UV_BIN" venv --python 3.12 "$env_dir"
  "$UV_BIN" pip install --python "$env_dir/bin/python" -r "$ROOT_DIR/$requirements"
}

create_empty_env() {
  local name="$1"
  local env_dir="$ROOT_DIR/.runtime/$name"
  [[ -x "$env_dir/bin/python" ]] || "$UV_BIN" venv --python 3.12 "$env_dir"
}

if [[ "$TARGET" == "all" || "$TARGET" == "api" ]]; then
  create_env api requirements-api.txt
  if [[ -f "$ROOT_DIR/frontend/package.json" ]]; then
    echo "[setup] Building the local frontend..."
    (cd "$ROOT_DIR/frontend" && retry 3 corepack pnpm@10.15.1 install --frozen-lockfile && corepack pnpm@10.15.1 build)
  fi
fi

if [[ "$TARGET" == "all" || "$TARGET" == "asr" ]]; then
  create_empty_env asr
  "$UV_BIN" pip install --python "$ROOT_DIR/.runtime/asr/bin/python" \
    torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu130
  "$UV_BIN" pip install --python "$ROOT_DIR/.runtime/asr/bin/python" -r "$ROOT_DIR/requirements-asr.txt"
  "$ROOT_DIR/.runtime/asr/bin/python" "$ROOT_DIR/scripts/download_models.py" asr
fi

if [[ "$TARGET" == "all" || "$TARGET" == "tts" ]]; then
  create_empty_env tts
  reinstall_tts=()
  if ! "$ROOT_DIR/.runtime/tts/bin/python" -c 'import torch; assert torch.version.cuda' >/dev/null 2>&1; then
    reinstall_tts=(--reinstall-package torch --reinstall-package torchaudio)
  fi
  "$UV_BIN" pip install --python "$ROOT_DIR/.runtime/tts/bin/python" "${reinstall_tts[@]}" \
    torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu130
  "$UV_BIN" pip install --python "$ROOT_DIR/.runtime/tts/bin/python" -r "$ROOT_DIR/requirements-tts.txt"
  "$ROOT_DIR/.runtime/tts/bin/python" "$ROOT_DIR/scripts/download_models.py" tts
fi

echo "[setup] $TARGET is ready. All runtime files are inside $ROOT_DIR"
