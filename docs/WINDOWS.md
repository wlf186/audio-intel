# Windows 11 原生部署

本文只适用于 **Windows 11 x64 原生环境**，不使用 WSL 或 Docker。项目提供 PowerShell 一键安装和运维脚本；模型、Python、缓存、日志、数据库和生成结果默认保存在仓库目录内，也可通过环境变量覆盖运行目录。

Windows 自动化会在 GitHub 的 Windows runner 上验证依赖解析、后端测试、前端构建和 mock 全链路。当前尚未在 Windows NVIDIA 实机上完成真实模型推理验收，因此不要把 CI 通过理解为 Windows GPU 性能或真实推理结果保证。

## 1. 机器与软件要求

- Windows 11 x64，建议完成 Windows Update。
- 16 GB 内存起步，32 GB 推荐；0.6B/1.7B 全部模型、隔离运行时和安装缓存约占 43 GiB，至少预留 55 GiB、建议 70 GiB。Windows 无法使用符号链接时缓存还可能额外占用空间。
- 本地 NTFS 磁盘，建议克隆到短路径，例如 `C:\ai\audio-intel`。避免 OneDrive、网络盘和移动盘。
- Git for Windows、Node.js 24 LTS（最低 22.20，自带 npm）。Python 3.12 和 uv 由项目安装到 `.runtime\`。
- GPU 可选。GPU 模式需要 NVIDIA 显卡、可用的 `nvidia-smi` 和 **580 或更高版本驱动**。安装器使用官方 PyTorch 2.11 CUDA 13.0 wheel，通常不需要另装完整 CUDA Toolkit。

官方参考：[PyTorch Windows 安装](https://pytorch.org/get-started/locally/)、[CUDA 驱动兼容矩阵](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)、[uv Windows 安装](https://docs.astral.sh/uv/getting-started/installation/)。

## 2. 全新安装

打开 Windows Terminal 或 PowerShell：

```powershell
New-Item -ItemType Directory -Path C:\ai -Force | Out-Null
Set-Location C:\ai
git clone https://github.com/wlf186/audio-intel.git
Set-Location audio-intel

.\service.cmd doctor
.\service.cmd setup all
.\service.cmd start all

Invoke-RestMethod http://127.0.0.1:20810/api/v1/health
```

浏览器访问 `http://127.0.0.1:20810`，API 文档位于 `http://127.0.0.1:20810/docs`。Swagger 的 JavaScript、CSS、图标和接口定义全部由本机服务提供，运行期不依赖 CDN 或在线校验器。

## 局域网 HTTPS

通过局域网 IP 使用浏览器麦克风前，先安装 `mkcert` 或把离线 `mkcert.exe` 放入 `PATH`，然后启用项目专用证书：

```powershell
.\service.cmd tls enable
.\service.cmd start all
```

命令自动把 localhost、主机名和活动网卡地址加入证书，并保留旧证书已有的 SAN；可用 `--host 192.168.1.20` 追加容器无法发现的宿主机/VPN 地址。模式保存在 `<AUDIO_INTEL_DATA_DIR>\tls\service-profile.json`（默认是 `data\tls\service-profile.json`），新开 PowerShell 后普通的 `start` 和 `restart` 会继续使用 HTTPS。若覆盖数据目录，执行 `tls enable` 和后续启停命令时必须保持相同的 `AUDIO_INTEL_DATA_DIR`。`.\service.cmd tls status` 可同时查看保存配置、证书信息和实际运行协议。

浏览器访问 `https://192.168.1.20:20810`。从登录页下载 `.cer`，核对 `.\service.cmd tls fingerprint` 的 SHA-256 指纹后，打开证书并安装到“受信任的根证书颁发机构”（当前用户或本地计算机），再重启 Chrome/Edge。若客户端还无法打开首次自签名连接，可通过可信文件传输复制服务端的 `data\tls\audio-intel-root-ca.cer`。管理员在服务端项目目录也可使用：

```powershell
certutil -addstore -f Root .\data\tls\audio-intel-root-ca.cer
```

IP 变化时重新运行 `.\service.cmd tls enable`；根 CA 保持不变，已安装客户端无需重新安装。启用和禁用默认只保存下次启动配置，`--restart` 才会立即应用；`tls enable --restart` 和 `tls disable --restart` 都会执行 `restart all`，重启 API、ASR 和 TTS。后者切回 HTTP，但证书不会删除。显式设置的 `AUDIO_INTEL_PROTOCOL` 和 TLS 文件环境变量仍优先于 profile，适合外部证书配置。启用 HTTPS 后 20810 仅接受 HTTPS，不同时提供 HTTP。不要分发 `data\tls\server-key.pem` 或 `data\tls\ca\rootCA-key.pem`。

只安装或启动部分能力：

```powershell
.\service.cmd setup asr
.\service.cmd start asr

.\service.cmd setup tts
.\service.cmd start tts

.\service.cmd status
.\service.cmd logs all
.\service.cmd stop all
```

`start asr` 和 `start tts` 都会同时确保 API 已启动。启动命令会等待 API 健康检查和对应 worker 注册完成后才返回；失败时清理本次新建的进程。`restart all` 使用相同的就绪检查。关闭 PowerShell 窗口不会主动停止后台进程，Windows 不提供也不需要 Linux 的 `run` 前台动作。

`stop` 会先校验 PID 对应的命令身份，再清理 supervisor、executor 和阶段子进程组成的完整进程树。陈旧或已经复用的 PID 不会被误杀；若仍有进程无法退出，命令返回非零而不会假报成功。

## 3. 代理与配置

代理变量必须在执行 setup 的同一个 PowerShell 中设置：

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:7890'
$env:HTTPS_PROXY = $env:HTTP_PROXY
.\service.cmd setup all
```

企业代理使用自签 CA 时，优先导出组织提供的 PEM 证书，不要关闭 TLS 校验：

```powershell
$env:UV_SYSTEM_CERTS = '1'
$env:REQUESTS_CA_BUNDLE = 'C:\certs\company-ca.pem'
```

端口、目录、API Key 等通用运行配置仍使用当前 PowerShell 的环境变量；脚本不会自动读取 `.env`，新终端必须重新设置这些部署变量后再执行 `start` 或 `restart`。`tls enable` 保存的 HTTPS profile 会单独自动加载：

```powershell
$env:AUDIO_INTEL_PORT = '20810'
$env:AUDIO_INTEL_API_KEY = 'replace-with-a-long-random-value'
.\service.cmd start all
```

数据、临时文件、缓存、日志、PID、模型和前端目录可使用 `.env.example` 中已有的变量覆盖；相对路径按仓库根目录解析。例如：

```powershell
$env:AUDIO_INTEL_DATA_DIR = 'D:\audio-intel-data'
$env:AUDIO_INTEL_LOG_DIR = 'D:\audio-intel-state\logs'
$env:AUDIO_INTEL_RUN_DIR = 'D:\audio-intel-state\run'
.\service.cmd start all
```

ASR 与 TTS 默认都选择 GPU 并开启单任务加速；无可用 NVIDIA GPU 时，应在页面选择 CPU，或由 API 显式传入 `compute_device=cpu`。默认准入限制为每类 5 个排队任务、2 个并行提交持久化和至少 5 GiB 数据卷空闲空间，可按需设置：

```powershell
$env:AUDIO_INTEL_MAX_QUEUED_ASR = '5'
$env:AUDIO_INTEL_MAX_QUEUED_TTS = '5'
$env:AUDIO_INTEL_MAX_CONCURRENT_SUBMISSIONS = '2'
$env:AUDIO_INTEL_MIN_FREE_DISK_BYTES = '5368709120'
```

## 4. GPU 验证

```powershell
nvidia-smi
.\.runtime\asr\Scripts\python.exe -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only')"
```

驱动低于 580 时先从 NVIDIA 官方渠道更新。ASR 与 TTS 0.6B/1.7B 都按 `nvidia-smi` 报告的总显存使用 3840/7936 MiB 门槛，而不是当前空闲显存；因此报告 8151 MiB 的 8 GiB 显卡可选择 1.7B。可查询受保护的 `GET /api/v1/capabilities`，以 `asr.models[].compute_devices` 和 `tts.model_capabilities[].compute_devices` 判断具体模型。门槛只是准入条件，RTX 笔记本的 WDDM、桌面和浏览器仍可能占用显存并造成运行期 OOM；此时关闭其他 GPU 程序后重试，或为任务选择 CPU。服务不会把显式 API GPU 任务静默回退到 CPU，前端则会在所选模型不满足门槛时提示原因并按 CPU 创建本次任务。当前 Windows NVIDIA 真实推理仍未实机验收。

## 5. 常见问题

### ASR 推理出现 `PermissionError: [WinError 5]`

旧版本的 ASR 子进程会反复替换父进程正在轮询的同一个进度 JSON；Windows 上的文件占用规则可能使 `os.replace` 失败，并连带中止本来正常的推理。升级后进度改为一次性发布、读取后清理的不可变编号快照，不再覆盖父进程可能正在读取的文件。该修复不改变识别结果、进度更新频率或任务 API，也不会让 TTS 进度改用文件通信。

### PowerShell 拒绝运行脚本

优先通过 `service.cmd` 调用，它只为当前进程使用 `ExecutionPolicy Bypass`，不会更改用户或系统策略。若组织组策略仍然阻止执行，请联系管理员，不要把系统策略永久改成 `Unrestricted`。

### 路径过长或模型解压失败

将仓库移动到 `C:\ai\audio-intel` 等短路径。必要时以管理员身份启用 Windows 长路径并重启：

```powershell
New-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
  -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

该设置并不能修复所有旧程序，因此短路径仍是首选。参见 [Microsoft MAX_PATH 文档](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation)。

### Hugging Face 提示无法创建符号链接

下载仍可继续，但可能重复占用磁盘。可在 Windows 设置中启用“开发人员模式”，或以管理员身份安装；不要仅为了隐藏问题而关闭所有警告。参见 [Hugging Face 缓存限制](https://huggingface.co/docs/huggingface_hub/guides/manage-cache)。

### 下载很慢或中断

```powershell
Get-ChildItem Env: | Where-Object Name -Match 'proxy'
Invoke-WebRequest -Method Head https://huggingface.co
Invoke-WebRequest -Method Head https://modelscope.cn
.\service.cmd setup all
```

模型下载支持续传，只有下载完成才写入 `.complete`。不要手工创建该文件。避免把仓库放在实时同步目录；杀毒软件扫描大型权重时也可能显著拖慢下载和加载，仅在确认代码与模型来源可信后才考虑对项目的 `models`、`cache`、`.runtime` 做最小范围排除。

`.complete` 的内容必须等于项目模型清单中的 revision。空文件和旧 revision 都会被 doctor 标记，重新运行对应的 `setup asr` 或 `setup tts` 会校验并修复。

### 端口或防火墙问题

```powershell
Get-NetTCPConnection -LocalPort 20810 -ErrorAction SilentlyContinue
.\service.cmd status
.\service.cmd logs all
```

本机访问不需要新增防火墙规则。局域网访问只应允许“专用网络”，并应配置 `AUDIO_INTEL_API_KEY`；不要把 20810 直接暴露到公网。远程页面在普通 HTTP 下可能无法获得麦克风权限，可按上面的“局域网 HTTPS”启用项目本地 CA，或继续使用文件上传。

如果 `start` 或 `restart` 返回失败，先查看 `logs\<组件>.log` 和 `logs\<组件>.error.log`。脚本只有在健康检查和 worker 注册成功后才报告启动完成；端口占用、运行时异常或 worker 未注册都会返回非零，并回滚本次新启动且没有既有服务依赖的组件。

### 内存不足、任务暂停或文件被占用

保留 Windows 的系统管理分页文件，长时间 TTS/ASR 时连接电源并禁用自动睡眠。升级、移动 `.runtime` 或删除模型前先执行 `.\service.cmd stop all`。执行中任务通过界面取消时，worker 会终止该任务的完整 Windows 进程树，确认退出后才开放删除；若任务因强制关机中断，重启服务会先清理已记录的遗留执行进程，再恢复过期任务。

同类队列排空后，ASR/TTS 执行器默认保留 60 秒热窗口，再连同完整 Windows 进程树安全重建并归还内存；窗口内的新任务会继续复用。可在启动服务前设置 `AUDIO_INTEL_EXECUTOR_IDLE_SECONDS`，`0` 表示队列排空后立即回收。执行器重建不会重启 API 或使浏览器会话失效。

## 6. 升级与验证

```powershell
.\service.cmd stop all
git pull --ff-only
.\service.cmd setup all
# tls enable 保存的 HTTPS profile 会自动加载；外部证书环境变量仍需在新终端重新设置。
.\service.cmd start all
.\.runtime\api\Scripts\python.exe scripts\smoke_test.py
```

升级前备份 `data\`。API 启动时会把数据库自动迁移到 schema v8；完整的不兼容 API 变更和迁移说明见 [升级指南](UPGRADE.md)。不要从其他机器复制 `.runtime\`；在目标机器重新 setup。模型目录可以复制，但每个模型的 `.complete` 内容必须与项目固定的 revision 一致。

`setup tts` 同时维护独立的 `.runtime\aligner`，这是超长克隆样本按词边界截断所需的内部运行时，不是额外服务。更多版本与锁文件说明见 [依赖维护](DEPENDENCIES.md)。
