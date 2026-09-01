<p align="center">
  <img src="frontend/public/sandevistan-audio.svg" width="104" alt="Sandevistan Audio 标志">
</p>

<h1 align="center">Sandevistan Audio</h1>

<p align="center">
  <strong>面向语音识别与合成的私有、离线优先工作站。</strong>
</p>

<p align="center">
  把一台 Linux 或 Windows 电脑变成私有语音工作站：在本地完成音频转写、说话人分离、字词级时间戳、声纹与热词管理，以及语音合成和克隆，并通过 Web UI 或 API 使用。
</p>

<p align="center">
  模型安装后推理全程离线，任务数据保留在本机。
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="#主要能力">主要能力</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#api-与集成">API</a> ·
  <a href="#文档">文档</a> ·
  <a href="https://github.com/wlf186/audio-intel/releases">版本发布</a>
</p>

<p align="center">
  <a href="https://github.com/wlf186/audio-intel/actions/workflows/linux.yml"><img alt="Linux 质量检查" src="https://github.com/wlf186/audio-intel/actions/workflows/linux.yml/badge.svg"></a>
  <a href="https://github.com/wlf186/audio-intel/actions/workflows/windows.yml"><img alt="原生 Windows 冒烟测试" src="https://github.com/wlf186/audio-intel/actions/workflows/windows.yml/badge.svg"></a>
  <a href="https://github.com/wlf186/audio-intel/releases"><img alt="最新版本" src="https://img.shields.io/github/v/release/wlf186/audio-intel"></a>
  <img alt="安装后离线运行" src="https://img.shields.io/badge/runtime-offline%20after%20setup-0f8b8d">
  <img alt="CPU 与 NVIDIA GPU" src="https://img.shields.io/badge/inference-CPU%20%7C%20NVIDIA%20GPU-5a67d8">
  <a href="LICENSE"><img alt="自有代码许可证：Apache 2.0" src="https://img.shields.io/badge/code%20license-Apache--2.0-blue"></a>
</p>

> [!IMPORTANT]
> 本项目是独立、非官方、以非商业方式维护的开源项目，未获得 CD PROJEKT RED、R. Talsorian Games 或任何相关权利方的授权、赞助、认可或背书。详见[品牌与项目身份声明](BRAND_NOTICE.md)。

## 界面预览

![Sandevistan Audio 本地 ASR 工作区，展示说话人分离结果与导出控件](docs/assets/readme/zh-CN/asr-workspace.webp)

<p align="center">
  <img src="docs/assets/readme/zh-CN/tts-workspace.webp" width="49%" alt="Sandevistan Audio 预置音色语音合成工作区">
  <img src="docs/assets/readme/zh-CN/job-history.webp" width="49%" alt="Sandevistan Audio 持久化 ASR 与 TTS 任务记录">
</p>

![Sandevistan Audio 本地中英双语 Swagger API 指南](docs/assets/readme/zh-CN/api-docs.webp)

> [!NOTE]
> Web UI 支持简体中文和英文，可在页眉或登录对话框中切换，选择会保存在当前浏览器。本地 Swagger API 指南同样提供中英双语内容。

## 主要能力

| 范围 | 能力 |
| --- | --- |
| **本地语音识别** | Qwen3-ASR 0.6B/1.7B、FSMN-VAD、CAM++ 说话人分离、句段与字词时间戳，以及 JSON/SRT/VTT/TXT 导出 |
| **说话人与声纹** | 复用声纹档案识别并命名已知人员；自定义及声纹衍生热词表改善领域词汇，已完成任务保留不可变快照 |
| **本地语音工作室** | Qwen3-TTS 0.6B/1.7B、预置音色、一次性或声纹库声音克隆、1.7B VoiceDesign，以及 WAV/FLAC/MP3 输出 |
| **持久任务引擎** | SQLite 持久化队列、上传与推理进度、本机历史 ETA、SSE、取消、重试、任务记录和安全清理 |
| **Web UI 与 API** | 中英双语本地 Web UI 和 Swagger 指南、原生异步 API，以及 OpenAI 兼容转写与语音端点 |
| **部署感知运行** | 默认推荐的 CPU/GPU 全量配置和可选 CPU-only 配置；UI 与 API 只展示当前部署真正可用的设备和模型控制项 |

### 与普通模型演示的区别

- **安装后本地运行。** 模型 revision 固定，运行期强制离线加载；输入、结果、声音、数据库和日志均位于项目可控目录。
- **严谨的进程隔离。** ASR 与 TTS 使用互不兼容的独立环境和可复用受监督执行器；完整任务进程树退出后，取消任务才进入终态。
- **模型感知控制。** UI 与 API 只暴露所选 Qwen checkpoint 和声音模式真正支持的控制项，不静默忽略无效参数。

### 典型用途

- 把多人会议转换成带姓名的转写结果和字幕文件。
- 使用预置音色、声音克隆和 1.7B 声音设计搭建私有语音工作室。
- 通过异步或 OpenAI 兼容 API，为本地工具提供持久化语音后端。

## 兼容性与硬件

默认且推荐的 **full 全量配置** 是标准部署方式。CPU-only 是开发者为缩小依赖体积而主动选择的精简配置，并非默认安装路径。

| 配置 | 运行时与行为 | 磁盘建议 | 适用情况 |
| --- | --- | --- | --- |
| **full — 默认推荐** | 全部模型和功能；同时安装 CPU FP32 与 NVIDIA GPU BF16 运行时；任务默认使用 GPU，也可显式选择 CPU | 固定模型、隔离运行时和安装缓存约 43 GiB；最低预留 55 GiB，建议 70 GiB | 希望使用受支持的默认配置，或可能使用 GPU 加速 |
| **CPU-only — 可选精简** | 保留相同 ASR/TTS 模型与功能，不安装 CUDA、NVIDIA 和 Triton 包；仅 CPU FP32；GPU 控件禁用，API 显式请求 GPU 返回 `503` | Linux 实测模型与项目运行时核心占用约 29 GiB；下载/安装缓存和任务数据另计；最低预留 40 GiB，建议 50 GiB | 明确接受明显更慢的推理速度，以缩小依赖体积 |

| 项目 | 支持基线 |
| --- | --- |
| 操作系统 | Ubuntu 22.04/24.04 x86_64；原生 Windows 11 x64 |
| NVIDIA GPU | 可选；`nvidia-smi` 必须正常，驱动需支持固定的 PyTorch CUDA 运行时 |
| GPU 准入 | 0.6B 模型：总显存 3840 MiB；1.7B 模型：总显存 7936 MiB |
| 内存 | 完整安装最低 16 GB；建议 32 GB |

GPU 准入读取报告的总显存而不是当前空闲显存，其他 GPU 进程仍可能导致 OOM。API 显式请求不可用的 GPU 时返回 `503`，不会静默切到 CPU；Web UI 会解释原因并为本次提交选择 CPU。

macOS 与 ARM 尚未验证，项目也没有官方容器镜像。Linux 前台模式可作为 OCI 容器入口，但调用方需负责准备或挂载运行时和模型。原生 Windows 生命周期已由 CI 覆盖，真实模型的 Windows GPU 推理尚无实机验证记录。

## 快速开始

以下命令安装默认推荐的 full 全量配置。需要 Git、curl、tar、Node.js 22.20+（推荐 Node.js 24 LTS）与 Corepack；Python 3.12 和固定版本的运行时会安装到项目目录。

### Ubuntu 22.04 / 24.04 x86_64

```bash
git clone https://github.com/wlf186/audio-intel.git
cd audio-intel

./service.sh doctor
./service.sh setup all
./service.sh start all

curl -fsS http://127.0.0.1:20810/api/v1/health
```

### 原生 Windows 11 x64

建议使用较短的本地 NTFS 路径和 Node.js 24 LTS：

```powershell
git clone https://github.com/wlf186/audio-intel.git C:\ai\audio-intel
Set-Location C:\ai\audio-intel

.\service.cmd doctor
.\service.cmd setup all
.\service.cmd start all

Invoke-RestMethod http://127.0.0.1:20810/api/v1/health
```

浏览器访问 <http://127.0.0.1:20810>。Web UI 支持简体中文和英文，可在页眉或登录对话框中切换，选择会保存在当前浏览器。中英双语交互式 API 指南位于 <http://127.0.0.1:20810/docs>，机器可读契约位于 `/openapi.json`。Swagger 资源和校验器均随服务本地托管。

选择 CPU-only 配置的开发者在专用安装命令后使用相同的启动命令：

```bash
./service.sh setup all --profile cpu
./service.sh start all
```

原生 Windows 使用 `.\service.cmd setup all --profile cpu`，随后执行 `.\service.cmd start all`。

所选配置会记录在 `.runtime`，后续安装/升级自动沿用。切换配置前须停止服务并清空或取消未终结任务，然后执行 `setup all --profile full|cpu`。

不需要完整模型集时，可以只安装或启动一条管线：

```bash
./service.sh setup asr   # 或：tts / api
./service.sh start asr   # 或：tts / api
./service.sh status
./service.sh logs all
./service.sh stop all
```

完整前提条件、代理、部分安装、前台/容器运行与升级步骤见 [Linux 安装指南](docs/INSTALL.md)和[原生 Windows 指南](docs/WINDOWS.md)。

## 工作原理

```text
20810 FastAPI + 本地 React Web UI
        │
        ├── SQLite WAL ASR 队列 ── ASR 监督器
        │       └── 可复用任务执行器
        │           VAD → 说话人分离 → Qwen3-ASR → 强制对齐
        │           └── JSON / SRT / VTT / TXT
        │
        └── SQLite WAL TTS 队列 ── TTS 监督器
                └── 可复用任务执行器
                    Qwen3-TTS 预置 / 克隆 / VoiceDesign
                    └── WAV / FLAC / MP3
```

Qwen ASR 与 Qwen TTS 依赖互不兼容的 Transformers 版本，因此 ASR、TTS 和内部长参考对齐器使用独立 Python 环境。ASR/TTS GPU 任务共享项目内锁，同一时刻只让一个大模型占用 GPU。已使用的执行器会为突发任务保留短暂热窗口，只有同类队列持续为空且旧进程树完整退出后才会回收。

默认开启的单任务加速会根据硬件和模型大小扩大内部批次，但不改变模型、精度、说话人语义、ASR 分块或 TTS 顺序解码器。OOM 会在同一任务内逐步降到 batch 1。完整的执行、取消、模型、进度和能力契约见[架构与能力](docs/ARCHITECTURE.md)。

## API 与集成

ASR、TTS、克隆参考分析和声纹样本上传这四个原生异步提交入口都要求 8–128 字符的 `Idempotency-Key`。首次接受返回 `202`，同请求重放返回 `200`，同键更改输入返回 `409`。

使用 CPU 路径提交最小原生 ASR 任务：

```bash
BASE_URL=${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}
ASR_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')

curl --fail-with-body -sS \
  -H "Idempotency-Key: $ASR_KEY" \
  -F file=@meeting.wav \
  -F language=Auto \
  -F diarize=true \
  -F align=true \
  -F compute_device=cpu \
  "$BASE_URL/api/v1/asr/jobs"
```

OpenAI 兼容同步转写：

```bash
curl --fail-with-body -sS \
  -F file=@meeting.wav \
  -F model=qwen3-asr-0.6b \
  -F compute_device=cpu \
  -F response_format=verbose_json \
  "$BASE_URL/v1/audio/transcriptions"
```

启用鉴权后增加 `Authorization: Bearer $AUDIO_INTEL_API_KEY`。长任务应使用原生异步 API，以获得队列状态、进度、ETA、ETag 轮询和 SSE。TTS、克隆、热词、声纹、取消、重试、产物、Range 请求与事件校准见 [API 指南](docs/API.md)。

## 文档

| 指南 | 内容 |
| --- | --- |
| [安装与复原](docs/INSTALL.md) | Linux 前提、完整/部分安装、代理、目录和服务模式 |
| [原生 Windows](docs/WINDOWS.md) | Windows 安装、生命周期、防火墙和排障 |
| [API](docs/API.md) | 原生异步与 OpenAI 兼容使用契约 |
| [架构与能力](docs/ARCHITECTURE.md) | 管线、模型、设备、加速、队列、进度和取消 |
| [局域网 HTTPS](docs/HTTPS.md) | 项目 CA、证书信任、SAN 更新和指纹核对 |
| [故障排查](docs/TROUBLESHOOTING.md) | GPU、模型、上传、队列、进度和进程清理 |
| [升级](docs/UPGRADE.md) | 数据备份、schema 兼容和升级步骤 |
| [依赖维护](docs/DEPENDENCIES.md) | 运行时隔离、版本固定、锁文件和安全审计说明 |
| [参与贡献](CONTRIBUTING.md) | 开发环境、验证命令和 Pull Request 要求 |
| [品牌与项目身份](BRAND_NOTICE.md) | 非官方身份、第三方权利及 Apache-2.0 授权边界 |

## 安全与数据归属

鉴权默认关闭，适合可信本机或局域网。对外暴露前应设置强随机 `AUDIO_INTEL_API_KEY` 并配置 TLS：

```bash
AUDIO_INTEL_API_KEY='replace-with-a-long-random-value' ./service.sh start all
```

浏览器登录会把 Key 换成不透明的 HttpOnly 同源会话 Cookie；原始 Key 不进入浏览器存储或 URL。`/api/v1/health` 是有意公开的最小探针，详细系统信息、媒体、模型、任务和结果仍受保护。

通过局域网 IP 使用麦克风时，浏览器通常要求 HTTPS。项目提供离线、项目专用的 `mkcert` 助手，操作见[局域网 HTTPS 指南](docs/HTTPS.md)。未配置鉴权和可信 TLS 时，不要把 20810 端口直接暴露到公网。

模型、任务输入、生成结果、SQLite、声音、声纹、缓存、日志和运行时默认都保存在项目目录。输入与结果会持续保留，直到显式永久删除。

## 支持与贡献

- 通过 [GitHub Issues](https://github.com/wlf186/audio-intel/issues) 报告可复现问题或提出功能建议。
- 修改运行时边界、模型加载、设备路由、进程监督或公共 API 前，先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 更新现有安装前查看[版本发布](https://github.com/wlf186/audio-intel/releases)和[升级指南](docs/UPGRADE.md)。

## 许可证

项目自有代码以 [Apache License 2.0](LICENSE) 发布。下载的模型权重不包含在仓库中，并继续适用各上游许可证；详见[第三方组件与模型声明](THIRD_PARTY_NOTICES.md)。代码许可证不授予第三方名称或知识产权的使用权；详见[品牌与项目身份声明](BRAND_NOTICE.md)。
