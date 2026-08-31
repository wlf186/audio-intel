# 安装与复原

## 1. 支持范围

本文命令针对 Ubuntu 22.04/24.04 x86_64。原生 Windows 11 x64 使用独立的 [Windows 部署指南](WINDOWS.md)；macOS 和 ARM 尚未验证。项目没有官方容器镜像或完整 GPU 容器兼容性承诺，但 Linux 前台模式可作为 rootless 或 rootful OCI 容器的服务入口。CPU 可运行全部能力，但 ASR 与 TTS 默认都选择 GPU；无 GPU 时请在页面或 API 中选择 `cpu`。

建议资源：16 GB RAM 起步、32 GB RAM 推荐。0.6B/1.7B 全部模型、隔离运行时和安装缓存约占 43 GiB，因此至少预留 55 GiB、建议 70 GiB 可用磁盘，为任务数据、升级和默认 5 GiB 准入保护留出空间。已验证的 4 GiB RTX A1000 可运行 0.6B GPU 路径；1.7B GPU 路径需要 8 GiB 档设备并另行实机验收。安装脚本固定 Python 3.12、PyTorch 2.11.0 CUDA 13.0 和受控的模型 revision。

## 2. 系统前提

```bash
sudo apt-get update
sudo apt-get install -y git curl jq tar coreutils python3

node --version       # 需要 22.20 或更高，推荐 24 LTS
corepack --version
nvidia-smi           # 仅 GPU 模式需要
```

`jq` 仅供 `/docs` 中可复制的 Bash API 示例解析 JSON；服务安装和运行本身不依赖它。仅在需要项目自带的局域网 HTTPS 证书助手时安装 `mkcert`，也可把离线 `mkcert` 二进制放入 `PATH`；普通 HTTP 和外部反向代理 TLS 不依赖它。

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

`setup all` 创建 `.runtime/api`、`.runtime/asr`、`.runtime/tts` 和 `.runtime/aligner`，构建 `frontend/dist`，并将模型下载到 `models/`。`setup asr/all` 下载固定 revision 的 Qwen3-ASR 0.6B 与 1.7B；`setup tts/all` 下载 Qwen3-TTS 0.6B/1.7B 的 Base、CustomVoice，以及 1.7B VoiceDesign。TTS 与 Qwen ASR 要求互斥的 Transformers 版本，因此长参考音频的对齐使用独立 aligner 环境；不要把两个 Qwen 包装进同一环境。所有缓存和临时文件也留在仓库目录中。下载完成后，`service.sh start` 默认设置 Hugging Face 与 Transformers 离线模式。

默认 HTTP 足以支持本机访问；通过局域网 IP 使用浏览器麦克风时，可直接启用项目本地 CA 和 HTTPS，无需另建反向代理：

```bash
./service.sh tls enable
./service.sh start all
```

`tls enable` 自动收集 localhost、主机名和活动网卡地址，并保留旧证书已有的 SAN；也可通过重复的 `--host` 追加容器无法发现的宿主机/VPN 地址。模式保存在 `<AUDIO_INTEL_DATA_DIR>/tls/service-profile.json`（默认是 `data/tls/service-profile.json`），新终端中的普通 `start`、`restart` 和 `run` 会自动沿用。地址变化后重新执行 `tls enable`；需要立即应用到后台服务时加 `--restart`。`tls enable --restart` 和 `tls disable --restart` 都会执行 `restart all`，重启 API、ASR 和 TTS；后者切回 HTTP 但不删除证书。20810 在 HTTPS 模式下只接受 HTTPS，不提供同端口 HTTP，也不自动重定向。客户端安装根证书和指纹核对步骤见 [README](../README.md#局域网-https-与浏览器录音)。

`start` 是后台模式；各组件在独立会话和进程组中运行，API 和 worker 真正就绪后命令才返回。关闭普通终端、调用脚本退出或上游仅清理启动命令所在进程组，不会停止这些后台组件。将服务作为容器主进程运行、需要前台监督，或所在执行器会在命令返回后清理整个 cgroup 时，使用：

```bash
./service.sh run all
```

`run` 会保持前台并转发停止信号，无需 systemd 或其他守护程序。构建容器时应先完成 `.runtime`、前端和模型准备，运行时为当前 UID 提供可写的数据、临时、缓存、日志和 `run` 目录；这些位置可用 `.env.example` 已有的 `AUDIO_INTEL_*_DIR` 变量指向挂载卷。端口仍为非特权的 `20810`，rootless 不需要额外脚本分支。

后台模式通过 `./service.sh restart all` 重启。重启会先完成运行时和模型预检，再停止目标的完整进程树；若任一组件未能完整停止，命令返回非零且不会启动新实例。前台模式应向当前 `run all` 进程发送 `SIGTERM`（交互终端可按 `Ctrl+C`），等待它清理完成后再执行 `./service.sh run all`；容器中直接使用 Docker、Podman 或编排器的重启操作。不要在另一个 shell 中对前台实例执行 `restart all`，也不要让容器的 `CMD` 使用后台 `start all`。独立会话不会逃逸容器或 systemd 的 cgroup，容器重启策略仍由运行时负责。

ASR 与 TTS GPU 能力均按所选模型判断：0.6B/1.7B 使用 `nvidia-smi` 报告的总显存门槛 3840/7936 MiB，而不是当前空闲显存。可从受保护的 `GET /api/v1/capabilities` 读取 `asr.models[].compute_devices` 和 `tts.model_capabilities[].compute_devices`；例如报告 8151 MiB 的 8 GiB 显卡可通过 1.7B 准入。准入门槛不排除其他 GPU 程序导致运行期 OOM，无 GPU 或显存不足时 API 消费方应显式选择 `compute_device=cpu`。

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

`service.sh` 不会自动读取通用 `.env`；每个新 shell 使用其中的端口、目录、API Key 等设置前仍必须重新加载。项目管理的 HTTPS 例外：`tls enable` 写入当前 `AUDIO_INTEL_DATA_DIR` 下的专用 profile 会自动加载。若覆盖数据目录，应在执行 `tls enable` 和后续启停命令前保持相同的 `AUDIO_INTEL_DATA_DIR`。显式 `AUDIO_INTEL_PROTOCOL` 和证书变量优先于该 profile，适用于外部管理的证书；若旧终端仍导出了这些变量，脚本会提示它们正在覆盖保存模式。

默认最多分别保留 5 个排队中的 ASR/TTS 任务、同时持久化 2 个提交，并为数据卷保留至少 5 GiB 空闲空间。通过 `AUDIO_INTEL_MAX_QUEUED_ASR`、`AUDIO_INTEL_MAX_QUEUED_TTS`、`AUDIO_INTEL_MAX_CONCURRENT_SUBMISSIONS` 和 `AUDIO_INTEL_MIN_FREE_DISK_BYTES` 调整；完整默认值见 `.env.example`。达到限制时提交返回 `429`，不会丢弃既有任务。

对不可信网络开放前，至少设置强随机 `AUDIO_INTEL_API_KEY`，并使用项目本地 CA 直连 HTTPS 或在外层反向代理配置 TLS。普通 HTTP 下远程浏览器可能拒绝麦克风权限；项目本地 CA 适合受控局域网，不应替代公网受信 CA。

## 5. 数据与升级

模型、数据库、任务输入输出、声纹样本和声音档案默认保留。升级前停止服务并备份 `data/`；随后执行：

```bash
git pull --ff-only
./service.sh setup all
# 若使用 .env，先执行：set -a; source .env; set +a
./service.sh restart all
```

上例的 `restart all` 适用于后台模式。若部署入口为 `run all`，应让当前前台进程退出，再由终端或容器运行时重新启动它。

不要复制 `.runtime/` 到另一台机器；在目标机器重新运行 setup。任务数据可按需要单独迁移。

数据库会在启动时自动迁移到 schema v8，既有任务、旧声音档案与声纹样本保持可读。v8 新增声纹人员备注、人名热词开关和只读系统词表；完整兼容性说明见 [升级指南](UPGRADE.md)。
