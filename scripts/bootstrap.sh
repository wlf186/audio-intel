#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-all}"
UV_BIN="$ROOT_DIR/.runtime/bin/uv"
UV_VERSION="0.12.5"
UV_LINUX_SHA256="68a509da24b06b4223a1c0175fb5eb5bc79342b76cbeff0cfe51ac3f5b17b6b2"
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

if [[ ! -x "$UV_BIN" ]] || [[ "$($UV_BIN --version 2>/dev/null || true)" != "uv $UV_VERSION"* ]]; then
  echo "[setup] Downloading uv $UV_VERSION..."
  archive="$ROOT_DIR/.runtime/uv.tar.gz"
  curl -fL --retry 3 -o "$archive" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz"
  echo "$UV_LINUX_SHA256  $archive" | sha256sum --check --status || { echo "[setup] uv checksum verification failed" >&2; exit 1; }
  tar -xzf "$archive" -C "$ROOT_DIR/.runtime"
  install -m 755 "$ROOT_DIR/.runtime/uv-x86_64-unknown-linux-gnu/uv" "$UV_BIN"
  rm -f "$archive"
fi

create_env() {
  local name="$1"
  local requirements="$2"
  local env_dir="$ROOT_DIR/.runtime/$name"
  [[ -x "$env_dir/bin/python" ]] || "$UV_BIN" venv --python 3.12 "$env_dir"
  local lock="$ROOT_DIR/requirements-lock/linux/${name}.txt"
  if [[ -f "$lock" ]]; then
    "$UV_BIN" pip sync --python "$env_dir/bin/python" --require-hashes --strict "$lock"
  else
    "$UV_BIN" pip install --python "$env_dir/bin/python" -r "$ROOT_DIR/$requirements"
  fi
}

create_empty_env() {
  local name="$1"
  local env_dir="$ROOT_DIR/.runtime/$name"
  [[ -x "$env_dir/bin/python" ]] || "$UV_BIN" venv --python 3.12 "$env_dir"
}

sync_model_env() {
  local name="$1"
  local requirements="$2"
  local python="$ROOT_DIR/.runtime/$name/bin/python"
  local lock="$ROOT_DIR/requirements-lock/linux/${name}.txt"
  if [[ -f "$lock" ]]; then
    "$UV_BIN" pip sync --python "$python" --torch-backend cu130 --require-hashes --strict "$lock"
  else
    "$UV_BIN" pip install --python "$python" --torch-backend cu130 -r "$ROOT_DIR/$requirements"
  fi
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
  sync_model_env asr requirements-asr.txt
  "$ROOT_DIR/.runtime/asr/bin/python" "$ROOT_DIR/scripts/download_models.py" asr
fi

if [[ "$TARGET" == "all" || "$TARGET" == "tts" ]]; then
  create_empty_env tts
  sync_model_env tts requirements-tts.txt
  create_empty_env aligner
  sync_model_env aligner requirements-aligner.txt
  "$ROOT_DIR/.runtime/tts/bin/python" "$ROOT_DIR/scripts/download_models.py" tts
fi

echo "[setup] $TARGET is ready. All runtime files are inside $ROOT_DIR"
