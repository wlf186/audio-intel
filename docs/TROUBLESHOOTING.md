# 故障排查

以下命令默认使用 Linux 的 `service.sh`。Windows 11 原生用户将其替换为 `service.cmd`，并优先查看 [Windows 专项排障](WINDOWS.md#5-常见问题)。

## 快速诊断

```bash
./service.sh doctor
./service.sh status
./service.sh logs all
curl -v http://127.0.0.1:20810/api/v1/health
```

若已启用 API Key，公开 `/health` 只返回最小状态。详细诊断使用：

```bash
curl -H "Authorization: Bearer $AUDIO_INTEL_API_KEY" http://127.0.0.1:20810/api/v1/system
```

## 下载缓慢或中断

确认代理已导出到执行 `setup` 的同一个 shell：

```bash
env | grep -i _proxy
curl -I https://huggingface.co
curl -I https://modelscope.cn
./service.sh setup all
```

安装脚本会对首次 pnpm 获取自动重试三次，模型下载器支持续传；模型目录只有成功后才会写入 `.complete` revision 标记。不要手工创建标记。若单个模型确认损坏，将对应 `models/<模型名>` 目录移动到备份位置后重新执行 `setup asr` 或 `setup tts`。

## GPU 不可用或显存不足

- `nvidia-smi` 必须成功，驱动需兼容安装的 PyTorch CUDA 13.0 wheel。
- API 返回 503 时选择 CPU，服务不会静默回退。
- 4 GB 显存下 ASR、Aligner 与 TTS GPU 任务仍由全局锁串行运行。TTS 默认仅在单个任务内对相邻文本块尝试 batch 2，并逐块执行声码器解码。
- “单任务加速”现在默认开启，ASR/TTS 会按硬件自动扩大内部批次；OOM 时按 `16→12→8→6→4→2→1` 回退并重试当前批次。排障或对照 batch 1 时可在页面关闭，API 客户端则显式传入 `accelerate_single_task=false`。完成任务可在结果 JSON 的 `acceleration.target_batch_size`、`stage_batch_sizes` 和 `oom_fallbacks` 查看实际选择。
- OOM 后先确认没有其他 GPU 程序，再重试任务；不要同时启动另一套模型服务。

若要判断开关在当前机器和素材上的实际收益，至少执行三组交替基准；短音频或单句文本只有一个块时通常没有明显收益：

```bash
.runtime/api/bin/python scripts/benchmark_single_task_acceleration.py asr --device gpu --audio meeting.wav --repeat 3
.runtime/api/bin/python scripts/benchmark_single_task_acceleration.py tts --device gpu --repeat 3
```

## API 提交返回 429

- `submission_concurrency_limited` 表示同时落盘的提交过多，通常按 `Retry-After` 等待约 1 秒即可。
- `queue_capacity_reached` 表示对应 ASR 或 TTS 队列已达到配置上限；等待前方任务完成，或在评估机器容量后调整 `AUDIO_INTEL_MAX_QUEUED_ASR` / `AUDIO_INTEL_MAX_QUEUED_TTS`。
- `insufficient_queue_storage` 表示接收本次输入后会低于 `AUDIO_INTEL_MIN_FREE_DISK_BYTES`；清理磁盘或调整保留值前先确认不会挤占模型、数据库和结果空间。
- 查看受保护的 `GET /api/v1/queue` 可获得当前排队数、准入预留、并行提交和磁盘余量，但它只是预检；最终以提交接口的原子准入结果为准。
- 超时、断线或 `429` 后必须复用原 `Idempotency-Key`；不要为同一次逻辑提交生成新键，否则可能创建重复任务。

## ETA 尚未出现或 SSE 断线

- ETA 只使用本机同类历史任务。少于 5 个有效样本时 `estimate.state` 为 `warming_up`，没有剩余时间属于预期行为；可用后的区间也只是建议，不是 SLA。
- 单任务 SSE 使用 `/api/v1/jobs/{job_id}/events`，全局快照使用 `/api/v1/events`。两者都没有历史重放；断线后重新连接，并用收到的首个快照或任务状态接口校准。
- 无法使用 SSE 时按响应中的 `poll_after_seconds` 轮询 `status_url`，保存响应 `ETag` 并在后续请求发送 `If-None-Match`；`304` 表示任务和同类队列上下文未变化。
- 浏览器原生 `EventSource` 不能附加自定义 Authorization Header。项目同源页面使用 HttpOnly 会话 Cookie；外部浏览器应用应通过同源后端代理，服务端客户端可直接发送 Bearer Header。

## 执行中任务取消后仍显示正在停止

- worker 会先给当前阶段 1 秒协作退出时间，必要时终止该任务的执行进程及其 ASR、Aligner 等后代进程；确认进程全部退出后才把任务标记为“已取消”并开放删除。
- 默认应在 3 秒内完成安全停止。任务记录显示“正在安全停止”期间不要直接删除 `data/` 或 `tmp/` 文件；终态出现后可重试或永久删除。
- 停止失败时任务会保持 `cancelling` 并继续重试，避免残留进程仍在写文件。查看 `./service.sh logs all`，并用 `nvidia-smi` 确认是否存在项目外的 GPU 进程。
- `AUDIO_INTEL_CANCEL_GRACE_SECONDS` 可调整协作退出窗口；降低它会更早强制停止，通常保持默认值 `1` 即可。

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

## 前端或服务无法访问

- `corepack pnpm@10.15.1 --dir frontend build` 可重建前端。
- 页脚 `NET_LISTEN` 和受保护的 `/api/v1/system` 中 `bind` 字段会显示服务实际配置的监听地址与端口；`0.0.0.0` 表示监听所有网卡，不等同于必须通过该地址访问。
- 默认端口可用 `ss -ltnp | grep 20810` 检查占用；若设置了 `AUDIO_INTEL_PORT`，请改查实际端口。
- 查看 `logs/api.log`、`logs/asr.log`、`logs/tts.log`；PID 位于 `run/`。
- 服务配置改变后使用 `./service.sh restart all`。

## 浏览器麦克风无法录音

- 浏览器麦克风要求安全上下文。本机请使用 `http://localhost:<端口>` 或 `http://127.0.0.1:<端口>`；通过局域网 IP 远程访问时应在反向代理配置 HTTPS，普通 HTTP 通常会被浏览器拒绝。
- 点击“开始录音”后，在浏览器站点权限和操作系统隐私设置中允许麦克风。若提示设备占用，请关闭会议、录音或语音聊天程序后重试。
- 每次最长录制 30 秒，停止后先试听或重录，再确认创建声纹入库任务。未确认的录音只存在页面内存中，切换人员或离开页面会释放麦克风并清除暂存录音。
- 浏览器不支持 MediaRecorder、权限仍不可用或需要远程 HTTP 访问时，切回“上传文件”即可继续入库。

## ASR/TTS 配置没有保持或需要重置

- ASR 与 TTS 参数分别保存在版本化的 `localStorage` 项中，互不覆盖；每个页面的“恢复默认配置”只重置本页参数。
- TTS 合成文本和参考文本只在当前 `sessionStorage` 会话保留，选择的音频文件不会进入浏览器存储。
- 清除浏览器站点数据或 localStorage 后会自动恢复默认配置；仅清理图片、脚本等 HTTP 缓存通常不会删除参数偏好。
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
