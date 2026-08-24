#!/usr/bin/env bash
set -euo pipefail
BASE_URL="${AUDIO_INTEL_URL:-http://127.0.0.1:20810}"
TMP_RESULT="$(mktemp -d)"
trap 'rm -rf "$TMP_RESULT"' EXIT
curl -fsS "$BASE_URL/api/v1/health" > "$TMP_RESULT/health.json"
curl -fsS -F file=@/etc/hosts -F language=Chinese -F speaker_count=2 -F diarize=true -F align=true "$BASE_URL/api/v1/asr/jobs" > "$TMP_RESULT/asr.json"
curl -fsS -F text='本地语音合成冒烟测试。' -F language=Chinese -F voice_mode=preset -F speaker=Vivian -F response_format=wav "$BASE_URL/api/v1/tts/jobs" > "$TMP_RESULT/tts.json"
echo "Submitted ASR and TTS jobs successfully."
