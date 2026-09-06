# API Guide

Sandevistan Audio exposes two API styles on the same FastAPI service:

- Native asynchronous endpoints for durable jobs, queue visibility, progress, retries, and result artifacts.
- OpenAI-compatible synchronous audio endpoints for clients that already speak the OpenAI wire format.

The interactive bilingual contract at `/docs` and the generated `/openapi.json` are authoritative for field-level schemas. This guide explains the lifecycle rules that clients must preserve.

## Base URL and authentication

```bash
BASE_URL=${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}
```

Authentication is disabled by default. When `AUDIO_INTEL_API_KEY` is configured, CLI and service clients send:

```text
Authorization: Bearer <key>
```

The bundled browser exchanges the key for an opaque HttpOnly same-origin session cookie. Pending voiceprint polling stops when the session expires and resumes after successful login if samples are still pending. Do not put the raw key in URLs or browser storage.

`GET /api/v1/health` is always public and intentionally minimal. `GET /api/v1/auth/session` may also be called before login to discover whether authentication is required. When project-managed HTTPS is enabled, the public `GET /api/v1/tls/bootstrap`, `GET /api/v1/tls/root-ca.cer`, and `GET /api/v1/tls/root-ca.pem` endpoints expose only the configured public CA and its SHA-256 fingerprint; CA downloads return `404` when unavailable. Detailed system, model, worker, media, and task surfaces remain protected. See the [local HTTPS guide](HTTPS.md) for certificate handling and trust verification.

The `version` returned by health and system responses is also used as OpenAPI `info.version`. A clean tagged checkout reports its `X.Y.Z` release version; later source commits use SemVer build metadata such as `X.Y.Z+3.g8adfe46`, with `.dirty` appended for tracked local modifications. A shallow or stale-tag checkout may omit the commit distance but retains the commit hash. Source archives without Git metadata report their embedded release fallback. Treat the full string as build identity rather than assuming that every source checkout equals the latest release artifact.

## Native asynchronous lifecycle

The following submission endpoints require an `Idempotency-Key` header containing 8–128 characters:

- `POST /api/v1/asr/jobs`
- `POST /api/v1/tts/clone-references`
- `POST /api/v1/tts/jobs`
- `POST /api/v1/tts/sequence-jobs`
- `POST /api/v1/voiceprints/people/{person_id}/samples/upload`

Generate one key per logical submission and reuse it after timeouts, disconnects, or `429` responses:

```bash
REQUEST_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
```

| Result | HTTP status | Client action |
| --- | --- | --- |
| First accepted submission | `202` | Store the returned job and status URL |
| Same key and same request | `200` | Treat the returned original job as the accepted submission |
| Same key with changed input | `409` | Generate a new key only for a genuinely new logical request |
| Admission rejected | `429` | Honor `Retry-After` and retry with the same key |

Admission protects per-kind queue limits, concurrent uploads, and minimum free disk. Reservations are process-local, so the supported service runs one API process.

`GET /api/v1/capabilities` and `GET /api/v1/system` expose `deployment.profile`, `deployment.default_compute_device`, and `deployment.gpu_runtime_installed`. Full deployments default omitted device fields to GPU. CPU-only deployments default them to CPU and mark every model-scoped GPU capability unavailable with `gpu_runtime_not_installed`; an explicit GPU submission returns that stable `503` problem code before a job or upload is accepted. Physical GPU telemetry may still appear under `system.hardware.gpu` and does not imply that a GPU inference runtime is installed.

### Submit ASR

```bash
ASR_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')

curl --fail-with-body -sS \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H "Idempotency-Key: $ASR_KEY" \
  -F file=@meeting.wav \
  -F language=Auto \
  -F speaker_count=auto \
  -F model=qwen3-asr-0.6b \
  -F diarize=true \
  -F align=true \
  -F use_voiceprint_library=true \
  -F compute_device=cpu \
  -F accelerate_single_task=true \
  "$BASE_URL/api/v1/asr/jobs"
```

Omit the `Authorization` header when authentication is disabled. Supported explicit aligned languages are Chinese, English, Cantonese, French, German, Italian, Japanese, Korean, Portuguese, Russian, and Spanish. `Auto` can detect other model languages, but those results remain at sentence/segment timestamp granularity.

Saved hotword lists are selected through one comma-separated `hotword_list_ids` form field as described by `/docs`. A task stores an immutable hotword snapshot, so later library edits do not rewrite history.

### Submit preset TTS

```bash
TTS_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')

curl --fail-with-body -sS \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H "Idempotency-Key: $TTS_KEY" \
  -F text='Hello from a private speech workstation.' \
  -F language=English \
  -F voice_mode=preset \
  -F speaker=Ryan \
  -F model=qwen3-tts-0.6b \
  -F response_format=wav \
  -F compute_device=cpu \
  -F accelerate_single_task=true \
  "$BASE_URL/api/v1/tts/jobs"
```

Read `GET /api/v1/capabilities` before displaying TTS controls. The 0.6B models and Base clone checkpoints reject natural-language instructions. The 1.7B CustomVoice preset path accepts an optional `instruct`; 1.7B VoiceDesign requires one. Unsupported combinations return `422` and are never silently ignored.

### Submit an ordered TTS sequence

`POST /api/v1/tts/sequence-jobs` accepts JSON with 1–100 ordered items sharing one model, device, language, and voice mode. Read `tts.sequence_jobs` before submitting: require `supported=true` and a supported `contract_version` (currently `1`), and respect `max_items` and `max_total_chars`. Fall back to single-item jobs when the capability is absent, unsupported, or uses a contract version the client does not understand.

- Only `preset` and `voiceprint` modes are supported. Each `preset` item requires an official `speaker`; optional per-item `instruct` is supported only by the 1.7B model. Omit `voiceprint_sample_id` in this mode.
- Each `voiceprint` item requires an existing ready, TTS-eligible `voiceprint_sample_id`; omit `speaker` and `instruct`. Different items may use different samples. Accepted jobs retain their own reference audio and metadata snapshots.
- Item `id` values must be unique within the request, contain 1–64 characters, and match `[A-Za-z0-9._-]+`. Each text must be nonempty after trimming leading/trailing whitespace. The sum of these trimmed text lengths must not exceed `max_total_chars` (configured by `AUDIO_INTEL_MAX_TTS_CHARS`).
- Output is always one WAV per item. Device omission follows the deployment default; `accelerate_single_task` defaults to `true` and can be disabled explicitly.

Generate a key once for this logical submission; reuse `SEQUENCE_KEY` and the same body when retrying the curl command after a timeout or `429`:

```bash
SEQUENCE_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
```

```bash
curl --fail-with-body -sS \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H "Idempotency-Key: $SEQUENCE_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-tts-0.6b","language":"English","voice_mode":"preset","compute_device":"cpu","items":[{"id":"intro","text":"Welcome.","speaker":"Ryan"},{"id":"reply","text":"Let us begin.","speaker":"Aiden"}]}' \
  "$BASE_URL/api/v1/tts/sequence-jobs"
```

For a voiceprint sequence, use a new logical submission key and replace the body with the following JSON, substituting real eligible sample IDs from the voiceprint library:

```json
{
  "model": "qwen3-tts-0.6b",
  "language": "English",
  "voice_mode": "voiceprint",
  "compute_device": "cpu",
  "items": [
    {"id": "intro", "text": "Welcome.", "voiceprint_sample_id": "sample_0123456789abcdef"},
    {"id": "reply", "text": "Let us begin.", "voiceprint_sample_id": "sample_fedcba9876543210"}
  ]
}
```

Sequence request objects and each item reject undeclared fields with `422` (`application/json`, `detail[].type=extra_forbidden`). Do not send `speed`, `pitch`, sampling controls, or `response_format`; WAV is fixed by contract v1.

A sequence occupies one TTS queue position. Progress counts synthesis chunks across all items. Failure or cancellation applies to the entire job; retrying it regenerates all items from the persisted request, with no per-item resume or partial result delivery.

After success, job detail contains `result.sequence`; `GET /api/v1/jobs/{job_id}/result` returns the result directly, with `sequence` at its root. This excerpt shows the ordered item-to-artifact mapping:

```json
{
  "sequence": {
    "contract_version": 1,
    "items": [
      {"id": "intro", "artifact_name": "item-0000.wav", "duration": 1.5, "sample_rate": 24000},
      {"id": "reply", "artifact_name": "item-0001.wav", "duration": 1.5, "sample_rate": 24000}
    ]
  }
}
```

Each `artifact_name` matches an entry in `artifacts[].name`. Use the returned names rather than deriving them from item IDs. With `JOB_ID` set to the accepted job ID, download the first item after success (requires `jq`):

```bash
ARTIFACT_NAME=$(curl --fail-with-body -sS \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  "$BASE_URL/api/v1/jobs/$JOB_ID/result" | jq -er '.sequence.items[0].artifact_name')
curl --fail-with-body -sS \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  "$BASE_URL/api/v1/jobs/$JOB_ID/artifacts/$ARTIFACT_NAME" -o first-item.wav
```

Sequence-specific `422` problem codes include:

| Code | Correction |
| --- | --- |
| `duplicate_tts_sequence_item` | Use distinct item IDs |
| `invalid_tts_sequence_text` | Supply nonempty text after trimming |
| `tts_sequence_too_large` | Split the request to respect the total character limit |
| `unknown_tts_speaker` | Select an official preset speaker |
| `invalid_tts_sequence_item` | Supply the fields required by the selected mode and omit conflicting fields |
| `voiceprint_sample_unavailable` | Select an existing ready sample with available audio and transcript |

Model/control validation and the shared `200`/`202`/`409`/`429`/`503` contracts also apply.

### Voice-clone reference flow

1. Submit a reference audio file to `POST /api/v1/tts/clone-references` with its own idempotency key.
2. Poll the returned ASR analysis job until it succeeds.
3. Review the detected reference text and language.
4. Submit `POST /api/v1/tts/jobs` with `voice_mode=inline_clone` and the resulting `reference_job_id`.

The older direct `reference_audio` plus `reference_text` TTS form remains compatible, but the analysis-job flow is preferred because it is inspectable and reusable from task history.

## Polling, SSE, and results

Submission responses provide a job ID, `status_url`, and polling guidance. Read job detail until it enters a terminal state:

```bash
curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  "$BASE_URL/api/v1/jobs/JOB_ID"
```

Clients should preserve the response `ETag` and send `If-None-Match` on later polls. `304 Not Modified` means neither the task nor its same-kind queue context has changed. Follow `poll_after_seconds`; do not use high-frequency database polling to simulate animation.

For live updates, use:

- `GET /api/v1/jobs/{job_id}/events` for one full task stream.
- `GET /api/v1/events` for global summary updates.

The global stream starts with a recent summary `snapshot`, then sends semantic `update` events and lightweight `{}` heartbeats. It never contains full request or result JSON. Reconnect and reconcile with the first snapshot or the job detail endpoint; SSE is not an event-history log.

`progress` is monotonic best-effort progress. Check `progress_detail.basis`: `estimated` values are not exact completion counts. Model loading publishes start and completion boundaries only; the service does not fabricate loading percentages or heartbeat activity while a backend blocks.

After a job succeeds, fetch the complete result and artifacts:

```bash
curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  "$BASE_URL/api/v1/jobs/JOB_ID/result"

curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  "$BASE_URL/api/v1/jobs/JOB_ID/artifacts/result.srt" -o result.srt
```

ASR source media is available at `GET /api/v1/jobs/{job_id}/source`, supports HTTP Range requests, and accepts `?download=true` to force a download.

## Job operations

Important job endpoints include:

- `GET /api/v1/jobs` — stable server-side pagination and filters; summaries only.
- `GET /api/v1/jobs/{job_id}` — full request and result detail.
- `POST /api/v1/jobs/{job_id}/cancel` — begin safe cancellation.
- `POST /api/v1/jobs/{job_id}/retry` — retry a terminal task.
- `DELETE /api/v1/jobs/{job_id}?purge=true` — permanently remove one eligible task and its files.
- `POST /api/v1/jobs/batch-delete` — delete up to 100 IDs with per-item results.

Retry requeues only a still-failed or cancelled job, atomically, at the tail of its same-kind queue. It preserves the job ID, request snapshots, and accumulated processing time. Success returns `200`; a state conflict returns `409`, and queue capacity, submission concurrency, or disk admission rejection returns `429` with `Retry-After`. Retry requires no `Idempotency-Key`.

```bash
curl --fail-with-body -i -X POST \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  "$BASE_URL/api/v1/jobs/$JOB_ID/retry"
```

Cancellation is terminal only after the task executor and all descendants have exited and temporary files have been cleaned. A running task cannot be permanently purged while its process tree may still write files.

## Libraries and capability discovery

Use `GET /api/v1/capabilities` instead of hard-coding model-dependent controls, device availability, upload limits, language lists, or speaker limits.

Main library surfaces:

- `GET|POST /api/v1/asr/hotword-lists`
- `PATCH|DELETE /api/v1/asr/hotword-lists/{item_id}`
- `GET|POST /api/v1/voiceprints/people`
- `PATCH|DELETE /api/v1/voiceprints/people/{person_id}`
- `POST /api/v1/voiceprints/people/{person_id}/samples/from-asr`
- `POST /api/v1/voiceprints/people/{person_id}/samples/upload`
- `DELETE /api/v1/voiceprints/people/{person_id}/samples/{sample_id}`
- `GET /api/v1/voiceprints/samples/{sample_id}/audio`
- `GET|POST /api/v1/tts/voices`
- `DELETE /api/v1/tts/voices/{voice_id}`

Completed jobs keep speaker-name, hotword, device, and model snapshots. Later library or hardware changes do not rewrite historical results.

## OpenAI-compatible audio

The compatibility endpoints wait synchronously and are best suited to short requests. `/v1/audio/speech` enforces the same trimmed-text limit as native TTS: 1 through `GET /api/v1/capabilities` → `limits.max_tts_chars` characters. Blank or overlong input returns `422` before a job is created:

```bash
curl --fail-with-body -sS \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -F file=@meeting.wav \
  -F model=qwen3-asr-0.6b \
  -F prompt='Project meeting' \
  -F response_format=verbose_json \
  -F compute_device=cpu \
  "$BASE_URL/v1/audio/transcriptions"

curl --fail-with-body -sS \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -d '{"model":"qwen3-tts-0.6b","input":"Hello","voice":"Ryan","language":"English","response_format":"wav","compute_device":"cpu"}' \
  "$BASE_URL/v1/audio/speech" -o speech.wav
```

`GET /v1/models` lists compatible model identifiers. OpenAI-compatible `instructions` only maps to the 1.7B preset-voice path; use the native API for VoiceDesign and durable long-running work.

## Error handling

| Status | Meaning |
| --- | --- |
| `401` | Missing or invalid Bearer/session authentication |
| `409` | Idempotency key reused with different content, or an ineligible job state for retry |
| `422` | Unsupported language, model, voice mode, control, or validation input |
| `429` | Submission concurrency, queue capacity, or disk admission limit |
| `503` | Requested GPU or model revision is unavailable; CPU-only deployments use the stable `gpu_runtime_not_installed` problem code for explicit GPU requests |

GPU requests never silently fall back to CPU. Retry `429` using its `Retry-After` value and, for keyed submissions, the original idempotency key. Treat `503` as a capability/configuration issue and explicitly select `compute_device=cpu` if that matches user intent.
