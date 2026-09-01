# 依赖维护

## 运行时矩阵

| 环境 | 关键版本 | 用途 |
|---|---|---|
| api | Python 3.12、FastAPI 0.141.1、Uvicorn 0.52.4 | API、队列管理、测试与前端托管 |
| asr | PyTorch 2.11.0+cu130（full）或 2.11.0+cpu（CPU-only）、qwen-asr 0.0.6、Transformers 4.57.6 | ASR、ForcedAligner、VAD、说话人分离 |
| tts | PyTorch 2.11.0+cu130（full）或 2.11.0+cpu（CPU-only）、qwen-tts 0.1.1、Transformers 4.57.3 | 0.6B/1.7B Base、CustomVoice 与 VoiceDesign 推理 |
| aligner | PyTorch 2.11.0+cu130（full）或 2.11.0+cpu（CPU-only）、qwen-asr 0.0.6、Transformers 4.57.6 | TTS 超长参考样本的按需对齐 |

qwen-tts 0.1.1 与 qwen-asr 0.0.6 精确要求不同 Transformers 版本，严禁同环境安装。Torch 2.11 要求 `setuptools<82`，因此模型环境固定到已修复已知旧版公告且满足该约束的 setuptools 81.0.0。

前端固定 pnpm 10.15.1，支持 Node 22.20+，CI 和文档推荐 Node 24 LTS；当前锁定 React 19、TypeScript 5.9、Vite 7、i18next 26 和 react-i18next 17。TypeScript 6、Vite 8、lucide-react 1.x 以及 Torch/Qwen 大版本升级不属于例行补丁，应单独做兼容与真实模型验证。

ASR、TTS 和 aligner 各有 full 与 `-cpu` 两套哈希锁。full 使用固定 CUDA 13.0 Torch backend；CPU-only 使用官方 CPU wheel，锁中不得出现 `nvidia-*` 或 `triton`。API 环境共用同一套锁。运行 `scripts/lock_dependencies.py` 或 `--check` 会同时生成或校验 Linux、Windows 的两种推理配置。

`mkcert` 是局域网直连 HTTPS 证书生成的可选系统工具，不属于 Python 或前端运行时锁。证书助手只从 `PATH` 调用它，并通过项目专用 `<AUDIO_INTEL_DATA_DIR>/tls/ca`（默认 `data/tls/ca`）设置 `CAROOT`；不会运行 `mkcert -install`。不使用内置证书助手、或由外部反向代理终止 TLS 时无需安装。

新增 0.6B/1.7B 模型大小或 Base、CustomVoice、VoiceDesign checkpoint 不等于新增 Python 包依赖：模型身份和 revision 由 `audio_intel/model_manifest.json` 固定。只要直接依赖清单未改变，就不应仅因增加 checkpoint 而重新生成哈希锁。

## 锁文件

根目录 `requirements-*.txt` 是人工维护的直接依赖；`requirements-lock/{linux,windows}/` 是 Python 3.12、x86_64 的完整哈希锁。安装器使用 `uv pip sync --require-hashes --strict`，并根据 `.runtime/deployment-profile` 为模型环境选择 CUDA 13.0 或官方 CPU Torch backend。

更新直接依赖后执行：

```bash
.runtime/api/bin/python scripts/lock_dependencies.py
.runtime/api/bin/python scripts/lock_dependencies.py --check
```

默认生成会优先保留现有锁中的兼容版本，只更新因直接依赖变化而必须调整的包。计划刷新全部间接依赖时显式执行：

```bash
.runtime/api/bin/python scripts/lock_dependencies.py --upgrade
.runtime/api/bin/python scripts/lock_dependencies.py --check
```

`--check` 以已提交锁为解析基线，验证它们仍与直接依赖一致；不能与 `--upgrade` 同时使用。提交直接清单和两个平台的全部生成锁。CI 会以已提交锁为基线重新编译并拒绝差异。uv 本身固定为 0.12.5，Linux 和 Windows 下载均验证上游 SHA256。

## 安全公告与 VEX

API 锁和前端锁要求审计无已知漏洞。Qwen/Torch 当前精确约束使部分包暂时无法安全提升，以下公告仅在模型运行时审计中显式放行：

- `PYSEC-2026-3447` / `GHSA-h35f-9h28-mq5c`：仅影响 macOS 文件系统上构建 sdist 时的 Unicode 排除规则；项目仅支持 Linux/Windows 运行且运行时不构建发行包。修复版 setuptools 83 又与 Torch 2.11 的 `<82` 约束冲突。
- `PYSEC-2025-194` / `GHSA-rrmf-rvhw-rf47`：`torch.jit.script` 处理攻击者本地脚本的内存破坏路径；服务不接收或编译用户脚本。
- `PYSEC-2026-2288` / `GHSA-69w3-r845-3855`：Trainer 恶意 checkpoint 路径。
- `PYSEC-2026-2289` / `GHSA-29pf-2h5f-8g72`：恶意远程配置/仓库加载路径。
- `PYSEC-2026-2290` / `GHSA-fgcw-684q-jj6r`：LightGlue 模型路径。
- `PYSEC-2025-217` / `CVE-2025-14929`：X-CLIP 恶意 checkpoint 转换路径。

本项目不使用 `torch.jit.script`、Trainer、LightGlue 或 X-CLIP，不接收用户提供的模型、checkpoint、配置或 Python 脚本；模型由受版本控制的 manifest 固定仓库和 revision，生产推理设置 Hugging Face/Transformers 离线模式并只加载项目本地目录。这是有边界的风险接受，不等于公告已修复。

每周 CI 使用上述精确 allowlist；任何新增公告都必须先让流水线失败，再选择升级或新增带依据的 VEX。每次 Qwen 包发布时重新检查是否可解除 Transformers 固定版本与对应豁免。
