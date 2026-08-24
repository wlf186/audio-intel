# 故障排查

## 快速诊断

```bash
./service.sh doctor
./service.sh status
./service.sh logs all
curl -v http://127.0.0.1:20810/api/v1/health
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
- 4 GB 显存必须保持 batch 1；ASR、Aligner 与 TTS GPU 任务由全局锁串行运行。
- OOM 后先确认没有其他 GPU 程序，再重试任务；不要同时启动另一套模型服务。

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

ASR/TTS 环境同理。确认恢复后再自行删除 `.broken` 目录。
