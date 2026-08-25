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
- 4 GB 显存下 ASR、Aligner 与 TTS GPU 任务仍由全局锁串行运行。TTS 仅在单个任务内对相邻文本块尝试 batch 2，并逐块执行声码器解码；显存门控失败或 OOM 时会自动恢复为 batch 1。
- OOM 后先确认没有其他 GPU 程序，再重试任务；不要同时启动另一套模型服务。

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
- `ss -ltnp | grep 20810` 检查端口占用；也可设置 `AUDIO_INTEL_PORT`。
- 查看 `logs/api.log`、`logs/asr.log`、`logs/tts.log`；PID 位于 `run/`。
- 服务配置改变后使用 `./service.sh restart all`。

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
