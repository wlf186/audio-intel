# 依赖维护

## 运行时矩阵

| 环境 | 关键版本 | 用途 |
|---|---|---|
| api | Python 3.12、FastAPI 0.141.1、Uvicorn 0.52.4、pypdf 6.19.0 | API、队列管理、文档解析、测试与前端托管 |
| asr | PyTorch 2.11.0+cu130（full）或 2.11.0+cpu（CPU-only）、qwen-asr 0.0.6、Transformers 4.57.6 | ASR、ForcedAligner、VAD、CPU 人声增强、说话人分离 |
| tts | PyTorch 2.11.0+cu130（full）或 2.11.0+cpu（CPU-only）、qwen-tts 0.1.1、Transformers 4.57.3 | 0.6B/1.7B Base、CustomVoice 与 VoiceDesign 推理 |
| aligner | PyTorch 2.11.0+cu130（full）或 2.11.0+cpu（CPU-only）、qwen-asr 0.0.6、Transformers 4.57.6 | TTS 默认参考截取及手动声纹区间的按需对齐 |

qwen-tts 0.1.1 与 qwen-asr 0.0.6 精确要求不同 Transformers 版本，严禁同环境安装。Torch 2.11 要求 `setuptools<82`，因此模型环境固定到已修复已知旧版公告且满足该约束的 setuptools 81.0.0。

前端固定 pnpm 10.15.1，支持 Node 22.20+，CI 和文档推荐 Node 24 LTS；当前锁定 React 19、TypeScript 5.9、Vite 7、i18next 26 和 react-i18next 17。TypeScript 6、Vite 8、lucide-react 1.x 以及 Torch/Qwen 大版本升级不属于例行补丁，应单独做兼容与真实模型验证。

ASR、TTS 和 aligner 各有 full 与 `-cpu` 两套哈希锁。full 使用固定 CUDA 13.0 Torch backend；CPU-only 使用官方 CPU wheel，锁中不得出现 `nvidia-*` 或 `triton`。API 环境共用同一套锁。运行 `scripts/lock_dependencies.py` 或 `--check` 会同时生成或校验 Linux、Windows 的两种推理配置。

`mkcert` 是局域网直连 HTTPS 证书生成的可选系统工具，不属于 Python 或前端运行时锁。证书助手只从 `PATH` 调用它，并通过项目专用 `<AUDIO_INTEL_DATA_DIR>/tls/ca`（默认 `data/tls/ca`）设置 `CAROOT`；不会运行 `mkcert -install`。不使用内置证书助手、或由外部反向代理终止 TLS 时无需安装。

新增 0.6B/1.7B 模型大小或 Base、CustomVoice、VoiceDesign checkpoint 不等于新增 Python 包依赖：模型身份和 revision 由 `audio_intel/model_manifest.json` 固定。只要直接依赖清单未改变，就不应仅因增加 checkpoint 而重新生成哈希锁。

## 锁文件

GTCRN-DNS3 和实验性 UL-UNAS-DNS3 均由 manifest 固定上游 commit、checkpoint 和 SHA-256，使用现有
ASR 环境的 Torch/NumPy 在 CPU FP32 运行，不增加依赖或环境。适配后的推理代码与
MIT 许可随源码提供，权重只在 setup 下载；运行期要求 revision 标记和 checksum
同时匹配。详见[人声增强](ASR_ENHANCEMENT.md)和[第三方声明](../THIRD_PARTY_NOTICES.md)。

GTCRN-DNS3 and experimental UL-UNAS-DNS3 use existing ASR Torch/NumPy on CPU FP32, with no new package or
environment. The manifest pins its commit and checksum; MIT inference code ships
in source, while weights download at setup and are verified for runtime readiness.
UL-UNAS is included for administrator experiments and failed public CER validation;
GTCRN 50% remains the default. Neither optional denoiser is required for base ASR readiness.

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


视频转写沿用 API/ASR 已固定的 PyAV 和 NumPy；API 显式依赖 Pillow 13.0.0，ASR/模型环境因 Gradio 约束保留 12.3.0（见下方 VEX）。简洁 PDF 在 API 环境使用 ReportLab 4.5.1 与仓库内 Noto Sans SC 字体，不依赖系统字体或在线服务。浏览器分片 SHA-256 使用 @noble/hashes 2.0.1，在 Web Worker 运行。字体来源与许可证见 [第三方说明](../THIRD_PARTY_NOTICES.md)。模型、Transformers、精度和既有推理批次保持不变。

API 环境固定使用 pypinyin 0.55.0 离线生成声纹人员的拼音排序与索引。姓氏读音覆盖只在排序时应用，不改写姓名、系统热词或历史快照；ASR/TTS/aligner 不安装该依赖。


## v0.1.35 定向依赖更新 / Targeted dependency updates

API 升级 Pillow 至 13.0.0；全部环境的 certifi 固定至 2026.10.19。DOCX 导出复用已有 python-docx 1.2.0，不新增依赖。模型、Torch、Transformers 和推理参数不变。API uses Pillow 13.0.0; every runtime locks certifi 2026.10.19. DOCX reuses existing python-docx 1.2.0. Model identities and inference settings are unchanged.

只更新指定间接依赖，保留其余兼容锁版本：

```bash
.runtime/api/bin/python scripts/lock_dependencies.py --upgrade-package certifi==2026.10.19
.runtime/api/bin/python scripts/lock_dependencies.py --check
```

`--upgrade-package` 可重复，接受包名或精确版本约束，不能与 `--check` 或全量 `--upgrade` 同用。Targeted upgrades preserve compatible unrelated lock entries; repeat the option for multiple packages. Check mode never upgrades.

### 模型环境 Pillow VEX / Model-runtime Pillow VEX

Gradio 6.17.3 要求 `pillow<13`，当前三个模型环境保留 Pillow 12.3.0。仅模型环境审计对下列公告设置精确例外，API 不使用这些例外。依据是当前服务的数据路径，不是包已修复；启用 Gradio 服务、接收图片或改变相关调用时必须重新评估。每次 Gradio/Qwen 更新重新检查兼容修复版。

| Advisory | 当前不可达路径 / Unreachable operation |
| --- | --- |
| [CVE-2026-112895](https://github.com/python-pillow/Pillow/security/advisories/GHSA-p4wg-p73j-c528) | Pillow PDF append/page-tree parsing |
| [CVE-2026-112400](https://github.com/python-pillow/Pillow/security/advisories/GHSA-65qm-jvmh-6pqg) | GIF seek after caught decompression-bomb error |
| [CVE-2026-112397](https://github.com/python-pillow/Pillow/security/advisories/GHSA-58r4-6rv3-gfh8) | ImageMorph on a zero-width image |
| [CVE-2026-112896](https://github.com/python-pillow/Pillow/security/advisories/GHSA-pchr-8j38-3x4p) | TIFF encoding with retained ColorMap metadata |
| [CVE-2026-112897](https://github.com/python-pillow/Pillow/security/advisories/GHSA-q45j-58j2-45rg) | TIFF strip decoding |
| [CVE-2026-112395](https://github.com/python-pillow/Pillow/security/advisories/GHSA-462x-pgg6-55fp) | Tiled TIFF decoding |
| [CVE-2026-112404](https://github.com/python-pillow/Pillow/security/advisories/GHSA-c6hw-5grf-ff96) | TIFF tile/RGBA decoding |
| [CVE-2026-112899](https://github.com/python-pillow/Pillow/security/advisories/GHSA-vx6w-vv9f-6gvg) | EPS/Ghostscript scaled rendering |
| [CVE-2026-112403](https://github.com/python-pillow/Pillow/security/advisories/GHSA-8g5g-8vmr-9782) | Pillow JPEG decoding |

ASR/aligner 仅向 Qwen 提供本地音频路径或数组，TTS 使用文本与本地音频；服务不启动 Gradio。`audio_intel/video_frames.py` 与 `video_detection.py` 通过 PyAV/NumPy 获取已解码像素，仅做变换与 JPEG 编码，不调用 Pillow 图片解码或上述操作。读取导出图片的 API 环境已升级。`test_video_frames_do_not_decode_images_with_pillow` 回归检查该边界。

The exceptions apply only to model runtimes constrained by Gradio, whose service paths consume audio or decoded pixel arrays. Pillow file decoding, PDF append, TIFF encoding, GIF seeking, morphology and EPS rendering are not used there. API image readers use the patched version. These are bounded non-reachability assessments, not fixes to Pillow 12.3.0; new advisories still fail audits, and new image inputs or Gradio serving invalidate this assessment.


同轮完整模型锁审计还发现旧间接依赖公告：hydra-core 升至 1.3.7、urllib3 至 2.8.0、Werkzeug 至 3.1.9。Qwen 精确固定的 Accelerate/Transformers 暂保留，以下新增例外仅限固定 manifest、本地离线推理：

- [PYSEC-2026-3804 / GHSA-4j2p-28q2-5m79](https://github.com/advisories/GHSA-4j2p-28q2-5m79)：Accelerate 恶意 checkpoint 索引路径；服务不接收用户 checkpoint 或 shard 索引。
- [PYSEC-2026-4174 / GHSA-x9r9-c232-4q39](https://github.com/advisories/GHSA-x9r9-c232-4q39)：远程 `custom_generate` 文件缓存；服务不接受或调用远程 custom_generate，强制离线加载本地模型。
- [PYSEC-2026-3929 / GHSA-xrqw-3rrv-vx5w](https://github.com/advisories/GHSA-xrqw-3rrv-vx5w)：恶意 chat_template 在 tokenizer/processor `save_pretrained()` 时写出目录；服务不导出 tokenizer/processor，也不接受用户模型配置。

Full model-lock audits additionally update hydra-core to 1.3.7, urllib3 to 2.8.0 and Werkzeug to 3.1.9. The three exact exceptions above cover attacker-supplied checkpoint indexes, remote custom generation and tokenizer/processor saving. None is exposed by the manifest-only offline service. They are not package fixes; reassess on Qwen releases or changes to model provenance/loading/export, and retain failure for undeclared advisories.
