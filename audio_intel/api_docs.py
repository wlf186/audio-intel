from __future__ import annotations

from typing import Any


API_DESCRIPTION = r"""
## 快速开始 / Quick start

本页和 `/openapi.json` 均由当前服务在本机提供。Swagger 的代码、样式、图标和校验逻辑全部同源加载，运行期间不会访问 CDN、在线校验器或模型云服务。

This page and `/openapi.json` are served by this machine. Swagger code, styles, icons, validation, data, and model inference stay local at runtime.

1. 调用公开的 `GET /api/v1/health` 检查服务。
2. 配置了 `AUDIO_INTEL_API_KEY` 时，点击 **Authorize** 并只输入密钥本身；Swagger 会发送 `Authorization: Bearer …`。不要把密钥放进 URL 或持久化浏览器存储。随后调用受保护的 `GET /api/v1/capabilities` 获取当前设备、模型能力、限制和默认值。
3. 长任务优先使用原生异步 `/api/v1/asr/jobs`、`/api/v1/tts/jobs`。每次逻辑提交生成一个 `Idempotency-Key`，网络重试必须复用它；然后通过 SSE 或响应中的 `status_url` 跟踪任务。

1. Check the public `GET /api/v1/health` probe.
2. If `AUDIO_INTEL_API_KEY` is configured, click **Authorize** and enter only the key. Never place it in a URL or persistent browser storage. Then read live devices, limits, and defaults from the protected `GET /api/v1/capabilities` endpoint.
3. Prefer native asynchronous jobs for long work. Generate one `Idempotency-Key` per logical submission and reuse it for network retries, then track the job through SSE or `status_url`. OpenAI-compatible endpoints block until completion.

原生异步 ASR、TTS、克隆参考分析和声纹样本上传都强制要求 8–128 字符的 `Idempotency-Key`。首次接受返回 `202`；相同键和相同请求重放返回原任务、`200` 和 `Idempotency-Replayed: true`；相同键用于不同请求返回 `409 idempotency_key_conflict`。队列、提交并发或磁盘保护拒绝时返回 `429`、稳定 `code` 和 `Retry-After`，消费方应保留同一个键稍后重试。

Native asynchronous ASR, TTS, clone-reference analysis, and voiceprint upload require an 8–128 character `Idempotency-Key`. The first accepted request returns `202`; replaying the same request returns the original job with `200` and `Idempotency-Replayed: true`; changing the request under the same key returns `409 idempotency_key_conflict`. Admission rejection returns `429`, a stable `code`, and `Retry-After`; keep the same key for that retry.

ASR 与 TTS 默认使用 `compute_device=gpu` 且启用 `accelerate_single_task`。TTS 输出语种默认 `Auto`；已知文本语种时建议显式选择，预置音色优先使用 `/api/v1/capabilities` 返回的母语映射。一次性克隆参考音频应先调用 `/api/v1/tts/clone-references` 自动转写，再核对参考文本和语种后提交 TTS。

ASR and TTS default to GPU with single-task acceleration enabled. TTS output language defaults to `Auto`; choose an explicit language when known, and prefer each preset speaker's native language reported by `/api/v1/capabilities`. Analyze one-off clone references first, then review the transcript and reference language before synthesis.

ASR 显式语种限 `Chinese、English、Cantonese、French、German、Italian、Japanese、Korean、Portuguese、Russian、Spanish`，这 11 种均支持字词级对齐。`Auto` 可能检测到模型支持的其他语种；识别仍会成功，但 `timestamp_precision` 为 `segment`。清单外的显式值会在创建任务前返回 `422`。

Explicit ASR languages are limited to the 11 word-aligned languages listed by `/api/v1/capabilities`. Auto detection may recognize another model language, in which case transcription still succeeds with `timestamp_precision=segment`. An unsupported explicit value returns `422` before a job is created.

<details>
<summary><strong>curl：异步 ASR、轮询与结果 / Async ASR, polling, and result</strong></summary>

```bash
BASE_URL=http://127.0.0.1:20810
AUTH="Authorization: Bearer $AUDIO_INTEL_API_KEY"
IDEMPOTENCY_KEY=$(uuidgen)
JOB_ID=$(curl -sS -H "$AUTH" -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -F file=@meeting.wav -F language=Auto \
  -F speaker_count=auto -F compute_device=gpu \
  "$BASE_URL/api/v1/asr/jobs" | jq -r .id)
curl -sS -H "$AUTH" "$BASE_URL/api/v1/jobs/$JOB_ID"
curl -sS -H "$AUTH" "$BASE_URL/api/v1/jobs/$JOB_ID/result"
```
</details>

<details>
<summary><strong>curl：一次性参考音频克隆 / Clone from a one-off reference</strong></summary>

```bash
REFERENCE_KEY=$(uuidgen)
REFERENCE_JOB_ID=$(curl -sS -H "$AUTH" -H "Idempotency-Key: $REFERENCE_KEY" -F file=@reference.wav \
  "$BASE_URL/api/v1/tts/clone-references" | jq -r .id)
# 按 status_url 轮询成功后，从 result_url 读取并核对 text/language。
REFERENCE_TEXT=$(curl -sS -H "$AUTH" \
  "$BASE_URL/api/v1/jobs/$REFERENCE_JOB_ID/result" | jq -r .text)
TTS_KEY=$(uuidgen)
curl -sS -H "$AUTH" -H "Idempotency-Key: $TTS_KEY" -F text='这是克隆生成的语音。' -F language=Chinese \
  -F voice_mode=inline_clone -F reference_job_id="$REFERENCE_JOB_ID" \
  -F reference_text="$REFERENCE_TEXT" -F reference_language=Chinese \
  "$BASE_URL/api/v1/tts/jobs"
```

分析任务是普通、可查询的 ASR 任务，会出现在任务记录中。`reference_text` 可用于提交人工修正后的准确文本；不要同时再传 `reference_audio`。

The analysis is a regular visible ASR job. Pass a reviewed or corrected `reference_text`; do not also upload `reference_audio` when using `reference_job_id`.
</details>

<details>
<summary><strong>curl：声纹样本 TTS 克隆 / TTS clone from a voiceprint sample</strong></summary>

```bash
SAMPLE_ID=$(curl -sS -H "$AUTH" "$BASE_URL/api/v1/voiceprints/people" |
  jq -r '.items[].samples[] | select(.state=="ready" and .tts_eligible) | .id' | head -n1)
TTS_KEY=$(uuidgen)
curl -sS -H "$AUTH" -H "Idempotency-Key: $TTS_KEY" -F text='这是克隆生成的语音。' -F language=Chinese \
  -F voice_mode=voiceprint -F voiceprint_sample_id="$SAMPLE_ID" \
  "$BASE_URL/api/v1/tts/jobs"
```

必须传具体的 `voiceprint_sample_id`；只传人员 ID 不会自动选择样本。

A concrete `voiceprint_sample_id` is required; a person ID alone never auto-selects a sample.
</details>

<details>
<summary><strong>Python httpx：提交并等待任务 / Submit and wait for a job</strong></summary>

```python
import time
import uuid
import httpx

base_url = "http://127.0.0.1:20810"
headers = {"Authorization": f"Bearer {api_key}"}
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
        job = client.get(job["status_url"]).json()
    if job["state"] != "succeeded":
        raise RuntimeError(job.get("error_message") or job["state"])
    result = client.get(job["result_url"]).json()
```
</details>

<details>
<summary><strong>fetch：同源浏览器或 Node 22 / Same-origin browser or Node 22</strong></summary>

```javascript
const baseUrl = 'http://127.0.0.1:20810'
const idempotencyKey = crypto.randomUUID() // Retain for retries of this logical submission.
const headers = {Authorization: `Bearer ${apiKey}`, 'Idempotency-Key': idempotencyKey}
const form = new FormData()
form.set('text', '这是本地生成的语音。')
form.set('voice_mode', 'preset')
form.set('speaker', 'Vivian')
let job = await fetch(`${baseUrl}/api/v1/tts/jobs`, {method: 'POST', headers, body: form}).then(r => r.json())
while (job.state === 'queued' || job.state === 'running') {
  await new Promise(resolve => setTimeout(resolve, (job.poll_after_seconds || 2) * 1000))
  const response = await fetch(`${baseUrl}${job.status_url}`, {headers})
  if (!response.ok) throw new Error(`status polling failed: ${response.status}`)
  job = await response.json()
}
```

浏览器跨域调用默认不开放 CORS；应使用同源页面、同源会话 Cookie，或由后端/Node 客户端持有 Bearer 密钥。

Cross-origin browser calls are not enabled by default. Use same-origin browser sessions or keep Bearer keys in a backend/Node client.
</details>

<details>
<summary><strong>SSE：跟踪单个任务 / Stream one job</strong></summary>

```bash
curl -N -H "$AUTH" "$BASE_URL/api/v1/jobs/$JOB_ID/events"
```

服务会立即发送 `event: job`，任务变化时继续发送，终态后关闭连接。SSE 没有历史重放；断线后重新连接，并以首个任务快照校准。浏览器原生 `EventSource` 不能设置自定义 Authorization Header，因此同源页面应使用 HttpOnly 会话 Cookie；外部服务端客户端可直接发送 Bearer Header。

The stream immediately emits `event: job`, continues on changes, and closes at a terminal state. There is no history replay. Reconnect and reconcile from the first snapshot. Native browser `EventSource` cannot set a custom Authorization header, so same-origin pages should use the HttpOnly session cookie; server-side clients may send Bearer directly.
</details>

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
- `429` 的稳定 `code` 为 `submission_concurrency_limited`、`queue_capacity_reached` 或 `insufficient_queue_storage`。按 `Retry-After` 等待并复用原 `Idempotency-Key`，不要为同一次逻辑提交生成新键。
- 输入和结果默认保留。受保护媒体支持 Bearer 或同源会话 Cookie；ASR 源文件支持 HTTP Range。
- 安装阶段可以下载固定版本依赖和模型；服务启动后的模型加载强制使用本地 revision，不存在云端回退。
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
