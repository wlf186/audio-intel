# Sandevistan-Audio

面向单机离线使用的 ASR、说话人分离、精确时间戳与 TTS 服务。前端和后端统一监听 `0.0.0.0:20810`，模型、缓存、输入、结果、数据库、日志和 Python 运行时均固定在项目目录内。

## 快速复原

当前自动安装流程验证于 **Ubuntu 22.04/24.04 x86_64**。需要 Git、curl、tar、Node.js 20.19+ 与 Corepack；Python 3.12 和 uv 会安装到项目目录。完整 ASR + TTS 建议至少 16 GB 内存（32 GB 更舒适）和 30 GB 可用磁盘。NVIDIA GPU 可选；GPU 模式要求 `nvidia-smi` 正常且驱动兼容 PyTorch CUDA 13.0，CPU 模式不需要显卡。

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

浏览器访问 `http://127.0.0.1:20810`，API 文档位于 `http://127.0.0.1:20810/docs`。只需要部分能力时可减少模型下载：

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

详细步骤见 [安装与复原](docs/INSTALL.md)，常见下载、GPU、端口与运行问题见 [故障排查](docs/TROUBLESHOOTING.md)。局域网可用本机 IP 访问；普通 HTTP 下浏览器录音权限受浏览器安全策略限制，文件上传不受影响。

## 运行架构

```text
20810 FastAPI + 本地 Web UI
        │
        ├── SQLite WAL 异步任务队列 ── ASR worker
        │       FSMN-VAD (CPU) → CAM++ (CPU)
        │       → Qwen3-ASR-0.6B (CPU FP32 / GPU BF16 子进程)
        │       → 子进程退出释放显存
        │       → ForcedAligner-0.6B (与 ASR 使用同一设备)
        │       → JSON / SRT / VTT / TXT
        │
        └── SQLite WAL 异步任务队列 ── TTS worker
                Qwen3-TTS CustomVoice 或 Base（二选一）
                CPU FP32 / GPU BF16 + SDPA + batch 1 → WAV / FLAC / MP3
```

ASR 按 FSMN-VAD 的语音区间合并为约 20–60 秒的块。11 种对齐器支持语言返回字/词级时间戳；其他语言仍返回语音段时间戳。CAM++ 当前采用 single-active-speaker 模式，重叠语音会按最大时间重叠归属给一位说话人。

ASR 默认使用 GPU，TTS 默认使用 CPU。两者均可按任务选择 `cpu` 或 `gpu`：CPU 使用 FP32，GPU 使用 BF16，不使用量化；GPU 任务通过项目内的全局锁串行执行，避免 4 GB 显存同时装载多个大模型。ASR 的 FSMN-VAD 和 CAM++ 始终在 CPU 上运行，设备选项会同时切换 ASR 主模型和 ForcedAligner。GPU 不可用时会明确返回 `503`，不会静默回退到 CPU。可通过 `GET /api/v1/capabilities` 查询当前设备能力与默认值。

声音克隆使用 Base 模型的 ICL 路径，要求 3–15 秒左右的干净参考音频和逐字准确的参考文本；代码固定 `x_vector_only_mode=False`。预置音色使用 CustomVoice 模型的 9 个官方音色。两个模型不会同时驻留内存。

## 本地目录

```text
models/       模型权重
data/         SQLite、原始输入、持久化结果、声音档案
tmp/          任务临时文件（成功或失败后自动清理）
cache/        uv、pip、Hugging Face、ModelScope、Torch 缓存
logs/         API / ASR / TTS 日志
run/          PID 文件
.runtime/     uv 与隔离的 api/asr/tts Python 环境
```

输入与结果默认一直保留，只有调用带 `purge=true` 的删除 API 或在前端确认永久删除后才会清理。

## 原生异步 API

```bash
# ASR
curl -F file=@meeting.wav \
  -F language=Auto -F speaker_count=auto \
  -F diarize=true -F align=true -F compute_device=gpu \
  http://127.0.0.1:20810/api/v1/asr/jobs

# TTS 预置音色
curl -F text='你好，这是本地语音。' \
  -F language=Chinese -F voice_mode=preset -F speaker=Vivian \
  -F response_format=wav -F compute_device=cpu \
  http://127.0.0.1:20810/api/v1/tts/jobs

# 查询任务及结果
curl http://127.0.0.1:20810/api/v1/jobs/JOB_ID
curl http://127.0.0.1:20810/api/v1/jobs/JOB_ID/result

# 批量永久删除任务（排队任务会先原子取消，运行中任务会逐项拒绝）
curl -H 'Content-Type: application/json' \
  -d '{"job_ids":["JOB_ID_1","JOB_ID_2"],"purge":true}' \
  http://127.0.0.1:20810/api/v1/jobs/batch-delete

# ASR 原始音源（支持 Range，可直接用于播放器）
curl http://127.0.0.1:20810/api/v1/jobs/JOB_ID/source -o source.wav
```

可管理的主要端点：

- `POST /api/v1/asr/jobs`、`POST /api/v1/tts/jobs`
- `GET /api/v1/jobs`、`GET /api/v1/jobs/{id}`
- `POST /api/v1/jobs/{id}/cancel`、`POST /api/v1/jobs/{id}/retry`
- `POST /api/v1/jobs/batch-delete`（最多 100 个 ID，返回逐项结果与实际释放空间）
- `GET /api/v1/jobs/{id}/artifacts/{name}`
- `GET /api/v1/jobs/{id}/source`（ASR 原始音源；`?download=true` 强制下载）
- `PATCH /api/v1/jobs/{id}/speakers/{speaker_id}`
- `POST /api/v1/tts/voices`、`GET /api/v1/tts/voices`
- `GET /api/v1/events`（SSE）

任务响应中的 `processing_seconds` 是累计实际处理耗时，不包含排队等待，并会跨失败重试累加；运行中任务配合 `processing_as_of` 可实时显示。`compute_device` 返回 `cpu` 或 `gpu`，`compute_device_name` 返回任务提交/执行时持久化的具体设备名；GPU 名称动态取自实际 `cuda:0`，不会因以后更换硬件而改写历史记录。永久删除会清理任务输入、输出、错误文件、临时目录和 SQLite 记录，并在批次结束后执行安全擦除、WAL 截断与数据库压缩。

## OpenAI 兼容消费

```bash
curl -F file=@meeting.wav -F model=qwen3-asr-0.6b -F compute_device=gpu \
  -F response_format=verbose_json \
  http://127.0.0.1:20810/v1/audio/transcriptions

curl -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-tts-0.6b","input":"你好","voice":"Vivian","response_format":"wav","compute_device":"cpu"}' \
  http://127.0.0.1:20810/v1/audio/speech -o speech.wav
```

兼容端点是同步等待接口；长任务建议使用原生异步 API。

## 配置与安全

默认不开启鉴权，适合可信本机/局域网。对不可信网络暴露前设置 Bearer Key：

```bash
AUDIO_INTEL_API_KEY='replace-with-a-long-random-value' ./service.sh start all
```

可复制 `.env.example` 为 `.env`，编辑后用 `set -a; source .env; set +a` 加载；`service.sh` 不会隐式读取环境文件。常用变量包括 `AUDIO_INTEL_HOST`、`AUDIO_INTEL_PORT`、`AUDIO_INTEL_API_KEY`、目录覆盖、上传限制和文本限制。不要提交 `.env`，也不要在未配置 TLS 和访问控制时直接暴露到公网。

## 验证

```bash
.runtime/api/bin/pytest -q
corepack pnpm@10.15.1 --dir frontend typecheck
corepack pnpm@10.15.1 --dir frontend test:e2e
AUDIO_INTEL_MOCK_MODE=1 ./service.sh start all  # 仅供快速管线验收，不是生产默认值
./scripts/smoke_test.sh
```

生产默认值始终是真实模型模式；mock 模式只用于验证任务队列、API、导出和 UI。

真实模型的可恢复两小时设备矩阵测试（先分别使用 CPU/GPU 合成，再使用同一份 CPU 合成音频分别执行 CPU/GPU ASR）：

```bash
.runtime/asr/bin/python -u scripts/stress_device_matrix.py
```

本轮验收目标为约 2 小时（`7200±130` 秒），过程和最终报告保存在 `data/stress/device-matrix-2h/`；中断后执行同一命令会从已提交的任务继续，不会重复已完成阶段。

## License

本项目自有代码以 [Apache License 2.0](LICENSE) 发布。模型权重不会包含在 Git 仓库中，下载后仍适用各上游模型许可证；详见 [第三方组件与模型声明](THIRD_PARTY_NOTICES.md)。本项目为非官方的赛博朋克风格界面，与相关游戏、商标或权利人不存在隶属或背书关系。
