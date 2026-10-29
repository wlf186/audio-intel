# API Guide

Sandevistan Audio exposes two API styles on the same FastAPI service:

- Native asynchronous endpoints for durable jobs, queue visibility, progress, retries, and result artifacts.
- OpenAI-compatible synchronous audio endpoints for clients that already speak the OpenAI wire format.

The interactive bilingual contract at `/docs` and the generated `/openapi.json` are authoritative for field-level schemas. This guide explains the lifecycle rules that clients must preserve.

## Base URL and authentication

The service entrypoints automatically read the project-root `.env`; exported environment variables take precedence. This affects the service and its children, not the calling shell or remote API clients. Set `AUDIO_INTEL_BASE_URL` in the **client** environment to its actual reachable URL, including the configured protocol and port (for example `http://127.0.0.1:20815`); the examples below retain `http://127.0.0.1:20810` as the default. Use `service.sh status` or `service.cmd status` to inspect the running endpoint. The bind address `0.0.0.0` is not a remote client address.

Provide the same `AUDIO_INTEL_API_KEY` separately to authenticated clients from your existing credential source. Starting the service does not populate that variable in your terminal. Swagger uses the origin serving `/docs` and the relative `/openapi.json`; no deployment-specific server URL is embedded in the contract. See the [configuration rules](INSTALL.md#4-代理与配置).

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

## Voiceprint groups and recognition scope / 声纹分组与识别范围

Every person belongs to one group, exposed as `group_id`. Existing/new people default to `voiceprint_ungrouped` (未分组 / Ungrouped), which cannot be renamed or deleted. Names are normalized and unique, 1–80 characters. Deleting another group atomically moves its members to Ungrouped; it retains people and audio. Personnel identity remains globally unique by normalized name plus note.

| Operation | Endpoint / Contract |
| --- | --- |
| List / create groups | `GET` / `POST /api/v1/voiceprints/groups`; creation body `{"name":"Team"}` |
| Rename / delete group | `PATCH` / `DELETE /api/v1/voiceprints/groups/{group_id}` |
| Move people | `POST /api/v1/voiceprints/people/batch-move`, `{"person_ids":["voice_…"],"group_id":"group_…"}`; all-or-nothing validation |
| Delete people | `POST /api/v1/voiceprints/people/batch-delete`, `{"person_ids":["voice_…"],"purge":true}`; per-person `deleted`, `status`, `detail`, protecting active imports and readers |

Audio and OpenAI-compatible submissions accept optional **JSON-array form text** `voiceprint_group_ids`; video submissions accept a JSON array. Omission means all groups, including Ungrouped. Empty/unknown groups are rejected when matching is enabled. An empty group is valid and contributes no candidates. The accepted request stores group names and eligible person IDs; later moves/additions do not alter scope. Missing people are skipped, while names and samples are read at execution. Retry preserves the request and idempotent replay preserves its first snapshot even after group deletion. Historical requests without snapshots retain legacy behavior. Scope snapshots appear only in per-job detail, never global summary/SSE.

```bash
BASE_URL=${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}
REQUEST_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
VOICEPRINT_GROUP_IDS=$(curl --fail-with-body -sS -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" "$BASE_URL/api/v1/voiceprints/groups" | jq -c '[.items[0].id]')
curl --fail-with-body -sS -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H "Idempotency-Key: $REQUEST_KEY" -F file=@meeting.wav -F compute_device=cpu \
  -F "voiceprint_group_ids=$VOICEPRINT_GROUP_IDS" "$BASE_URL/api/v1/asr/jobs"
```

中文：分组删除只移动人员，批量删除人员需明确确认。ASR 提交时固定人员范围，后续移动不影响排队任务；浏览器默认使用全部，手动多选长期记忆，失效选择不自动扩大范围。

## On-demand redacted transcript export / 按需脱敏导出

`POST /api/v1/jobs/{job_id}/transcript-export` is a protected synchronous download for successful audio/video ASR. It needs no Idempotency-Key; cookie clients retain same-origin write checks. Body: `format` (`pdf`, `docx`, `json`, `txt`, `srt`, `vtt`; default `pdf`), `redact` (default `true`), and `replacements` (default `[]`, at most 100 unique `{source,target}` pairs, 8000 characters per value). Undeclared fields and invalid/duplicate rules return `422`. PDF/DOCX renderer saturation returns `429` with Retry-After; oversized exports return `413`.

Names use uppercase Latin word or Chinese pinyin initials: Vince Carter → VC, 刘亦菲 → LYF, Liu Yifei → LY, Marco → M. Collisions become VC-1, VC-2 in speaker order. Generic speaker numbers remain. Notes mask the middle `ceil(n/2)` Unicode characters: 01234567 → 01****67, 广东移动有限公司 → 广东****公司. Literal keyword replacements affect transcript text and timed words, are case-sensitive, longest-first and single-pass without chaining. Matched word spans merge in the export copy and retain their full time range. Empty targets delete the matched text.

Redacted JSON contains transcript fields, speakers, segments/timed words and safe timing/model/video metadata; auxiliary identity/hotword snapshots and artifact paths are omitted. Redacted document titles remain generic, but attachment filenames always use the task basename plus export extension: `Recording_10.m4a` becomes `Recording_10.txt`, regardless of redaction. Legacy PDF GET/HEAD and ASR transcript.json/txt/srt/vtt downloads follow the same naming; artifact identifiers, URLs and saved filenames remain unchanged. Download transformations never modify stored jobs or historical artifacts. Stored audio and video images remain unchanged; names in body text only change when covered by explicit keywords. `redact=false` disables text redaction and keyword rules. Export-hidden pictures still become placeholders in PDF/DOCX, with their text retained. GET/HEAD PDF routes retain their existing text behavior and honor picture visibility.

```bash
BASE_URL=${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}
JOB_ID=${JOB_ID:?export the completed ASR job ID first}
curl --fail-with-body -sS -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"format":"txt","redact":true,"replacements":[{"source":"广东移动","target":"gy"}]}' \
  "$BASE_URL/api/v1/jobs/$JOB_ID/transcript-export" -o Recording_10.txt
```

```python
import os
from pathlib import Path
from email.message import Message
import httpx
base = os.getenv("AUDIO_INTEL_BASE_URL", "http://127.0.0.1:20810")
headers = {"Authorization": "Bearer " + os.environ["AUDIO_INTEL_API_KEY"]} if os.getenv("AUDIO_INTEL_API_KEY") else {}
response = httpx.post(base + "/api/v1/jobs/" + os.environ["JOB_ID"] + "/transcript-export", headers=headers,
                      json={"format": "json", "redact": True, "replacements": [{"source": "广东移动", "target": "gy"}]}, timeout=300)
response.raise_for_status()
disposition = Message()
disposition["Content-Disposition"] = response.headers["Content-Disposition"]
Path(Path(disposition.get_filename()).name).write_bytes(response.content)
```

中文：界面全部转写下载默认脱敏，但下载文件名保留任务名称，去掉末尾音视频扩展名后加导出扩展名。上面的 Python 示例读取 UTF-8 响应头命名；Bash 的 `-o` 显式指定本地路径。替换编辑器按行 `原词=替换词`，按第一个等号拆分，忽略空行、允许空替换词；错误显示行号。独立 localStorage `audio-intel:transcript-export:v1` 保存开关和原始编辑内容，刷新、重开、退出登录及资料库替换后保留；清空替换规则保留脱敏开关，只清空规则，可撤销（保留完整原始输入，包括错误内容）；下一次编辑、页面卸载或刷新后撤销失效。ASR 偏好使用 v7，兼容迁移旧版本（包括 v0.1.33 的 v4）。


### DOCX 简洁导出 / Concise DOCX export

`POST /api/v1/jobs/{job_id}/transcript-export` 的 `format` 新增 `docx`，默认仍为 `pdf`。能力接口的 `asr.transcript_export.formats` 声明支持格式；旧 PDF GET/HEAD 地址保持不变，不新增 DOCX GET 路由。音频合并相邻同说话人发言，视频按画面组织图文，使用已有脱敏、关键词替换与导出隐藏设置。隐藏图片不嵌入 DOCX，文字完整保留。仅支持已完成 ASR 任务。

响应 MIME 为 `application/vnd.openxmlformats-officedocument.wordprocessingml.document`，带 `Content-Length`、UTF-8 `Content-Disposition` 与 `Cache-Control: no-store`。认证和同源写入规则与其他 POST 导出一致。生成器独立限制为一次并发，5 分钟内存缓存，总缓存不超过 64 MiB；正文上限 4,000,000 字符，引用的可见图片总量上限 128 MiB。超限 413，繁忙 429 并附 `Retry-After: 2`。缓存随结果、导出设置和任务更新时间变化，无持久导出文件。

```python
import os
from pathlib import Path
from email.message import Message
import requests

base = os.environ["AUDIO_INTEL_BASE_URL"].rstrip("/")
job_id = os.environ["AUDIO_INTEL_JOB_ID"]
headers = {"Authorization": f"Bearer {os.environ['AUDIO_INTEL_API_KEY']}"} if os.environ.get("AUDIO_INTEL_API_KEY") else {}
response = requests.post(f"{base}/api/v1/jobs/{job_id}/transcript-export",
    headers=headers, json={"format": "docx", "redact": True, "replacements": []}, timeout=120)
response.raise_for_status()
disposition = Message()
disposition["Content-Disposition"] = response.headers["Content-Disposition"]
Path(Path(disposition.get_filename()).name).write_bytes(response.content)
```

English: The existing POST export accepts `format="docx"`; PDF remains the default. Capabilities advertise DOCX. The binary Word response uses its standard MIME type and UTF-8 filename. Audio paragraphs and illustrated video sections follow PDF semantics, including redaction and replacement rules. Export-hidden images are omitted from the package and replaced with placeholders without losing text, even when `redact=false`. Authentication and same-origin checks remain unchanged. An independent single renderer uses a five-minute, 64 MiB bounded memory cache; limits are 4,000,000 text characters and 128 MiB visible-image bytes. Oversize requests return 413, busy requests 429 with Retry-After 2. No new dependency, persistent artifact or database migration is required.

## Optional expression optimization / 可选表达优化

Configure an explicitly selected Ollama service in System → Expression optimization; the feature is disabled by default and has no cloud fallback. Discovery and verification use synthetic text, never historical transcripts. See the [complete setup, limits and lifecycle](EXPRESSION_OPTIMIZATION.md).

- Protected configuration: `GET/PUT /api/v1/settings/transcript-optimization`, `POST .../discover` and `POST .../verify`. Enabling requires a matching, unexpired verification token or the unchanged saved verified profile. Changing the URL, model or proxy policy invalidates verification.
- Completed ordinary audio/video ASR: `GET/POST /api/v1/jobs/{job_id}/transcript-optimization`, `POST .../pause`, `POST .../resume`, `GET .../result?run_id=...`, and `DELETE ...?purge=true`. Creation requires `Idempotency-Key`, with 202/200/409/429 semantics and `Retry-After` on capacity failures. Create bodies accept `rewrite_mode` (`polish`, `rewrite`, `editorial`; default `rewrite`) and `regenerate`. Mode is included in the idempotency fingerprint, summary/result, and immutable run configuration. A different mode with the same key returns 409; old requests omitting mode can replay legacy keys. Pause/resume bodies contain `run_id` and optional `phase` (`generation`, the default, or `review`), preserving the saved mode. Status supports ETag/304. `complete=true` means published prose is available, independently of `review_state`; API restart pauses interrupted generation and requeues interrupted review while retaining deliberate pauses.
- Policy v6 result paragraphs combine consecutive same-speaker sources, including their IDs, offsets and times. `generation_method` identifies model/basic_cleanup/mixed. `review_outcome` distinguishes pending/pass/fact/uncertain/unavailable; review never changes published text. A completed review with `unavailable_units>0` can resume only its unavailable subrequests. This count includes groups containing an unavailable part and can overlap factual-concern groups; do not sum those counters as disjoint categories. Historical unknown review remains null.
- Capability discovery adds `asr.transcript_optimization`, including `rewrite_modes` and `default_rewrite_mode`. Browsers request optimizer resources only after authentication and an explicit `supported=true`; missing/unsupported capability snapshots retain original reading without optimizer requests. Job lists and SSE carry optimization summaries only, without draft/source text. Original ASR results, timed subtitles and voiceprint references are unchanged.
- `POST /api/v1/jobs/{job_id}/transcript-export` accepts `text_version="original"` (default), `"optimized"`, or `"comparison"`. Non-original requests require the published `optimization_id`; optimized exports allow PDF/DOCX/TXT and comparisons PDF/DOCX. Comparisons use landscape A4, timed original sources on the left and readable prose on the right, with equal-sized text and repeating column headers across page breaks. Legacy drafts retain change highlighting. Pending or unavailable review does not prevent download and is disclosed in the export. Unsupported combinations return 422, unfinished or stale drafts 409. Redaction and replacements apply to both sides and review explanations. Filenames append `-优化稿` or `-对照版` to the task basename.

中文：在系统状态明确选择并验证 Ollama 后启用；独立单并发队列，最多 5 个排队任务，生成优先于后台复核。原文始终保留，暂停保存检查点，重新生成正文就绪后才替换旧稿。默认原文导出兼容已有客户端；优化稿不用于精确字幕或声纹。当前策略 v6 使用 SQLite v18，新增后台复核和子批次检查点；升级仅需一致的 SQLite 备份、不复制媒体、不重写历史正文。阅读偏好属于当前浏览器工作会话，最多 200 个任务，退出登录和删除任务时清理。

```bash
# First configure and enable a verified model in System; use a completed ASR task.
.runtime/api/bin/python scripts/optimize_transcript.py \
  --url "${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}" --job "$JOB_ID"
```

Authentication is read from `AUDIO_INTEL_API_KEY`; the example stops on terminal failure or pause. It does not enable the feature or modify the original transcript.

## Native asynchronous lifecycle

The following submission endpoints require an `Idempotency-Key` header containing 8–128 characters:

- `POST /api/v1/asr/jobs`
- `POST /api/v1/asr/video-jobs`
- `POST /api/v1/tts/clone-references`
- `POST /api/v1/tts/jobs`
- `POST /api/v1/tts/sequence-jobs`
- `POST /api/v1/tts/document-imports` (returns a parser import, not an inference job)
- `POST /api/v1/tts/document-jobs`
- `POST /api/v1/voiceprints/people/{person_id}/samples/upload`

The independent library-transfer upload also requires an `Idempotency-Key`; its receipt, preview and application lifecycle is described under [Library export and import](#library-export-and-import).

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

`GET /api/v1/capabilities` and `GET /api/v1/system` expose `deployment.profile`, `deployment.default_compute_device`, and `deployment.gpu_runtime_installed`. Full deployments default omitted device fields to GPU. CPU-only deployments default them to CPU and mark every model-scoped GPU capability unavailable with `gpu_runtime_not_installed`; an explicit GPU submission returns that stable `503` problem code before a job is accepted. Video uploads have no device setting: their device admission is checked later at `POST /api/v1/asr/video-jobs`, after uploading. Physical GPU telemetry may still appear under `system.hardware.gpu` and does not imply that a GPU inference runtime is installed.

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

新完成的 ASR 任务在结果中提供 `playback_url`，指向 `GET /api/v1/jobs/{job_id}/playback`。它返回增强前保存的 16 kHz、单声道、PCM16 原音 WAV，使片段、字词和波形跳转与识别时间轴一致，避免 VBR MP3 随机跳转偏差。该接口受 Bearer / 浏览器会话鉴权保护，支持 Range（`206` / `416`）；非 ASR 或尚未成功的任务返回 `409`，音频缺失返回 `404`。历史任务不补生成文件，缺少此字段时继续使用原音源。原文件下载和字幕时间戳保持不变。

New successful ASR results include `playback_url`, pointing to `GET /api/v1/jobs/{job_id}/playback`. This serves the saved unenhanced 16 kHz mono PCM16 WAV on the recognition timeline, avoiding inaccurate VBR MP3 seeks for segments, words and waveforms. Bearer or browser-session authentication is required. Range requests support `206` / `416`; non-ASR or unsuccessful jobs return `409`, and missing audio returns `404`. Historical jobs are not backfilled and continue using their original source when this field is absent. Original downloads and transcript timestamps are unchanged.

`GET /api/v1/jobs/{job_id}/playback?download=true` 下载与播放器“原音”一致的 WAV，音频和视频任务均适用。
`download` 为可选 boolean，默认 `false`；`true` 返回 `Content-Disposition: attachment`，文件名为安全处理后的任务名（去扩展名）加 `-original.wav`。
内容与普通回放逐字节相同，不重新处理或创建副本；鉴权、Range 及 `404/409/416` 语义保持一致。
它与 `/source?download=true` 不同：后者提供上传的原始音频或视频文件。

Optional query `download=true` serves the same unenhanced WAV as an attachment for audio and video jobs.
The default is false. The filename uses the sanitized task stem plus `-original.wav`; long stems are truncated while preserving the WAV suffix.
No audio is reprocessed, copied or backfilled. Authentication and Range/error behavior remain unchanged.
`/source?download=true` continues to download the original upload, including the video container for video jobs.

播放 WAV 随任务删除；每小时音频约增加 115 MB 存储。它不加入字幕导出文件列表，也不改变浏览器偏好或草稿的保存期限。

The playback WAV is deleted with its job and adds approximately 115 MB per audio hour. It is separate from transcript export artifacts and does not change browser preference or draft lifetimes.

```bash
# JOB_ID must identify a successful new ASR task.
curl --fail-with-body \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H 'Range: bytes=0-43' \
  "$AUDIO_INTEL_URL/api/v1/jobs/$JOB_ID/playback" -o playback-header.bin
# Download the complete unenhanced WAV.
curl --fail-with-body -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  "$AUDIO_INTEL_URL/api/v1/jobs/$JOB_ID/playback?download=true" -o original.wav
```

## ASR speaker names and notes

`PATCH /api/v1/jobs/{job_id}/speakers/{speaker_id}` edits a successful ASR result. Its JSON body requires `name` and accepts optional `note`. Names must be nonempty after trimming and are retained in full, including library names longer than 80 characters. Notes follow the library's 20-character normalized limit: omission preserves the current note, while `null`, empty or whitespace-only input clears it. Results expose optional active `speakers[].name` and `note` alongside `label`; historical results may lack these fields.

Automatic voiceprint recognition lets each detected speaker select its best match above the existing threshold, so several clusters can match one person. Automatic results and manual saves merge named speakers within a task by normalized **name + note** (NFKC, whitespace folding, case folding). Different notes and unnamed default speakers remain separate. Each merged group retains the first ID in the result's speaker list; segment and word speaker references use that ID. Segment IDs, order, text and timestamps are preserved. Later edits apply to the entire merged person. Match scores retain the representative record's original snapshot; `voiceprint_library.matched_speakers` counts matches before merging.

The response is the complete updated result (`200`). Refresh local speaker IDs from it: a merged-away ID returns `404`. Invalid names or notes return `422`; non-ASR, unfinished or conflicting/deleting tasks return `409`. File/database save failures return `503` and may be retried. Saving refreshes the task's JSON/TXT/SRT/VTT exports; playback audio, library people, existing samples and other historical jobs keep their snapshots. Existing tasks are merged only on explicit save, with no startup or read-time bulk rewrite.

The browser dialog can copy an existing library person's name and note into editable fields, including people without samples. Choosing someone without a note clears the prior note. Manual entry remains available during library loading or errors, and loading errors offer retry. Selection is dialog-local and does not add a persistent person-ID binding or change browser-storage lifetimes.

Choose one example below, using a speaker ID from the latest result of the intended completed task. Editing changes that task and its exports.

```bash
set -euo pipefail
BASE_URL=${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}
JOB_ID=${JOB_ID:?export the completed ASR job ID first}
SPEAKER_ID=${SPEAKER_ID:?export a current speaker ID from its result first}
AUTH=(); if [[ -n "${AUDIO_INTEL_API_KEY:-}" ]]; then AUTH=(-H "Authorization: Bearer $AUDIO_INTEL_API_KEY"); fi

curl --fail-with-body -sS "${AUTH[@]}" -X PATCH \
  "$BASE_URL/api/v1/jobs/$JOB_ID/speakers/$SPEAKER_ID" \
  --json '{"name":"张三","note":"研发一部"}'
```

API clients can select a person from `GET /api/v1/voiceprints/people` and copy just its `name` and `note`. The saved values are task-local snapshots; later library edits do not rewrite them. Using the same environment and `AUTH` setup above:

```bash
PERSON_ID=${PERSON_ID:?export the existing library person ID first}
PERSON=$(curl --fail-with-body -sS "${AUTH[@]}" \
  "$BASE_URL/api/v1/voiceprints/people" \
  | jq -ce --arg person_id "$PERSON_ID" \
    '.items[] | select(.id == $person_id) | {name, note: (.note // null)}')
curl --fail-with-body -sS "${AUTH[@]}" -X PATCH \
  "$BASE_URL/api/v1/jobs/$JOB_ID/speakers/$SPEAKER_ID" --json "$PERSON"
```

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

`samples/from-asr` accepts completed audio and video ASR jobs with the existing JSON body `{"job_id":"...","segment_ids":[0,2]}`. IDs refer to original `result.segments`, not merged presentation paragraphs. All selected segments must belong to one speaker; each becomes one WAV sample. Duplicate imports return `409`. Video samples are cut from the saved `output/playback.wav` on the transcription timeline; missing playback returns `404` without falling back to container timestamps. Audio jobs retain their existing source-recording behavior.

中文：视频入库复用同一接口和原始语句 ID，每句单独成样本，重复入库返回 `409`。视频按任务保存的播放音频截取，缺失返回 `404`，不会回退到原视频时间轴或自动补写历史文件。

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

## Voiceprint identities and sample names

People retain stable IDs and are unique by normalized **name + note** (NFKC, whitespace folding, case folding). Notes remain optional and limited to 20 characters; `null`, empty, and whitespace-only notes are equivalent. Creating or updating a duplicate pair returns `409`. System full-name and surname-free hotword lists deduplicate terms, exclude notes, and retain a shared name while any matching person opts in. Custom list names remain unique.

Every sample response includes persistent `name`. Upload accepts an optional `name` base; otherwise it uses the filename without extension. The recording UI supplies a timestamp name, and ASR imports use task name plus segment number. Creation adds ` (2)`, ` (3)`, etc. for collisions, truncating the base to keep the total at most 80 characters. Existing samples retain their pre-upgrade library number as a fixed name.

`PATCH /api/v1/voiceprints/people/{person_id}/samples/{sample_id}` accepts only `{"name":"Meeting room recording"}` and returns the updated sample (`200`). Normalized names must contain 1–80 characters, without control characters, and be unique within that person. Duplicate names return `409`; invalid names or extra fields return `422`; missing samples or mismatched person IDs return `404`. Samples in every state can be renamed without changing audio, transcripts, or analysis jobs.

This example requires `jq` and a local `reference.wav`. Keep `VOICEPRINT_KEY` for retries of the same upload:

```bash
PERSON_ID=$(curl --fail-with-body -sS -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H 'Content-Type: application/json' -d '{"name":"Alex Li","note":"Research"}' \
  "$BASE_URL/api/v1/voiceprints/people" | jq -er '.id')
VOICEPRINT_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
SAMPLE_ID=$(curl --fail-with-body -sS -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H "Idempotency-Key: $VOICEPRINT_KEY" -F file=@reference.wav \
  -F compute_device=cpu -F name='Meeting room' \
  "$BASE_URL/api/v1/voiceprints/people/$PERSON_ID/samples/upload" | jq -er '.sample.id')
curl --fail-with-body -sS -X PATCH -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H 'Content-Type: application/json' -d '{"name":"Quiet meeting room"}' \
  "$BASE_URL/api/v1/voiceprints/people/$PERSON_ID/samples/$SAMPLE_ID"
```

TTS cloning selects a stable sample ID. New single and sequence requests snapshot `voiceprint_person_name`, `voiceprint_person_note`, and `voiceprint_sample_name` (inside `voiceprint_references` for sequences). Renaming the library does not rewrite accepted jobs or invalidate same-request idempotent replay. Failed/cancelled retries use the complete persisted request. Legacy `POST /api/v1/tts/voices` returns `409` when a name matches multiple people; use the native voiceprint endpoints with an explicit person ID instead.

The automatic clone-reference limit comes from `limits.max_clone_reference_seconds` (currently 15); manual voiceprint ranges are advertised separately in `tts.model_capabilities[].controls.reference_range`. Long references use the beginning through the last complete word within that limit, leaving the original sample intact. Before submission, the UI displays the limit; after a single clone completes, `reference_duration_original` and `reference_duration_used` provide measured seconds, and `reference_truncated` indicates truncation. Missing historical durations remain unknown. Ordered sequence results retain contract v1.


## Document TTS

Use [Document TTS](DOCUMENT_TTS.md) for EPUB, TXT, Markdown, text PDF, DOCX, XLSX and PPTX. Upload a document, poll its import to `ready`, request a segmentation preview, then submit its revision and selected IDs to `/api/v1/tts/document-jobs`. This accepts up to the capability-advertised section count (2,000 by default) with either multipart or URL-encoded forms. Ordinary TTS retains its 50,000-character limit.

The browser bulk-removal flow invokes the existing single-import DELETE for each selected entry, reports individual outcomes and retains failures for retry; there is no atomic bulk-delete endpoint. Imports are retained until explicitly deleted. `GET /api/v1/tts/document-imports?offset=0&limit=20` preserves the array response and includes `size_bytes` (source) and `storage_bytes` (source, parsed text and log). Omit pagination to list all imports. Reuse an existing ready import without uploading again. `DELETE .../{identifier}` rejects active parsing or snapshot copying with 409 and does not remove already submitted jobs.

`POST /api/v1/tts/document-imports/{identifier}/retry` requeues only a failed parser import using the saved file (202; 409 if no longer failed or source missing). It shares upload/parse admission (429 with Retry-After), needs no Idempotency-Key, and never silently retries a malformed document. Uploads authenticate and reserve capacity before reading the body, stream directly to a temporary source, and remove partial files on failure. Same-key upload retries validate the complete file hash and reuse the original import.

`segmentation_mode=auto` prefers reliable structure; `target_section_chars` is only a fallback for unstructured text or a preface, not a cap on structural chapters. `length` uses the target throughout. `basis` identifies a structural source or a text boundary, not a second segmentation-mode parameter.

The typed section response is stable before and after executor initialization: `index` is the original document section number, `position` is the selected job order, `start/end` and their `start_offset/end_offset` aliases address the canonical text. `char_count`, `basis`, optional page bounds, `state`, `retries`, `artifact` and `updated_at` are always represented. Whitespace-only sections are merged losslessly; affected old previews return 409 on submission and must be reviewed again. Persisted job snapshots remain unchanged.

New document jobs persist server-owned `request.document.audio_metadata`; callers cannot supply it. All section downloads and the complete MP3 share the album, using the saved document title, voice/person name and UTC creation minute; selected section order supplies track numbers. Historical jobs are not automatically retagged. See [album rules and the offline single-job tool](DOCUMENT_TTS.md#下载音频的专辑标签--downloaded-audio-album-tags).

ZIP and complete MP3 downloads are streamed without stored exports. Handle 401/404/409/429 before consuming the body and honor `Retry-After` (also `retry_after_seconds` in problem JSON). After the response has begun, transport failures terminate the download; retry from the beginning. The browser UI retains the page on an error and delegates large downloads to the browser download manager without a whole-file Blob.

### 音频波形 / Audio waveforms

`GET /api/v1/jobs/{job_id}/artifacts/{name}/waveform` 使用浏览器会话或 Bearer 鉴权。`name` 必须来自成功任务的 `artifacts[].name`，并进行 URL 编码。返回 `{ "artifact_name": "speech.wav", "duration": 1.5, "waveform": [0.1, 0.4, 0.2] }`，其中峰值按时间均匀分布，范围为 0–1，最多 240 项。

单段文本、文档章节与有序序列在音频生成后准备波形。历史音频或缓存缺失时分块解码补算；同一文件合并计算，最多并发补算两段。尚未成功返回 409，文件缺失或越界返回 404，非 WAV/MP3/FLAC 返回 415，无法解码返回 422，计算繁忙返回 429 和 `Retry-After: 2`。补算期间清理任务返回 409。缓存随任务删除，既有结果字段和契约版本保持兼容。

The authenticated endpoint above returns uniformly timed peak amplitudes for each audio artifact. New single, document and sequence audio is precomputed; historical audio is decoded incrementally on demand and cached. At most two cold computations run concurrently; busy clients should honor `Retry-After`. Waveform files are removed with their job. Existing result schemas and contract versions remain compatible.

可执行示例 / Executable example:

```bash
.runtime/api/bin/python scripts/tts_document.py manuscript.md --waveform
```


## Manual voiceprint reference ranges

`POST /api/v1/tts/jobs` and `/api/v1/tts/document-jobs` accept optional form fields `reference_start_seconds` and `reference_end_seconds`. Both must be supplied together, only with `voice_mode=voiceprint`. Sequence requests accept the same numeric fields on each `items[]` entry. The requested interval must lie within the original sample and be 3–30 seconds long; it may start anywhere, including beyond the first 30 seconds. Non-finite values, incomplete pairs, duplicate form endpoints, unsupported modes and out-of-bounds ranges return `422`.

Omit both endpoints to keep the existing automatic beginning-of-sample selection, up to 15 seconds. Browser-saved ranges are local preferences, never server defaults for API callers. Explicit ranges are part of request identity: replaying the same key and interval returns `200`, while changing the interval with that key returns `409`. Old requests without these fields remain replayable.

Audio and transcript are narrowed together to complete aligned words inside the requested interval. The effective interval must still contain 3–30 seconds; otherwise select a wider range. Existing word timestamps are reused. Missing or inconsistent alignment is recomputed offline in the TTS worker from the submitted snapshot; unavailable language/alignment or insufficient complete speech fails the job explicitly. No alternate interval is silently substituted. The original library sample is not cropped or rewritten.

Single/document results, and each voiceprint sequence result item, expose `reference_start_seconds_used`, `reference_end_seconds_used`, and `reference_text_used`, alongside `reference_duration_original`, `reference_duration_used`, and `reference_truncated`. Times are relative to the original sample. Retries use submission-time snapshots; sequence contract v1 and output order are unchanged. The same sample can have different intervals in different sequence items.

```bash
curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H "Idempotency-Key: $(uuidgen)" \
  -F 'text=这次使用样本中间的声音。' -F 'voice_mode=voiceprint' \
  -F "voiceprint_sample_id=$SAMPLE_ID" -F 'compute_device=gpu' \
  -F 'reference_start_seconds=10' -F 'reference_end_seconds=40' \
  "$BASE_URL/api/v1/tts/jobs"
```

For `/api/v1/tts/sequence-jobs`, supply an item such as:

```json
{"id":"middle","text":"使用中间区间。","voiceprint_sample_id":"sample_id","reference_start_seconds":10,"reference_end_seconds":40}
```

The document CLI supports the same selection:

```bash
.runtime/api/bin/python scripts/tts_document.py manuscript.md \
  --base-url "$BASE_URL" --voiceprint-sample-id "$SAMPLE_ID" \
  --reference-start-seconds 10 --reference-end-seconds 40
```

`GET /api/v1/voiceprints/samples/{sample_id}/audio/waveform` returns the same `{artifact_name,duration,waveform}` shape as artifact waveforms, with at most 240 timed peaks. It requires Bearer or browser-session authentication. Fetch only when the editor is opened; honor `429`/`Retry-After`, allow retry, and use the protected sample audio endpoint for playback and Range requests. Waveform caches are isolated per sample location and removed with the sample. Deletion during waveform reading returns `409`; retry after the reader finishes.

## TTS generation guard / 语音生成保护

真实 TTS 生成默认启用逐文本块保护，覆盖单条、文档、有序序列和 OpenAI 兼容请求。正常调用保持固定模型、精度、采样配置和分块方式。预算由目标文本 token 数计算：`min(8192, max(750, ceil(tokens * 13.5)))`；无 tokenizer 计数时使用明确标记的字符估算。预算不是精确时长预测。达到预算但没有自然 EOS、空音频或非有限波形不会作为成功产物发布。

Each failing original text chunk gets at most **three additional singleton generation calls** with the same voice/reference and default sampling parameters. Only retries may remove clearly identified table-of-contents dot leaders; title/page-number content remains intact. A durable ledger retains the allowance through document resource recovery. User-initiated whole-job retry starts a new allowance; ordered sequences still retry the entire saved request. Exhaustion fails the job with `error_code=TtsGenerationGuardError`; completed document sections remain reusable, while the incomplete section is not published.

`progress_detail.stage_code=tts_chunk_retry` uses `current/total` and `unit=attempt` for retry count; codec-frame activity remains observed and overall progress does not decrease. Job detail/result responses may include:

```json
{"generation_guard":{"version":1,"checked_chunks":8,"retried_chunks":1,"retry_attempts":2,"recovered_chunks":1}}
```

Counters describe original chunks actually checked in the current job attempt; reused historical sections are not newly checked. Missing `generation_guard` means no recorded guard coverage (including older results and mock mode), never an implied successful check. Recovery counters do not constitute comprehensive audio-quality assessment. No new submission parameters or sampling controls are exposed. Global job lists and SSE remain summary-only.

Existing authenticated polling clients can inspect the optional result without changing submission:

```bash
curl --fail-with-body -sS "${AUTH[@]}" "$BASE_URL/api/v1/jobs/$JOB_ID/result" \
  | jq 'if has("generation_guard") then .generation_guard else {status:"not_recorded"} end'
```

对需要裁剪的参考音频，默认和手动选择均校验对齐时间及文本映射；无效对齐只重新计算一次，仍无效则明确失败。默认最多 15 秒、手动 3–30 秒不变；默认短样本不受手动区间的 3 秒下限影响。不会自动修改历史音频或参考转写。

<a id="video-transcription-and-concise-pdf"></a>

## Video transcription and concise PDF / DOCX

See [the bilingual video guide](VIDEO_TRANSCRIPTION.md) for formats, limits, expiry, browser storage and detector tradeoffs. The original ASR endpoint remains compatible. New video tasks use the same ASR queue with `purpose=video_transcription`; global lists/SSE remain summary-only.

1. `POST /api/v1/asr/video-uploads` with `Idempotency-Key` and JSON `{ "name": "lecture.mp4", "size_bytes": 123456 }` returns an upload ID, `chunk_bytes` and durable `offset` (`201`). Reuse the key after a lost response.
2. `PATCH /api/v1/asr/video-uploads/{upload_id}` sends a binary chunk with `Upload-Offset` and hexadecimal SHA-256 `Upload-Checksum`. Nonfinal chunks must be exactly `chunk_bytes`. GET returns the ordered checksum manifest; HEAD returns offset/length/expiry headers. Verify local confirmed bytes before resuming.
3. `POST /api/v1/asr/video-jobs` with its own `Idempotency-Key` and JSON `{ "upload_id": "…", "language": "Auto", "compute_device": "gpu" }` creates a task (`202`), or replays the same task (`200`). Explicit CPU is supported. Optional `video_roi` has normalized `x`, `y`, `width`, `height`. Submission snapshots settings/hotwords; editing a completed task never rewrites the submission. Queue/disk admission uses `429` plus `Retry-After`; conflicts use `409`.
4. Read `GET /api/v1/jobs/{job_id}/video-scenes` for current `revision` and illustrated paragraphs. Each scene includes `id`, `start`, `end`, `frame_time`, `image`, `paragraphs`. Get JPEGs at `/video-frames/{name}` or preview a moment at `/video-preview?time=12.5`.
5. `PATCH /api/v1/jobs/{job_id}/video-scenes` accepts `{ "revision": 1, "action": "split", "scene_id": "scene_1", "time": 12.5 }`. Actions: `merge_next`, `split`, `boundary`, `frame`, `restore`; `time` is needed for split/boundary/frame. Restore needs no scene ID. Stale revision returns `409`; reload and reconcile. Original automatic sections remain in `result.video.original_scenes`.
6. `GET /api/v1/jobs/{job_id}/transcript.pdf` downloads a concise PDF for any completed ASR job, including historical audio jobs. Video adds images. HEAD prepares/validates the same cached export. A busy renderer returns `429`. PDF bytes are not persisted as an artifact. Existing audio JSON/TXT/SRT/VTT exports remain available.
7. `DELETE /api/v1/asr/video-uploads/{upload_id}` immediately discards an unsubmitted upload (`204`). Submitted uploads follow normal task deletion. Expired uploads return `410` until swept, then `404`.

All routes require the normal Bearer/session authentication and same-origin write rules. Strict JSON bodies reject undeclared fields. Upload SHA-256 confirms transfer integrity, not trust in media contents. The executable [Python example](../scripts/submit_video.py) preserves resumable state across client restarts, including the accepted job ID until a requested PDF is completely saved.

### Video binary responses and revision handling

Successful PDF GET responses use `application/pdf`; frame and preview GET responses use `image/jpeg`, all binary. PDF HEAD prepares the same export and returns `Content-Type`, `Content-Length` and UTF-8 `Content-Disposition` without a body. Upload HEAD returns `Upload-Offset`, `Upload-Length` and `Upload-Expires` (Unix seconds) without a body. Upload creation/replay returns `201`; it is separate from the eight asynchronous submission endpoints. Use the actual returned upload ID in the video-job example, not a literal placeholder.

Scene times are seconds from the shared video origin. ROI coordinates are fractions of the complete picture, with `x + width <= 1` and `y + height <= 1`. `frame` permits the section start but excludes its end; `split` requires a time strictly inside that section; `boundary` requires a time strictly between the current section's start and the following section's end. Merge/boundary need a following section. Capture `revision` when editing begins and never replace it with a background refresh revision while retaining old inputs. A stale revision returns `409`; reload and reconcile before editing again. Swagger includes examples for all five actions.

视频 PDF/JPEG 成功响应为二进制；HEAD 无响应体，上传到期时间单位为 Unix 秒。上传创建/重放的 201 与异步任务首次 202、重放 200 分开。时间均为视频相对秒，ROI 为画面比例。编辑时必须固定开始时的修订号，冲突后重新读取，不得保留旧输入却替换成后台的新 revision。PDF 429 按 Retry-After 重试，413 拆分源文件，401 重新鉴权；下载重试不应重新提交视频任务。

## Library export and import

See [the bilingual library guide](LIBRARY.md) for merge/replace rules, sorting, limits, backup storage and browser preference changes. All library endpoints require the existing Bearer/session authentication when configured.

| Endpoint | Contract |
| --- | --- |
| `GET /api/v1/library/export` | Stream the complete ZIP; unfinished enrollment or missing ready audio returns 409 |
| `POST /api/v1/library/imports` | Multipart `file`; mandatory 8–128 character `Idempotency-Key`; first acceptance 202, identical bytes replay 200, conflict/expired 409, admission 429 |
| `GET /api/v1/library/imports?limit=20` | Recent persisted receipts (array, limit 1–100) |
| `GET /api/v1/library/imports/{identifier}` | Poll queued/validating/ready/applying/completed/failed/expired state |
| `POST /api/v1/library/imports/{identifier}/preview` | Strict JSON `{mode: "merge"}` or `{mode: "replace"}`; returns counts and preview revision |
| `POST /api/v1/library/imports/{identifier}/apply` | Strict JSON `mode` plus `preview_revision`; 202, completed matching replay 200; changed/busy library 409 |
| `GET /api/v1/library/imports/{identifier}/backup` | Protected streaming ZIP of the pre-replacement library; supports Range |
| `DELETE /api/v1/library/imports/{identifier}` | 204; remove inactive staged files, retain completed results/backups; active operation 409 |

`completed` records the committed database transaction. The manager may still be retiring files and protecting writes, including staging DELETE, with `409 library_maintenance`; retry shortly. The completion receipt stays durable for crash recovery. / `completed` 表示数据库已提交；旧文件清理未完成时，写操作及暂存包删除仍可能返回 `409 library_maintenance`，应稍后重试，完成回执继续用于崩溃恢复。

A complete executable Python client downloads on A and restores on B. Set client URLs and credentials separately; the key variables are optional for installations without authentication. Install `requests` in your separate client environment before running this example. `LIBRARY_MODE=merge` is the default; `replace` explicitly selects full replacement and requires idle queues. No history is copied or edited. Retry upload errors with the same `request_key` for the same bytes.

```python
import os
import time
import uuid
from pathlib import Path

import requests

a_url = os.environ["LIBRARY_SOURCE_URL"].rstrip("/")
b_url = os.environ["LIBRARY_TARGET_URL"].rstrip("/")
mode = os.environ.get("LIBRARY_MODE", "merge")
if mode not in {"merge", "replace"}:
    raise ValueError("LIBRARY_MODE must be merge or replace")

def session(key_name):
    client = requests.Session()
    key = os.environ.get(key_name)
    if key:
        client.headers["Authorization"] = "Bearer " + key
    return client

a = session("LIBRARY_SOURCE_KEY")
b = session("LIBRARY_TARGET_KEY")
archive = Path("audio-intel-library.zip")
with a.get(a_url + "/api/v1/library/export", stream=True, timeout=120) as response:
    response.raise_for_status()
    with archive.open("wb") as output:
        for chunk in response.iter_content(256 * 1024):
            output.write(chunk)
request_key = uuid.uuid4().hex
with archive.open("rb") as source:
    response = b.post(b_url + "/api/v1/library/imports",
                      headers={"Idempotency-Key": request_key},
                      files={"file": (archive.name, source, "application/zip")}, timeout=120)
response.raise_for_status()
url = b_url + "/api/v1/library/imports/" + response.json()["id"]

def wait():
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        response = b.get(url, timeout=30)
        response.raise_for_status()
        receipt = response.json()
        if receipt["state"] not in {"queued", "validating", "applying"}:
            if receipt.get("error") or receipt["state"] in {"failed", "expired"}:
                raise RuntimeError(receipt.get("error") or receipt["state"])
            return receipt
        time.sleep(1)
    raise TimeoutError("Inspect the persisted import receipt before retrying")

if wait()["state"] != "ready":
    raise RuntimeError("Archive is not ready")
response = b.post(url + "/preview", json={"mode": mode}, timeout=120)
response.raise_for_status()
preview = response.json()
print("Preview:", preview)
response = b.post(url + "/apply",
                  json={"mode": mode, "preview_revision": preview["preview_revision"]}, timeout=120)
response.raise_for_status()
receipt = wait()
if receipt["state"] != "completed":
    raise RuntimeError("Import was not applied; inspect receipt and refresh preview")
print("Result:", receipt["result"])
print("Replacement backup:", receipt.get("backup_url"))
```

`GET /api/v1/voiceprints/people` returns required `sort_initial` (A–Z or #) and full-pinyin/Latin ordered people; `/api/v1/tts/voices` uses the same ordering. The frontend passes that order through clone, document TTS, ASR rename/enrollment and video enrollment pickers. System hotword term order and historical speaker snapshots are unchanged.
## ASR speech enhancement / ASR 人声增强

网页音频和视频表单使用一个“音频增强”总开关，提交 `audio_enhancement=auto|off`，
统一开启／关闭背景降噪和人声增益；API 也推荐同一总开关，默认效果保持不变；开启后可通过高级参数分别配置力度并恢复默认值。独立参数继续兼容现有 API
客户端及历史任务，网页不再分别提供两项开关。浏览器偏好迁移及暂停上传行为见
[增强说明](ASR_ENHANCEMENT.md#browser-control--网页总开关)。

The browser audio/video forms use one Audio enhancement checkbox. The API also
recommends the combined control; submit
`audio_enhancement=auto|off` to enable/disable both operations. Default processing is unchanged; optional numeric strengths configure each operation. Independent API fields and saved request/result contracts remain
supported; paused uploads retain their original settings.

三个入口新增可选 `speech_enhancement_strength`（有限浮点 0–2，默认 1，网页 0–200%）和
`background_denoise_strength`（有限浮点 0–1，GTCRN 默认 0.5，网页 0–100%）。
音频／OpenAI 兼容用 multipart，视频用 JSON；非法范围和非有限数返回 `422`。
能力中两项的 `strength: {min,max,step,default,unit}` 是范围与默认值的权威来源；实验
UL-UNAS 后端降噪默认 1。增益倍率作用于目标 dB 增益并受原安全限制，降噪值是自动选中
片段的湿声比例，不保证消除全部背景音。调高可能影响自然度或识别率。

Zero skips that operation (`bypassed / strength_zero`); denoising at zero loads no model.
The master switch off still disables both. New policy-6 requests snapshot both values;
per-operation results optionally include `strength`. Replay inherits omitted strengths;
changed values return `409`. Historical policy ≤5 replay/retry retains its old semantics;
supplying numeric strengths for a historical idempotency record returns `409` and requires a new key.
The UI saves valid changes immediately under preference v7; resetting strengths leaves
other settings intact. Only new submissions use changed values. Paused uploads retain snapshots.

独立布尔参数 `speech_enhancement`（人声增强）与 `background_denoise`（背景降噪）
适用于下面三个入口，新任务均默认 `true`。音频及兼容入口使用 multipart 的 `true/false`，
视频入口使用 JSON boolean。双开先降噪再平稳增益；仅增益不加载降噪模型。
总开关 `audio_enhancement=auto|off` 统一开启／关闭两项，与任一独立字段混用返回 `422`。
省略参数重放时继承已保存设置，改变任一开关返回 `409`；历史请求不自动升级处理链。

Independent booleans `speech_enhancement` and `background_denoise` default to true
on all three endpoints. Both enabled means denoise then continuous gain; gain
alone loads no denoiser. The recommended combined control cannot be mixed with
independent fields (422).
Replay inherits saved settings; changes conflict (409). Historical requests retain
their previous processing path.

Capabilities add `asr.audio_enhancement.speech_enhancement` and
`.background_denoise`, each with `supported/available/default/strength`. Policy-6 detail/results
add the same two objects with `enabled/status/reason`; denoising also identifies
the actual model/revision/mix. Failure is independent: `status=applied` with
`reason=partial_fallback` can mean gain succeeded while denoising failed.
Global job lists and SSE remain summary-only. The validated GTCRN mix remains the
default; the stronger UL-UNAS candidate failed public CER validation. See the
[enhancement guide](ASR_ENHANCEMENT.md#independent-controls--独立开关).

```bash
BASE_URL="${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}"
API_KEY="${AUDIO_INTEL_API_KEY:?Set AUDIO_INTEL_API_KEY}"
# Recommended: combined automatic denoising and speech gain.
curl --fail-with-body -H "Authorization: Bearer $API_KEY" -H "Idempotency-Key: combined-audio-001" \
  -F file=@meeting.wav -F audio_enhancement=auto \
  -F speech_enhancement_strength=1.5 -F background_denoise_strength=0.75 "$BASE_URL/api/v1/asr/jobs"
# Compatibility example: independent gain without denoising; do not mix fields.
curl -H "Authorization: Bearer $API_KEY" -H "Idempotency-Key: independent-gain-001" \
  -F file=@meeting.wav -F speech_enhancement=true -F background_denoise=false \
  "$BASE_URL/api/v1/asr/jobs"
```

新音频、视频及 OpenAI 兼容转写默认 `audio_enhancement=auto`，显式 `off` 关闭。
增强使用本地 CPU FP32，与 ASR 的 CPU/GPU 选择独立；保留全部交谈者，干净且音量
充足的片段旁路。声纹导入和克隆参考分析不启用增强。

New audio, video and compatible transcription requests default to `auto`; `off`
opts out. Enhancement runs locally on CPU FP32 independently of ASR device choice,
preserves conversation speakers, and bypasses clean audible blocks. Voiceprint
imports and clone-reference analysis are excluded.

| Endpoint | Input / 输入 |
| --- | --- |
| `POST /api/v1/asr/jobs` | multipart `audio_enhancement=auto\|off` |
| `POST /api/v1/asr/video-jobs` | JSON `audio_enhancement: "auto"` or `"off"` |
| `POST /v1/audio/transcriptions` | multipart `audio_enhancement=auto\|off` |

`GET /api/v1/capabilities` 的 `asr.audio_enhancement` 返回 `supported`、`available`、
`default`、`modes`、固定模型及 revision、`compute_device=cpu` 和回放能力。
`available` 反映所选降噪模型是否就绪；两项独立能力分别反映增益和降噪可用性。
`available=false` 不禁用基础 ASR 或独立增益。当前策略 6 分别尝试两项处理，降噪
失败时增益仍可成功；总状态可能为 `applied`、原因为 `partial_fallback`。非法模式返回 `422`。

Capabilities expose support, availability, default/modes, pinned model
identity/revision, CPU execution and enhanced-playback support. Top-level availability
reflects the denoiser; per-operation capabilities report gain separately. Missing
denoising weights leave base ASR and gain available. Policies 5 and 6 report independent
outcomes; partial success is `applied` with `partial_fallback`. Invalid modes return `422`.

| Result `status` | Meaning / 含义 |
| --- | --- |
| `off` | Saved request disables enhancement / 保存的请求关闭增强 |
| `bypassed` | No processing needed; original used / 无需增强，使用原音 |
| `applied` | Enhanced input for transcription/alignment / 转写及对齐使用增强音 |
| `fallback` | No usable enhanced output; reason recorded, original used / 未生成可用增强音，记录原因后使用原音 |

详情及结果中的 `audio_enhancement` 可选；缺失的历史结果不表示增强成功。
客户端应保留对未知 `reason` 的兼容。实际增强时返回 `playback_url` 与可选 `waveform`，
并声明独立 `enhanced.wav` 产物。该 URL 使用普通 Bearer/浏览器会话鉴权，支持
`Range`；追加 `/waveform` 获取波形。原 `/playback` 和源文件语义不变，纯旁路不存
额外 WAV。全局任务列表及 SSE 不包含增强详情或媒体结果。

Job detail/results optionally include `audio_enhancement`; absence in history
does not imply processing. Tolerate future reason strings. Only applied results
offer a separate authenticated Range-capable enhanced artifact and optional peaks;
append `/waveform` to its URL to fetch peaks lazily. Original playback/source
semantics remain, bypass retains no extra WAV, and global summaries/SSE omit detail.
`max_gain_db` is gain in dB; `input_samples` is preserved sample count.
`gain_seconds`/`denoised_seconds` cover processing blocks, not exact voiced duration.
`elapsed_seconds` measures enhancement, excluding subsequent VAD and other stages.

当前策略 6 保留策略 4 引入的人声响度目标、50% GTCRN 混合及连续增益；
使用 6 秒人声上下文与跨块连续增益，
最大变化每秒 3 dB，避免逐秒增益判断带动背景声起落。两次人声检测的并集仍用于 ASR 分段。
新结果可包含下列可选字段，旧任务缺失时不表示已执行新策略：

| Field / 字段 | Meaning / 含义 |
| --- | --- |
| `processing_version` | Signal policy version, independent of model revision / 信号处理策略版本，与模型 revision 独立 |
| `pause_attenuated_seconds` | Seconds actually attenuated, not exact non-speech duration / 实际衰减秒数，不是精确非语音时长 |
| `max_pause_attenuation_db` | Maximum applied reduction, positive dB / 实际最大衰减量，正数 dB |
| `pause_suppression_reason` | Applied/skipped reason; tolerate future values / 执行或跳过原因，客户端应兼容未知值 |

Current policy 6 retains policy 4's continuous gain, loudness target and
50% GTCRN mix, preserving sample count and media clock. Six-second speech context and peak-safe continuous gain bounded to 3 dB/s
replace per-second gain decisions. Policies 4 and 5 report zero for both pause
attenuation fields and `disabled_for_naturalness`. Historical policy-2/3 diagnostics remain
valid, including `applied`, `no_speech`, `no_noise`, `no_long_pauses`,
`vad_unavailable`, and `bypassed`. A second-VAD failure retains base enhancement
and original speech regions. `elapsed_seconds` includes signal processing and
excludes VAD. The combined field accepts only `auto|off`; compatible independent
fields remain available. Enhancement progress in `media_second` accumulates work
across passes and must not be read as recording duration; use `result.duration`.

同一幂等键改变增强模式返回 `409`。重放旧请求时省略字段仍返回原任务，旧请求没有
该字段按 `off` 解释；失败／取消重试保留保存的模式。历史请求、结果和媒体不自动改写。

Changing mode with the same idempotency key returns `409`. Omitting the field when
replaying a historical request preserves its original off semantics. Retry uses
the persisted mode; historical requests/results/media are never backfilled.

```bash
BASE_URL=${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}
AUTH=(); if [[ -n "${AUDIO_INTEL_API_KEY:-}" ]]; then AUTH=(-H "Authorization: Bearer $AUDIO_INTEL_API_KEY"); fi
REQUEST_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
# Native asynchronous ASR, explicit automatic enhancement.
curl --fail-with-body -sS "${AUTH[@]}" -H "Idempotency-Key: $REQUEST_KEY" \
  -F file=@meeting.wav -F compute_device=cpu -F audio_enhancement=auto "$BASE_URL/api/v1/asr/jobs"
# Compatible synchronous transcription, explicit opt-out.
curl --fail-with-body -sS "${AUTH[@]}" -F file=@meeting.wav \
  -F model=qwen3-asr-0.6b -F compute_device=cpu -F audio_enhancement=off "$BASE_URL/v1/audio/transcriptions"
# Use an upload ID from the completed video-upload workflow described above.
VIDEO_UPLOAD_ID=${VIDEO_UPLOAD_ID:?Set a completed video upload ID}
VIDEO_REQUEST_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
jq -n --arg upload_id "$VIDEO_UPLOAD_ID" \
  '{upload_id:$upload_id,compute_device:"cpu",audio_enhancement:"auto"}' | \
  curl --fail-with-body -sS "${AUTH[@]}" -H "Idempotency-Key: $VIDEO_REQUEST_KEY" \
    -H 'Content-Type: application/json' --data-binary @- "$BASE_URL/api/v1/asr/video-jobs"
```

See the [processing and validation contract](ASR_ENHANCEMENT.md). Background
speakers and severe reverberation can remain; text agreement, listening level and
non-speech energy changes do not establish accuracy without a trusted transcript.

## Task storage and timeline waveforms / 任务占用与时间轴波形

`POST /api/v1/jobs/storage` accepts `{ "job_ids": ["job-id"], "refresh": false }` (1–100 IDs, duplicates coalesced, no submission/idempotency semantics). Authentication is required. Returns `{ "items": [{ "id": "job-id", "status": "ready", "bytes": 1048576, "checked_at": "2026-10-19T00:00:00Z", "basis": "allocated" }] }`. Each item is `ready`, `error`, or `not_found`; unknown bytes/basis are null, never fabricated zero. Measures only `data/jobs/<id>` and `tmp/<id>` with no symlink/junction traversal. `basis=allocated` uses filesystem blocks; `logical` is the fallback on platforms without block accounting. It excludes shared voiceprints, documents, models and the database. Active cache TTL is 5 seconds, terminal TTL 30 seconds, with up to two scans; `refresh=true` bypasses cache. Busy returns 429 and `Retry-After: 2`. Measurements are estimates and can differ from deletion's reclaimed bytes. Global job summaries and SSE remain unchanged and do not include storage or read results.

中文：任务占用是按需批量查询的只读元数据；逐项状态、空值与真实零值必须区分。客户端仅查询当前页，勾选按已知项汇总，部分未知时明确提示；刷新可绕过短期缓存。统计不改写历史任务。

`GET /api/v1/jobs/{job_id}/waveform?track=original&start=0&end=60&points=1024` reads saved timeline WAV only. `track` is `original` (default) or `enhanced`; start defaults to 0, end to track end, points is 1–2048 (default 1024). Returns `{ "track": "original", "duration": 120, "start": 0, "end": 60, "waveform": [0.2, 0.4] }`, with actual sample-aligned window boundaries and uniformly spaced peaks in [0,1]. The frontend uses returned window coordinates. End clips to duration; empty/reversed/nonfinite windows return 422. Requires authentication and a successful ASR job (otherwise 409); undeclared/missing WAV returns 404, never reprocesses media or falls back to source. Uses bounded-memory WAV seeking, a bounded in-memory cache and the existing two-calculation budget (429 + `Retry-After: 2`). Deletion while a reader is active returns 409. Existing artifact waveform responses remain capped at 240 points.

中文：视频时间轴从此接口按可见窗口读取原音／增强音，播放时间更新不重复请求；该接口不解析原视频、不生成缺失文件、不持久化新波形缓存。

Executable client / 可执行客户端：

```bash
.runtime/api/bin/python scripts/job_metadata.py storage JOB_ID
.runtime/api/bin/python scripts/job_metadata.py waveform JOB_ID --track enhanced --start 30 --end 60 --points 1024
```

Set `AUDIO_INTEL_BASE_URL` and optional `AUDIO_INTEL_API_KEY` in the client shell. Windows uses `.runtime\api\Scripts\python.exe`.

### 视频画面检测预览 / Video picture detection previews

`asr.video.scene_detection` in capabilities provides the detector version and min/max/step/default for `min_change_percent` (default 3, range 0.5–50) and `min_stable_seconds` (default 1, range 0.5–5). `POST /api/v1/asr/video-jobs` accepts optional `video_scene_detection` with these fields. Omission uses server defaults; new requests snapshot presentation-v3 and effective settings. Historical retry/idempotent replay preserves its original request. Browser result-page preferences are supplied explicitly on later submissions; paused uploads keep their saved parameters.

capabilities 提供画面检测版本、范围及默认值；视频提交可传 `video_scene_detection` 两项参数，省略时使用服务端推荐值。新任务保存版本与有效参数；历史重试及幂等重放保持原请求。浏览器从结果页记住参数并在后续提交时显式传入，暂停上传不改参数。

| Method / 方法 | Path / 路径 | Contract / 契约 |
| --- | --- | --- |
| POST | `/api/v1/jobs/{job_id}/video-scene-previews` | Required `Idempotency-Key`; JSON `revision`, `settings`. First 202, same-request replay 200, conflict 409, busy/disk limit 429 with Retry-After. / 创建预览，不改原结果。 |
| GET | `/api/v1/jobs/{job_id}/video-scene-previews/{preview_id}` | State, settings and measured phase progress; ready includes candidate scenes and protected preview URLs. / 查询真实进度与候选画面。 |
| GET | `/api/v1/jobs/{job_id}/video-scene-previews/{preview_id}/frames/{name}` | Generated preview JPEG only. / 仅该预览生成的图片。 |
| DELETE | `/api/v1/jobs/{job_id}/video-scene-previews/{preview_id}` | Wait for child exit, discard preview, 204. / 等待进程退出后清理。 |
| PATCH | `/api/v1/jobs/{job_id}/video-scenes` | `action=apply_detection`, `revision`, `preview_id`; apply a ready preview, 200 with updated result, stale revision 409. / 显式应用。 |

Preview settings example: `{"revision":1,"settings":{"min_change_percent":3,"min_stable_seconds":1}}`. States are running, ready, failed, cancelled, applying and applied. Progress phases are scanning, detecting, selecting and images; they describe video seconds or prepared image counts, not ASR progress. One active preview; at most eight retained; expiry after 30 minutes or restart returns 410. Idempotency receipts share the preview lifetime. Poll manually after network errors; recreate an expired preview. No inference queue entry is created. All routes require normal session/Bearer authentication and same-origin write protections.

示例请求如上。进度按扫描／缓存计算的媒体秒数、截图数量报告，不伪造 ASR 进度。最多一个活动预览、八份保留预览；30 分钟或重启后返回 410，幂等记录同寿命。网络错误可重查，过期重新预览，不创建 ASR 队列任务。所有接口继续受鉴权及同源写保护。

presentation-v3 将分段和截图确认分开：截图需要在同一分段内连续稳定达到用户配置的 `min_stable_seconds`，与音频、句子时长无关。截图使用独立的降噪容差（显著颜色变化面积低于 0.4%），小通知不能因低于分段面积阈值而绕过确认。多个合格区间采用最后一个区间的首个实际采样帧；短暂变化不覆盖已确认截图。无合格画面时 `image`、`frame_time` 和预览 `preview_url` 为 null，界面与 PDF/DOCX 显示“无稳定画面”，文字和时间段保留；这不是加载错误或导出隐藏。分段范围与截图时刻分别显示。

保持时间范围仍为 0.5–5 秒，输入步进 0.1 秒（如 0.6）；约每 0.5 秒抽样，按实际时间跨度确认，不提前、跨分段或在片尾补足时长，因此非抽样间隔整数倍的设置可能等到下一个抽样点。检测仍是有容差的视觉启发式，不进行 OCR/通知语义识别。自动截图遵守此规则；明确手工指定截图的 API 保留原有语义。

Presentation-v3 separates sections from screenshot confirmation. Automatic pictures require a continuous sampled stable interval inside one section lasting at least the configured `min_stable_seconds`, independently of audio or sentence length. A separate noise tolerance (significant RGB changes over less than 0.4% of pixels) prevents section-area settings from bypassing screenshot confirmation. The first actual sample of the latest qualifying interval represents the section; brief changes cannot overwrite it. Without a qualifying picture, `image`, `frame_time` and preview `preview_url` are null. The reader, detection dialog, timeline and PDF/DOCX show No stable picture while preserving text, ranges and navigation; this is distinct from loading failure and export hiding. Section ranges and capture times are labeled separately.

Duration remains 0.5–5 seconds with a 0.1-second input step, including 0.6. Sampling remains about 0.5 seconds. Confirmation uses observed elapsed time without early acceptance, crossing sections or padding EOF; fractional settings may wait until the next sample. This remains a noise-tolerant visual heuristic without OCR or notification recognition. Explicit manual frame selection retains its API semantics.

New video results and applied detection previews use `video.contract_version=2`; readers continue accepting version 1. Preview responses expose `contract_version=2`. Picture fields must be both non-null or both null. New submissions use v3; historical v1/v2 retries and idempotent replays keep their original policy. Re-detection upgrades a result only on explicit apply, preserving `original_scenes`.

Result `video.scene_detection` records applied parameters; `video.automatic_scenes` records the latest applied automatic sections. `scenes` remains the editable current set and `original_scenes` retains the initial snapshot. `restore` restores `automatic_scenes`, falling back to the initial snapshot for legacy results. Missing legacy parameters do not imply the new defaults were used. Applying preserves the original ASR segments, words and speaker data. Preview generation never changes the job result. CLI examples: `scripts/preview_video_scenes.py JOB_ID` previews; explicit `--apply` saves. `scripts/submit_video.py` also accepts `--scene-min-change-percent` and `--scene-min-stable-seconds`.

结果新增实际参数和最近一次自动分段；首次快照保留。恢复操作优先使用最近一次自动分段，旧结果沿用首次快照。历史缺失参数不表示使用过新默认值。应用不改原始转写文字、词与说话人；预览不改任务结果。CLI 默认只预览，显式 `--apply` 才更新。


### 视频导出画面标记 / Video export visibility

`PATCH /api/v1/jobs/{job_id}/video-scenes` 新增 `action="set_export_visibility"`，同时提交 `revision` 与完整 `export_hidden_scene_ids` 数组（最多 2000 个当前画面 ID，不能重复；空列表恢复全部）。任务视频结果返回同名可选字段，历史缺失视为全部显示。未知 ID 返回 422，修订冲突返回 409；真正改变标记会递增视频修订号，不解码或改写图片。

PDF（直接下载与 POST 导出）和 DOCX 遵循标记，使用占位框并保留文字；JSON 保留标记，文字导出不受影响。该行为独立于 `redact`。应用变化后的检测参数会清空标记，同参数仅继承内容与时间均未变的画面。手工分段 API 也只继承未变画面的标记。

English: PATCH video-scenes with `action="set_export_visibility"`, `revision`, and the complete `export_hidden_scene_ids` list (at most 2000 unique current picture IDs; empty restores all). The optional result field defaults to no hidden pictures for legacy tasks. Unknown IDs are 422 and stale revisions 409; actual changes increment the video revision without decoding or rewriting images. Both PDF routes and DOCX export honor markers independently of text redaction and preserve text; JSON includes markers. Changed detection parameters reset markers; unchanged detection and manual API edits retain only identical pictures and time ranges.

```python
import requests
base_url = "http://127.0.0.1:20810"
api_key = "YOUR_API_KEY"
job_id = "YOUR_JOB_ID"
headers = {"Authorization": f"Bearer {api_key}"}
job = requests.get(f"{base_url}/api/v1/jobs/{job_id}", headers=headers).json()
video = job["result"]["video"]
response = requests.patch(f"{base_url}/api/v1/jobs/{job_id}/video-scenes", headers=headers,
    json={"revision": video["revision"], "action": "set_export_visibility",
          "export_hidden_scene_ids": [video["scenes"][0]["id"]]})
response.raise_for_status()
```
