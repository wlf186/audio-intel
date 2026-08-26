# 安装与复原

## 1. 支持范围

本文命令针对 Ubuntu 22.04/24.04 x86_64。原生 Windows 11 x64 使用独立的 [Windows 部署指南](WINDOWS.md)；macOS、ARM 和容器部署尚未验证。CPU 可运行全部能力，但 ASR 与 TTS 默认都选择 GPU；无 GPU 时请在页面或 API 中选择 `cpu`。

建议资源：16 GB RAM 起步、32 GB RAM 推荐、完整安装预留 30 GB 磁盘。已验证 GPU 为 4 GB RTX A1000。安装脚本固定 Python 3.12、PyTorch 2.11.0 CUDA 13.0 和受控的模型 revision。

## 2. 系统前提

```bash
sudo apt-get update
sudo apt-get install -y git curl tar coreutils python3

node --version       # 需要 22.20 或更高，推荐 24 LTS
corepack --version
nvidia-smi           # 仅 GPU 模式需要
```

如果发行版没有合适的 Node.js，请从 <https://nodejs.org/> 安装 Node 24 LTS 并启用 Corepack。Node 20 已结束维护，不再属于本项目支持基线。

## 3. 全新安装

```bash
git clone https://github.com/wlf186/audio-intel.git
cd audio-intel
./service.sh doctor
./service.sh setup all
./service.sh start all
curl -fsS http://127.0.0.1:20810/api/v1/health
```

`setup all` 创建 `.runtime/api`、`.runtime/asr`、`.runtime/tts` 和 `.runtime/aligner`，构建 `frontend/dist`，并将模型下载到 `models/`。TTS 与 Qwen ASR 要求互斥的 Transformers 版本，因此长参考音频的对齐使用独立 aligner 环境；不要把两个 Qwen 包装进同一环境。所有缓存和临时文件也留在仓库目录中。下载完成后，`service.sh start` 默认设置 Hugging Face 与 Transformers 离线模式。

安装使用 `requirements-lock/linux/` 中带哈希的锁文件。uv 固定为 0.12.5，安装器会在解压前校验官方 SHA256。模型 `.complete` 不是布尔标记，其内容必须等于清单中的固定 revision；空文件或错误 revision 会触发修复下载。

只部署单项能力：

```bash
./service.sh setup asr && ./service.sh start asr
./service.sh setup tts && ./service.sh start tts
./service.sh setup api
```

## 4. 代理与配置

```bash
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=$HTTP_PROXY
./service.sh setup all
```

配置持久化示例：

```bash
cp .env.example .env
# 编辑 .env，不要提交该文件
set -a; source .env; set +a
./service.sh start all
```

默认最多分别保留 5 个排队中的 ASR/TTS 任务、同时持久化 2 个提交，并为数据卷保留至少 5 GiB 空闲空间。通过 `AUDIO_INTEL_MAX_QUEUED_ASR`、`AUDIO_INTEL_MAX_QUEUED_TTS`、`AUDIO_INTEL_MAX_CONCURRENT_SUBMISSIONS` 和 `AUDIO_INTEL_MIN_FREE_DISK_BYTES` 调整；完整默认值见 `.env.example`。达到限制时提交返回 `429`，不会丢弃既有任务。

对不可信网络开放前，至少设置强随机 `AUDIO_INTEL_API_KEY`，并在外层反向代理配置 TLS。普通 HTTP 下远程浏览器可能拒绝麦克风权限。

## 5. 数据与升级

模型、数据库、任务输入输出、声纹样本和声音档案默认保留。升级前停止服务并备份 `data/`；随后执行：

```bash
git pull --ff-only
./service.sh setup all
./service.sh restart all
```

不要复制 `.runtime/` 到另一台机器；在目标机器重新运行 setup。任务数据可按需要单独迁移。

数据库会在启动时自动迁移到 schema v5，既有任务与旧声音档案保持可读。完整兼容性说明见 [升级指南](UPGRADE.md)。
