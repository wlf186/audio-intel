# Sandevistan-Audio

面向单机离线使用的 ASR、说话人分离、精确时间戳与 TTS 服务。前端和后端默认统一监听 `0.0.0.0:20810`，可通过 `AUDIO_INTEL_HOST` 和 `AUDIO_INTEL_PORT` 修改；模型、缓存、输入、结果、数据库、日志和 Python 运行时均固定在项目目录内。

## 快速复原

自动安装支持 **Ubuntu 22.04/24.04 x86_64** 和 **Windows 11 x64 原生环境**。完整 ASR + TTS 建议至少 16 GB 内存（32 GB 更舒适）；0.6B/1.7B 全部模型、隔离运行时和安装缓存合计约 43 GiB，因此至少预留 55 GiB、建议 70 GiB 可用磁盘，为任务数据、升级和 5 GiB 准入保护留出空间。NVIDIA GPU 可选；GPU 模式要求 `nvidia-smi` 正常且驱动兼容 PyTorch CUDA 13.0，CPU 模式不需要显卡。Windows 自动化会在 Windows CI 验证，但真实模型尚无 Windows GPU 实机验收记录。

Linux 需要 Git、curl、tar、Node.js 22.20+（推荐 Node.js 24 LTS）与 Corepack；复制本地 `/docs` 的 Bash API 示例时还需要 `jq`，但服务运行本身不依赖它。Python 3.12 和固定版本的 uv 会安装到项目目录：

```bash
git clone https://github.com/wlf186/audio-intel.git
cd audio-intel

# 检查机器与本地资源
./service.sh doctor

# 首次安装项目内运行时、前端和全部模型（下载量较大且支持断点续传）
./service.sh setup all

# 启动 API、ASR、TTS；完成安装后模型推理强制离线
./service.sh start all

curl http://127.0.0.1:20810/api/v1/health
```

Windows 11 原生环境建议使用本地 NTFS 短路径和 Node.js 24 LTS：

```powershell
git clone https://github.com/wlf186/audio-intel.git C:\ai\audio-intel
Set-Location C:\ai\audio-intel
.\service.cmd doctor
.\service.cmd setup all
.\service.cmd start all
Invoke-RestMethod http://127.0.0.1:20810/api/v1/health
```

浏览器访问 `http://127.0.0.1:20810`，完整中英双语 API 消费指南与可交互契约位于 `http://127.0.0.1:20810/docs`，机器可读定义位于 `/openapi.json`。Swagger 代码、样式、图标和校验器均随服务本地托管，运行期不访问 CDN。只需要部分能力时可减少模型下载：

```bash
./service.sh setup asr   # 或 tts / api

./service.sh start asr
./service.sh start tts

./service.sh status
./service.sh logs all
./service.sh stop all
```

需要代理时，在安装前导出标准代理变量；curl、uv、Hugging Face 与 ModelScope 会继承它们：

```bash
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=$HTTP_PROXY
./service.sh setup all
```

Linux 详细步骤见 [安装与复原](docs/INSTALL.md)，Windows 原生部署见 [Windows 11 指南](docs/WINDOWS.md)，通用问题见 [故障排查](docs/TROUBLESHOOTING.md)。局域网可用本机 IP 访问；普通 HTTP 下浏览器录音权限受浏览器安全策略限制，文件上传不受影响。

界面右上角的 `OFFLINE_MODE` 表示模型运行时是否启用离线加载，不表示服务只监听 `localhost`；页脚的 `DATA_LOCAL READY` 表示服务同时报告了离线模式和本地数据目录，`NET_LISTEN` 则动态显示 `/api/v1/system` 返回的实际监听地址与端口。健康检查尚未完成或服务失联时，这些位置会显示 `CHECKING`、`UNKNOWN` 或 `DISCONNECTED`，不会继续展示过期的监听地址。

ASR 与 TTS 参数分别以轻量、带版本的 `localStorage` 配置保存在当前浏览器中，两个页面的“恢复默认配置”只重置本页参数，不会清除已选文件或正在编辑的文本。TTS 合成文本和参考文本只保留在当前 `sessionStorage` 会话，音频文件不会写入浏览器存储；清除站点数据或对应 localStorage 后会自动恢复默认配置，仅清理 HTTP 缓存通常不会删除这些偏好。

## 运行架构

```text
20810 FastAPI + 本地 Web UI
        │
        ├── SQLite WAL 异步任务队列 ── ASR worker 监督器
        │       └── 可重启任务执行器
        │           FSMN-VAD (CPU) → CAM++ (CPU)
        │           → Qwen3-ASR-0.6B / 1.7B (CPU FP32 / GPU BF16 阶段子进程)
        │           → 阶段子进程退出释放模型内存
        │           → ForcedAligner-0.6B (与 ASR 使用同一设备)
        │           → JSON / SRT / VTT / TXT
        │
        └── SQLite WAL 异步任务队列 ── TTS worker 监督器
                └── 可重启任务执行器
                    Qwen3-TTS 0.6B / 1.7B CustomVoice、Base 或 VoiceDesign（按任务加载一个）
                    └── 超长克隆参考 → 独立 aligner 环境（按需启动后退出）
                    CPU FP32 / GPU BF16 + SDPA + 默认开启、可关闭的单任务自动批处理 → WAV / FLAC / MP3
```

ASR 公开支持 `Auto`，以及 Chinese、English、Cantonese、French、German、Italian、Japanese、Korean、Portuguese、Russian、Spanish 这 11 种可进行 ForcedAligner 字/词级对齐的语言；显式传入其他语言会返回 `422`。Auto 仍可能检测出模型支持的其他语种，此时任务正常完成并返回句段级时间戳，页面会明确提示本次未执行字词对齐。ASR 按 FSMN-VAD 的语音区间合并为约 20–60 秒的块，并利用可用的字词时间戳把大块重新切成连续的说话人轮次；即使选择“仅句级时间戳”，多人任务也会在内部对齐后隐藏字词明细。短录音会绕过 FunASR 少于 20 个声纹窗口时的单人回退，已知人数使用 KMeans，自动人数使用短音频余弦聚类。CAM++ 采用 single-active-speaker 模式，真正重叠语音仍只会归属给一位说话人。

ASR 默认使用 `qwen3-asr-0.6b`，也可在普通转写、TTS 克隆参考分析、声纹样本上传和 OpenAI 兼容转写中选择 `qwen3-asr-1.7b`。`setup asr/all` 会下载两个固定 revision；运行期只从本地模型目录离线加载。1.7B 的 CPU 路径始终可选。GPU 能力按 `nvidia-smi` 报告的总显存而不是当前空闲显存判断，并为驱动或硬件保留区预留 256 MiB 容差：1.7B 的 8 GiB 档门槛为 7936 MiB，0.6B 的 4 GiB 档门槛为 3840 MiB，因此报告 8151 MiB 的 8 GiB 显卡可选择 1.7B。该门槛只决定准入，其他 GPU 进程仍可能导致实际推理 OOM。显式 API GPU 请求不满足条件时返回 `503`，页面则显示原因并按 CPU 创建本次任务。1.7B 复用单任务自动批处理和 OOM 降档机制，并采用更保守的起始批次；当前 4 GiB RTX A1000 只能实机覆盖 0.6B GPU 路径，1.7B GPU 仍需在 8 GiB 设备上验收。

“热词库”按场景保存多个本地词表，0.6B 与 1.7B 的接口和限制相同。普通 ASR 或 OpenAI 兼容转写可通过 `hotword_list_ids` 选择最多 8 个词表；留空表示不启用已保存词表。提交时会规范化、去重并生成 Qwen ASR 的 `Vocabulary: ...` 上下文，同时把词表内容快照写入任务，因此后续编辑或删除词表不会改变历史任务或幂等重放。一次性 `context` / `prompt` 会放在自动生成的 Vocabulary 段之前；克隆参考和声纹入库不使用热词。热词属于识别提示而不是强制词典，建议只放容易误识别的专有名词并保持词表聚焦，不能保证每个词都会命中。

ASR 与 TTS 默认都使用 GPU。两者均可按任务选择 `cpu` 或 `gpu`：CPU 使用 FP32，GPU 使用 BF16，不使用量化；GPU 任务通过项目内的全局锁串行执行，避免同时装载多个大模型。TTS 与 ASR 一样按 `nvidia-smi` 报告的总显存执行 3840/7936 MiB 准入；因此 8151 MiB 的 8 GiB 卡可选择 1.7B。API 显式请求不合格 GPU 时返回 `503`，页面会说明原因并将本次任务切到 CPU。ASR 的 FSMN-VAD 和 CAM++ 始终在 CPU 上运行，设备选项会同时切换 ASR 主模型和 ForcedAligner；GPU 模式下 CAM++ 会在 CPU 内部批处理，并与 GPU 识别和对齐重叠执行。自动说话人数会对低支持度的疑似拆分簇执行一次保守的整段声纹复核，只有滑窗和整段两个视角均有充分区分度时才合并；手动指定人数不受该复核影响。

ASR/TTS 提交页默认开启“单任务加速”；原生 API 与 OpenAI 兼容端点的布尔参数 `accelerate_single_task` 也默认为 `true`，可显式传入 `false` 关闭。开启后只按当前 CPU 核心/可用内存或 GPU 总显存自动扩大任务内部批次，不增加任务并发，也不改变模型、精度、分块、说话人算法或 TTS 声码器的逐块解码。GPU 分档为 `<8/8/12/16/24/32+ GB → 2/4/6/8/12/16`；CPU 在物理核心与可用内存同时达到 `8+12/16+24/32+48/48+64` 时使用 `2/4/6/8`。ASR 与 TTS 的 1.7B 模型都会在硬件分档基础上降低两个批次档位，采用更保守的起始值；关闭加速时固定为 batch 1。发生 OOM 会按 `16→12→8→6→4→2→1` 在当前任务内重试。任务结果的 `acceleration` 会记录模型修正后的任务/阶段目标、实际批次、硬件诊断、降档步数和 OOM 回退。GPU 不可用时仍明确返回 `503`，不会静默回退到 CPU。可通过 `GET /api/v1/capabilities` 查询当前设备能力、说话人数上限与开关默认值。

“声纹库”可为同一人员保存多个独立样本。样本既可从同一 ASR 说话人的选中段落加入，也可上传音频，或直接用浏览器麦克风录制最长 30 秒的单人语音；录音停止后可先试听、重录，再确认创建一个可见的 ASR 入库任务来自动生成转写和字词时间戳。未确认的录音只暂存在当前页面内存中，不会写入浏览器长期存储；麦克风功能要求使用 `localhost`、`127.0.0.1` 或 HTTPS，权限不可用时仍可上传文件。ASR 的 `use_voiceprint_library` 默认开启，只在普通说话人分离结束后用 CAM++ 匹配并命名，不会改变聚类、说话人 ID 或分段；任务中的名称是历史快照，之后修改库中人员名称不会回写历史任务。

TTS 默认使用 `qwen3-tts-0.6b`，也可切换 `qwen3-tts-1.7b`。预置音色按所选大小加载 CustomVoice；声音克隆按所选大小加载 Base 的 ICL 路径，要求干净参考音频和逐字准确的参考文本，并固定 `x_vector_only_mode=False`；1.7B 还可加载 VoiceDesign，根据必填自然语言描述直接设计新音色。页面上传或录制一次性参考音频后，会先创建一个可查询、可在任务记录中查看的 ASR 分析任务，自动填写参考文本和语种，用户核对后再提交 TTS；API 消费方可用 `/api/v1/tts/clone-references` 和 `reference_job_id` 完成同一流程，旧的 `reference_audio` + `reference_text` 提交方式仍兼容。超过 15 秒的库样本会在完整词边界截断。每个 TTS 执行器同一时间只驻留当前任务所需的一个 checkpoint。

TTS 输出 `language` 默认是 `Auto`，支持中、英、日、韩、德、法、俄、葡、西、意语。已知文本语种时建议显式选择；预置音色应优先选择 `/api/v1/capabilities` 中 `preset_speaker_native_languages` 指示的母语。一次性克隆的 `reference_language` 独立控制参考音频的识别和长音频对齐，不应与输出语种混淆。

## 本地目录

```text
models/       模型权重
data/         SQLite、原始输入、持久化结果、声音档案
tmp/          任务临时文件（成功或失败后自动清理）
cache/        uv、pip、Hugging Face、ModelScope、Torch 缓存
logs/         API / ASR / TTS 日志
run/          监督器 PID 与执行器身份元数据
.runtime/     uv 与隔离的 api/asr/tts/aligner Python 环境
```

输入与结果默认一直保留，只有调用带 `purge=true` 的删除 API 或在前端确认永久删除后才会清理。

## 原生异步 API

```bash
BASE_URL=http://127.0.0.1:20810
ASR_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
# 创建场景词表并取得真实 ID；已有词表也可直接复用其 ID。
HOTWORD_LIST_ID=$(curl --fail-with-body -sS \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"项目术语-$ASR_KEY\",\"terms\":[\"Qwen3-ASR\",\"Sandevistan-Audio\"]}" \
  "$BASE_URL/api/v1/asr/hotword-lists" | \
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
# ASR
curl -F file=@meeting.wav \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H "Idempotency-Key: $ASR_KEY" \
  -F language=Auto -F speaker_count=auto \
  -F model=qwen3-asr-0.6b -F hotword_list_ids="$HOTWORD_LIST_ID" \
  -F diarize=true -F align=true -F use_voiceprint_library=true \
  -F compute_device=gpu -F accelerate_single_task=true \
  "$BASE_URL/api/v1/asr/jobs"

# 提交响应返回前词表内容已经写入任务快照，演示词表可以立即删除。
curl --fail-with-body -sS -X DELETE \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  "$BASE_URL/api/v1/asr/hotword-lists/$HOTWORD_LIST_ID"

# TTS 预置音色
TTS_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
curl -F text='你好，这是本地语音。' \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -H "Idempotency-Key: $TTS_KEY" \
  -F language=Chinese -F voice_mode=preset -F speaker=Vivian \
  -F response_format=wav -F compute_device=gpu -F accelerate_single_task=true \
  "$BASE_URL/api/v1/tts/jobs"

# 按 status_url / poll_after_seconds 轮询到 succeeded 后才能读取 result_url。
# 完整的错误处理、429 重试、克隆、Python、Node 与 SSE 示例见本地 /docs。
curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" "$BASE_URL/api/v1/jobs/JOB_ID"
curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" "$BASE_URL/api/v1/jobs/JOB_ID/result"

# 批量永久删除任务（排队任务会先原子取消，运行中任务会逐项拒绝）
curl -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -d '{"job_ids":["JOB_ID_1","JOB_ID_2"],"purge":true}' \
  http://127.0.0.1:20810/api/v1/jobs/batch-delete

# ASR 原始音源（支持 Range，可直接用于播放器）
curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" http://127.0.0.1:20810/api/v1/jobs/JOB_ID/source -o source.wav
```

以下仅列出主要端点；完整且实时的接口定义以 `/docs` 和 `/openapi.json` 为准：

- `POST /api/v1/asr/jobs`、`POST /api/v1/tts/clone-references`、`POST /api/v1/tts/jobs`
- `GET|POST /api/v1/asr/hotword-lists`、`PATCH|DELETE /api/v1/asr/hotword-lists/{id}`
- `GET /api/v1/jobs`、`GET /api/v1/jobs/{id}`
- `GET /api/v1/queue`、`GET /api/v1/jobs/{id}/events`（单任务 SSE）
- `POST /api/v1/jobs/{id}/cancel`、`POST /api/v1/jobs/{id}/retry`
- `DELETE /api/v1/jobs/{id}?purge=true`
- `POST /api/v1/jobs/batch-delete`（最多 100 个 ID，返回逐项结果与实际释放空间）
- `GET /api/v1/jobs/{id}/artifacts/{name}`
- `GET /api/v1/jobs/{id}/source`（ASR 原始音源；`?download=true` 强制下载）
- `PATCH /api/v1/jobs/{id}/speakers/{speaker_id}`
- `GET|POST /api/v1/voiceprints/people`、`PATCH|DELETE /api/v1/voiceprints/people/{id}`
- `POST /api/v1/voiceprints/people/{id}/samples/from-asr`（同一说话人的段落分别入库）
- `POST /api/v1/voiceprints/people/{id}/samples/upload`、`DELETE /api/v1/voiceprints/people/{id}/samples/{sample_id}`
- `GET /api/v1/voiceprints/samples/{sample_id}/audio`
- `POST /api/v1/tts/voices`、`GET /api/v1/tts/voices`、`DELETE /api/v1/tts/voices/{id}`
- `GET /api/v1/health`（公开最小探针）、`GET /api/v1/system`（详细且受保护）
- `GET|POST|DELETE /api/v1/auth/session`
- `GET /api/v1/events`（全局 SSE）
- `GET /v1/models` 与 OpenAI 兼容音频端点

上述四个原生异步提交端点（ASR、TTS、克隆参考分析、声纹样本上传）都必须发送 8–128 字符的 `Idempotency-Key`。每次逻辑提交生成一个新键；超时、断线或 `429` 后必须用原键重试。首次接受返回 `202`，相同请求重放返回原任务和 `200`，同键更改请求返回 `409`。`429` 会同时返回稳定错误码、`Retry-After` 和当前容量信息。

`GET /api/v1/jobs` 支持 `kind`、`state`、`q`、`limit`、`offset` 服务端分页，始终按 `created_at DESC, id DESC` 稳定排序；`count` 是本页数量，`total` 是筛选后的任务总数，`has_more` 表示是否还有下一页。

TTS 高级控制以 `/api/v1/capabilities.tts.model_capabilities[]` 为准：0.6B 不接受自然语言指令；1.7B CustomVoice 的预置音色可选 `instruct`，VoiceDesign 则必须用 `instruct` 描述声线、语速、音调、韵律和情绪；Base 克隆模式不接受指令。官方公共推理接口没有独立数值语速/音高参数，本项目也不开放 `temperature`、`top_k`、`top_p`、`repetition_penalty` 等固定采样项。OpenAI 兼容 `instructions` 仅覆盖 1.7B 预置音色；VoiceDesign 请使用原生异步接口。不支持的组合返回 `422`，不会静默忽略。

排队任务响应中的 `queue.position` 是同类 FIFO 队列中从 1 开始的位置，任务开始运行后为 `null`。`progress` 是单调的最佳整体进度；模型实际提供细粒度活动时，ASR 推理与 TTS 解码按约 0.5 秒的最小写入间隔持久化。模型加载只报告 `model_load` 的开始和完成边界，阻塞加载期间可能长时间没有新活动，服务不会伪造百分比或心跳。消费方必须查看 `progress_detail.basis`：`estimated` 表示百分比包含最佳估算，不能当作精确完成量；`current/total/unit` 是已确认的阶段单元，`activity` 则描述当前推理调用的 `model_load`、`codec_frame`、`output_token` 或 `model_layer` 活动，其中 `activity.basis` 单独说明活动总量是否估算。`estimate` 在至少积累 5 个相同模型、设备和相近任务特征的本机历史样本后返回耗时区间和置信度；0.6B 与 1.7B 分别热身，它只是建议，不是 SLA。SSE 断线时应重新连接并用任务状态接口校准，也可按 `poll_after_seconds` 轮询并使用 ETag/`If-None-Match`。`processing_seconds` 是累计实际处理耗时，不包含排队等待，并会跨失败重试累加；运行中任务配合 `processing_as_of` 可实时显示。`compute_device` 返回 `cpu` 或 `gpu`，`compute_device_name` 返回任务提交/执行时持久化的具体设备名；GPU 名称动态取自实际 `cuda:0`，不会因以后更换硬件而改写历史记录。ASR/TTS worker 使用常驻监督器管理可复用的任务执行进程；取消先等待短暂的协作退出，随后在必要时终止当前任务的完整进程树，确认 GPU 与文件锁释放后才进入“已取消”。默认协作等待 1 秒，可通过 `AUDIO_INTEL_CANCEL_GRACE_SECONDS` 调整。永久删除会清理任务输入、输出、错误文件、临时目录和 SQLite 记录，并在批次结束后执行安全擦除、WAL 截断与数据库压缩。

## OpenAI 兼容消费

```bash
curl -F file=@meeting.wav -F model=qwen3-asr-0.6b -F compute_device=gpu \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -F prompt='项目会议' -F response_format=verbose_json \
  http://127.0.0.1:20810/v1/audio/transcriptions

curl -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -d '{"model":"qwen3-tts-0.6b","input":"你好","voice":"Vivian","language":"Chinese","response_format":"wav","compute_device":"gpu"}' \
  http://127.0.0.1:20810/v1/audio/speech -o speech.wav
```

兼容端点是同步等待接口；长任务建议使用原生异步 API。

## 配置与安全

默认不开启鉴权，适合可信本机/局域网。对不可信网络暴露前设置 Bearer Key：

```bash
AUDIO_INTEL_API_KEY='replace-with-a-long-random-value' ./service.sh start all
```

浏览器首次访问会提示输入 Key，并将其换成只存在内存中的 HttpOnly 同源会话 Cookie；原始 Key 不进入 URL 或浏览器存储，服务重启后需重新登录。CLI 和外部客户端继续使用 `Authorization: Bearer ...`。`/api/v1/health` 始终公开但只暴露状态、版本和离线标志；硬件、进程、模型路径等信息位于受保护的 `/api/v1/system`。

可复制 `.env.example` 为 `.env`，编辑后用 `set -a; source .env; set +a` 加载；`service.sh` 不会隐式读取环境文件。常用变量包括 `AUDIO_INTEL_HOST`、`AUDIO_INTEL_PORT`、`AUDIO_INTEL_API_KEY`、`AUDIO_INTEL_CANCEL_GRACE_SECONDS`，以及 `AUDIO_INTEL_MAX_QUEUED_ASR`、`AUDIO_INTEL_MAX_QUEUED_TTS`、`AUDIO_INTEL_MAX_CONCURRENT_SUBMISSIONS`、`AUDIO_INTEL_MIN_FREE_DISK_BYTES` 四项准入保护配置。目录覆盖、上传限制、文本限制和全部默认值以 `.env.example` 为准。不要提交 `.env`，也不要在未配置 TLS 和访问控制时直接暴露到公网。

## 验证

```bash
.runtime/api/bin/python -m pytest -q
.runtime/api/bin/python scripts/lock_dependencies.py --check
corepack pnpm@10.15.1 --dir frontend typecheck
corepack pnpm@10.15.1 --dir frontend test:e2e
AUDIO_INTEL_MOCK_MODE=1 ./service.sh start all  # 仅供快速管线验收，不是生产默认值
./scripts/smoke_test.sh
```

服务启动后，可用相同输入交替执行关闭/开启任务并输出中位耗时、加速倍数及每次实际批次：

```bash
.runtime/api/bin/python scripts/benchmark_single_task_acceleration.py asr --model qwen3-asr-1.7b --device cpu --audio meeting.wav
.runtime/api/bin/python scripts/benchmark_single_task_acceleration.py tts --model qwen3-tts-1.7b --device cpu
```

Windows mock 全链路验证：

```powershell
$env:AUDIO_INTEL_MOCK_MODE = '1'
.\service.cmd start all
.\.runtime\api\Scripts\python.exe scripts\smoke_test.py
.\service.cmd stop all
```

生产默认值始终是真实模型模式；mock 模式只用于验证任务队列、API、导出和 UI。依赖版本、锁文件和当前无法升级的模型运行时公告见 [依赖维护说明](docs/DEPENDENCIES.md)，升级步骤见 [升级指南](docs/UPGRADE.md)。

真实模型的可恢复两小时设备矩阵测试（先分别使用 CPU/GPU 合成，再使用同一份 CPU 合成音频分别执行 CPU/GPU ASR）：

```bash
.runtime/asr/bin/python -u scripts/stress_device_matrix.py
```

本轮验收目标为约 2 小时（`7200±130` 秒），过程和最终报告保存在 `data/stress/device-matrix-2h/`；中断后执行同一命令会从已提交的任务继续，不会重复已完成阶段。

## License

本项目自有代码以 [Apache License 2.0](LICENSE) 发布。模型权重不会包含在 Git 仓库中，下载后仍适用各上游模型许可证；详见 [第三方组件与模型声明](THIRD_PARTY_NOTICES.md)。本项目为非官方的赛博朋克风格界面，与相关游戏、商标或权利人不存在隶属或背书关系。
