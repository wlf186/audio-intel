# Sandevistan-Audio

面向单机离线使用的 ASR、说话人分离、精确时间戳与 TTS 服务。前端和后端默认统一监听 `0.0.0.0:20810`，可通过 `AUDIO_INTEL_HOST` 和 `AUDIO_INTEL_PORT` 修改；模型、缓存、输入、结果、数据库、日志和 Python 运行时均固定在项目目录内。

## 快速复原

自动安装支持 **Ubuntu 22.04/24.04 x86_64** 和 **Windows 11 x64 原生环境**。完整 ASR + TTS 建议至少 16 GB 内存（32 GB 更舒适）和 30 GB 可用磁盘。NVIDIA GPU 可选；GPU 模式要求 `nvidia-smi` 正常且驱动兼容 PyTorch CUDA 13.0，CPU 模式不需要显卡。Windows 自动化会在 Windows CI 验证，但真实模型尚无 Windows GPU 实机验收记录。

Linux 需要 Git、curl、tar、Node.js 22.20+（推荐 Node.js 24 LTS）与 Corepack；Python 3.12 和固定版本的 uv 会安装到项目目录：

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
        │           → Qwen3-ASR-0.6B (CPU FP32 / GPU BF16 阶段子进程)
        │           → 阶段子进程退出释放模型内存
        │           → ForcedAligner-0.6B (与 ASR 使用同一设备)
        │           → JSON / SRT / VTT / TXT
        │
        └── SQLite WAL 异步任务队列 ── TTS worker 监督器
                └── 可重启任务执行器
                    Qwen3-TTS CustomVoice 或 Base（二选一）
                    └── 超长克隆参考 → 独立 aligner 环境（按需启动后退出）
                    CPU FP32 / GPU BF16 + SDPA + 可选单任务自动批处理 → WAV / FLAC / MP3
```

ASR 按 FSMN-VAD 的语音区间合并为约 20–60 秒的块。11 种对齐器支持语言返回字/词级时间戳，并利用这些时间戳把 ASR 大块重新切成连续的说话人轮次；即使选择“仅句级时间戳”，多人任务也会在内部对齐后隐藏字词明细。短录音会绕过 FunASR 少于 20 个声纹窗口时的单人回退，已知人数使用 KMeans，自动人数使用短音频余弦聚类。其他语言仍返回语音段时间戳。CAM++ 采用 single-active-speaker 模式，真正重叠语音仍只会归属给一位说话人。

ASR 默认使用 GPU，TTS 默认使用 CPU。两者均可按任务选择 `cpu` 或 `gpu`：CPU 使用 FP32，GPU 使用 BF16，不使用量化；GPU 任务通过项目内的全局锁串行执行，避免 4 GB 显存同时装载多个大模型。ASR 的 FSMN-VAD 和 CAM++ 始终在 CPU 上运行，设备选项会同时切换 ASR 主模型和 ForcedAligner；GPU 模式下 CAM++ 会在 CPU 内部批处理，并与 GPU 识别和对齐重叠执行。自动说话人数会对低支持度的疑似拆分簇执行一次保守的整段声纹复核，只有滑窗和整段两个视角均有充分区分度时才合并；手动指定人数不受该复核影响。TTS GPU 模式默认会在显存充足时对同一任务的相邻文本块执行 batch 2，声码器仍逐块解码。

ASR/TTS 提交页默认开启“单任务加速”；原生 API 与 OpenAI 兼容端点的布尔参数 `accelerate_single_task` 也默认为 `true`，可显式传入 `false` 关闭。开启后只按当前 CPU 核心/可用内存或 GPU 总显存自动扩大任务内部批次，不增加任务并发，也不改变模型、精度、分块、说话人算法或 TTS 声码器的逐块解码。GPU 分档为 `<8/8/12/16/24/32+ GB → 2/4/6/8/12/16`；CPU 在物理核心与可用内存同时达到 `8+12/16+24/32+48/48+64` 时使用 `2/4/6/8`。发生 OOM 会按 `16→12→8→6→4→2→1` 在当前任务内重试。任务结果的 `acceleration` 会记录目标/实际批次和回退。GPU 不可用时仍明确返回 `503`，不会静默回退到 CPU。可通过 `GET /api/v1/capabilities` 查询当前设备能力、说话人数上限与开关默认值。

“声纹库”可为同一人员保存多个独立样本。样本既可从同一 ASR 说话人的选中段落加入，也可上传音频，或直接用浏览器麦克风录制最长 30 秒的单人语音；录音停止后可先试听、重录，再确认创建一个可见的 ASR 入库任务来自动生成转写和字词时间戳。未确认的录音只暂存在当前页面内存中，不会写入浏览器长期存储；麦克风功能要求使用 `localhost`、`127.0.0.1` 或 HTTPS，权限不可用时仍可上传文件。ASR 的 `use_voiceprint_library` 默认开启，只在普通说话人分离结束后用 CAM++ 匹配并命名，不会改变聚类、说话人 ID 或分段；任务中的名称是历史快照，之后修改库中人员名称不会回写历史任务。

声音克隆使用 Base 模型的 ICL 路径，要求干净参考音频和逐字准确的参考文本；代码固定 `x_vector_only_mode=False`。除直接上传外，可明确选择声纹库中的人员和样本。超过 15 秒的库样本会先依据已有字词时间戳在完整词边界截断；旧样本没有时间戳时按需运行 ForcedAligner。预置音色使用 CustomVoice 模型的 9 个官方音色。两个 TTS 模型不会同时驻留内存。

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
# ASR
curl -F file=@meeting.wav \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -F language=Auto -F speaker_count=auto \
  -F diarize=true -F align=true -F use_voiceprint_library=true \
  -F compute_device=gpu -F accelerate_single_task=true \
  http://127.0.0.1:20810/api/v1/asr/jobs

# TTS 预置音色
curl -F text='你好，这是本地语音。' \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -F language=Chinese -F voice_mode=preset -F speaker=Vivian \
  -F response_format=wav -F compute_device=cpu -F accelerate_single_task=true \
  http://127.0.0.1:20810/api/v1/tts/jobs

# 查询任务及结果
curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" http://127.0.0.1:20810/api/v1/jobs/JOB_ID
curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" http://127.0.0.1:20810/api/v1/jobs/JOB_ID/result

# 批量永久删除任务（排队任务会先原子取消，运行中任务会逐项拒绝）
curl -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -d '{"job_ids":["JOB_ID_1","JOB_ID_2"],"purge":true}' \
  http://127.0.0.1:20810/api/v1/jobs/batch-delete

# ASR 原始音源（支持 Range，可直接用于播放器）
curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" http://127.0.0.1:20810/api/v1/jobs/JOB_ID/source -o source.wav
```

以下仅列出主要端点；完整且实时的接口定义以 `/docs` 和 `/openapi.json` 为准：

- `POST /api/v1/asr/jobs`、`POST /api/v1/tts/jobs`
- `GET /api/v1/jobs`、`GET /api/v1/jobs/{id}`
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
- `GET /api/v1/events`（SSE）
- `GET /v1/models` 与 OpenAI 兼容音频端点

任务响应中的 `processing_seconds` 是累计实际处理耗时，不包含排队等待，并会跨失败重试累加；运行中任务配合 `processing_as_of` 可实时显示。`compute_device` 返回 `cpu` 或 `gpu`，`compute_device_name` 返回任务提交/执行时持久化的具体设备名；GPU 名称动态取自实际 `cuda:0`，不会因以后更换硬件而改写历史记录。ASR/TTS worker 使用常驻监督器管理可复用的任务执行进程；取消先等待短暂的协作退出，随后在必要时终止当前任务的完整进程树，确认 GPU 与文件锁释放后才进入“已取消”。默认协作等待 1 秒，可通过 `AUDIO_INTEL_CANCEL_GRACE_SECONDS` 调整。永久删除会清理任务输入、输出、错误文件、临时目录和 SQLite 记录，并在批次结束后执行安全擦除、WAL 截断与数据库压缩。

## OpenAI 兼容消费

```bash
curl -F file=@meeting.wav -F model=qwen3-asr-0.6b -F compute_device=gpu \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -F response_format=verbose_json \
  http://127.0.0.1:20810/v1/audio/transcriptions

curl -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" \
  -d '{"model":"qwen3-tts-0.6b","input":"你好","voice":"Vivian","response_format":"wav","compute_device":"cpu"}' \
  http://127.0.0.1:20810/v1/audio/speech -o speech.wav
```

兼容端点是同步等待接口；长任务建议使用原生异步 API。

## 配置与安全

默认不开启鉴权，适合可信本机/局域网。对不可信网络暴露前设置 Bearer Key：

```bash
AUDIO_INTEL_API_KEY='replace-with-a-long-random-value' ./service.sh start all
```

浏览器首次访问会提示输入 Key，并将其换成只存在内存中的 HttpOnly 同源会话 Cookie；原始 Key 不进入 URL 或浏览器存储，服务重启后需重新登录。CLI 和外部客户端继续使用 `Authorization: Bearer ...`。`/api/v1/health` 始终公开但只暴露状态、版本和离线标志；硬件、进程、模型路径等信息位于受保护的 `/api/v1/system`。

可复制 `.env.example` 为 `.env`，编辑后用 `set -a; source .env; set +a` 加载；`service.sh` 不会隐式读取环境文件。常用变量包括 `AUDIO_INTEL_HOST`、`AUDIO_INTEL_PORT`、`AUDIO_INTEL_API_KEY`、`AUDIO_INTEL_CANCEL_GRACE_SECONDS`、目录覆盖、上传限制和文本限制。不要提交 `.env`，也不要在未配置 TLS 和访问控制时直接暴露到公网。

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
.runtime/api/bin/python scripts/benchmark_single_task_acceleration.py asr --device gpu --audio meeting.wav
.runtime/api/bin/python scripts/benchmark_single_task_acceleration.py tts --device cpu
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
