# 故障排查

以下命令默认使用 Linux 的 `service.sh`。Windows 11 原生用户将其替换为 `service.cmd`，并优先查看 [Windows 专项排障](WINDOWS.md#5-常见问题)。

## 快速诊断

```bash
./service.sh doctor
./service.sh status
./service.sh logs all
BASE_URL=${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}
curl -v "$BASE_URL/api/v1/health"
```

若配置了 HTTPS，探针地址也必须改为 `https://...`。`ERR_SSL_PROTOCOL_ERROR` 通常表示浏览器使用 HTTPS 访问了仍以 HTTP 启动的端口；运行 `./service.sh status` 或 `service.cmd status` 查看实际协议。证书名称错误通常表示当前 IP 不在证书 SAN 中，请重新执行 `tls enable --host 当前IP` 更新服务器证书并保持 HTTPS profile。

`service.sh` 和 `service.cmd` 自动读取仓库根目录 `.env`，不自动读取 `.env.local-deploy`。外部已导出的环境变量（包括空值）优先于文件；若改文件后端口没有变化，先查看 `status` 的配置来源、`configured listener` 与实际端点，并检查旧终端是否保留同名变量。`start` 不替换运行中的组件，应用修改需 `restart`。`20810` 是默认端口，客户端探针需另设实际 `AUDIO_INTEL_BASE_URL`；服务不会把 `.env` 导出回客户端 shell。

`tls enable` 创建的 `<AUDIO_INTEL_DATA_DIR>/tls/service-profile.json` 会自动加载。运行 `tls status` 查看保存模式、证书和实际端点；环境或 `.env` 中显式的协议/证书配置优先于 profile。只残留 TLS 文件但协议是 HTTP 时仍会被预检拒绝。配置语法错误会报告文件和行号；修复后重试，或在外部设置 `AUDIO_INTEL_LOAD_ENV=0` 跳过文件排查，停止服务时须同时恢复原 `AUDIO_INTEL_RUN_DIR`。

若已启用 API Key，公开 `/health` 只返回最小状态。详细诊断使用：

```bash
curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" "$BASE_URL/api/v1/system"
```

## 下载缓慢或中断

确认代理已写入 `.env`，或导出到执行 `setup` 的同一个 shell：

```bash
env | grep -i _proxy
curl -I https://huggingface.co
curl -I https://modelscope.cn
./service.sh setup all
```

- 若 CPU-only 部署显式请求 GPU，API 返回 `503 gpu_runtime_not_installed`；这不是显卡探测失败。需要 GPU 时停止服务、处理非终态任务，然后运行 `./service.sh setup all --profile full`（Windows 使用 `service.cmd`）。
- 若启动提示 inference runtime profile mismatch，说明 `.runtime/deployment-profile` 与某个 Torch 环境不一致；不要手动混装依赖，重新执行 `setup all --profile <当前所需配置>`。

安装脚本会对首次 pnpm 获取自动重试三次，Hugging Face／ModelScope 模型下载支持续传；GTCRN-DNS3 通过 `raw.githubusercontent.com` 完整下载并校验，中断或校验失败需重新下载，代理需覆盖该域名。模型目录只有成功后才会写入 `.complete` revision 标记。不要手工创建标记。若单个模型确认损坏，将对应 `models/<模型名>` 目录移动到备份位置后重新执行 `setup asr` 或 `setup tts`。

## 人声增强不可用或效果不合适

查看 `GET /api/v1/capabilities` 的 `asr.audio_enhancement.available` 和任务详情的
`audio_enhancement.status/reason`。模型缺失、revision/checksum 不匹配或资源不足时，
自动模式使用原音并记录回退，基础 ASR 仍可用；按维护流程停止服务后执行
`setup asr`（Windows 使用 `service.cmd setup asr`）修复模型，不手工填写 `.complete`。
CPU-only 配置同样支持增强。取消任务仍等待完整进程树退出。

背景中的其他人声或较强混响可能保留；若增强不适合某段录音，新提交时关闭“人声增强”
或传 `audio_enhancement=off`。历史任务的重试保留原模式，改模式需新提交。
结果默认播放原音，只有实际增强时才提供增强音轨。参见[人声增强指南](ASR_ENHANCEMENT.md)。

Check capability availability and the per-job status/reason. An unavailable or
invalid enhancer falls back to original audio without disabling base ASR. Repair
models with `setup asr` while stopped, following the maintenance workflow. CPU-only
deployments support enhancement too. Other speakers/reverberation may remain;
submit a new job with `off` when preferred. Retry preserves the persisted mode.

## GPU 不可用或显存不足

- `nvidia-smi` 必须成功，驱动需兼容安装的 PyTorch CUDA 13.0 wheel。
- ASR 与 TTS GPU 资格都按所选模型和 `nvidia-smi` 报告的**总显存**判断，不读取当前空闲显存。0.6B/1.7B 的门槛分别是 3840/7936 MiB；例如 4096 MiB 可选择 0.6B，8151 MiB 可选择 1.7B。
- 查询受保护的 `GET /api/v1/capabilities`，查看 `asr.models[].compute_devices` 或 `tts.model_capabilities[].compute_devices` 中的 `available`、`minimum_memory_mib`、`total_memory_mib` 和 `unavailable_reason_code`。两个顶层 `compute_devices` 兼容字段都只代表默认 0.6B 模型。
- `gpu_unavailable` 表示没有兼容 GPU，`insufficient_gpu_memory` 表示所选模型未达到总显存门槛，`asr_model_unavailable` / `tts_model_unavailable` 表示固定 revision 未完整安装。显式 API GPU 请求返回 `503`，不会静默回退；前端会显示原因并按 CPU 创建本次任务。
- 总显存达到门槛只表示允许尝试，不保证其他 GPU 程序、WDDM 或遗留 CUDA 上下文不会耗尽当前空闲显存；0.6B TTS 已在干净的 4 GiB GPU 上直接加载验证通过，batch 1 仍失败时应先排查当前占用或改用 CPU。
- 系统页会分别显示设备范围 GPU 当前已用、当前空闲和“系统保留估算”。估算值按 `max(total-used-free, 0)` 计算，通常对应驱动或固件保留，不提供进程归属，也不等同于应用残留；“当前已用”则可能同时包含本服务、其他应用和图形上下文。
- 终态 CUDA OOM 会把加载前、失败时的显存和 `nvidia-smi` 可见计算进程写入任务 `error.log`，随后完整退出并重建该执行器。失败任务不会自动改用 CPU 或自动重试；关闭占用 GPU 的外部程序后重新提交即可。
- ASR、Aligner 与 TTS GPU 任务由全局锁串行运行。单任务加速开启时，ASR/TTS 按硬件和模型保守分档批处理；1.7B 会在硬件档位基础上降低两个批次档位。TTS decoder 仍按文本块顺序执行，关闭加速时固定为 batch 1。
- “单任务加速”默认开启；推理批次 OOM 会按 `16→12→8→6→4→2→1` 回退并重试当前批次，batch 1 或模型加载仍 OOM 才属于上述终态 OOM。排障或对照 batch 1 时可在页面关闭，API 客户端则显式传入 `accelerate_single_task=false`。完成任务可在结果 JSON 的 `acceleration.target_batch_size`、`stage_target_batch_sizes`、`stage_batch_sizes`、`batch_penalty_steps` 和 `oom_fallbacks` 查看实际选择。
- 若错误显示 GPU 几乎无空闲、但当前任务的 PyTorch 或进程占用仍很小，说明空间主要被其它或遗留上下文占用，不是当前模型稳态本身超限；结合错误日志中的前后快照和可见 PID 排查。项目 GPU 锁只约束本服务的协作任务，不能阻止外部 CUDA 程序。
- OOM 后先确认没有其他 GPU 程序，再重试任务；不要同时启动另一套模型服务。

若要判断开关在当前机器和素材上的实际收益，至少执行三组交替基准；短音频或单句文本只有一个块时通常没有明显收益：

```bash
.runtime/api/bin/python scripts/benchmark_single_task_acceleration.py asr --model qwen3-asr-0.6b --device gpu --audio meeting.wav --repeat 3
.runtime/api/bin/python scripts/benchmark_single_task_acceleration.py asr --model qwen3-asr-1.7b --device cpu --audio meeting.wav --repeat 3
.runtime/api/bin/python scripts/benchmark_single_task_acceleration.py tts --model qwen3-tts-1.7b --device cpu --repeat 3
```

## 热词未生效或提交返回 422

- 0.6B 和 1.7B 使用相同热词接口。热词会作为 Qwen ASR 的上下文提示，不是强制词典；音质、发音、上下文歧义和词表噪声仍会影响结果。优先加入专有名词、项目代号和容易混淆的短语，避免把通用高频词全部加入。
- 单个词表最多 200 个词条，最多保存 100 个词表；一次任务最多选择 8 个词表、合并后最多 500 个唯一词条，自动生成的 `Vocabulary: ...` 段最多 8000 字符。具体值以 `/api/v1/capabilities` 的 `asr.hotword_library` 为准。
- “声纹库人名（全名）”和“声纹库人名（去姓）”是额外的系统只读词表，不占 100 个自定义词表配额。两表均只同步已开启“加入热词库”的人员；去姓表仅生成可靠提取的两字中文名或英文首名，单字名、格式模糊的姓名会跳过。两表仍需在 ASR 中手动选择；为空或合并后超限时不能用于提交。
- 名称和词条会执行 NFKC、首尾/重复空白规范化及大小写无关去重；空词条被移除，单个词条最长 64 字符。未知词表 ID、选择过多或合并超限返回 `422`。
- 原生 ASR 的 `context`、OpenAI 兼容转写的 `prompt` 会放在自动生成的 Vocabulary 段之前。留空 `hotword_list_ids` 只表示不使用已保存词表，不会清除显式 `context`/`prompt`。
- 提交时会把词表内容写入 `request.hotword_lists` 快照，结果的 `hotword_context` 返回所用 ID、名称和去重词数。后续编辑或删除词表不会改变历史任务或相同幂等请求的重放。
- 热词仅用于普通 ASR 和 `/v1/audio/transcriptions`；TTS 克隆参考分析与声纹样本入库不会启用热词。

## TTS 提交因 instruct 或 instructions 返回 422

- 消费方应读取受保护的 `GET /api/v1/capabilities`，按所选 `tts.model_capabilities[]` 的 `voice_modes` 和 `controls` 决定显示及发送字段；顶层 `tts.controls` 只代表默认 0.6B。
- 0.6B 所有模式、以及 1.7B Base 克隆模式都必须省略 `instruct`。1.7B CustomVoice 预置音色可以选填自然语言指令，1.7B VoiceDesign 必须填写。选错组合分别返回 `unsupported_tts_control`、`unsupported_tts_voice_mode` 或 `tts_instruction_required`。
- 指令用于综合描述声线、语速、音调、韵律和情绪；API 没有独立数值 `speed`/`pitch`，也不接受底层采样参数。OpenAI 兼容 `instructions` 只支持 1.7B 预置音色，VoiceDesign 使用原生异步接口。

## API 提交返回 429

- `submission_concurrency_limited` 表示同时落盘的提交过多，通常按 `Retry-After` 等待约 1 秒即可。
- `queue_capacity_reached` 表示对应 ASR 或 TTS 队列已达到配置上限；等待前方任务完成，或在评估机器容量后调整 `AUDIO_INTEL_MAX_QUEUED_ASR` / `AUDIO_INTEL_MAX_QUEUED_TTS`。
- `insufficient_queue_storage` 表示接收本次输入后会低于 `AUDIO_INTEL_MIN_FREE_DISK_BYTES`；清理磁盘或调整保留值前先确认不会挤占模型、数据库和结果空间。
- 查看受保护的 `GET /api/v1/queue` 可获得当前排队数、准入预留、并行提交和磁盘余量，但它只是预检；最终以提交接口的原子准入结果为准。
- 超时、断线或 `429` 后必须复用原 `Idempotency-Key`；不要为同一次逻辑提交生成新键，否则可能创建重复任务。

## ETA 尚未出现或 SSE 断线

- ETA 只使用本机相同模型、设备、模式和相近任务特征的历史任务；ASR 与 TTS 的 0.6B/1.7B 都分别热身。少于 5 个有效样本时 `estimate.state` 为 `warming_up`，切换模型后暂时没有剩余时间属于预期行为；可用后的区间也只是建议，不是 SLA。
- 单任务 SSE 使用 `/api/v1/jobs/{job_id}/events`。全局 `/api/v1/events` 连接后发送一次任务摘要 `snapshot`，随后只发送语义 `update` 和轻量 `heartbeat`；两者都没有历史重放，断线后以首帧或任务状态接口校准。
- 无法使用 SSE 时按响应中的 `poll_after_seconds` 轮询 `status_url`，保存响应 `ETag` 并在后续请求发送 `If-None-Match`；`304` 表示任务和同类队列上下文未变化。

## 推理进度显示“估算”或短暂停顿

- `progress` 保证不会倒退，但不是模型承诺的精确剩余比例。`model_load` 只报告模型加载的开始和完成边界；底层阻塞加载期间可能没有更新，服务不会伪造百分比或心跳。TTS codec 帧总量和 ASR 输出 token 总量只能在完成前估算，因此 `progress_detail.basis=estimated` 属于正常状态。
- `progress_detail.current/total` 表示已确认完成的文本或音频分块；`activity` 表示当前推理调用内部的模型活动。`activity.sequence` 在新批次或 OOM 降批重试时递增，消费方应按整个任务快照替换显示，不要自行累计。
- 模型实际产生细粒度活动时，服务最多约每 0.5 秒持久化一次；这不是固定心跳，极短任务或阻塞调用可能只显示阶段边界。GPU 同步、音频编码、SSE/轮询传输也可能造成短暂停顿；任务完成时会以确认值收敛到 100%。
- 需要更及时的页面/API 更新时优先使用单任务 SSE；轮询客户端应遵循 `poll_after_seconds`，不应为追求动画效果高频请求数据库。

若仅打开首页也出现异常下行流量，先检查浏览器 Network 中 `/api/v1/events`：空闲 30 秒内不应重复出现 `snapshot`/`update`，只应有约两条 `{}` 心跳。列表和全局事件都不应包含 `request`/`result`；完整结果只应在打开成功任务时由 `/api/v1/jobs/{job_id}` 获取一次。若这些约束失效，应视为契约回归，而不是调大心跳间隔掩盖。
- 浏览器原生 `EventSource` 不能附加自定义 Authorization Header。项目同源页面使用 HttpOnly 会话 Cookie；外部浏览器应用应通过同源后端代理，服务端客户端可直接发送 Bearer Header。

## 点击处理后停在上传或创建任务

- “准备上传”表示浏览器正在建立请求；视频续传的“正在校验已上传内容”表示逐块校验本地文件，显示已校验字节与进度，不重新传输已确认数据，可暂停后继续；“正在上传”及字节百分比表示音频仍在客户端到服务端的传输途中；“上传完成，正在创建任务”表示文件已传完，服务端正在校验、持久化并写入队列，不是推理进度。
- 在“创建任务”前可点击“取消上传”。页面会保留已选择的文件或麦克风录音，并用同一个幂等键重新上传，避免一次网络重试创建重复任务；进入服务端创建阶段后不再提供客户端取消按钮。
- 页面从 `/api/v1/capabilities` 读取上传上限并提前拒绝明显过大的文件。若没有百分比、长时间停在准备阶段或显示连接错误，检查浏览器 Network、反向代理请求体限制、局域网带宽和 `logs/api.error.log`，然后使用保留的文件重试。
- 任务创建成功后，页面会切换到任务记录和队列进度；上传百分比不会伪装成 ASR/TTS 推理百分比。

## 执行中任务取消后仍显示正在停止

- worker 会先给当前阶段 1 秒协作退出时间，必要时终止该任务的执行进程及其 ASR、Aligner 等后代进程；确认进程全部退出后才把任务标记为“已取消”并开放删除。
- 默认应在 3 秒内完成安全停止。任务记录显示“正在安全停止”期间不要直接删除 `data/` 或 `tmp/` 文件；终态出现后可重试或永久删除。
- 停止失败时任务会保持 `cancelling` 并继续重试，避免残留进程仍在写文件。查看 `./service.sh logs all`，并用 `nvidia-smi` 确认是否存在项目外的 GPU 进程。
- `AUDIO_INTEL_CANCEL_GRACE_SECONDS` 可调整协作退出窗口；降低它会更早强制停止，通常保持默认值 `1` 即可。

## ASR 在收尾阶段失败或出现 WorkerProcessExit

超长录音可能在旧版 88% 收尾阶段因波形预览将全部采样转成 Python 列表而耗尽主机内存。v0.1.43 改为固定块统计，保留旧峰值和分箱规则；它控制的是波形附加内存，解码数组和模型仍占用内存。可捕获的波形异常降级为空波形并记录警告。

新发生的执行器异常保留 `WorkerProcessExit`，错误信息包含可用退出码、POSIX 信号或 Windows 状态。任务目录的 `diagnostics/attempt-N-executor-exit.json` 和 `error.log` 记录最后阶段、观察到的执行器 RSS 高水位、最近系统可用内存及可用的 cgroup OOM 计数变化；采样不是精确峰值。SIGKILL 本身不能证明 OOM，计数也可能来自同组其他进程；不可采集时记录未知。诊断是尽力写入，不阻塞队列恢复。

历史失败记录不补写原因或自动重试。原输入仍保留时可明确重试，或以新幂等键提交独立验证任务。重试仍完整执行原请求，没有 ASR 阶段断点恢复。

English: ASR waveform reduction has bounded extra memory since v0.1.43. Decoding and models still use memory. Exit diagnostics retain the stable WorkerProcessExit code and provide platform-aware status, observed RSS and best-effort cgroup evidence. SIGKILL or a shared OOM counter alone cannot prove the cause. Historical failures are unchanged; explicitly retry or submit a separate job. ASR does not resume partial stages.

## 任务完成后内存没有立即下降

- ASR/TTS 执行器会在连续任务和短暂突发之间复用。只有同类队列已经排空时才开始空闲计时；默认 60 秒内出现新任务会取消计时并继续复用，不会为了回收内存跳过或延迟已排队任务。
- TTS CPU 会在该窗口内保留当前 checkpoint；切换 checkpoint 或改用 GPU 时先清理旧 CPU 模型。TTS GPU checkpoint 逐任务释放，但 CUDA context 会随空闲执行器一起回收。ASR 的主模型和 ForcedAligner 逐阶段退出，空闲回收主要归还 VAD、CAM++ 和科学计算库的进程高水位。
- 空闲窗口结束后，`run/asr-executor.json` 或 `run/tts-executor.json` 中的 PID 应变化，旧 PID 及其后代必须全部退出；监督器 PID、排队任务、API 和浏览器会话保持不变。新执行器未处理任务时不会被周期性重复重建。
- `AUDIO_INTEL_EXECUTOR_IDLE_SECONDS` 可调整窗口，设为 `0` 表示队列排空后立即回收；修改环境后需按当前部署模式重启服务。若持续空闲后 PID 未变化，查看 `./service.sh logs all`，不要手工杀死执行器或删除 `run/` 元数据。

## 多人会议被合并为同一说话人

- 支持 ForcedAligner 的语言会按字词时间戳把长 ASR 块重新切成说话人轮次；选择“仅句级时间戳”也会在内部完成对齐，不会返回字词明细。
- 说话人数已知时请在提交页选择准确人数，短录音和短促交互会更稳定。自动人数会复核仅出现一两轮、且疑似由稳定说话人拆出的簇；证据不足时保留独立说话人，仍可能受音色相似、噪声和混响影响。
- 当前是 single-active-speaker 模式，真正同时说话的区间只会标记一位主导说话人。
- 可停止服务后运行本地合成回归；生成音频和报告只写入已忽略的 `tmp/`：

```bash
.runtime/tts/bin/python scripts/benchmark_diarization.py synthesize --compute-device gpu
.runtime/asr/bin/python scripts/benchmark_diarization.py evaluate --compute-device gpu
```

基准包含快速三人对话、单人/双人对照、重叠语音，以及真实第四人只发言一次的两个反例；后两项用于防止自动复核过度合并真实参与者。

## 转写人员重复、合并后 ID 变化或重命名保存失败

- 在“任务与结果”中重命名说话人时，可手动填写姓名、备注，也可从声纹库选已有人员后继续编辑。人员列表加载失败可重试，期间仍能手动保存；选中无备注人员会清空原备注。姓名完整保留，备注最多 20 字。
- 自动声纹匹配允许多个识别簇命中同一人员，新结果会合并规范化姓名和备注都相同的说话人。已有历史任务只在显式保存时合并；不同真实人员需要不同姓名或备注，同名但不同备注保持独立。默认未命名 Speaker 不自动合并。
- 合并保留结果列表中的第一个说话人 ID，随后编辑会作用于整组段落，不能通过再次改名拆开。API 客户端应刷新保存响应中的说话人列表；继续提交已移除的 ID 会返回 `404`。段落 ID、文字和时间戳保持不变。
- 选择库人员仅复制当前姓名、备注，后续库内改名不更新任务。任务当前显示名优先于旧声纹匹配快照，匹配分数仍是代表记录的原始分数。
- 保存返回 `503` 时保留输入并重试，检查磁盘空间、文件权限和 `logs/api.error.log`；导出文件暂时被占用也可能导致失败。任务删除冲突返回 `409`。JSON/TXT/SRT/VTT 导出随成功保存同步更新，声纹库与播放音频不随任务改名更新。详见 [API](API.md#asr-speaker-names-and-notes)。

## 前端或服务无法访问

- `corepack pnpm@10.15.1 --dir frontend build` 可重建前端。
- 页脚 `NET_LISTEN` 和受保护的 `/api/v1/system` 中 `bind` 字段会显示服务实际配置的监听地址与端口；`0.0.0.0` 表示监听所有网卡，不等同于必须通过该地址访问。
- 默认端口可用 `ss -ltnp | grep 20810` 检查占用；若设置了 `AUDIO_INTEL_PORT`，请改查实际端口。
- 查看 `logs/api.log`、`logs/asr.log`、`logs/tts.log`；PID 位于 `run/`。
- 由 `start all` 启动的后台服务在配置改变后使用 `./service.sh restart all`。
- `restart` 会先执行启动预检，再停止目标的完整进程树；任一组件未能完整停止时命令返回非零，不会继续启动新实例。应先查看对应日志和残留 PID，不要反复执行重启。

`start` 会为 API、ASR 和 TTS 分别创建独立会话和进程组，因此普通终端关闭、调用脚本退出或上游仅清理启动命令所在进程组后，服务应继续运行。若 `start`/`restart` 已显示成功，但命令结束后服务仍立即消失并由上游返回 `502`，通常是容器、systemd 或远程执行平台回收了整个 cgroup，而不是监听地址从 `0.0.0.0` 被修改。独立会话不能逃逸 cgroup 生命周期；此类环境应让 `./service.sh run all` 持续作为前台主进程，并由平台负责重启。

rootless 本身不会导致该问题。确认当前 UID 对 `.env.example` 中配置的数据、缓存、日志和运行目录可写即可。不要为此在容器内加入 systemd；重启策略应交给容器运行时。

前台模式不要从另一个终端执行 `restart all`：该命令会停止 `run all` 管理的子进程，前台管理进程随即退出；若运行在受管 cgroup 中，随后启动的后台进程仍属于该 cgroup，也可能被平台回收。交互终端应在原窗口按 `Ctrl+C`，确认退出后重新执行 `./service.sh run all`；Docker、Podman 或编排环境应直接重启容器，让运行时发送停止信号并重新执行入口命令。

## 浏览器麦克风无法录音

- 浏览器麦克风要求安全上下文。本机请使用 `http://localhost:<端口>` 或 `http://127.0.0.1:<端口>`；通过局域网 IP 远程访问时，使用 `./service.sh tls enable` 配置项目直连 HTTPS，或在外层反向代理终止 TLS。命令会自动收集活动地址，也可用 `--host 当前IP` 明确追加。普通 HTTP 通常会被浏览器拒绝。
- 直连 HTTPS 时先查看 `./service.sh status` 确认实际协议，再从登录页或全局右上角“HTTPS 证书”安装根 CA 并核对 `tls fingerprint`。若首次连接完全无法打开，可通过可信文件传输复制 `data/tls/audio-intel-root-ca.cer` 到客户端安装。
- 点击“开始录音”后，在浏览器站点权限和操作系统隐私设置中允许麦克风。若提示设备占用，请关闭会议、录音或语音聊天程序后重试。
- 每次最长录制 30 秒，停止后先试听或重录，再确认创建声纹入库任务。未确认的录音只存在页面内存中，切换人员或离开页面会释放麦克风并清除暂存录音。
- 浏览器不支持 MediaRecorder、权限仍不可用或需要远程 HTTP 访问时，切回“上传文件”即可继续入库。

## 浏览器配置或编辑草稿没有保持

- ASR 与 TTS 参数分别保存在版本化的 `localStorage` 项中，互不覆盖；ASR 热词表勾选会在刷新、切页、重开浏览器和成功提交后继续沿用。每个页面的“恢复默认配置”只重置本页参数，恢复 ASR 默认配置也会清空记忆的热词选择。
- TTS 合成文本、表达指令、参考文本和分析引用只在当前 `sessionStorage` 会话保留，选择的音频文件不会进入浏览器存储。
- 热词库未保存的场景名称和词条也保存在当前标签页的 `sessionStorage` 中；切换页面或刷新后可恢复，保存词表会删除草稿。打开其他词表（含系统词表）或“取消并清空”如会覆盖未保存内容，会先要求确认；取消确认继续保留草稿。草稿仍只在当前标签页会话内保存，存储寿命不变。热词固定按回车分隔，逗号和分号会保留在词条中。
- 清除浏览器站点数据、对应 localStorage 或 sessionStorage 项后会恢复默认值；仅清理图片、脚本等 HTTP 缓存通常不会删除参数偏好或草稿。
- 原始 API Key 从不进入 localStorage、sessionStorage 或 URL；参数记忆不会改变下方浏览器鉴权的安全行为。

## 重建项目内运行时

停止服务后，只移动出现问题的环境并重新安装，避免触碰 `data/` 和 `models/`：

```bash
./service.sh stop all
mv .runtime/api .runtime/api.broken
./service.sh setup api
./service.sh start all
```

ASR/TTS 环境同理。TTS 的超长克隆参考对齐失败时，还需一起重建 `.runtime/aligner`，最简单的做法是重新执行 `./service.sh setup tts`。确认恢复后再自行删除 `.broken` 目录。

## API Key 页面反复要求登录

- 浏览器会话只保存在 API 进程内，服务重启后重新输入 Key 属于预期行为。
- 页面不会在 `sessionStorage`、`localStorage` 或 URL 中保存原始 Key；不要自行把 Key 拼到媒体 URL。
- 反向代理必须让页面、API 和音频文件保持同源，并正确传递 Cookie、Range、Origin 与 HTTPS scheme。
- CLI 不使用浏览器 Cookie，继续发送 `Authorization: Bearer ...`。

## ASR 新建转写与任务结果

ASR 工作台使用固定的“新建音频转写 / 新建视频转写 / 任务与结果”页签。上传及参数设置位于新建页，历史任务、进度、转写正文和下载位于结果页；提交后可点击“查看本次任务”定位这次上传。后台任务完成不会替换正在阅读的其他结果，离开结果页会暂停播放。

切换这三个页签保留已选文件、参数，以及当前任务的搜索、筛选和阅读位置。文件只存在页面内存中，不写入浏览器存储；刷新或离开 ASR 页面后需重新选择。成功提交会清空文件，上传失败或取消则保留文件。参数仍使用原有 localStorage 保存周期。

热词表通过“选择热词表”弹窗搜索和勾选，勾选立即保存。热词加载失败时可以在弹窗内重试；若已有热词选择，需加载成功或明确清空选择后再提交。说话人重命名及声纹入库失败会保留在对应弹窗中，修正或重试即可。

## TTS 结果或波形没有显示

文本、文档和序列音频统一在 TTS 的“任务与结果”页签查看。提交后点击“查看本次任务”，或从结果页任务列表选择；更早的任务可从全局任务记录打开。排队、运行、失败或取消的任务显示自身状态，不会显示其他任务的音频。

波形与音频播放独立加载。历史音频首次打开可能需要补算波形；出现波形加载失败时可重试，429 按提示等待后再试，期间仍可播放或下载。切换章节时等待当前章节波形，不应沿用上一章图形。

如果播放时间不前进或浏览器报告媒体错误，先下载该音频检查能否播放，并核对媒体请求是否成功（200 或 Range 的 206）。如果时间前进但听不到声音，检查播放器音量、浏览器标签页静音和系统输出设备。仅缺少波形不表示音频静音；排障时提供任务 ID、浏览器和下载文件的播放结果。

## TTS 音频异常、局部重试与参考区间

- “正在重试异常语音”表示当前内部文本块未自然结束或产生无效波形，最多额外重试 3 次。“已自动恢复 N 处”统计恢复的原始块数，不是重新生成的整章数。文档资源故障的 30/120 秒恢复等待另行计数；不要把局部重试看作整项任务反复排队。
- 耗尽后任务失败，`error_code=TtsGenerationGuardError`。文档已完成段保留，未完成段临时文件清理；手动重试复用校验通过的已完成段并启动新的尝试预算。有序序列仍重跑完整保存请求。
- 检查任务详情/结果中的可选 `generation_guard`，以及任务目录 `diagnostics/tts-generation/<attempt>/` 下的逐块日志和失败诊断。记录实际生成步数、预算、自然结束状态、参考/文本哈希及模型 revision。日志是本地任务数据，不应公开原始参考或私人正文。旧结果缺少统计不表示通过检查。
- 8192 是生成步数上限；页面的目标分段字数与模型内部文本块不是同一单位。正常结束的输出仍可能有重复、专名误读或噪音。遇到这些情况，记录章节和时间区间，对照原文、参考文字及音频；不能仅凭 EOS 或自动恢复计数判断全部音质正常，也不要将正常的长句一概视为异常。
- 默认参考最多取开头 15 秒；声纹库样本可手动选择任意位置的 3–30 秒。“区间过短”也可能来自向内对齐完整词后不足 3 秒，应扩大选区。缺失/无效时间戳会在独立 aligner 中重新计算一次；仍失败时核对样本文字、语种和 aligner 安装，不会自动换成其他区间。
- “原文换行”中的短行通常来自提取正文；“连续阅读”只改变显示，不修改合成输入。结构模式的目标字数仅为备用值，完整结构章节可以超过目标长度。
- VLC 等播放器按专辑归类依赖 MP3 标签。新文档任务的单段、ZIP 和完整 MP3 使用相同专辑；时间采用 UTC，曲序按实际选择的分段顺序。旧下载需重新下载才能获得补写后的标签，旧任务不会随升级自动补写。完整 MP3 在点击下载后流式重封装，不预先占用一份完整音频空间。

## 视频续传、分段编辑和 PDF

- “正在校验已上传内容”正在计算已确认部分的 SHA-256，磁盘较慢或文件较大时可能耗时；看校验字节增长，不要将其误判为网络上传卡住。可暂停，重新选择同一文件继续。410/404 表示上传已过期或被清理，清理浏览器记录后重新上传。
- 图文阅读先点击“编辑分段”才显示截图、边界、拆分、合并和恢复操作。弹窗提示分段已更新时，本次输入尚未保存；“放弃本次编辑并重新加载”会丢弃输入，之后按最新分段重做修改。
- PDF 429 是生成资源繁忙，等待提示时间后重新点击导出；413 是输入超过导出上限，拆分源音视频重新转写，音频也可下载明细/字幕；401 重新登录；网络失败只重试导出即可。
- 命令行使用同一源文件、同一 `--state` 和 `--pdf` 重跑，会恢复原任务与下载。不要为了网络重试删除状态文件或生成新幂等键。任务失败/取消时会显示原任务 ID，确需重新转写才使用新状态文件。
- 浏览器不能预览某些容器内的编码，并不等于服务端不能解码；无法框选时可使用全画面。开发者 CI 排障见 [贡献指南](../CONTRIBUTING.md#transcription-ci-lessons)，完整格式、到期和存储说明见 [音视频指南](VIDEO_TRANSCRIPTION.md)。

## 表达优化连接、检查与结果

- 模型列表是可选接口，不能获取时仍可手动输入模型 ID 验证。`context_length` 是输入加输出的客户端预算，不覆盖 Ollama 默认值、不证明服务端窗口；分配与模型摘要字段为空是正常状态。Ollama Base URL 通常以 `/v1` 结尾，实际窗口须在服务端配置。401/403 检查模型服务密钥，400 检查模型及高级参数，容量错误调整预算或服务端窗口后重试。
- 正文生成、自动检查、小结／标题及必要的兜底结束后统一发布。正文阶段的持续连接、窗口或容量故障保存进度并暂停；复核连接故障可记录为不可用，证据引用无法定位时保留不确定及诊断，不当作通过。检查提示不会撤销已生成正文。
- 小结 v2 的模型异常自动尝试定向修复、关键词及原文要点；关键词合格后即结束该项。小结阶段的窗口／容量故障和连续服务故障可直接触发程序摘取，仍受长度限制。“关键词／原文要点”是明确标记的替代输出，已计入 ready；成功交付比例不等于事实准确率。
- “补试未生成小结”用于暂停或遗留缺项，不必为普通生成异常反复点击。用户主动暂停或服务重启后仍需显式继续；来源或提示策略不兼容需重新生成，自动兜底不绕过这些校验。
- 当前为 SQLite v23；仅 v18 及更早版本迁往 v19 时一次性清理旧优化，v19→v20→v21→v22→v23 保留历史稿与检查点。v22 会将旧 Ollama 连接改为 `/v1` 并关闭启用；重新验证后启用，再显式继续任务。应用新的画面边界也会使对应优化失效，需要重新优化；只预览参数或更换截图不会。
- 小模型可能保留原本清晰的短句，也可能误改事实；改字比例不是质量指标。详见[表达优化](EXPRESSION_OPTIMIZATION.md)及[验证边界](EXPRESSION_OPTIMIZATION_VALIDATION.md)。

English: Model discovery is optional; enter a model ID manually if unavailable. Verification checks synthetic text/JSON output, not allocated context. Configure the actual Ollama context server-side. Check model API credentials for 401/403, parameters for 400, and budgets/server limits for capacity errors. Existing drafts and checkpoints survive the v22 migration.

## ASR resource budgets / ASR 资源预算

所有 ASR 队列任务（音频、视频、兼容转写、参考分析和声纹入库）使用内存优先的 float32 音轨缓存和有界读取；缓存保留未量化采样供既有 VAD、说话人和波形分析使用，转写与对齐则沿用 PCM16 音频量化结果。缓存最多占任务内存预算的 25%，并受 `AUDIO_INTEL_ASR_AUDIO_CACHE_MAX_BYTES` 限制（默认 4 GiB，0 为直接落盘）。超过额度或出现内存压力时仅淘汰需要释放的块，每个采样最多落盘一次；归一化在读取副本上计算，不重写缓存。视频补静音使用逻辑区间，仍按完整时间轴核算 PCM16 文件和 WAV 容量。波形提前计算，最后一个音频读取者结束后释放缓存。区间读取允许浮点秒端点换算产生的一个采样余量，保持旧切片内容。解码/归一化每块最多 65,536 采样，VAD 保持原有 60 秒边界、缓存和末尾刷新；说话人窗口与有效批量不变。全局聚类、增强统计和结果元数据仍需要内存，缓存额度不是恒定总内存保证。

`AUDIO_INTEL_ASR_MEMORY_BUDGET_BYTES` 限制每任务新增主机内存，`AUDIO_INTEL_ASR_WORK_BUDGET_BYTES` 限制工作文件和生成产物（不含上传原件）。0 为自动值，正整数为管理员上限。自动内存额度为有效可用内存减去 max(1 GiB, 有效总内存的 10%) 后的一半；执行器起始进程树 RSS 加额度形成观察上限。Linux 可读取的 cgroup v2 余量参与计算，其他情况采用系统内存。自动空间额度为磁盘空闲减去未完成上传预留和现有磁盘安全水位后的一半。预算在任务开始冻结，运行时再次检查真实余量。跨目录盘符分别受实际剩余空间约束；建议临时目录与数据目录在同一文件系统。

资源不足时任务以 `AsrResourceLimitError` 明确失败，错误说明阶段、资源、需求和预算，上传原件保留。有效延迟和静音不裁剪，大时间戳间隙在写入前核算容量；原 PCM16 WAV 容量限制继续生效，不自动改用 RF64。必需步骤不降级算法、不跳过说话人、不自动重试；用户可调整预算/释放资源后完整重试。每次尝试的 `diagnostics/attempt-N-resource-budget.json` 最多 16 KiB，仅本地保存，不向全局列表/SSE 发布采样。RSS 进程树求和会保守计入共享页，一秒监督采样不是操作系统硬上限，外部进程仍可能造成 OOM。

English: All ASR queue paths use a memory-first unquantized float32 cache with bounded reads for existing VAD, speaker and waveform analysis; transcription/alignment retain their PCM16 audio quantization. Cache RAM is capped at 25% of the task RAM budget and AUDIO_INTEL_ASR_AUDIO_CACHE_MAX_BYTES (4 GiB by default; zero selects disk). Evicted samples are written at most once, normalization never rewrites the cache, and timestamp padding uses logical zero intervals. `AUDIO_INTEL_ASR_MEMORY_BUDGET_BYTES` and `AUDIO_INTEL_ASR_WORK_BUDGET_BYTES` cap additional host RAM and generated workspace; zero selects automatic budgets, frozen at task start with live headroom checks. Memory defaults to half the available capacity after reserving max(1 GiB, 10% of effective total RAM); workspace defaults to half the free space after upload reservations and the disk safety margin. Preserve VAD state/boundaries, model identity, precision, batching, diarization and media timing. Exceeding a budget fails explicitly with AsrResourceLimitError and retains the original input. No request fields or database schema change; historical data is not rewritten. RSS monitoring is conservative and is not an OS hard limit. No automatic algorithm downgrade or partial resume is introduced.

缓存诊断在既有本地 attempt 文件中记录峰值缓存、累计浮点写入和淘汰原因。内存足够时浮点缓存写入为零；这不表示原有 PCM16、增强文件或任务总写入为零，也不等于 SSD NAND 实测写入量。可用 ASR 环境运行 `scripts/benchmark_asr_storage.py --source <media> --output-dir <new-directory>` 计量；测试应隔离运行目录并设置 `AUDIO_INTEL_LOAD_ENV=0`。

English: Existing local attempt diagnostics record peak cache allocation, cumulative float-cache writes and eviction reasons. A RAM-resident task writes no float cache, while its existing PCM16/enhancement outputs still write normally. These counters measure logical payload writes, not SSD NAND writes. Run `scripts/benchmark_asr_storage.py --source <media> --output-dir <new-directory>` with the ASR interpreter and `AUDIO_INTEL_LOAD_ENV=0`; keep test state isolated.

## ASR chunk input / ASR 分块输入

ASR 转写和对齐默认按原采样边界读取已保存的 16 kHz 单声道 PCM16 原音或增强音，不再重复生成分块 WAV；音频量化结果、VAD 分块、模型精度和批次规则保持一致。服务端 `AUDIO_INTEL_ASR_CHUNK_INPUT` 未设置或空值使用 `range`；`wav` 恢复旧分块文件路径，其他值启动报错，修改后须按维护流程重启。它不是 HTTP 请求参数，也不改变请求／结果结构。原始上传、归一化音轨、实际增强音及最终产物仍正常保存，分块免落盘不表示任务总写入为零。

ASR transcription and alignment read exact sample intervals from the saved 16 kHz mono PCM16 original or enhanced track, avoiding duplicate chunk WAVs while preserving audio quantization, VAD boundaries, model precision and batching. The server setting `AUDIO_INTEL_ASR_CHUNK_INPUT` defaults to `range` when absent or empty; `wav` restores legacy chunk files. Other values fail startup; restart through the maintenance procedure after changes. This is not an HTTP request field and changes no request/result schema. Uploaded originals, normalized audio, applied enhancement and final artifacts still write normally; eliminating chunk files does not eliminate all task writes.

### Reproduce chunk I/O / 复现分块 I/O

`AUDIO_INTEL_ASR_AUDIO_CACHE_MAX_BYTES=0` selects direct disk storage for the float32 cache; it does not disable writes and is independent of chunk input mode. To diagnose a range-input regression, set `AUDIO_INTEL_ASR_CHUNK_INPUT=wav`, wait for idle and follow the [maintenance procedure](UPGRADE.md#local-development-and-deployment) to restart; restore `range` through the same procedure. Preserve the original input and diagnostics for comparison.

中文：缓存容量设为 0 表示浮点缓存直接落盘，与分块输入模式独立。排查区间输入异常时可设置 `wav`，等待空闲后按维护流程重启；恢复 `range` 也需重启。保留原始输入及诊断以便对比，不修改正常部署的其他配置。

Use an existing saved **16 kHz mono PCM16 WAV**, opened read-only. The script creates and removes legacy comparison chunks under the report directory, performs no inference and downloads no models. Use a fresh output directory; the report contains checksums, not transcript text. From the repository root:

```bash
AUDIO_INTEL_LOAD_ENV=0 .runtime/asr/bin/python scripts/benchmark_io_efficiency.py \
  --source /path/to/saved-pcm16.wav --output tmp/io-check/report.json --repeat 3
```

```powershell
$env:AUDIO_INTEL_LOAD_ENV="0"
& .\.runtime\asr\Scripts\python.exe scripts/benchmark_io_efficiency.py --source C:\audio\saved-pcm16.wav --output tmp/io-check/report.json --repeat 3
```

Replace the source with your saved WAV. `samples_equal` verifies identical decoded samples; `wav_bytes` and `range_bytes` measure temporary chunk file bytes, while `wav_seconds` and `range_seconds` measure this I/O comparison only. These are not end-to-end inference timings or SSD NAND-write/lifetime measurements. Cache effects and hardware affect timings. The separate `benchmark_asr_storage.py` measures float-cache/decode storage instead.

中文：替换示例源路径，使用新的输出目录。基准只读源 WAV，但会在报告目录创建并清理旧路径对照分块；不运行推理、不下载模型。`samples_equal` 检查采样一致，字节字段只计临时分块，耗时只涵盖本次 I/O 对照；不能据此推算端到端加速或 SSD 寿命。浮点缓存／解码写入仍由另一存储基准计量。
