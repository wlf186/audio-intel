from __future__ import annotations

from typing import Any

from .model_registry import asr_models, tts_models


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

ASR 默认模型为 `qwen3-asr-0.6b`，也可选择 `qwen3-asr-1.7b`。消费方应读取 `asr.models[].compute_devices`，按模型判断设备能力；GPU 门槛使用报告的**总显存**而非当前空闲显存。0.6B/1.7B 的门槛分别为 3840/7936 MiB，因此报告 8151 MiB 的 8 GiB 显卡可选择 1.7B。门槛是准入条件，不保证运行时不会受其他 GPU 进程影响而 OOM。显式 API GPU 请求不可用时返回 `503`，不会自动改为 CPU。

The default ASR model is `qwen3-asr-0.6b`; `qwen3-asr-1.7b` is also available. Read `asr.models[].compute_devices` for model-scoped device eligibility. GPU admission uses reported total memory, not current free memory. The 0.6B/1.7B thresholds are 3840/7936 MiB, so an 8151 MiB 8 GiB GPU is eligible for 1.7B. Admission does not guarantee that unrelated GPU use cannot cause an OOM. An explicit unavailable GPU request returns `503` and never changes to CPU automatically.

两个 ASR 模型都可使用本地热词库。只读系统词表“声纹库人名”自动同步已开启“加入热词库”的声纹人员名字，但与自定义词表一样需要显式选择。一次最多选择 8 个词表；留空禁用已保存词表。服务会规范化并去重词条，把 `Vocabulary: ...` 追加到一次性 `context`/`prompt`，并在提交时保存不可变词表快照。热词是识别提示而非强制词典；克隆参考分析和声纹样本入库不使用热词。

Both ASR models support the local hotword library. The read-only `声纹库人名` system list synchronizes opted-in voiceprint person names, but still requires explicit selection. Select up to eight lists, or leave the field empty to disable stored lists. The service stores immutable list snapshots at submission. Hotwords are recognition hints; clone-reference analysis and voiceprint imports do not use them.

TTS 默认模型组为 `qwen3-tts-0.6b`，也可选择 `qwen3-tts-1.7b`。逐模型能力位于 `tts.model_capabilities[]`；GPU 总显存门槛同样为 3840/7936 MiB。1.7B 的预置音色支持可选自然语言 `instruct`，`voice_design` 用必填指令描述音色、语速、音调、韵律和情绪；Base 克隆模式不支持指令。官方没有独立数值语速或音高参数，底层采样配置也不公开。消费方必须按所选模型的 `controls` 决定显示和发送哪些字段。

The default TTS model group is `qwen3-tts-0.6b`, with `qwen3-tts-1.7b` also available. Read per-model behavior from `tts.model_capabilities[]`; total-memory GPU thresholds are likewise 3840/7936 MiB. A 1.7B preset voice accepts an optional natural-language `instruct`, while `voice_design` requires an instruction describing timbre, rate, pitch, prosody, and emotion. Base voice-clone modes do not accept instructions. There are no dedicated numeric speaking-rate or pitch parameters, and low-level sampling remains fixed. Send controls only when the selected model capability advertises them.

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
TMP_DIR=$(mktemp -d)
HOTWORD_LIST_ID=
cleanup(){ rm -rf "$TMP_DIR"; if [[ -n "$HOTWORD_LIST_ID" ]]; then curl -sS -X DELETE "${AUTH[@]}" "$BASE_URL/api/v1/asr/hotword-lists/$HOTWORD_LIST_ID" >/dev/null || true; fi; }
trap cleanup EXIT

HOTWORD_LIST_ID=$(curl --fail-with-body -sS "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d "{\"name\":\"API 示例术语 $IDEMPOTENCY_KEY\",\"terms\":[\"Qwen3-ASR\",\"Sandevistan-Audio\"]}" \
  "$BASE_URL/api/v1/asr/hotword-lists" | jq -r .id)

while :; do
  HTTP_STATUS=$(curl -sS -o "$TMP_DIR/job.json" -w '%{http_code}' "${AUTH[@]}" \
    -H "Idempotency-Key: $IDEMPOTENCY_KEY" -F file=@meeting.wav \
    -F model=qwen3-asr-0.6b -F hotword_list_ids="$HOTWORD_LIST_ID" \
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
<summary><strong>curl：1.7B 预置音色与音色设计 / 1.7B preset and voice design</strong></summary>

```bash
BASE_URL=http://127.0.0.1:20810
AUTH=(); if [[ -n "${AUDIO_INTEL_API_KEY:-}" ]]; then AUTH=(-H "Authorization: Bearer $AUDIO_INTEL_API_KEY"); fi
curl --fail-with-body -sS "${AUTH[@]}" -H "Idempotency-Key: $(python3 -c 'import uuid; print(uuid.uuid4())')" \
  -F text='欢迎使用本地语音服务。' -F model=qwen3-tts-1.7b \
  -F voice_mode=preset -F speaker=Vivian -F language=Chinese \
  -F instruct='语速舒缓，用温柔而开心的语气说。' -F compute_device=cpu \
  "$BASE_URL/api/v1/tts/jobs" | jq .

curl --fail-with-body -sS "${AUTH[@]}" -H "Idempotency-Key: $(python3 -c 'import uuid; print(uuid.uuid4())')" \
  -F text='系统已经准备就绪。' -F model=qwen3-tts-1.7b \
  -F voice_mode=voice_design -F language=Chinese \
  -F instruct='成熟低沉的女性声音，音调略低，语速沉稳，带着克制的喜悦。' \
  -F compute_device=cpu "$BASE_URL/api/v1/tts/jobs" | jq .
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
<summary><strong>SSE：全局摘要增量 / Stream global summary deltas</strong></summary>

```javascript
const jobs = new Map()
const events = new EventSource('/api/v1/events')
events.addEventListener('snapshot', event => {
  jobs.clear()
  for (const job of JSON.parse(event.data).jobs) jobs.set(job.id, job)
})
events.addEventListener('update', event => {
  const delta = JSON.parse(event.data)
  for (const id of delta.removed_job_ids) jobs.delete(id)
  for (const job of delta.jobs) jobs.set(job.id, job)
})
```

全局事件只含任务摘要；打开某个成功任务时再请求 `/api/v1/jobs/{job_id}`。`heartbeat` 的数据恒为 `{}`，不应触发列表或详情刷新。

Global events contain summaries only. Fetch `/api/v1/jobs/{job_id}` when opening a completed task. A `heartbeat` always contains `{}` and must not trigger list or detail refreshes.
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
- `progress` 是单调的 `0–1` 最佳整体进度；`progress_detail.stage_code` 是稳定阶段。`basis=estimated` 表示阶段百分比包含估算，`current/total/unit` 是已确认阶段单元，`activity` 是当前模型加载、codec 帧、输出 token 或模型层活动。模型实际提供细粒度活动时约每 0.5 秒最多持久化一次；`model_load` 只报告加载开始/完成边界，阻塞加载期间不保证心跳，所有进度都不能作为 SLA。
- `estimate` 使用相同设备、模型、模式和任务特征的本机历史；ASR/TTS 的 0.6B/1.7B 分别热身。少于 5 个有效样本时为 `warming_up`；可用后返回区间、样本数和置信度，不能作为 SLA。
- 全局 SSE `/api/v1/events` 首帧为摘要 `snapshot`，随后只发送语义 `update`（变更任务、`removed_job_ids`、当前 worker）和空闲 `heartbeat`；单任务 `/api/v1/jobs/{job_id}/events` 保持完整任务契约。两者都没有事件 ID或历史重放。
- `GET /api/v1/jobs` 只返回摘要，不含 `request`/`result`；`count` 是本页数量，不是任务总数。完整详情按需读取 `GET /api/v1/jobs/{job_id}`。
- 运行任务请求取消后仍保持 `state=running`，但 `stage=cancelling`；只有完整执行进程树退出后才进入终态 `cancelled`。结果接口在任务成功前返回 `409`。

- ASR and TTS have separate FIFO queues. Queue positions are one-based within each kind. Progress is monotonic and best-effort. Fine-grained activity is persisted at most about every 0.5 seconds when the model exposes it; `model_load` reports start/end boundaries only and does not promise a heartbeat during a blocking load.
- Progress is monotonic and best-effort. Inspect `progress_detail.basis` before presenting it as exact; `activity` describes the current model call and may itself have an estimated total. ETA ranges are advisory local-history estimates, never an SLA. SSE has no replay; reconnect and reconcile through the ETag-enabled status endpoint.

## 重要注意事项 / Important notes

- `compute_device=gpu` 不可用时返回 `503`，不会静默回退 CPU。ASR 使用 `asr.models[]`，TTS 使用 `tts.model_capabilities[]` 判断所选模型；`minimum_memory_mib` 与 `total_memory_mib` 都是总显存口径。
- `hotword_list_ids` 是逗号分隔的本地词表 ID；系统词表也必须显式选择。未知 ID、空系统词表或合并后超限返回 `422`。提交后的词表内容是不可变快照。
- 声纹人员名字必填，备注选填且最多 20 字。“加入热词库”默认开启，只控制名字是否进入系统人名词表，不影响声纹匹配或 TTS。新 ASR 匹配把名字和备注快照保存为“名字（备注）”。
- ASR 消费方应从 `GET /api/v1/capabilities` 返回的 `asr.languages` 读取可提交语种；不要把模型的全部识别语种误认为全部支持字词级时间戳。
- 删除操作要求 `purge=true` 且不可恢复；运行中任务必须先取消并等待终态。
- 声纹克隆只能使用 `state=ready` 且 `tts_eligible=true` 的样本。OpenAI 兼容 TTS 目前仅支持预置音色和兼容 voice profile。
- TTS 的 `language` 控制输出文本语种，`reference_language` 控制一次性克隆参考的转写/对齐语种；两者不是同一个参数。已知语种时显式填写可减少自动判断歧义。
- 0.6B TTS 不接受自然语言指令；1.7B 仅在 preset 和 voice_design 模式接受，后者必填。克隆模式、独立 speed/pitch 和底层采样参数均不支持，发送时返回 `422` 而非静默忽略。
- `429` 的稳定 `code` 为 `submission_concurrency_limited`、`queue_capacity_reached` 或 `insufficient_queue_storage`。按 `Retry-After` 等待并复用原 `Idempotency-Key`，不要为同一次逻辑提交生成新键。
- 输入和结果默认保留。受保护媒体支持 Bearer 或同源会话 Cookie；ASR 源文件支持 HTTP Range。
- 安装阶段可以下载固定版本依赖和模型；服务启动后的模型加载强制使用本地 revision，不存在云端回退。

</details>
"""


OPENAPI_TAGS = [
    {"name": "Service / 服务", "description": "公开健康探针、能力和受保护系统状态。 / Health, capabilities, and system status."},
    {"name": "Authentication / 鉴权", "description": "Bearer API key 与同源 HttpOnly 浏览器会话。 / Bearer API key and same-origin browser sessions."},
    {"name": "ASR / 语音识别", "description": "原生异步 ASR 任务。 / Native asynchronous transcription jobs."},
    {"name": "TTS / 语音合成", "description": "预置音色、声音克隆与 1.7B 音色设计。 / Preset voices, voice cloning, and 1.7B voice design."},
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
ASR_VALIDATION_RESPONSE = {
    422: {
        "description": "ASR 模型、热词或参数校验失败 / ASR model, hotword, or parameter validation failed",
        "content": {
            "application/problem+json": {
                "schema": {"$ref": "#/components/schemas/ProblemDetail"},
                "examples": {
                    "unknown_asr_model": {
                        "summary": "未知 ASR 模型 / Unknown ASR model",
                        "value": {
                            "type": "about:blank", "title": "Unknown ASR model", "status": 422,
                            "code": "unknown_asr_model", "detail": "Unknown ASR model",
                        },
                    },
                    "unknown_hotword_list": {
                        "summary": "未知热词词表 / Unknown hotword list",
                        "value": {
                            "type": "about:blank", "title": "Unknown hotword list IDs: hotwords_missing", "status": 422,
                            "code": "http_422", "detail": "Unknown hotword list IDs: hotwords_missing",
                        },
                    },
                    "hotword_selection_limit": {
                        "summary": "选择词表过多 / Too many selected hotword lists",
                        "value": {
                            "type": "about:blank", "title": "No more than 8 hotword lists may be selected", "status": 422,
                            "code": "http_422", "detail": "No more than 8 hotword lists may be selected",
                        },
                    },
                },
            },
            "application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}},
        },
    }
}
ASR_SERVICE_RESPONSE = {
    503: problem_examples_response(
        "ASR 模型或请求的 GPU 不可用 / ASR model or requested GPU unavailable",
        503,
        {
            "asr_model_unavailable": {
                "code": "asr_model_unavailable",
                "detail": "The selected ASR model is not installed at the pinned revision",
            },
            "gpu_unavailable": {
                "code": "gpu_unavailable",
                "detail": "GPU compute is unavailable; select CPU or check NVIDIA runtime",
            },
            "insufficient_gpu_memory": {
                "code": "insufficient_gpu_memory",
                "detail": "Qwen3-ASR-1.7B requires at least 7936 MiB total GPU memory; detected 4096 MiB",
            },
        },
    )
}
TTS_CONTROL_VALIDATION_RESPONSE = {
    422: {
        "description": "参数、业务或不支持的 TTS 控制参数 / Parameter, business, or unsupported TTS control validation failure",
        "content": {
            "application/problem+json": {
                "schema": {"$ref": "#/components/schemas/ProblemDetail"},
                "examples": {
                    name: {
                        "summary": summary,
                        "value": {"type": "about:blank", "title": detail, "status": 422, "code": code, "detail": detail},
                    }
                    for name, summary, code, detail in (
                        ("unknown_tts_model", "未知 TTS 模型 / Unknown TTS model", "unknown_tts_model", "Unknown TTS model"),
                        ("unsupported_voice_mode", "模型不支持该音色模式 / Voice mode unsupported by model", "unsupported_tts_voice_mode", "The selected TTS model does not support this voice mode"),
                        ("unsupported_instruction", "模型或模式不支持指令 / Instructions unsupported by model or mode", "unsupported_tts_control", "Natural-language instructions are not supported by this model and voice mode"),
                        ("instruction_required", "VoiceDesign 缺少指令 / VoiceDesign instruction required", "tts_instruction_required", "A voice-design instruction is required"),
                        ("invalid_instruction", "指令过长 / Instruction too long", "invalid_tts_instruction", "instruct must not exceed 1000 characters"),
                    )
                },
            },
            "application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}},
        },
    }
}
TTS_SERVICE_RESPONSE = {
    503: problem_examples_response(
        "TTS 模型或请求的 GPU 不可用 / TTS model or requested GPU unavailable",
        503,
        {
            "tts_model_unavailable": {
                "code": "tts_model_unavailable",
                "detail": "The selected TTS checkpoint is not installed at the pinned revision",
            },
            "gpu_unavailable": {
                "code": "gpu_unavailable",
                "detail": "GPU compute is unavailable; select CPU or check NVIDIA runtime",
            },
            "insufficient_gpu_memory": {
                "code": "insufficient_gpu_memory",
                "detail": "Qwen3-TTS-1.7B requires at least 7936 MiB total GPU memory; detected 4096 MiB",
            },
        },
    )
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
    "model_name": "实际加载的固定版本模型名称 / Pinned checkpoint name loaded for execution",
    "model_capabilities": "按公共模型 ID 列出的设备、音色模式与控制能力 / Device, voice-mode, and control capabilities by public model ID",
    "checkpoints": "该模型组按音色模式使用的固定版本检查点 / Pinned checkpoints used by this model group and voice mode",
    "instruct": "模型原生自然语言音色与表达指令 / Native natural-language voice and expression instruction",
    "precision": "模型执行精度 / Model execution precision",
    "quantized": "是否使用量化模型 / Whether a quantized model was used",
}

DATETIME_FIELDS = {
    "created_at", "updated_at", "started_at", "finished_at", "heartbeat_at",
    "processing_as_of", "earliest", "latest",
}

REQUEST_EXAMPLES: dict[tuple[str, str], dict[str, dict[str, Any]]] = {
    ("/api/v1/asr/jobs", "post"): {
        "meeting": {"summary": "默认模型会议转写 / Meeting transcription with the default model", "value": {"file": "meeting.wav", "model": "qwen3-asr-0.6b", "language": "Auto", "speaker_count": "auto", "diarize": True, "align": True, "compute_device": "gpu", "accelerate_single_task": True}},
        "large_model_hotwords": {"summary": "1.7B CPU 与场景热词 / 1.7B CPU with scenario hotwords", "value": {"file": "meeting.wav", "model": "qwen3-asr-1.7b", "language": "Chinese", "context": "项目会议", "hotword_list_ids": "hotwords_0123456789abcdef", "speaker_count": "auto", "compute_device": "cpu", "accelerate_single_task": True}},
    },
    ("/api/v1/asr/hotword-lists", "post"): {
        "project_terms": {"summary": "创建项目词表 / Create a project vocabulary", "value": {"name": "项目代号", "terms": ["Sandevistan-Audio", "Qwen3-ASR"]}},
    },
    ("/api/v1/asr/hotword-lists/{item_id}", "patch"): {
        "replace_terms": {"summary": "完整替换词条 / Replace all terms", "value": {"terms": ["Qwen3-ASR", "ForcedAligner"]}},
    },
    ("/api/v1/voiceprints/people", "post"): {
        "person_with_note": {"summary": "创建带备注的人员 / Create a person with a note", "value": {"name": "张三", "note": "研发一部", "include_in_hotword_library": True}},
    },
    ("/api/v1/voiceprints/people/{person_id}", "patch"): {
        "disable_name_hotword": {"summary": "更新备注并停止人名同步 / Update note and disable name sync", "value": {"note": "13800000000", "include_in_hotword_library": False}},
    },
    ("/v1/audio/transcriptions", "post"): {
        "hotwords": {"summary": "兼容转写与本地热词 / Compatible transcription with local hotwords", "value": {"file": "meeting.wav", "model": "qwen3-asr-1.7b", "prompt": "项目会议", "hotword_list_ids": "hotwords_0123456789abcdef", "language": "Auto", "response_format": "verbose_json", "compute_device": "cpu"}},
    },
    ("/api/v1/tts/jobs", "post"): {
        "preset": {"summary": "默认 0.6B 预置音色 / Default 0.6B preset voice", "value": {"text": "你好，这是本地语音。", "model": "qwen3-tts-0.6b", "language": "Chinese", "voice_mode": "preset", "speaker": "Vivian", "response_format": "wav", "compute_device": "gpu"}},
        "preset_1_7b": {"summary": "1.7B 预置音色与自然语言表达指令 / 1.7B preset with expression instruction", "value": {"text": "别担心，我们一步一步来。", "model": "qwen3-tts-1.7b", "language": "Chinese", "voice_mode": "preset", "speaker": "Vivian", "instruct": "温柔、安心地说，语速稍慢，音调自然。", "response_format": "wav", "compute_device": "cpu"}},
        "voice_design": {"summary": "1.7B 音色设计 / 1.7B voice design", "value": {"text": "欢迎收听今天的节目。", "model": "qwen3-tts-1.7b", "language": "Chinese", "voice_mode": "voice_design", "instruct": "成熟清晰的女性播音声线，语速中等，情绪沉稳而友好。", "response_format": "wav", "compute_device": "cpu"}},
        "inline_clone": {"summary": "分析任务克隆 / Clone from analyzed reference", "value": {"text": "这是克隆语音。", "model": "qwen3-tts-1.7b", "language": "Chinese", "voice_mode": "inline_clone", "reference_job_id": "0123456789abcdef0123456789abcdef", "reference_text": "与参考音频一致的文本", "reference_language": "Chinese"}},
        "voiceprint": {"summary": "声纹样本克隆 / Voiceprint sample clone", "value": {"text": "这是克隆语音。", "model": "qwen3-tts-1.7b", "language": "Chinese", "voice_mode": "voiceprint", "voiceprint_sample_id": "sample_0123456789abcdef"}},
    },
}

JOB_EXAMPLES = {
    "queued": {"summary": "排队 / Queued", "value": {"id": "0123456789abcdef0123456789abcdef", "kind": "tts", "state": "queued", "progress": 0, "stage": "queued", "display_name": "语音合成", "request": {"text": "你好", "compute_device": "gpu"}, "created_at": "2026-08-27T12:00:00+00:00", "compute_device": "gpu", "compute_device_name": "NVIDIA GPU", "status_url": "/api/v1/jobs/0123456789abcdef0123456789abcdef", "queue": {"scope": "tts", "position": 2, "depth": 4, "capacity": 5, "waiting_for": "worker"}, "poll_after_seconds": 2}},
    "running": {"summary": "运行与细粒度进度 / Running with fine progress", "value": {"id": "0123456789abcdef0123456789abcdef", "kind": "tts", "state": "running", "progress": 0.53, "stage": "synthesis", "display_name": "语音合成", "request": {"text": "你好", "compute_device": "gpu"}, "created_at": "2026-08-27T12:00:00+00:00", "started_at": "2026-08-27T12:00:02+00:00", "compute_device": "gpu", "compute_device_name": "NVIDIA GPU", "progress_detail": {"stage_code": "synthesis", "stage_progress": 0.4, "basis": "estimated", "current": 1, "total": 3, "unit": "text_chunk", "activity": {"sequence": 2, "current": 41, "total": 90, "unit": "codec_frame", "basis": "estimated", "updated_at": "2026-08-27T12:00:10+00:00"}}, "poll_after_seconds": 1}},
    "succeeded": {"summary": "成功 / Succeeded", "value": {"id": "0123456789abcdef0123456789abcdef", "kind": "tts", "state": "succeeded", "progress": 1, "stage": "completed", "display_name": "语音合成", "request": {"text": "你好", "compute_device": "gpu"}, "result": {"duration": 1.8, "format": "wav", "speaker": "Vivian", "artifacts": []}, "created_at": "2026-08-27T12:00:00+00:00", "finished_at": "2026-08-27T12:00:12+00:00", "compute_device": "gpu", "compute_device_name": "NVIDIA GPU", "result_url": "/api/v1/jobs/0123456789abcdef0123456789abcdef/result"}},
}

RESULT_EXAMPLES = {
    "asr": {"summary": "ASR 结果 / ASR result", "value": {"text": "欢迎使用本地转写。", "language": "Chinese", "duration": 3.2, "timestamp_precision": "word_or_character", "model": "qwen3-asr-1.7b", "model_name": "Qwen3-ASR-1.7B", "model_revision": "7278e1e70fe206f11671096ffdd38061171dd6e5", "hotword_context": {"enabled": True, "list_ids": ["hotwords_voiceprint_people"], "list_names": ["声纹库人名"], "term_count": 2}, "segments": [{"id": 0, "start": 0, "end": 3.2, "speaker": "Speaker_0", "speaker_label": "张三（研发一部）", "text": "欢迎使用本地转写。", "words": []}], "speakers": [{"id": "Speaker_0", "label": "张三（研发一部）", "label_source": "voiceprint", "voiceprint_match": {"person_id": "voice_0123456789abcdef", "name": "张三", "note": "研发一部", "score": 0.82}}], "artifacts": []}},
    "tts": {"summary": "TTS 结果 / TTS result", "value": {"duration": 1.8, "format": "wav", "sample_rate": 24000, "voice_mode": "preset", "speaker": "Vivian", "model": "qwen3-tts-1.7b", "model_name": "Qwen3-TTS-12Hz-1.7B-CustomVoice", "model_revision": "0c0e3051f131929182e2c023b9537f8b1c68adfe", "instruct": "温柔、安心地说，语速稍慢。", "compute_device": "gpu", "precision": "BF16", "quantized": False, "artifacts": []}},
}


def _device_examples(minimum_memory_mib: int) -> list[dict[str, Any]]:
    return [
        {"id": "cpu", "precision": "FP32", "available": True, "default": False, "quantized": False},
        {
            "id": "gpu", "precision": "BF16", "available": True, "default": True, "quantized": False,
            "minimum_memory_mib": minimum_memory_mib, "total_memory_mib": 8151,
        },
    ]


def _capabilities_example() -> dict[str, Any]:
    asr_entries = []
    for model in asr_models():
        asr_entries.append({
            "id": model["public_id"], "name": model["name"], "revision": model["revision"],
            "installed": True, "installation_state": "ready", "default": bool(model.get("default")),
            "compute_devices": _device_examples(int(model["minimum_gpu_memory_mib"])),
        })

    empty_controls = {
        "instruction_voice_modes": [], "instruction_required_voice_modes": [],
        "max_instruction_chars": 1000, "speaking_rate_parameter": False,
        "pitch_parameter": False, "sampling_parameters": False,
    }
    tts_entries = []
    physical_models = []
    for model in tts_models():
        is_large = model["public_id"] == "qwen3-tts-1.7b"
        controls = dict(empty_controls)
        if is_large:
            controls.update({
                "instruction_voice_modes": ["preset", "voice_design"],
                "instruction_required_voice_modes": ["voice_design"],
            })
        checkpoints = []
        for variant, checkpoint in model["checkpoints"].items():
            physical_models.append(checkpoint["name"])
            checkpoints.append({
                "variant": variant, "name": checkpoint["name"], "revision": checkpoint["revision"],
                "installed": True, "installation_state": "ready",
            })
        voice_modes = ["preset", "profile", "inline_clone", "voiceprint"]
        if is_large:
            voice_modes.append("voice_design")
        tts_entries.append({
            "id": model["public_id"], "name": model["name"], "default": bool(model["default"]),
            "installed": True, "installation_state": "ready", "voice_modes": voice_modes,
            "compute_devices": _device_examples(int(model["minimum_gpu_memory_mib"])),
            "controls": controls, "checkpoints": checkpoints,
        })

    default_asr = next(item for item in asr_entries if item["default"])
    default_tts = next(item for item in tts_entries if item["default"])
    return {
        "services": ["asr", "tts"], "offline": True,
        "asr": {
            "model": default_asr["name"], "default_model": default_asr["id"], "models": asr_entries,
            "diarization": "CAM++ single-active-speaker", "speaker_count": {"min": 1, "max": 15, "default": "auto"},
            "voiceprint_library": True, "languages": ["Auto", "Chinese", "English"], "default_language": "Auto",
            "timestamp_precisions": ["segment", "word_or_character"], "aligner_languages": ["Chinese", "English"],
            "exports": ["json", "srt", "vtt", "txt"], "compute_devices": default_asr["compute_devices"],
            "single_task_acceleration": {"supported": True, "default": True},
            "hotword_library": {
                "supported": True, "max_lists": 100, "max_terms_per_list": 200, "max_selected_lists": 8,
                "max_selected_terms": 500, "max_prompt_chars": 8000, "max_name_chars": 80, "max_term_chars": 64,
            },
        },
        "tts": {
            "models": physical_models, "default_model": default_tts["id"], "model_capabilities": tts_entries,
            "voice_modes": ["preset", "profile", "inline_clone", "voiceprint", "voice_design"],
            "preset_speakers": ["Vivian"], "preset_speaker_native_languages": {"Vivian": "Chinese"},
            "languages": ["Auto", "Chinese", "English"], "default_language": "Auto", "formats": ["wav", "flac", "mp3"],
            "compute_devices": default_tts["compute_devices"],
            "single_task_acceleration": {"supported": True, "default": True}, "controls": default_tts["controls"],
        },
        "limits": {
            "max_upload_bytes": 1073741824, "max_tts_chars": 10000, "max_clone_reference_seconds": 15,
            "max_queued_asr": 5, "max_queued_tts": 5, "max_concurrent_submissions": 2,
            "min_free_disk_bytes": 5368709120,
        },
        "events": {
            "sse": True, "global_url": "/api/v1/events", "per_job_url_template": "/api/v1/jobs/{job_id}/events",
            "heartbeat_seconds": 15, "history_replay": False, "global_mode": "summary_delta",
        },
    }


CAPABILITIES_EXAMPLE = _capabilities_example()


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

    if "CapabilitiesResponse" in schemas:
        schemas["CapabilitiesResponse"]["example"] = CAPABILITIES_EXAMPLE

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
