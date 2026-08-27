from __future__ import annotations

from typing import Any


API_DESCRIPTION = r"""
## 快速开始 / Quick start

本页、`/openapi.json`、Swagger 代码/样式/图标、数据和模型推理均由本机提供；运行期间不访问 CDN、在线校验器或模型云服务。 / This page, its assets, data, and inference stay local at runtime.

1. `GET /api/v1/health`：公开健康探针 / public health probe。
2. **Authorize** → `GET /api/v1/capabilities`：鉴权并读取实时能力和默认值 / authenticate and read live capabilities.
3. 长任务使用异步 `/api/v1/asr/jobs` 或 `/api/v1/tts/jobs`，复用 `Idempotency-Key` 并通过 SSE 或 `status_url` 跟踪；OpenAI 客户端可使用同步 `/v1/audio/transcriptions`、`/v1/audio/speech`。

<details>
<summary><strong>提交契约、默认值与语种 / Submission contract, defaults, and languages</strong></summary>

原生异步 ASR、TTS、克隆参考分析和声纹样本上传都强制要求 8–128 字符的 `Idempotency-Key`。首次接受返回 `202`；相同键和相同请求重放返回原任务、`200` 和 `Idempotency-Replayed: true`；相同键用于不同请求返回 `409 idempotency_key_conflict`。队列、提交并发或磁盘保护拒绝时返回 `429`、稳定 `code` 和 `Retry-After`，消费方应保留同一个键稍后重试。

Native asynchronous ASR, TTS, clone-reference analysis, and voiceprint upload require an 8–128 character `Idempotency-Key`. The first accepted request returns `202`; replaying the same request returns the original job with `200` and `Idempotency-Replayed: true`; changing the request under the same key returns `409 idempotency_key_conflict`. Admission rejection returns `429`, a stable `code`, and `Retry-After`; keep the same key for that retry.

ASR 与 TTS 默认使用 `compute_device=gpu` 且启用 `accelerate_single_task`。TTS 输出语种默认 `Auto`；已知文本语种时建议显式选择，预置音色优先使用 `/api/v1/capabilities` 返回的母语映射。一次性克隆参考音频应先调用 `/api/v1/tts/clone-references` 自动转写，再核对参考文本和语种后提交 TTS。

ASR and TTS default to GPU with single-task acceleration enabled. TTS output language defaults to `Auto`; choose an explicit language when known, and prefer each preset speaker's native language reported by `/api/v1/capabilities`. Analyze one-off clone references first, then review the transcript and reference language before synthesis.

当前安装的 Qwen3-TTS 0.6B Base/CustomVoice 不支持自然语言风格指令，也没有独立的语速或音高参数。请从 `/api/v1/capabilities.tts.controls` 判断公共控制能力；`instruct` 与 OpenAI 兼容接口的 `instructions` 仅为弃用兼容字段，非空值会在创建任务前返回 `422`。模型内部的 `temperature=0.9`、`top_k=50`、`top_p=1.0` 和 `repetition_penalty=1.05` 是固定采样配置，不是公开的语调、语速、风格或情绪控制参数。

The installed Qwen3-TTS 0.6B Base/CustomVoice checkpoints do not support natural-language style instructions or dedicated speaking-rate and pitch parameters. Read public control support from `/api/v1/capabilities.tts.controls`; `instruct` and the OpenAI-compatible `instructions` field are deprecated compatibility fields, and a non-empty value returns `422` before a job is created. Internal defaults such as `temperature=0.9`, `top_k=50`, `top_p=1.0`, and `repetition_penalty=1.05` are fixed sampling configuration, not public tone, rate, style, or emotion controls.

ASR 显式语种限 `Chinese、English、Cantonese、French、German、Italian、Japanese、Korean、Portuguese、Russian、Spanish`，这 11 种均支持字词级对齐。`Auto` 可能检测到模型支持的其他语种；识别仍会成功，但 `timestamp_precision` 为 `segment`。清单外的显式值会在创建任务前返回 `422`。

Explicit ASR languages are limited to the 11 word-aligned languages listed by `/api/v1/capabilities`. Auto detection may recognize another model language, in which case transcription still succeeds with `timestamp_precision=segment`. An unsupported explicit value returns `422` before a job is created.

</details>

<details>
<summary><strong>curl：异步 ASR、轮询与结果 / Async ASR, polling, and result</strong></summary>

```bash
set -euo pipefail
BASE_URL=http://127.0.0.1:20810
AUTH=()
if [[ -n "${AUDIO_INTEL_API_KEY:-}" ]]; then AUTH=(-H "Authorization: Bearer $AUDIO_INTEL_API_KEY"); fi
IDEMPOTENCY_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
TMP_DIR=$(mktemp -d); trap 'rm -rf "$TMP_DIR"' EXIT

while :; do
  HTTP_STATUS=$(curl -sS -o "$TMP_DIR/job.json" -w '%{http_code}' "${AUTH[@]}" \
    -H "Idempotency-Key: $IDEMPOTENCY_KEY" -F file=@meeting.wav \
    -F language=Auto -F speaker_count=auto -F compute_device=gpu \
    "$BASE_URL/api/v1/asr/jobs")
  [[ "$HTTP_STATUS" == 200 || "$HTTP_STATUS" == 202 ]] && break
  if [[ "$HTTP_STATUS" == 429 ]]; then sleep "$(jq -r '.retry_after_seconds // 1' "$TMP_DIR/job.json")"; continue; fi
  jq . "$TMP_DIR/job.json" >&2; exit 1
done
JOB_ID=$(jq -r .id "$TMP_DIR/job.json")
while :; do
  curl --fail-with-body -sS "${AUTH[@]}" "$BASE_URL/api/v1/jobs/$JOB_ID" -o "$TMP_DIR/job.json"
  STATE=$(jq -r .state "$TMP_DIR/job.json")
  [[ "$STATE" == queued || "$STATE" == running ]] || break
  sleep "$(jq -r '.poll_after_seconds // 2' "$TMP_DIR/job.json")"
done
[[ "$STATE" == succeeded ]] || { jq . "$TMP_DIR/job.json" >&2; exit 1; }
curl --fail-with-body -sS "${AUTH[@]}" "$BASE_URL/api/v1/jobs/$JOB_ID/result" | jq .
```
</details>

<details>
<summary><strong>curl：一次性参考音频克隆 / Clone from a one-off reference</strong></summary>

```bash
set -euo pipefail
BASE_URL=http://127.0.0.1:20810
AUTH=(); if [[ -n "${AUDIO_INTEL_API_KEY:-}" ]]; then AUTH=(-H "Authorization: Bearer $AUDIO_INTEL_API_KEY"); fi
wait_job(){ local id=$1 state; while :; do JOB=$(curl --fail-with-body -sS "${AUTH[@]}" "$BASE_URL/api/v1/jobs/$id"); state=$(jq -r .state <<<"$JOB"); [[ "$state" == queued || "$state" == running ]] || break; sleep "$(jq -r '.poll_after_seconds // 2' <<<"$JOB")"; done; [[ "$state" == succeeded ]] || { jq . <<<"$JOB" >&2; return 1; }; }

REFERENCE_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
REFERENCE_JOB=$(curl --fail-with-body -sS "${AUTH[@]}" -H "Idempotency-Key: $REFERENCE_KEY" \
  -F file=@reference.wav "$BASE_URL/api/v1/tts/clone-references")
REFERENCE_JOB_ID=$(jq -r .id <<<"$REFERENCE_JOB")
wait_job "$REFERENCE_JOB_ID"
REFERENCE_RESULT=$(curl --fail-with-body -sS "${AUTH[@]}" "$BASE_URL/api/v1/jobs/$REFERENCE_JOB_ID/result")
REFERENCE_TEXT=$(jq -r .text <<<"$REFERENCE_RESULT") # 提交前核对或修正文本和 language。
TTS_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
TTS_JOB=$(curl --fail-with-body -sS "${AUTH[@]}" -H "Idempotency-Key: $TTS_KEY" -F text='这是克隆生成的语音。' -F language=Chinese \
  -F voice_mode=inline_clone -F reference_job_id="$REFERENCE_JOB_ID" \
  -F reference_text="$REFERENCE_TEXT" -F reference_language=Chinese \
  "$BASE_URL/api/v1/tts/jobs")
TTS_JOB_ID=$(jq -r .id <<<"$TTS_JOB")
wait_job "$TTS_JOB_ID"
curl --fail-with-body -sS "${AUTH[@]}" "$BASE_URL/api/v1/jobs/$TTS_JOB_ID/result" | jq .
```

分析任务是普通、可查询的 ASR 任务，会出现在任务记录中。`reference_text` 可用于提交人工修正后的准确文本；不要同时再传 `reference_audio`。

The analysis is a regular visible ASR job. Pass a reviewed or corrected `reference_text`; do not also upload `reference_audio` when using `reference_job_id`.
</details>

<details>
<summary><strong>curl：声纹样本 TTS 克隆 / TTS clone from a voiceprint sample</strong></summary>

```bash
set -euo pipefail
BASE_URL=http://127.0.0.1:20810
AUTH=(); if [[ -n "${AUDIO_INTEL_API_KEY:-}" ]]; then AUTH=(-H "Authorization: Bearer $AUDIO_INTEL_API_KEY"); fi
SAMPLE_ID=$(curl --fail-with-body -sS "${AUTH[@]}" "$BASE_URL/api/v1/voiceprints/people" |
  jq -r '.items[].samples[] | select(.state=="ready" and .tts_eligible) | .id' | head -n1)
[[ -n "$SAMPLE_ID" ]] || { echo '没有可用于 TTS 的声纹样本' >&2; exit 1; }
TTS_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
curl --fail-with-body -sS "${AUTH[@]}" -H "Idempotency-Key: $TTS_KEY" -F text='这是克隆生成的语音。' -F language=Chinese \
  -F voice_mode=voiceprint -F voiceprint_sample_id="$SAMPLE_ID" \
  "$BASE_URL/api/v1/tts/jobs"
```

必须传具体的 `voiceprint_sample_id`；只传人员 ID 不会自动选择样本。

A concrete `voiceprint_sample_id` is required; a person ID alone never auto-selects a sample.
</details>

<details>
<summary><strong>Python httpx：提交并等待任务 / Submit and wait for a job</strong></summary>

```python
import os
import time
import uuid
import httpx

base_url = "http://127.0.0.1:20810"
api_key = os.getenv("AUDIO_INTEL_API_KEY")
headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
idempotency_key = str(uuid.uuid4())  # Keep this value until this logical submission succeeds.
with httpx.Client(base_url=base_url, headers=headers, timeout=120) as client:
    with open("meeting.wav", "rb") as audio:
        response = client.post(
            "/api/v1/asr/jobs", headers={"Idempotency-Key": idempotency_key},
            files={"file": audio}, data={"language": "Auto"},
        )
        response.raise_for_status()
        job = response.json()
    while job["state"] in {"queued", "running"}:
        time.sleep(job.get("poll_after_seconds") or 2)
        response = client.get(job["status_url"])
        response.raise_for_status()
        job = response.json()
    if job["state"] != "succeeded":
        raise RuntimeError(job.get("error_message") or job["state"])
    response = client.get(job["result_url"])
    response.raise_for_status()
    result = response.json()
```
</details>

<details>
<summary><strong>Node 22 fetch：Bearer 客户端 / Bearer client</strong></summary>

```javascript
const baseUrl = 'http://127.0.0.1:20810'
const apiKey = process.env.AUDIO_INTEL_API_KEY
if (!apiKey) throw new Error('Set AUDIO_INTEL_API_KEY')
const idempotencyKey = crypto.randomUUID() // Retain for retries of this logical submission.
const headers = {Authorization: `Bearer ${apiKey}`, 'Idempotency-Key': idempotencyKey}
const form = new FormData()
form.set('text', '这是本地生成的语音。')
form.set('voice_mode', 'preset')
form.set('speaker', 'Vivian')
let response = await fetch(`${baseUrl}/api/v1/tts/jobs`, {method: 'POST', headers, body: form})
if (!response.ok) throw new Error(`submission failed: ${response.status} ${await response.text()}`)
let job = await response.json()
while (job.state === 'queued' || job.state === 'running') {
  await new Promise(resolve => setTimeout(resolve, (job.poll_after_seconds || 2) * 1000))
  response = await fetch(`${baseUrl}${job.status_url}`, {headers})
  if (!response.ok) throw new Error(`status polling failed: ${response.status}`)
  job = await response.json()
}
if (job.state !== 'succeeded') throw new Error(job.error_message || job.state)
response = await fetch(`${baseUrl}${job.result_url}`, {headers})
if (!response.ok) throw new Error(`result failed: ${response.status}`)
console.log(await response.json())
```

Node 或其他服务端客户端持有 Bearer 密钥。遇到 `429` 时读取 `Retry-After`，等待后使用原 `idempotencyKey` 和完全相同的表单重试。
</details>

<details>
<summary><strong>同源浏览器 fetch：HttpOnly 会话 / Same-origin browser session</strong></summary>

```javascript
const form = new FormData()
form.set('text', '这是本地生成的语音。')
form.set('voice_mode', 'preset')
form.set('speaker', 'Vivian')
const response = await fetch('/api/v1/tts/jobs', {
  method: 'POST', credentials: 'same-origin',
  headers: {'Idempotency-Key': crypto.randomUUID()}, body: form,
})
if (!response.ok) throw new Error(`submission failed: ${response.status} ${await response.text()}`)
const job = await response.json()
const events = new EventSource(`/api/v1/jobs/${job.id}/events`)
events.addEventListener('job', event => {
  const snapshot = JSON.parse(event.data)
  if (!['queued', 'running'].includes(snapshot.state)) events.close()
})
```

浏览器跨域调用默认不开放 CORS；应使用同源页面、同源会话 Cookie，或由后端/Node 客户端持有 Bearer 密钥。

Cross-origin browser calls are not enabled by default. Use same-origin browser sessions or keep Bearer keys in a backend/Node client.
</details>

<details>
<summary><strong>SSE：跟踪单个任务 / Stream one job</strong></summary>

```bash
set -euo pipefail
BASE_URL=http://127.0.0.1:20810
JOB_ID=${JOB_ID:?export the submitted job ID first}
AUTH=(); if [[ -n "${AUDIO_INTEL_API_KEY:-}" ]]; then AUTH=(-H "Authorization: Bearer $AUDIO_INTEL_API_KEY"); fi
curl --fail-with-body -N "${AUTH[@]}" "$BASE_URL/api/v1/jobs/$JOB_ID/events"
```

服务会立即发送 `event: job`，任务变化时继续发送，终态后关闭连接。SSE 没有历史重放；断线后重新连接，并以首个任务快照校准。浏览器原生 `EventSource` 不能设置自定义 Authorization Header，因此同源页面应使用 HttpOnly 会话 Cookie；外部服务端客户端可直接发送 Bearer Header。

The stream immediately emits `event: job`, continues on changes, and closes at a terminal state. There is no history replay. Reconnect and reconcile from the first snapshot. Native browser `EventSource` cannot set a custom Authorization header, so same-origin pages should use the HttpOnly session cookie; server-side clients may send Bearer directly.
</details>

<details>
<summary><strong>任务状态、进度与重要注意事项 / Lifecycle, progress, and important notes</strong></summary>

## 任务状态 / Job lifecycle

- `state`: `queued → running → succeeded|failed|cancelled`。ASR 与 TTS 使用独立的 FIFO 队列。
- 排队任务的 `queue.position` 从 1 开始，表示同类 FIFO 队列中的位置；任务运行后该字段为 `null`。`GET /api/v1/queue` 返回容量、准入预留和磁盘余量。ASR/TTS 队列彼此独立。
- `progress` 是单调的 `0–1` 最佳整体进度；`progress_detail.stage_code` 是稳定阶段。`basis=estimated` 表示阶段百分比包含估算，`current/total/unit` 是已确认阶段单元，`activity` 是当前推理调用的 codec 帧、输出 token 或模型层活动。活动约每 0.5 秒最多持久化一次，不能作为 SLA。
- `estimate` 使用相同设备、模式和任务特征的本机历史。少于 5 个有效样本时为 `warming_up`；可用后返回区间、样本数和置信度，不能作为 SLA。
- SSE `/api/v1/events` 与 `/api/v1/jobs/{id}/events` 共享一次本地数据库快照并向客户端分发；没有事件 ID 或历史重放。断线后重连，并以首个快照或支持 ETag 的任务状态接口校准。轮询间隔可参考 `poll_after_seconds`。
- `GET /api/v1/jobs` 的 `count` 是本页数量，不是任务总数。
- 运行任务请求取消后仍保持 `state=running`，但 `stage=cancelling`；只有完整执行进程树退出后才进入终态 `cancelled`。结果接口在任务成功前返回 `409`。

- ASR and TTS have separate FIFO queues. Queue positions are one-based within each kind.
- Progress is monotonic and best-effort. Inspect `progress_detail.basis` before presenting it as exact; `activity` describes the current model call and may itself have an estimated total. ETA ranges are advisory local-history estimates, never an SLA. SSE has no replay; reconnect and reconcile through the ETag-enabled status endpoint.

## 重要注意事项 / Important notes

- `compute_device=gpu` 不可用时返回 `503`，不会静默回退 CPU。
- ASR 消费方应从 `/api/v1/capabilities.asr.languages` 读取可提交语种；不要把模型的全部识别语种误认为全部支持字词级时间戳。
- 删除操作要求 `purge=true` 且不可恢复；运行中任务必须先取消并等待终态。
- 声纹克隆只能使用 `state=ready` 且 `tts_eligible=true` 的样本。OpenAI 兼容 TTS 目前仅支持预置音色和兼容 voice profile。
- TTS 的 `language` 控制输出文本语种，`reference_language` 控制一次性克隆参考的转写/对齐语种；两者不是同一个参数。已知语种时显式填写可减少自动判断歧义。
- 当前 0.6B TTS 仅根据文本语义和标点自然生成韵律，不支持可控风格/情绪指令，也没有独立语速或音高参数；以 `/api/v1/capabilities.tts.controls` 为准。弃用的 `instruct`/`instructions` 非空时返回 `422`，不会静默忽略。
- `429` 的稳定 `code` 为 `submission_concurrency_limited`、`queue_capacity_reached` 或 `insufficient_queue_storage`。按 `Retry-After` 等待并复用原 `Idempotency-Key`，不要为同一次逻辑提交生成新键。
- 输入和结果默认保留。受保护媒体支持 Bearer 或同源会话 Cookie；ASR 源文件支持 HTTP Range。
- 安装阶段可以下载固定版本依赖和模型；服务启动后的模型加载强制使用本地 revision，不存在云端回退。

</details>
"""


OPENAPI_TAGS = [
    {"name": "Service / 服务", "description": "公开健康探针、能力和受保护系统状态。 / Health, capabilities, and system status."},
    {"name": "Authentication / 鉴权", "description": "Bearer API key 与同源 HttpOnly 浏览器会话。 / Bearer API key and same-origin browser sessions."},
    {"name": "ASR / 语音识别", "description": "原生异步 ASR 任务。 / Native asynchronous transcription jobs."},
    {"name": "TTS / 语音合成", "description": "预置音色、声音档案、内联克隆和声纹样本克隆。 / Preset, profile, inline, and voiceprint synthesis."},
    {"name": "Voiceprints / 声纹库", "description": "人员、样本、入库和受保护音频。 / People, samples, imports, and protected audio."},
    {"name": "Jobs / 任务", "description": "状态、进度、结果、取消、重试、文件和 SSE。 / Status, progress, results, cancellation, retry, media, and SSE."},
    {"name": "OpenAI compatibility / OpenAI 兼容", "description": "同步兼容接口；长任务优先使用原生异步 API。 / Synchronous compatibility endpoints; prefer native jobs for long work."},
]


def bilingual(chinese: str, english: str) -> str:
    return f"{chinese}\n\n**English:** {english}"


def problem_response(description: str, status: int) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/problem+json": {
                "schema": {"$ref": "#/components/schemas/ProblemDetail"},
                "example": {
                    "type": "about:blank", "title": description, "status": status,
                    "code": f"http_{status}", "detail": description,
                },
            }
        },
    }


def problem_examples_response(
    description: str,
    status: int,
    examples: dict[str, dict[str, Any]],
    schema: str = "ProblemDetail",
    headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "description": description,
        "content": {
            "application/problem+json": {
                "schema": {"$ref": f"#/components/schemas/{schema}"},
                "examples": {
                    key: {
                        "summary": value["detail"],
                        "value": {
                            "type": "about:blank", "title": value["detail"],
                            "status": status, **value,
                        },
                    }
                    for key, value in examples.items()
                },
            }
        },
    }
    if headers:
        response["headers"] = headers
    return response


def idempotency_replay_response(schema: str) -> dict[int, dict[str, Any]]:
    return {
        200: {
            "description": "同一请求的幂等重放 / Idempotent replay of the same request",
            "headers": {
                "Idempotency-Replayed": {
                    "description": "幂等重放时固定为 true / Always true for an idempotent replay",
                    "schema": {"type": "string", "enum": ["true"]},
                }
            },
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{schema}"},
                }
            },
        }
    }


def conditional_job_responses() -> dict[int, dict[str, Any]]:
    etag_header = {
        "description": "任务与同类队列上下文版本 / Version of the job and same-kind queue context",
        "schema": {"type": "string"},
    }
    cache_header = {
        "description": "要求客户端重新校验 / Requires client revalidation",
        "schema": {"type": "string", "example": "no-cache"},
    }
    return {
        200: {
            "description": "当前任务快照 / Current job snapshot",
            "headers": {"ETag": etag_header, "Cache-Control": cache_header},
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/JobResponse"},
                }
            },
        },
        304: {
            "description": "If-None-Match 仍匹配；无响应体 / If-None-Match still matches; no response body",
            "headers": {"ETag": etag_header, "Cache-Control": cache_header},
        },
    }


def sse_response(event: str, schema: str, example: str) -> dict[str, Any]:
    return {
        "description": "Server-Sent Events",
        "content": {
            "text/event-stream": {
                "schema": {"type": "string"},
                "example": f"event: {event}\\ndata: {example}\\n\\n",
            }
        },
        "x-event-data-schema": {"$ref": f"#/components/schemas/{schema}"},
    }


AUTH_RESPONSES = {
    401: problem_response("缺少或无效的 Bearer 密钥 / Missing or invalid Bearer key", 401),
    403: problem_response("浏览器 Cookie 写入不是同源请求 / Cookie-authenticated write is not same-origin", 403),
}

NOT_FOUND_RESPONSE = {404: problem_response("资源不存在 / Resource not found", 404)}
CONFLICT_RESPONSE = {409: problem_response("资源状态冲突 / Resource state conflict", 409)}
SERVICE_RESPONSE = {503: problem_response("服务或请求的计算设备不可用 / Service or requested compute device unavailable", 503)}
VALIDATION_RESPONSE = {
    422: {
        "description": "参数或业务校验失败 / Parameter or business validation failed",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetail"}},
            "application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}},
        },
    }
}
TTS_CONTROL_VALIDATION_RESPONSE = {
    422: {
        "description": "参数、业务或不支持的 TTS 控制参数 / Parameter, business, or unsupported TTS control validation failure",
        "content": {
            "application/problem+json": {
                "schema": {"$ref": "#/components/schemas/ProblemDetail"},
                "examples": {
                    "unsupported_instruction": {
                        "summary": "当前 0.6B 模型不支持风格指令 / Style instructions are unsupported by the current 0.6B models",
                        "value": {
                            "type": "about:blank",
                            "title": "Style instructions are not supported by the installed Qwen3-TTS 0.6B models; omit this field or send an empty value",
                            "status": 422,
                            "code": "http_422",
                            "detail": "Style instructions are not supported by the installed Qwen3-TTS 0.6B models; omit this field or send an empty value",
                        },
                    }
                },
            },
            "application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}},
        },
    }
}
TOO_LARGE_RESPONSE = {413: problem_response("上传文件超过服务限制 / Uploaded file exceeds the service limit", 413)}
IDEMPOTENCY_RESPONSES = {
    400: problem_examples_response(
        "缺少或无效的 Idempotency-Key / Missing or invalid Idempotency-Key",
        400,
        {
            "required": {
                "code": "idempotency_key_required",
                "detail": "Idempotency-Key is required",
            },
            "invalid": {
                "code": "invalid_idempotency_key",
                "detail": "Idempotency-Key must contain 8-128 HTTP token characters",
            },
        },
    ),
    409: problem_examples_response(
        "幂等键对应的请求不一致 / Idempotency-Key request conflict",
        409,
        {
            "conflict": {
                "code": "idempotency_key_conflict",
                "detail": "Idempotency-Key was already used with a different request",
            },
        },
    ),
}
OPTIONAL_IDEMPOTENCY_RESPONSES = {
    400: problem_examples_response(
        "无效的可选 Idempotency-Key / Invalid optional Idempotency-Key",
        400,
        {
            "invalid": {
                "code": "invalid_idempotency_key",
                "detail": "Idempotency-Key must contain 8-128 HTTP token characters",
            },
        },
    ),
    409: IDEMPOTENCY_RESPONSES[409],
}
ADMISSION_RESPONSE = {
    429: problem_examples_response(
        "本地队列、提交并发或磁盘保护拒绝 / Local queue, submission, or storage admission rejected",
        429,
        {
            "submission_concurrency_limited": {
                "code": "submission_concurrency_limited",
                "detail": "Too many submissions are being persisted; retry shortly",
                "retry_after_seconds": 1,
                "queue": {"kind": "asr", "depth": 2, "capacity": 5},
                "storage": {"free_bytes": 10737418240, "minimum_free_bytes": 5368709120},
            },
            "queue_capacity_reached": {
                "code": "queue_capacity_reached",
                "detail": "The ASR queue has reached its configured capacity",
                "retry_after_seconds": 30,
                "queue": {"kind": "asr", "depth": 5, "capacity": 5},
                "storage": {"free_bytes": 10737418240, "minimum_free_bytes": 5368709120},
            },
            "insufficient_queue_storage": {
                "code": "insufficient_queue_storage",
                "detail": "The local data volume does not have enough reserved free space",
                "retry_after_seconds": 300,
                "queue": {"kind": "tts", "depth": 0, "capacity": 5},
                "storage": {"free_bytes": 4294967296, "minimum_free_bytes": 5368709120},
            },
        },
        schema="AdmissionProblemDetail",
        headers={
            "Retry-After": {
                "description": "建议等待秒数 / Suggested delay in seconds",
                "schema": {"type": "integer", "minimum": 1},
            }
        },
    )
}

BINARY_SCHEMA = {"type": "string", "format": "binary"}


FIELD_DESCRIPTIONS = {
    "id": "资源的稳定本地标识 / Stable local resource identifier",
    "name": "资源名称 / Resource name",
    "kind": "任务类型 / Job kind",
    "state": "当前状态 / Current state",
    "status": "服务或请求状态 / Service or request status",
    "version": "服务版本 / Service version",
    "progress": "任务整体进度 0–1 / Overall job progress from 0 to 1",
    "stage": "当前处理阶段 / Current processing stage",
    "display_name": "面向用户的任务名称 / User-facing job name",
    "request": "创建任务时保存的请求快照 / Request snapshot stored when the job was created",
    "result": "成功任务的结果；未完成时为 null / Successful result; null before completion",
    "error_code": "稳定错误代码 / Stable error code",
    "error_message": "可供用户或开发者阅读的错误详情 / Human-readable error detail",
    "created_at": "创建时间（ISO 8601） / Creation time in ISO 8601 format",
    "updated_at": "最后更新时间（ISO 8601） / Last update time in ISO 8601 format",
    "started_at": "开始处理时间（ISO 8601） / Processing start time in ISO 8601 format",
    "finished_at": "终态时间（ISO 8601） / Terminal time in ISO 8601 format",
    "heartbeat_at": "worker 最近心跳时间（ISO 8601） / Latest worker heartbeat in ISO 8601 format",
    "processing_as_of": "累计处理耗时的基准时间 / Reference time for accumulated processing duration",
    "processing_seconds": "累计实际处理秒数，不含排队 / Accumulated processing seconds excluding queue wait",
    "duration": "音频时长，单位秒 / Audio duration in seconds",
    "start": "片段或字词开始时间，单位秒 / Segment or word start time in seconds",
    "end": "片段或字词结束时间，单位秒 / Segment or word end time in seconds",
    "text": "转写文本或待合成文本 / Transcript or text to synthesize",
    "language": "语种；可用值以 capabilities 为准 / Language; read supported values from capabilities",
    "compute_device": "计算设备 cpu 或 gpu / Compute device: cpu or gpu",
    "compute_device_name": "提交或执行时记录的具体设备名称 / Concrete device name recorded at submission or execution",
    "items": "返回的资源条目 / Returned resource items",
    "count": "当前响应中的条目数量 / Number of items in this response",
    "total": "当前筛选条件下的总数 / Total matching the current filters",
    "limit": "当前分页上限 / Current page-size limit",
    "offset": "当前分页偏移 / Current page offset",
    "has_more": "是否还有后续分页 / Whether another page is available",
    "status_url": "任务状态与进度轮询地址 / Job status and progress polling URL",
    "result_url": "成功后读取结果的地址 / Result URL available after success",
    "source_url": "受保护的 ASR 原始音源地址 / Protected ASR source-media URL",
    "poll_after_seconds": "建议轮询间隔秒数 / Suggested polling interval in seconds",
    "artifacts": "可通过受保护 URL 下载的任务产物 / Job artifacts available through protected URLs",
    "waveform": "归一化波形采样值 / Normalized waveform samples",
    "segments": "带时间范围的转写片段 / Timestamped transcription segments",
    "speakers": "任务完成时保存的说话人快照 / Speaker snapshots stored at completion",
    "sample_rate": "音频采样率，单位 Hz / Audio sample rate in Hz",
    "format": "输出文件格式 / Output file format",
    "voice_mode": "TTS 音色来源模式 / TTS voice-source mode",
    "precision": "模型执行精度 / Model execution precision",
    "quantized": "是否使用量化模型 / Whether a quantized model was used",
}

DATETIME_FIELDS = {
    "created_at", "updated_at", "started_at", "finished_at", "heartbeat_at",
    "processing_as_of", "earliest", "latest",
}

REQUEST_EXAMPLES: dict[tuple[str, str], dict[str, dict[str, Any]]] = {
    ("/api/v1/asr/jobs", "post"): {
        "meeting": {"summary": "会议转写 / Meeting transcription", "value": {"file": "meeting.wav", "language": "Auto", "speaker_count": "auto", "diarize": True, "align": True, "compute_device": "gpu", "accelerate_single_task": True}},
    },
    ("/api/v1/tts/jobs", "post"): {
        "preset": {"summary": "预置音色 / Preset voice", "value": {"text": "你好，这是本地语音。", "language": "Chinese", "voice_mode": "preset", "speaker": "Vivian", "response_format": "wav", "compute_device": "gpu"}},
        "inline_clone": {"summary": "分析任务克隆 / Clone from analyzed reference", "value": {"text": "这是克隆语音。", "language": "Chinese", "voice_mode": "inline_clone", "reference_job_id": "0123456789abcdef0123456789abcdef", "reference_text": "与参考音频一致的文本", "reference_language": "Chinese"}},
        "voiceprint": {"summary": "声纹样本克隆 / Voiceprint sample clone", "value": {"text": "这是克隆语音。", "language": "Chinese", "voice_mode": "voiceprint", "voiceprint_sample_id": "sample_0123456789abcdef"}},
    },
}

JOB_EXAMPLES = {
    "queued": {"summary": "排队 / Queued", "value": {"id": "0123456789abcdef0123456789abcdef", "kind": "tts", "state": "queued", "progress": 0, "stage": "queued", "display_name": "语音合成", "request": {"text": "你好", "compute_device": "gpu"}, "created_at": "2026-08-27T12:00:00+00:00", "compute_device": "gpu", "compute_device_name": "NVIDIA GPU", "status_url": "/api/v1/jobs/0123456789abcdef0123456789abcdef", "queue": {"scope": "tts", "position": 2, "depth": 4, "capacity": 5, "waiting_for": "worker"}, "poll_after_seconds": 2}},
    "running": {"summary": "运行与细粒度进度 / Running with fine progress", "value": {"id": "0123456789abcdef0123456789abcdef", "kind": "tts", "state": "running", "progress": 0.53, "stage": "synthesis", "display_name": "语音合成", "request": {"text": "你好", "compute_device": "gpu"}, "created_at": "2026-08-27T12:00:00+00:00", "started_at": "2026-08-27T12:00:02+00:00", "compute_device": "gpu", "compute_device_name": "NVIDIA GPU", "progress_detail": {"stage_code": "synthesis", "stage_progress": 0.4, "basis": "estimated", "current": 1, "total": 3, "unit": "text_chunk", "activity": {"sequence": 2, "current": 41, "total": 90, "unit": "codec_frame", "basis": "estimated", "updated_at": "2026-08-27T12:00:10+00:00"}}, "poll_after_seconds": 1}},
    "succeeded": {"summary": "成功 / Succeeded", "value": {"id": "0123456789abcdef0123456789abcdef", "kind": "tts", "state": "succeeded", "progress": 1, "stage": "completed", "display_name": "语音合成", "request": {"text": "你好", "compute_device": "gpu"}, "result": {"duration": 1.8, "format": "wav", "speaker": "Vivian", "artifacts": []}, "created_at": "2026-08-27T12:00:00+00:00", "finished_at": "2026-08-27T12:00:12+00:00", "compute_device": "gpu", "compute_device_name": "NVIDIA GPU", "result_url": "/api/v1/jobs/0123456789abcdef0123456789abcdef/result"}},
}

RESULT_EXAMPLES = {
    "asr": {"summary": "ASR 结果 / ASR result", "value": {"text": "欢迎使用本地转写。", "language": "Chinese", "duration": 3.2, "timestamp_precision": "word_or_character", "segments": [], "speakers": [], "artifacts": []}},
    "tts": {"summary": "TTS 结果 / TTS result", "value": {"duration": 1.8, "format": "wav", "sample_rate": 24000, "voice_mode": "preset", "speaker": "Vivian", "compute_device": "gpu", "precision": "bfloat16", "quantized": False, "artifacts": []}},
}


def _example_for_schema(value: dict[str, Any], schemas: dict[str, Any], seen: set[str] | None = None) -> Any:
    seen = set() if seen is None else seen
    if "example" in value:
        return value["example"]
    if "default" in value:
        return value["default"]
    if value.get("enum"):
        return value["enum"][0]
    if "$ref" in value:
        name = value["$ref"].rsplit("/", 1)[-1]
        if name in seen:
            return {}
        return _example_for_schema(schemas.get(name, {}), schemas, seen | {name})
    for key in ("allOf", "anyOf", "oneOf"):
        choices = [item for item in value.get(key, []) if item.get("type") != "null"]
        if choices:
            return _example_for_schema(choices[0], schemas, seen)
    value_type = value.get("type")
    if value_type == "object" or "properties" in value:
        return {
            name: _example_for_schema(prop, schemas, seen)
            for name, prop in value.get("properties", {}).items()
            if not prop.get("writeOnly")
        }
    if value_type == "array":
        return [_example_for_schema(value.get("items", {}), schemas, seen)]
    if value_type == "integer":
        return max(1, int(value.get("minimum", 1)))
    if value_type == "number":
        return max(0.5, float(value.get("minimum", 0.5)))
    if value_type == "boolean":
        return True
    if value.get("format") == "date-time":
        return "2026-08-27T12:00:00+00:00"
    if value.get("format") == "binary":
        return "audio.wav"
    return "string"


def enrich_openapi_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Add documentation-only descriptions and examples without changing wire models."""
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    for schema_name, definition in schemas.items():
        if schema_name in {"HTTPValidationError", "ValidationError"}:
            continue
        for field_name, prop in definition.get("properties", {}).items():
            prop.setdefault(
                "description",
                FIELD_DESCRIPTIONS.get(
                    field_name,
                    f"{field_name} 字段；具体语义见所属接口 / {field_name} field; see the owning operation for semantics",
                ),
            )
            if field_name in DATETIME_FIELDS:
                targets = prop.get("anyOf", [prop])
                for target in targets:
                    if target.get("type") == "string":
                        target.setdefault("format", "date-time")
        if definition.get("properties") and "example" not in definition:
            definition["example"] = _example_for_schema(definition, schemas, {schema_name})

    for path, methods in schema.get("paths", {}).items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            request_body = operation.get("requestBody", {})
            for media in request_body.get("content", {}).values():
                if "examples" not in media:
                    custom = REQUEST_EXAMPLES.get((path, method))
                    media["examples"] = custom or {
                        "default": {
                            "summary": "请求示例 / Request example",
                            "value": _example_for_schema(media.get("schema", {}), schemas),
                        }
                    }
            for status, response in operation.get("responses", {}).items():
                if not str(status).startswith("2"):
                    continue
                for content_type, media in response.get("content", {}).items():
                    if content_type in {"text/event-stream", "application/octet-stream"} or content_type.startswith(("audio/", "video/")):
                        continue
                    response_schema = media.get("schema", {})
                    ref_name = response_schema.get("$ref", "").rsplit("/", 1)[-1]
                    if "example" not in media and "examples" not in media:
                        if ref_name in {"JobResponse", "EventJobResponse"}:
                            media["examples"] = JOB_EXAMPLES
                        elif ref_name == "JobResultResponse":
                            media["examples"] = RESULT_EXAMPLES
                        else:
                            media["example"] = _example_for_schema(response_schema, schemas)
    return schema
