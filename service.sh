#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-status}"
TARGET="${2:-all}"
START_TIMEOUT_SECONDS=20
STOP_TIMEOUT_SECONDS=15

resolve_dir() {
  local value="$1"
  [[ "$value" == /* ]] && printf '%s\n' "$value" || printf '%s/%s\n' "$ROOT_DIR" "$value"
}

DATA_DIR="$(resolve_dir "${AUDIO_INTEL_DATA_DIR:-data}")"
TEMP_DIR="$(resolve_dir "${AUDIO_INTEL_TEMP_DIR:-tmp}")"
CACHE_DIR="$(resolve_dir "${AUDIO_INTEL_CACHE_DIR:-cache}")"
LOG_DIR="$(resolve_dir "${AUDIO_INTEL_LOG_DIR:-logs}")"
RUN_DIR="$(resolve_dir "${AUDIO_INTEL_RUN_DIR:-run}")"
MODELS_DIR="$(resolve_dir "${AUDIO_INTEL_MODELS_DIR:-models}")"
FRONTEND_DIR="$(resolve_dir "${AUDIO_INTEL_FRONTEND_DIR:-frontend/dist}")"

if [[ "$ACTION" == "tls" ]]; then
  case "$TARGET" in create|renew|fingerprint) ;; *) echo "Usage: ./service.sh tls {create|renew|fingerprint} [--host HOST ...]" >&2; exit 2 ;; esac
  exec python3 "$ROOT_DIR/scripts/setup_local_tls.py" "$TARGET" "${@:3}"
fi

case "$TARGET" in all|api|asr|tts) ;; *) echo "Target must be all, api, asr, or tts" >&2; exit 2 ;; esac

export PYTHONPATH="$ROOT_DIR"
export PYTHONUNBUFFERED=1
export AUDIO_INTEL_HOST="${AUDIO_INTEL_HOST:-0.0.0.0}"
export AUDIO_INTEL_PORT="${AUDIO_INTEL_PORT:-20810}"
export AUDIO_INTEL_PROTOCOL="${AUDIO_INTEL_PROTOCOL:-http}"
export AUDIO_INTEL_PROTOCOL="${AUDIO_INTEL_PROTOCOL,,}"
export AUDIO_INTEL_MOCK_MODE="${AUDIO_INTEL_MOCK_MODE:-0}"
export AUDIO_INTEL_DATA_DIR="$DATA_DIR"
export AUDIO_INTEL_TEMP_DIR="$TEMP_DIR"
export AUDIO_INTEL_CACHE_DIR="$CACHE_DIR"
export AUDIO_INTEL_LOG_DIR="$LOG_DIR"
export AUDIO_INTEL_RUN_DIR="$RUN_DIR"
export AUDIO_INTEL_MODELS_DIR="$MODELS_DIR"
export AUDIO_INTEL_FRONTEND_DIR="$FRONTEND_DIR"
if [[ -n "${AUDIO_INTEL_TLS_CERT_FILE:-}" ]]; then export AUDIO_INTEL_TLS_CERT_FILE="$(resolve_dir "$AUDIO_INTEL_TLS_CERT_FILE")"; fi
if [[ -n "${AUDIO_INTEL_TLS_KEY_FILE:-}" ]]; then export AUDIO_INTEL_TLS_KEY_FILE="$(resolve_dir "$AUDIO_INTEL_TLS_KEY_FILE")"; fi
if [[ -n "${AUDIO_INTEL_TLS_CA_FILE:-}" ]]; then export AUDIO_INTEL_TLS_CA_FILE="$(resolve_dir "$AUDIO_INTEL_TLS_CA_FILE")"; fi
export HF_HOME="${HF_HOME:-$CACHE_DIR/huggingface}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-$HF_HOME/hub}"
export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$CACHE_DIR/modelscope}"
export TORCH_HOME="${TORCH_HOME:-$CACHE_DIR/torch}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$CACHE_DIR/xdg}"
export TMPDIR="${TMPDIR:-$TEMP_DIR}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export TOKENIZERS_PARALLELISM=false

mkdir -p "$RUN_DIR" "$LOG_DIR" "$DATA_DIR" "$TEMP_DIR" "$CACHE_DIR" "$MODELS_DIR"

pid_path() { printf '%s/%s.pid\n' "$RUN_DIR" "$1"; }

read_pid() {
  local path pid
  path="$(pid_path "$1")"
  [[ -r "$path" ]] || return 1
  read -r pid < "$path" || return 1
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "$pid"
}

process_matches() {
  local component="$1" pid="$2" command state
  [[ -r "/proc/$pid/cmdline" && -r "/proc/$pid/stat" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  state="$(sed -E 's/^.*\) ([A-Z]) .*$/\1/' "/proc/$pid/stat" 2>/dev/null || true)"
  [[ "$state" != "Z" ]] || return 1
  command="$( { tr '\0' ' ' < "/proc/$pid/cmdline"; } 2>/dev/null || true)"
  case "$component" in
    api) [[ "$command" == *"-m uvicorn audio_intel.api:app"* ]] ;;
    asr|tts) [[ "$command" == *"-m audio_intel.worker $component"* ]] ;;
  esac
}

pid_alive() {
  local pid
  pid="$(read_pid "$1")" || return 1
  process_matches "$1" "$pid"
}

remove_stale_pid() {
  local component="$1" path
  path="$(pid_path "$component")"
  [[ ! -e "$path" ]] || pid_alive "$component" || rm -f "$path"
}

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
  if [[ "$component" == "api" ]] && { [[ ! -f "$FRONTEND_DIR/index.html" ]] || [[ ! -f "$FRONTEND_DIR/docs-assets/swagger-ui-bundle.js" ]] || [[ ! -f "$FRONTEND_DIR/docs-assets/swagger-ui.css" ]]; }; then
    "$ROOT_DIR/scripts/bootstrap.sh" api
    if [[ ! -f "$FRONTEND_DIR/index.html" || ! -f "$FRONTEND_DIR/docs-assets/swagger-ui-bundle.js" || ! -f "$FRONTEND_DIR/docs-assets/swagger-ui.css" ]]; then
      echo "Configured frontend directory is incomplete: $FRONTEND_DIR" >&2
      return 1
    fi
  fi
  if [[ "$component" == "asr" && "$AUDIO_INTEL_MOCK_MODE" != "1" ]] && ! models_ready asr; then
    "$ROOT_DIR/scripts/bootstrap.sh" asr
  fi
  if [[ "$component" == "tts" && "$AUDIO_INTEL_MOCK_MODE" != "1" ]] && { [[ ! -x "$ROOT_DIR/.runtime/aligner/bin/python" ]] || ! models_ready tts; }; then
    "$ROOT_DIR/scripts/bootstrap.sh" tts
  fi
}

component_command() {
  local component="$1"
  if [[ "$component" == "api" ]]; then
    COMPONENT_COMMAND=("$ROOT_DIR/.runtime/api/bin/python" -m uvicorn audio_intel.api:app --host "$AUDIO_INTEL_HOST" --port "$AUDIO_INTEL_PORT")
    if [[ "$AUDIO_INTEL_PROTOCOL" == "https" ]]; then
      COMPONENT_COMMAND+=(--ssl-certfile "$AUDIO_INTEL_TLS_CERT_FILE" --ssl-keyfile "$AUDIO_INTEL_TLS_KEY_FILE")
    fi
  else
    local worker_python="$ROOT_DIR/.runtime/$component/bin/python"
    [[ "$AUDIO_INTEL_MOCK_MODE" == "1" ]] && worker_python="$ROOT_DIR/.runtime/api/bin/python"
    COMPONENT_COMMAND=("$worker_python" -m audio_intel.worker "$component")
  fi
}

launch_component() {
  local component="$1" mode="$2" log="$LOG_DIR/$component.log" pid
  component_command "$component"
  if [[ "$mode" == "foreground" ]]; then
    "${COMPONENT_COMMAND[@]}" > >(tee -a "$log") 2> >(tee -a "$log" >&2) &
    pid="$!"
  else
    pid="$("$ROOT_DIR/.runtime/api/bin/python" "$ROOT_DIR/scripts/service_process.py" \
      launch-detached "$log" "${COMPONENT_COMMAND[@]}")" || return 1
  fi
  if [[ ! "$pid" =~ ^[1-9][0-9]*$ ]]; then
    echo "invalid $component process id returned during launch: $pid" >&2
    return 1
  fi
  printf '%s\n' "$pid" > "$(pid_path "$component")"
  [[ "$mode" != "foreground" ]] || RUN_PIDS["$component"]="$pid"
}

wait_ready() {
  local component="$1" pid
  pid="$(read_pid "$component")" || return 1
  if [[ "$component" == "api" ]]; then
    "$ROOT_DIR/.runtime/api/bin/python" "$ROOT_DIR/scripts/service_process.py" \
      wait-api "$pid" "$AUDIO_INTEL_HOST" "$AUDIO_INTEL_PORT" "$START_TIMEOUT_SECONDS" \
      "$AUDIO_INTEL_PROTOCOL"
  else
    "$ROOT_DIR/.runtime/api/bin/python" "$ROOT_DIR/scripts/service_process.py" \
      wait-worker "$component" "$pid" "$START_TIMEOUT_SECONDS"
  fi
}

cleanup_component() {
  local component="$1" pid="$2"
  if [[ -x "$ROOT_DIR/.runtime/api/bin/python" ]]; then
    "$ROOT_DIR/.runtime/api/bin/python" "$ROOT_DIR/scripts/service_process.py" cleanup "$component" "$pid"
  else
    process_matches "$component" "$pid" && kill -KILL "$pid" 2>/dev/null || true
  fi
}

STARTED_NEW=0
start_one() {
  local component="$1" mode="$2" pid
  STARTED_NEW=0
  remove_stale_pid "$component"
  if pid_alive "$component"; then
    echo "$component already running (pid $(read_pid "$component"))"
    return 0
  fi
  if ! launch_component "$component" "$mode"; then
    echo "$component failed to launch; see $LOG_DIR/$component.log" >&2
    return 1
  fi
  if ! pid="$(read_pid "$component")"; then
    echo "$component launch did not produce a valid PID file" >&2
    return 1
  fi
  if ! wait_ready "$component"; then
    echo "$component failed to start; see $LOG_DIR/$component.log" >&2
    tail -n 30 "$LOG_DIR/$component.log" >&2 || true
    cleanup_component "$component" "$pid" || true
    rm -f "$(pid_path "$component")"
    return 1
  fi
  STARTED_NEW=1
  echo "started $component (pid $pid)"
}

stop_one() {
  local component="$1" path pid deadline cleanup_status=0
  path="$(pid_path "$component")"
  if ! pid="$(read_pid "$component")"; then
    rm -f "$path"
    echo "$component is not running"
    return 0
  fi

  if process_matches "$component" "$pid"; then
    kill -TERM "$pid" 2>/dev/null || true
    deadline=$((SECONDS + STOP_TIMEOUT_SECONDS))
    while process_matches "$component" "$pid" && (( SECONDS < deadline )); do sleep 0.2; done
  fi

  cleanup_component "$component" "$pid" || cleanup_status=$?
  if process_matches "$component" "$pid" || (( cleanup_status != 0 )); then
    echo "failed to stop $component completely" >&2
    return 1
  fi
  rm -f "$path"
  echo "stopped $component"
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

configure_services() {
  [[ -n "${AUDIO_INTEL_SERVICES:-}" ]] && return
  case "$TARGET" in
    asr) pid_alive tts && export AUDIO_INTEL_SERVICES=asr,tts || export AUDIO_INTEL_SERVICES=asr ;;
    tts) pid_alive asr && export AUDIO_INTEL_SERVICES=asr,tts || export AUDIO_INTEL_SERVICES=tts ;;
    *) export AUDIO_INTEL_SERVICES=asr,tts ;;
  esac
}

preflight() {
  local component
  for component in $(start_targets); do ensure_ready "$component"; done
  "$ROOT_DIR/.runtime/api/bin/python" "$ROOT_DIR/scripts/setup_local_tls.py" validate-config \
    --protocol "$AUDIO_INTEL_PROTOCOL" --cert "${AUDIO_INTEL_TLS_CERT_FILE:-}" \
    --key "${AUDIO_INTEL_TLS_KEY_FILE:-}" --ca "${AUDIO_INTEL_TLS_CA_FILE:-}"
}

stop_list() {
  local component status=0
  for component in "$@"; do stop_one "$component" || status=1; done
  return "$status"
}

rollback_started() {
  local preserve_api="$1" index component status=0
  for ((index=${#STARTED_COMPONENTS[@]}-1; index>=0; index--)); do
    component="${STARTED_COMPONENTS[index]}"
    [[ "$component" == "api" && "$preserve_api" == "1" ]] && continue
    stop_one "$component" || status=1
  done
  return "$status"
}

start_action() {
  local run_preflight="${1:-1}" component preserve_api=0
  STARTED_COMPONENTS=()
  configure_services
  (( run_preflight == 0 )) || preflight
  if [[ "$TARGET" == "asr" || "$TARGET" == "tts" ]] && pid_alive api; then
    preserve_api=1
    stop_one api
  fi
  for component in $(start_targets); do
    if ! start_one "$component" background; then
      rollback_started "$preserve_api" || true
      return 1
    fi
    (( STARTED_NEW == 0 )) || STARTED_COMPONENTS+=("$component")
  done
  echo "Sandevistan-Audio: $AUDIO_INTEL_PROTOCOL://127.0.0.1:$AUDIO_INTEL_PORT"
}

restart_action() {
  local -a targets
  configure_services
  preflight
  read -r -a targets <<< "$(stop_targets)"
  stop_list "${targets[@]}" || return 1
  start_action 0
}

run_cleanup() {
  local index component pid status=0
  (( RUN_CLEANED == 1 )) && return 0
  RUN_CLEANED=1
  trap - INT TERM
  for ((index=${#RUN_COMPONENTS[@]}-1; index>=0; index--)); do
    component="${RUN_COMPONENTS[index]}"
    stop_one "$component" || status=1
    pid="${RUN_PIDS[$component]:-}"
    if [[ -n "$pid" ]]; then
      cleanup_component "$component" "$pid" || status=1
      rm -f "$(pid_path "$component")"
    fi
  done
  return "$status"
}

run_action() {
  local component
  RUN_COMPONENTS=()
  declare -gA RUN_PIDS=()
  RUN_CLEANED=0
  for component in api asr tts; do
    remove_stale_pid "$component"
    if pid_alive "$component"; then
      echo "run cannot manage existing $component process; stop all services first" >&2
      return 1
    fi
  done
  configure_services
  preflight
  trap 'trap - EXIT; status=0; run_cleanup || status=$?; exit "$status"' INT TERM
  trap 'status=$?; trap - EXIT; run_cleanup || true; exit "$status"' EXIT
  for component in $(start_targets); do
    RUN_COMPONENTS+=("$component")
    if ! start_one "$component" foreground; then
      run_cleanup || true
      return 1
    fi
  done
  echo "Sandevistan-Audio: $AUDIO_INTEL_PROTOCOL://127.0.0.1:$AUDIO_INTEL_PORT"
  while true; do
    for component in "${RUN_COMPONENTS[@]}"; do
      if ! process_matches "$component" "${RUN_PIDS[$component]}"; then
        echo "$component exited unexpectedly; stopping remaining services" >&2
        run_cleanup || true
        return 1
      fi
    done
    sleep 0.5
  done
}

case "$ACTION" in
  start) start_action ;;
  run) run_action ;;
  stop)
    read -r -a targets <<< "$(stop_targets)"
    stop_list "${targets[@]}"
    ;;
  restart) restart_action ;;
  status)
    for component in api asr tts; do
      remove_stale_pid "$component"
      if pid_alive "$component"; then
        if [[ "$component" == "api" ]]; then
          pid="$(read_pid "$component")"
          endpoint="$("$ROOT_DIR/.runtime/api/bin/python" "$ROOT_DIR/scripts/service_process.py" endpoint "$pid" 2>/dev/null || true)"
          echo "$component: running (pid $pid, ${endpoint:-protocol unknown})"
        else
          echo "$component: running (pid $(read_pid "$component"))"
        fi
      else
        echo "$component: stopped"
      fi
    done
    ;;
  logs) tail -n 120 -F "$LOG_DIR"/$( [[ "$TARGET" == all ]] && echo '*.log' || echo "$TARGET.log" ) ;;
  setup) "$ROOT_DIR/scripts/bootstrap.sh" "$TARGET" ;;
  doctor) python3 "$ROOT_DIR/scripts/doctor.py" ;;
  *) echo "Usage: ./service.sh {start|run|stop|restart|status|logs|setup|doctor} [all|asr|tts|api] | tls {create|renew|fingerprint}" >&2; exit 2 ;;
esac
