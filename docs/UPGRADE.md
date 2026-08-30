# 升级指南

## 升级前

停止全部服务并备份 `data/`。这里包含 SQLite 队列、历史输入输出、声音档案和声纹库；不要删除或覆盖它。

```bash
./service.sh stop all
cp -a data "data.backup.$(date +%Y%m%d-%H%M%S)"
git pull --ff-only
./service.sh setup all
./service.sh start all
```

Windows 使用 `service.cmd`，并通过资源管理器或备份工具复制 `data\`。

## 自动迁移与兼容性

- API 启动时自动将 SQLite 迁移到当前 schema v8。v8 为声纹人员新增备注与人名热词开关，并创建只读系统词表“声纹库人名”；既有人员默认加入该词表。若升级前已有同名自定义词表，会保留内容并改名为“声纹库人名（原自定义）”（冲突时追加序号）。迁移是就地操作，因此备份必须在启动新版本前完成。
- 历史 ASR/TTS 任务、旧声音档案和既有声纹样本保持可读；人员名字、备注及开关变化不会回写历史任务或已提交热词快照。
- 浏览器鉴权改用进程内会话 Cookie，升级或重启后需要重新输入 API Key。
- `/api/v1/health` 现在是公开最小探针；原详细结构迁移到受保护的 `/api/v1/system`。监控脚本如依赖硬件、worker、模型或路径字段必须切换端点并增加 Bearer Header。
- `.complete` 必须包含固定模型 revision。旧的空 marker 会在 setup 时被判定为无效并修复。
- TTS 安装现在同时创建独立 aligner 环境；不要复用旧 TTS 环境中的 qwen-asr。
- ASR/TTS worker 现在由监督器管理可重启执行器，`setup all` 会将进程树管理所需的 `psutil` 同步到两个模型环境。启动时会校验并清理可信的遗留执行器元数据，再恢复中断任务。
- ASR/TTS 执行器现在只在同类队列有连续任务时保持热状态；队列排空并默认空闲 60 秒后会安全重建，以归还 VAD、CAM++、TTS CPU checkpoint 和 CUDA context 的进程高水位。可用 `AUDIO_INTEL_EXECUTOR_IDLE_SECONDS` 调整，`0` 表示立即回收。监督器、FIFO、任务状态、API、数据库和浏览器会话均不变。
- Linux `service.sh` 的 `start` 现在将各组件放入独立会话和进程组，记录真实服务 PID，可在普通终端、调用脚本或其进程组退出后继续后台运行；容器或平台按 cgroup 管理生命周期时仍应使用 `run` 前台动作。`restart` 会先预检，再清理旧的完整进程树，停止失败时返回非零且不启动新实例。启动就绪检查、PID 身份校验和目录覆盖行为保持兼容；不涉及 HTTP API、数据库或原生 Windows 行为变更。
- 原生 Windows `service.cmd` 的动作保持不变；`start`/`restart` 现在等待 API 与 worker 真正就绪，`stop` 校验 PID 身份并清理完整进程树。已有 `AUDIO_INTEL_*_DIR` 覆盖也会用于日志和 PID 等生命周期状态，不涉及 HTTP API 或数据库迁移。
- 原生 ASR/TTS API、OpenAI 兼容音频端点和提交页现在默认启用 `accelerate_single_task`。依赖旧版 batch 1 默认行为的客户端必须显式传入 `false`；模型、精度、ASR 分块与说话人语义不变。
- ASR/TTS 新提交默认都使用 GPU；TTS 输出语种默认由 `Chinese` 改为 `Auto`。已有浏览器偏好保持不变，无 GPU 的 API 消费方需显式传 `compute_device=cpu`，依赖固定中文默认值的消费方需显式传 `language=Chinese`。
- 一次性 TTS 克隆参考新增 `/api/v1/tts/clone-references` 分析端点和 `reference_job_id` 提交方式。分析任务会保留在 ASR 任务记录中，旧的 `reference_audio` + `reference_text` 请求继续兼容。
- ASR 页面、声纹样本入库和 Capabilities 现在统一公开 `Auto + 11` 种支持字词级对齐的语言。原生 ASR、OpenAI 转写和声纹入库显式传入清单外语言时由运行期失败或透传改为同步 `422`；`Auto` 检测到其他模型语种时仍成功返回句段级时间戳。
- TTS 新增 `qwen3-tts-1.7b` 模型组，可按任务选择 CustomVoice、Base 或 VoiceDesign checkpoint；默认仍为 0.6B。`setup tts/all` 会下载新增的三个固定 revision。浏览器 TTS 偏好从 v1 自动迁移到包含 `model` 的 v2，旧客户端省略 `model` 时行为不变。
- 原生 `instruct` 和 OpenAI 兼容 `instructions` 现在可用于 1.7B 预置音色；原生 1.7B VoiceDesign 必须提供 `instruct`。0.6B 和 Base 克隆仍拒绝非空指令。没有独立数值语速/音高或公共采样参数；客户端应按 `GET /api/v1/capabilities` 返回的 `tts.model_capabilities[]` 动态显示控制项，而不是只读取代表默认模型的 `tts.controls`。
- TTS GPU 准入与 ASR 一致，0.6B/1.7B 使用 3840/7936 MiB 总显存门槛；Capabilities 与 TTS 结果新增模型组、checkpoint 和指令信息，均为兼容性扩展。此项不涉及数据库迁移。
- **不兼容变更：** 原生异步 ASR、TTS、TTS 克隆参考分析和声纹样本上传现在强制要求 `Idempotency-Key`。现有客户端必须为每次逻辑提交生成 8–128 字符的键，并在超时、断线或 `429` 后复用；相同键改变请求会返回 `409`。`429` 的分类和恢复步骤见 [故障排查](TROUBLESHOOTING.md#api-提交返回-429)。
- **不兼容变更：** `GET /api/v1/jobs` 与全局 `/api/v1/events` 现在只返回任务摘要，不再包含 `request`/`result`。全局 SSE 首帧仍为 `snapshot`，后续改为 `update`（仅变更任务、`removed_job_ids`、当前 worker）和空闲 `heartbeat`；Capabilities 以 `events.global_mode=summary_delta` 标识。依赖列表内完整结果、或把每个全局事件都按完整快照覆盖的客户端，必须改为按增量合并，并在确需详情时读取 `GET /api/v1/jobs/{job_id}`。单任务状态接口和单任务 SSE 仍返回完整契约。此变更不迁移数据库，也不改写历史任务。
- 新增同类队列位置、稳定阶段/细粒度进度、本机历史 ETA 区间、`GET /api/v1/queue`、单任务 SSE 和 ETag 条件轮询。TTS 解码与 ASR 推理的顶层 `progress` 现在会持续变化；`progress_detail.basis=estimated` 时百分比是最佳估算，`activity` 提供当前调用的模型活动。新增响应字段是兼容性扩展，但使用严格反序列化模型的客户端需要先允许这些字段。ETA 是热身后才出现的建议区间，不是 SLA。
- Windows 上的 ASR 子进程进度通信改为不可变编号快照，修复父进程读取进度时覆盖同一路径可能触发的 `PermissionError: [WinError 5]`。进度频率、API 和识别结果不变；TTS 仍直接写入任务进度，不使用该文件通信机制。
- ASR/TTS 页面参数现在分别保存在浏览器 localStorage，并提供页面级“恢复默认配置”；清除站点数据后会恢复默认。ASR 与 TTS 偏好都迁移到包含 `model` 的 v2，未保存模型时仍使用 0.6B。TTS 文本、`instruct`、参考文本和分析引用仅保留在 sessionStorage；热词库未保存草稿同样只在当前标签页会话保留，保存或“取消并清空”后删除。文件和未确认的麦克风录音不会持久化。
- ASR 新增 1.7B 模型选择和热词库。默认仍是 0.6B；旧客户端省略 `model` 和 `hotword_list_ids` 时行为不变。`setup asr/all` 会额外下载固定 revision、当前约 4.4 GiB 的 1.7B 权重。GPU 按标称 4/8 GiB 档位并扣除 256 MiB 报告容差判断，因此 0.6B/1.7B 的实际门槛分别是 3840/7936 MiB，判断口径是报告的总显存而非当前空闲显存。门槛只决定准入，不保证其他 GPU 程序不会造成运行期 OOM。
- Capabilities 新增 `asr.default_model`、`asr.models[]` 和 `asr.hotword_library`，ASR 结果新增模型身份与 `hotword_context`。这些都是兼容性扩展；严格反序列化客户端应先允许新字段，并按 `asr.models[].compute_devices` 判断所选模型，而不是继续使用只代表默认模型的顶层 `asr.compute_devices`。
- 声纹库新增浏览器麦克风录音入口，继续复用现有样本上传 API，不新增数据库字段或迁移。远程普通 HTTP 访问仍可能被浏览器拒绝麦克风权限，可继续使用文件上传。
- Web UI 改进长转写渐进加载、可键盘操作的波形定位、失败任务本地化详情、资源加载失败隔离、模型状态分组和页面导航滚动复位；不改变 API、数据库或浏览器存储生命周期。

## 升级后验证

```bash
./service.sh doctor
curl -fsS http://127.0.0.1:20810/api/v1/health
.runtime/api/bin/python scripts/smoke_test.py
.runtime/api/bin/python -m pytest -q
corepack pnpm@10.15.1 --dir frontend typecheck
```

若真实模型、Torch、精度或设备路由发生变化，还必须执行真实 ASR、TTS 克隆和说话人分离回归，不能仅依赖 mock 测试。
