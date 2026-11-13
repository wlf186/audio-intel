# 依赖维护

## 运行时矩阵

| 环境 | 关键版本 | 用途 |
|---|---|---|
| api | Python 3.12、FastAPI 0.141.1、Uvicorn 0.52.4、pypdf 6.21.4 | API、队列管理、文档解析、测试与前端托管 |
| asr | PyTorch 2.11.0+cu130（full）或 2.11.0+cpu（CPU-only）、qwen-asr 0.0.6、Transformers 4.57.6 | ASR、ForcedAligner、VAD、CPU 人声增强、说话人分离 |
| tts | PyTorch 2.11.0+cu130（full）或 2.11.0+cpu（CPU-only）、qwen-tts 0.1.1、Transformers 4.57.3 | 0.6B/1.7B Base、CustomVoice 与 VoiceDesign 推理 |
| aligner | PyTorch 2.11.0+cu130（full）或 2.11.0+cpu（CPU-only）、qwen-asr 0.0.6、Transformers 4.57.6 | TTS 默认参考截取及手动声纹区间的按需对齐 |

qwen-tts 0.1.1 与 qwen-asr 0.0.6 精确要求不同 Transformers 版本，严禁同环境安装。Torch 2.11 要求 `setuptools<82`，因此模型环境固定到已修复已知旧版公告且满足该约束的 setuptools 81.0.0。

前端固定 pnpm 10.34.5，支持 Node 22.20+，CI 和文档推荐 Node 24 LTS；当前锁定 React 19、TypeScript 5.9、Vite 7、i18next 26 和 react-i18next 17。TypeScript 6、Vite 8、lucide-react 1.x 以及 Torch/Qwen 大版本升级不属于例行补丁，应单独做兼容与真实模型验证。

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

`--check` 以已提交锁为解析基线，验证它们仍与直接依赖一致；不能与 `--upgrade` 同时使用。提交直接清单和两个平台的全部生成锁。CI 会以已提交锁为基线重新编译并拒绝差异。uv 本身固定为 0.13.4，Linux 和 Windows 下载均验证上游 SHA256。

## 安全公告与 VEX

API 锁和前端锁要求审计无已知漏洞。Qwen/Torch 当前精确约束使部分包暂时无法安全提升，以下公告仅在模型运行时审计中显式放行：

- `PYSEC-2026-3447` / `GHSA-h35f-9h28-mq5c`：仅影响 macOS 文件系统上构建 sdist 时的 Unicode 排除规则；项目仅支持 Linux/Windows 运行且运行时不构建发行包。修复版 setuptools 83 又与 Torch 2.11 的 `<82` 约束冲突。
- `PYSEC-2025-194` / `GHSA-rrmf-rvhw-rf47`：`torch.jit.script` 处理攻击者本地脚本的内存破坏路径；服务不接收或编译用户脚本。
- `PYSEC-2026-2288` / `GHSA-69w3-r845-3855`：Trainer 恶意 checkpoint 路径。
- `PYSEC-2026-2289` / `GHSA-29pf-2h5f-8g72`：恶意远程配置/仓库加载路径。
- `PYSEC-2026-2290` / `GHSA-fgcw-684q-jj6r`：LightGlue 模型路径。
- `PYSEC-2025-217` / `CVE-2025-14929`：X-CLIP 恶意 checkpoint 转换路径。

本项目不使用 `torch.jit.script`、Trainer、LightGlue 或 X-CLIP，不接收用户提供的模型、checkpoint、配置或 Python 脚本；模型由受版本控制的 manifest 固定仓库和 revision，生产推理设置 Hugging Face/Transformers 离线模式并只加载项目本地目录。这是有边界的风险接受，不等于公告已修复。

审计以 `dependency-exceptions.json` 为唯一机器可读例外来源，绑定公告、包版本、环境、模型 manifest 校验值与复查日期。新增或重新评估的例外最多有效 30 天；API、前端、工具不增加例外。上述说明与下方逐项 VEX 是证据说明，不能单独让流水线放行。任何未豁免公告均使审计失败。每次 Qwen 包发布时重新检查是否可解除 Transformers 固定版本与对应豁免。


视频转写沿用 API/ASR 已固定的 PyAV 和 NumPy；API 显式依赖 Pillow 13.0.0，ASR/模型环境因 Gradio 约束保留 12.3.0（见下方 VEX）。简洁 PDF 在 API 环境使用 ReportLab 4.5.1 与仓库内 Noto Sans SC 字体，不依赖系统字体或在线服务。浏览器分片 SHA-256 使用 @noble/hashes 2.0.1，在 Web Worker 运行。字体来源与许可证见 [第三方说明](../THIRD_PARTY_NOTICES.md)。模型、Transformers、精度和既有推理批次保持不变。

API 环境固定使用 pypinyin 0.55.0 离线生成声纹人员的拼音排序与索引。姓氏读音覆盖只在排序时应用，不改写姓名、系统热词或历史快照；ASR/TTS/aligner 不安装该依赖。


## v0.1.38 PDF 解析依赖更新 / PDF parser update

v0.1.38 候选 `8103d0dd25b72b8ecb0c982448673e3975949d22` 的[Linux 标签检查](https://github.com/wlf186/audio-intel/actions/runs/39703554657/job/119578150991)通过后端测试后，在依赖审计中报告 pypdf 6.19.0 的五条公告：CVE-2026-113792、CVE-2026-113795、CVE-2026-113793、CVE-2026-113794、CVE-2026-113786。API 固定升级至 [pypdf 6.21.2](https://github.com/py-pdf/pypdf/releases/tag/6.21.2)，重新生成并验证 Linux/Windows、full/CPU-only 全部锁，保留其他包版本。PDF 导入和转写 PDF 导出沿用既有回归；新增 UniGB-UTF16 横排/竖排中文提取用例，旧版会把 `中文转写测试` 读成乱码。模型环境及推理设置不变，无数据迁移或历史文档重算。

The API pins pypdf 6.21.2 after the candidate tag audit reported five advisories against 6.19.0. Regenerate and check all platform/profile locks while preserving unrelated versions. Verify document imports, transcript PDF exports and horizontal/vertical UniGB-UTF16 text extraction, then require fresh main and tag CI for the corrected commit. Historical document snapshots and model environments remain unchanged.

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

2026-11-13 逐条复核新增公告，以下记录同样只适用于固定 Pillow 12.3.0 的12份模型环境锁，复查日期为2026-12-09；API不使用这些例外，依赖版本保持不变：

| 公告 | 本服务中不可达的入口 |
| --- | --- |
| [CVE-2026-128066 / GHSA-wqgw-9xfh-qf2g](https://github.com/python-pillow/Pillow/security/advisories/GHSA-wqgw-9xfh-qf2g) | TIFF/EXIF IFD overlapping-tag parsing through `Image.open()`, `getexif()` or EXIF transpose |
| [CVE-2026-128067 / GHSA-xcvg-w56q-r89m](https://github.com/python-pillow/Pillow/security/advisories/GHSA-xcvg-w56q-r89m) | libtiff TIFF encoding with inherited or caller-supplied geometry/EXIF tags |

视频帧仍由PyAV解码为RGB像素，只写JPEG；编码不传入EXIF、tiffinfo、ICC或comment。边界回归现同时拒绝Pillow图片打开和EXIF解析，并断言写出格式及元数据参数，在API与实际ASR环境执行。ASR/aligner/TTS的音频输入及不启动Gradio的路径不变。这是有限范围的不可达判断，不是修复模型环境中的Pillow；新增图片输入、EXIF操作、TIFF写出或Gradio服务必须重新评估。

The two additional advisories were reviewed individually on 2026-11-13, with review due on 2026-12-09. Exceptions cover only the twelve model-runtime locks pinned to Pillow 12.3.0. API uses patched Pillow 13.0.0. Decoded-pixel video paths write only metadata-free JPEG and do not call Pillow file opening or EXIF parsing; regression coverage exercises those boundaries in the API and actual ASR environments. This is bounded non-reachability, not a package fix; new image/EXIF/TIFF inputs or Gradio serving invalidate it. Undeclared advisories continue to fail the audit.


同轮完整模型锁审计还发现旧间接依赖公告：hydra-core 升至 1.3.7、urllib3 至 2.8.0、Werkzeug 至 3.1.9。Qwen 精确固定的 Accelerate/Transformers 暂保留，以下新增例外仅限固定 manifest、本地离线推理：

- [PYSEC-2026-3804 / GHSA-4j2p-28q2-5m79](https://github.com/advisories/GHSA-4j2p-28q2-5m79)：Accelerate 恶意 checkpoint 索引路径；服务不接收用户 checkpoint 或 shard 索引。
- [PYSEC-2026-4174 / GHSA-x9r9-c232-4q39](https://github.com/advisories/GHSA-x9r9-c232-4q39)：远程 `custom_generate` 文件缓存；服务不接受或调用远程 custom_generate，强制离线加载本地模型。
- [PYSEC-2026-3929 / GHSA-xrqw-3rrv-vx5w](https://github.com/advisories/GHSA-xrqw-3rrv-vx5w)：恶意 chat_template 在 tokenizer/processor `save_pretrained()` 时写出目录；服务不导出 tokenizer/processor，也不接受用户模型配置。

Full model-lock audits additionally update hydra-core to 1.3.7, urllib3 to 2.8.0 and Werkzeug to 3.1.9. The three exact exceptions above cover attacker-supplied checkpoint indexes, remote custom generation and tokenizer/processor saving. None is exposed by the manifest-only offline service. They are not package fixes; reassess on Qwen releases or changes to model provenance/loading/export, and retain failure for undeclared advisories.

## CI 安全巡检与依赖更新

`Dependency security audit` 每周一 04:23 UTC 独立执行，也由现有提交、PR、标签和手动 Linux 检查调用。完整功能回归按原有事件执行，并在每月 1 日 04:43 UTC 于 Linux 和原生 Windows 定时执行。GitHub 排队可能延迟实际启动。Linux 汇总要求 backend、browser、security 全部成功；安全失败不阻断独立的功能诊断。

在已安装 API 运行时且 Node/Corepack 可用的环境执行：

```bash
.runtime/bin/uv run --no-project --python .runtime/api/bin/python --with pip-audit==2.10.1 python scripts/audit_dependencies.py
```

Windows 使用 `.runtime\bin\uv.exe`，并将 `--python` 指向 `.runtime\api\Scripts\python.exe`。无需安装模型或 CUDA wheels：工具只读取全部 14 份 Linux/Windows、full/CPU-only 运行锁，查询 OSV，并检查前端及 uv/pnpm。官方 Torch `+cu130`/`+cpu` 映射到同一上游版本；未知本地版本和遗漏包使检查失败。前端所有等级的未处理公告均失败。

完整结果写入 `tmp/dependency-audit.json`，摘要为同名 `.md`，CI 始终上传报告并保存 30 天。摘要包含 UTC、触发类型、SHA 和受影响范围；别名仅在展示时归并，原始公告保留。各环境独立收集结果，最后汇总判定。查询故障与依赖告警分开记录；超时、429、5xx 最多尝试三次，失败不视为无漏洞。

本轮定向更新 Brotli 1.2.1、filelock 4.2.1、hydra-core 1.3.8、msgpack 1.2.3，以及校验固定的 uv 0.13.4。Torch、Qwen、Transformers、CUDA、模型及推理参数保持固定。后续更新继续使用 `--upgrade-package`，提交所有平台/profile 锁；推理环境变化须完成真实 ASR、TTS、对齐验证。无数据库或历史文件迁移，无需额外复制媒体。仍需依照升级指南完成停服和收尾。

### 本轮新增模型环境 VEX（复查：2026-12-09）

以下结论仅适用于 JSON 中列出的 Pillow 12.3.0 模型环境，API 不适用。每条公告在结构化清单中保留原始 ID 与别名。API 已使用 Pillow 13.0.0。

| 公告 | 未调用的受影响操作 |
| --- | --- |
| [CVE-2026-112390](https://osv.dev/vulnerability/CVE-2026-112390) | MIC/TIFF frame enumeration |
| [CVE-2026-112391](https://osv.dev/vulnerability/CVE-2026-112391) | PDF page-tree parsing |
| [CVE-2026-112392](https://osv.dev/vulnerability/CVE-2026-112392) | rank/median/min/max filtering |
| [CVE-2026-112393](https://osv.dev/vulnerability/CVE-2026-112393) | TIFF encoding to memory |
| [CVE-2026-112394](https://osv.dev/vulnerability/CVE-2026-112394) | P;2L/P;4L planar raw decoding |
| [CVE-2026-112396](https://osv.dev/vulnerability/CVE-2026-112396) | TIFF-tag encoding error cleanup |
| [CVE-2026-112398](https://osv.dev/vulnerability/CVE-2026-112398) | FlashPix JPEG tile decoding |
| [CVE-2026-112399](https://osv.dev/vulnerability/CVE-2026-112399) | APNG background disposal |
| [CVE-2026-112401](https://osv.dev/vulnerability/CVE-2026-112401) | GIF comment decoding |
| [CVE-2026-112402](https://osv.dev/vulnerability/CVE-2026-112402) | NEAREST pure-scale/translation Image.transform error path |
| [CVE-2026-112405](https://osv.dev/vulnerability/CVE-2026-112405) | Image.merge with integer/float first bands or IPTC decoding |
| [CVE-2026-112406](https://osv.dev/vulnerability/CVE-2026-112406) | PDF page-tree rewriting |
| [CVE-2026-112891](https://osv.dev/vulnerability/CVE-2026-112891) | GIF comment encoding |
| [CVE-2026-112892](https://osv.dev/vulnerability/CVE-2026-112892) | MSP RLE decoding |
| [CVE-2026-112893](https://osv.dev/vulnerability/CVE-2026-112893) | FreeType glyph stroking |
| [CVE-2026-112894](https://osv.dev/vulnerability/CVE-2026-112894) | multi-page PDF encoding |
| [CVE-2026-112898](https://osv.dev/vulnerability/CVE-2026-112898) | quantization with an empty palette |
| [CVE-2026-112900](https://osv.dev/vulnerability/CVE-2026-112900) | EPS/Ghostscript invocation |
| [CVE-2026-112901](https://osv.dev/vulnerability/CVE-2026-112901) | XBM header parsing |
| [CVE-2026-112902](https://osv.dev/vulnerability/CVE-2026-112902) | TIFF-tag encoding |
| [CVE-2026-114501](https://osv.dev/vulnerability/CVE-2026-114501) | Arrow buffer export with PILLOW_ALIGNMENT |
| [CVE-2026-96636](https://osv.dev/vulnerability/CVE-2026-96636) | MPO/DCX frame seeking |
| [CVE-2026-96637](https://osv.dev/vulnerability/CVE-2026-96637) | SPIDER frame seeking |
| [CVE-2026-96638](https://osv.dev/vulnerability/CVE-2026-96638) | BMP RLE decoding |
| [CVE-2026-96639](https://osv.dev/vulnerability/CVE-2026-96639) | multi-frame PSD/MPO/DCX/MIC decoding |
| [CVE-2026-96640](https://osv.dev/vulnerability/CVE-2026-96640) | PDF append page-tree traversal |
| [CVE-2026-96641](https://osv.dev/vulnerability/CVE-2026-96641) | PILfont negative-advance text measurement |
| [CVE-2026-96642](https://osv.dev/vulnerability/CVE-2026-96642) | TarIO archive member traversal |
| [CVE-2026-96643](https://osv.dev/vulnerability/CVE-2026-96643) | EXIF header parsing |
| [CVE-2026-96644](https://osv.dev/vulnerability/CVE-2026-96644) | WMF/EMF rendering on Windows |
| [CVE-2026-96778](https://osv.dev/vulnerability/CVE-2026-96778) | JPEG comment encoding after ICC metadata fills the buffer |
| [GHSA-78g4-xqwv-q3g8](https://osv.dev/vulnerability/GHSA-78g4-xqwv-q3g8) | JPEG2000 decoding through bundled libopenjp2 |

视频帧只来自 PyAV 的已解码 RGB 数组；缩放使用 BICUBIC/LANCZOS，JPEG 写出不携带 ICC/comment 元数据。没有受影响的 NEAREST 纯缩放/平移 transform、字体绘制、Arrow 导出或表中解码、过滤和格式操作。新增测试约束这些输入与编码边界，并在真实 ASR 环境执行。Qwen 的音频输入及不启动 Gradio 的边界继续适用；增加图片输入或相关调用立即使本评估失效。

`GHSA-xmj3-9gf7-h52w` 仅豁免模型环境 setuptools 81.0.0：Windows 旧式 `Wheel.install_as_egg` 安装器路径不被服务或 bootstrap 调用，两平台均使用独立 uv 安装带哈希的锁，服务不接收或安装用户 wheel。Torch 的 `<82` 约束继续保留；不是对此漏洞的修复声明。pnpm 工具更新至 10.34.5，工具不使用豁免。
