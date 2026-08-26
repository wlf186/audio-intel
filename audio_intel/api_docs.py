from __future__ import annotations

from typing import Any


API_DESCRIPTION = r"""
## 快速开始 / Quick start

本页和 `/openapi.json` 均由当前服务在本机提供。Swagger 的代码、样式、图标和校验逻辑全部同源加载，运行期间不会访问 CDN、在线校验器或模型云服务。

This page and `/openapi.json` are served by this machine. Swagger code, styles, icons, validation, data, and model inference stay local at runtime.

1. 调用 `GET /api/v1/health` 检查服务；调用 `GET /api/v1/capabilities` 获取当前设备、模型能力、限制和默认值。
2. 配置了 `AUDIO_INTEL_API_KEY` 时，点击 **Authorize** 并只输入密钥本身；Swagger 会发送 `Authorization: Bearer …`。不要把密钥放进 URL 或持久化浏览器存储。
3. 长任务优先使用原生异步 `/api/v1/asr/jobs`、`/api/v1/tts/jobs`，然后轮询响应中的 `status_url`。OpenAI 兼容端点是同步接口，适合已有客户端，不提供执行中的进度查询。

1. Check `GET /api/v1/health`, then read live devices, limits, and defaults from `GET /api/v1/capabilities`.
2. If `AUDIO_INTEL_API_KEY` is configured, click **Authorize** and enter only the key. Never place it in a URL or persistent browser storage.
3. Prefer the native asynchronous job endpoints for long work. OpenAI-compatible endpoints block until completion and do not expose in-flight progress.

<details>
<summary><strong>curl：异步 ASR、轮询与结果 / Async ASR, polling, and result</strong></summary>

```bash
BASE_URL=http://127.0.0.1:20810
AUTH="Authorization: Bearer $AUDIO_INTEL_API_KEY"
JOB_ID=$(curl -sS -H "$AUTH" -F file=@meeting.wav -F language=Auto \
  -F speaker_count=auto -F compute_device=gpu \
  "$BASE_URL/api/v1/asr/jobs" | jq -r .id)
curl -sS -H "$AUTH" "$BASE_URL/api/v1/jobs/$JOB_ID"
curl -sS -H "$AUTH" "$BASE_URL/api/v1/jobs/$JOB_ID/result"
```
</details>

<details>
<summary><strong>curl：声纹样本 TTS 克隆 / TTS clone from a voiceprint sample</strong></summary>

```bash
SAMPLE_ID=$(curl -sS -H "$AUTH" "$BASE_URL/api/v1/voiceprints/people" |
  jq -r '.items[].samples[] | select(.state=="ready" and .tts_eligible) | .id' | head -n1)
curl -sS -H "$AUTH" -F text='这是克隆生成的语音。' -F language=Chinese \
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
import httpx

base_url = "http://127.0.0.1:20810"
headers = {"Authorization": f"Bearer {api_key}"}
with httpx.Client(base_url=base_url, headers=headers, timeout=120) as client:
    with open("meeting.wav", "rb") as audio:
        job = client.post("/api/v1/asr/jobs", files={"file": audio}, data={"language": "Auto"}).json()
    while job["state"] in {"queued", "running"}:
        time.sleep(2)
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
const headers = {Authorization: `Bearer ${apiKey}`}
const form = new FormData()
form.set('text', '这是本地生成的语音。')
form.set('voice_mode', 'preset')
form.set('speaker', 'Vivian')
let job = await fetch(`${baseUrl}/api/v1/tts/jobs`, {method: 'POST', headers, body: form}).then(r => r.json())
while (job.state === 'queued' || job.state === 'running') {
  await new Promise(resolve => setTimeout(resolve, 2000))
  job = await fetch(`${baseUrl}${job.status_url}`, {headers}).then(r => r.json())
}
```

浏览器跨域调用默认不开放 CORS；应使用同源页面、同源会话 Cookie，或由后端/Node 客户端持有 Bearer 密钥。

Cross-origin browser calls are not enabled by default. Use same-origin browser sessions or keep Bearer keys in a backend/Node client.
</details>

## 任务状态 / Job lifecycle

- `state`: `queued → running → succeeded|failed|cancelled`。ASR 与 TTS 使用独立的 FIFO 队列。
- `progress` 是 `0–1` 的阶段检查点，不是剩余时间预测；`stage` 给出当前流水线阶段。
- API 当前不返回 `queue_position`。`GET /api/v1/jobs` 的 `count` 是本页数量，不是任务总数。
- SSE `/api/v1/events` 最多发送最近 25 个任务的变化快照，每约 2 秒检查一次；没有事件 ID 或历史重放。断线后应重新连接并通过任务状态接口校准。
- 任务取消只有在完整执行进程树退出后才会进入终态。结果接口在任务成功前返回 `409`。

- ASR and TTS have separate FIFO queues. `progress` is a stage checkpoint, not an ETA.
- There is no `queue_position`. SSE has no replay; reconnect and reconcile through the job status endpoint.

## 重要注意事项 / Important notes

- `compute_device=gpu` 不可用时返回 `503`，不会静默回退 CPU。
- 删除操作要求 `purge=true` 且不可恢复；运行中任务必须先取消并等待终态。
- 声纹克隆只能使用 `state=ready` 且 `tts_eligible=true` 的样本。OpenAI 兼容 TTS 目前仅支持预置音色和兼容 voice profile。
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

BINARY_SCHEMA = {"type": "string", "format": "binary"}
