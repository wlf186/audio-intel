#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-all}"
shift || true
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

PROFILE_PATH="$ROOT_DIR/.runtime/deployment-profile"
REQUESTED_PROFILE=""
while (( $# )); do
  case "$1" in
    --profile)
      [[ $# -ge 2 ]] || { echo "--profile requires full or cpu" >&2; exit 2; }
      REQUESTED_PROFILE="${2,,}"
      shift 2
      ;;
    *) echo "Unknown setup option: $1" >&2; exit 2 ;;
  esac
done
case "$REQUESTED_PROFILE" in ""|full|cpu) ;; *) echo "Profile must be full or cpu" >&2; exit 2 ;; esac

STORED_PROFILE="full"
if [[ -f "$PROFILE_PATH" ]]; then
  read -r STORED_PROFILE < "$PROFILE_PATH" || true
  STORED_PROFILE="${STORED_PROFILE,,}"
  case "$STORED_PROFILE" in full|cpu) ;; *) echo "Invalid deployment profile in $PROFILE_PATH" >&2; exit 1 ;; esac
fi
PROFILE="${REQUESTED_PROFILE:-$STORED_PROFILE}"

resolve_project_path() {
  local value="$1"
  [[ "$value" == /* ]] && printf '%s\n' "$value" || printf '%s/%s\n' "$ROOT_DIR" "$value"
}

if [[ -n "$REQUESTED_PROFILE" && "$PROFILE" != "$STORED_PROFILE" ]]; then
  existing_state=0
  [[ -f "$PROFILE_PATH" || -d "$ROOT_DIR/.runtime/asr" || -d "$ROOT_DIR/.runtime/tts" || -d "$ROOT_DIR/.runtime/aligner" ]] && existing_state=1
  if (( existing_state == 1 )) && [[ "$TARGET" != "all" ]]; then
    echo "Changing an existing deployment profile requires: ./service.sh setup all --profile $PROFILE" >&2
    exit 1
  fi
  if (( existing_state == 1 )); then
    guard_python="$ROOT_DIR/.runtime/api/bin/python"
    [[ -x "$guard_python" ]] || guard_python="$(command -v python3 || true)"
    [[ -n "$guard_python" ]] || { echo "Python is required to validate a profile switch" >&2; exit 1; }
    "$guard_python" "$ROOT_DIR/scripts/runtime_profile.py" guard-switch \
      --run-dir "$(resolve_project_path "${AUDIO_INTEL_RUN_DIR:-run}")" \
      --database "$(resolve_project_path "${AUDIO_INTEL_DATA_DIR:-data}")/audio_intel.sqlite3"
  fi
fi

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
  local lock_name="$name"
  local torch_backend="cu130"
  if [[ "$PROFILE" == "cpu" ]]; then
    lock_name="$name-cpu"
    torch_backend="cpu"
  fi
  local lock="$ROOT_DIR/requirements-lock/linux/${lock_name}.txt"
  if [[ -f "$lock" ]]; then
    "$UV_BIN" pip sync --python "$python" --torch-backend "$torch_backend" --require-hashes --strict "$lock"
  else
    "$UV_BIN" pip install --python "$python" --torch-backend "$torch_backend" -r "$ROOT_DIR/$requirements"
  fi
  "$python" "$ROOT_DIR/scripts/runtime_profile.py" validate "$PROFILE"
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

profile_temporary="$PROFILE_PATH.tmp.$$"
printf '%s\n' "$PROFILE" > "$profile_temporary"
mv -f "$profile_temporary" "$PROFILE_PATH"

echo "[setup] $TARGET is ready with the $PROFILE profile. All runtime files are inside $ROOT_DIR"
