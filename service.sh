#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-status}"
TARGET="${2:-all}"
RUN_DIR="$ROOT_DIR/run"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR" "$ROOT_DIR/data" "$ROOT_DIR/tmp" "$ROOT_DIR/cache" "$ROOT_DIR/models"

case "$TARGET" in all|api|asr|tts) ;; *) echo "Target must be all, api, asr, or tts" >&2; exit 2 ;; esac

export PYTHONPATH="$ROOT_DIR"
export AUDIO_INTEL_HOST="${AUDIO_INTEL_HOST:-0.0.0.0}"
export AUDIO_INTEL_PORT="${AUDIO_INTEL_PORT:-20810}"
export AUDIO_INTEL_MOCK_MODE="${AUDIO_INTEL_MOCK_MODE:-0}"
export HF_HOME="$ROOT_DIR/cache/huggingface"
export HUGGINGFACE_HUB_CACHE="$ROOT_DIR/cache/huggingface/hub"
export MODELSCOPE_CACHE="$ROOT_DIR/cache/modelscope"
export TORCH_HOME="$ROOT_DIR/cache/torch"
export XDG_CACHE_HOME="$ROOT_DIR/cache/xdg"
export TMPDIR="$ROOT_DIR/tmp"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export TOKENIZERS_PARALLELISM=false

pid_alive() { [[ -f "$RUN_DIR/$1.pid" ]] && kill -0 "$(cat "$RUN_DIR/$1.pid")" 2>/dev/null; }

models_ready() {
  local component="$1"
  "$ROOT_DIR/.runtime/$component/bin/python" -c 'import sys; from audio_intel.config import settings; from audio_intel.model_registry import target_ready; raise SystemExit(0 if target_ready(settings.models_dir, sys.argv[1]) else 1)' "$component"
}

ensure_ready() {
  local component="$1"
  if [[ "$AUDIO_INTEL_MOCK_MODE" == "1" && "$component" != "api" ]]; then
    ensure_ready api
    return
  fi
  if [[ ! -x "$ROOT_DIR/.runtime/$component/bin/python" ]]; then
    "$ROOT_DIR/scripts/bootstrap.sh" "$component"
  fi
  if [[ "$component" == "api" ]] && { [[ ! -f "$ROOT_DIR/frontend/dist/index.html" ]] || [[ ! -f "$ROOT_DIR/frontend/dist/docs-assets/swagger-ui-bundle.js" ]] || [[ ! -f "$ROOT_DIR/frontend/dist/docs-assets/swagger-ui.css" ]]; }; then
    "$ROOT_DIR/scripts/bootstrap.sh" api
  fi
  if [[ "$component" == "asr" && "$AUDIO_INTEL_MOCK_MODE" != "1" ]] && ! models_ready asr; then
    "$ROOT_DIR/scripts/bootstrap.sh" asr
  fi
  if [[ "$component" == "tts" && "$AUDIO_INTEL_MOCK_MODE" != "1" ]] && { [[ ! -x "$ROOT_DIR/.runtime/aligner/bin/python" ]] || ! models_ready tts; }; then
    "$ROOT_DIR/scripts/bootstrap.sh" tts
  fi
}

start_one() {
  local component="$1"
  pid_alive "$component" && { echo "$component already running (pid $(cat "$RUN_DIR/$component.pid"))"; return; }
  ensure_ready "$component"
  if [[ "$component" == "api" ]]; then
    nohup "$ROOT_DIR/.runtime/api/bin/python" -m uvicorn audio_intel.api:app --host "$AUDIO_INTEL_HOST" --port "$AUDIO_INTEL_PORT" \
      >>"$LOG_DIR/api.log" 2>&1 &
  else
    local worker_python="$ROOT_DIR/.runtime/$component/bin/python"
    [[ "$AUDIO_INTEL_MOCK_MODE" == "1" ]] && worker_python="$ROOT_DIR/.runtime/api/bin/python"
    nohup "$worker_python" -m audio_intel.worker "$component" \
      >>"$LOG_DIR/$component.log" 2>&1 &
  fi
  echo $! > "$RUN_DIR/$component.pid"
  sleep 0.4
  if ! pid_alive "$component"; then
    echo "$component failed to start; see $LOG_DIR/$component.log" >&2
    tail -n 30 "$LOG_DIR/$component.log" >&2 || true
    exit 1
  fi
  echo "started $component (pid $(cat "$RUN_DIR/$component.pid"))"
}

stop_one() {
  local component="$1"
  if ! pid_alive "$component"; then rm -f "$RUN_DIR/$component.pid"; echo "$component is not running"; return; fi
  local pid; pid="$(cat "$RUN_DIR/$component.pid")"
  kill "$pid"
  for _ in {1..30}; do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
  kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" || true
  rm -f "$RUN_DIR/$component.pid"
  echo "stopped $component"
}

components() {
  case "$TARGET" in
    all) echo "api asr tts" ;;
    api) echo "api" ;;
    asr) echo "api asr" ;;
    tts) echo "api tts" ;;
  esac
}

start_targets() {
  case "$TARGET" in
    all) echo "api asr tts" ;;
    api) echo "api" ;;
    asr) echo "api asr" ;;
    tts) echo "api tts" ;;
  esac
}

stop_targets() {
  case "$TARGET" in
    all) echo "tts asr api" ;;
    api) echo "api" ;;
    asr) echo "asr" ;;
    tts) echo "tts" ;;
  esac
}

case "$ACTION" in
  start)
    if [[ -z "${AUDIO_INTEL_SERVICES:-}" ]]; then
      case "$TARGET" in
        asr) pid_alive tts && export AUDIO_INTEL_SERVICES=asr,tts || export AUDIO_INTEL_SERVICES=asr ;;
        tts) pid_alive asr && export AUDIO_INTEL_SERVICES=asr,tts || export AUDIO_INTEL_SERVICES=tts ;;
        *) export AUDIO_INTEL_SERVICES=asr,tts ;;
      esac
    fi
    if [[ "$TARGET" == "asr" || "$TARGET" == "tts" ]] && pid_alive api; then stop_one api; fi
    for component in $(start_targets); do start_one "$component"; done
    echo "Sandevistan-Audio: http://127.0.0.1:$AUDIO_INTEL_PORT"
    ;;
  stop) for component in $(stop_targets); do stop_one "$component"; done ;;
  restart) "$0" stop "$TARGET"; "$0" start "$TARGET" ;;
  status)
    for component in api asr tts; do
      if pid_alive "$component"; then echo "$component: running (pid $(cat "$RUN_DIR/$component.pid"))"; else echo "$component: stopped"; fi
    done
    ;;
  logs) tail -n 120 -F "$LOG_DIR"/$( [[ "$TARGET" == all ]] && echo '*.log' || echo "$TARGET.log" ) ;;
  setup) "$ROOT_DIR/scripts/bootstrap.sh" "$TARGET" ;;
  doctor) python3 "$ROOT_DIR/scripts/doctor.py" ;;
  *) echo "Usage: ./service.sh {start|stop|restart|status|logs|setup|doctor} [all|asr|tts|api]" >&2; exit 2 ;;
esac
