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

- API 启动时自动将 SQLite 迁移到当前 schema v4；迁移是就地操作，因此备份必须在启动新版本前完成。
- 历史 ASR/TTS 任务、旧声音档案和既有声纹样本保持可读；人员重命名不会回写历史任务中的说话人快照。
- 浏览器鉴权改用进程内会话 Cookie，升级或重启后需要重新输入 API Key。
- `/api/v1/health` 现在是公开最小探针；原详细结构迁移到受保护的 `/api/v1/system`。监控脚本如依赖硬件、worker、模型或路径字段必须切换端点并增加 Bearer Header。
- `.complete` 必须包含固定模型 revision。旧的空 marker 会在 setup 时被判定为无效并修复。
- TTS 安装现在同时创建独立 aligner 环境；不要复用旧 TTS 环境中的 qwen-asr。
- ASR/TTS worker 现在由监督器管理可重启执行器，`setup all` 会将进程树管理所需的 `psutil` 同步到两个模型环境。启动时会校验并清理可信的遗留执行器元数据，再恢复中断任务；无需新增数据库迁移，schema 仍为 v4。
- 原生 ASR/TTS API、OpenAI 兼容音频端点和提交页现在默认启用 `accelerate_single_task`。依赖旧版 batch 1 默认行为的客户端必须显式传入 `false`；模型、精度、ASR 分块与说话人语义不变。
- ASR/TTS 页面参数现在分别保存在浏览器 localStorage，并提供页面级“恢复默认配置”；清除站点数据后会恢复默认。TTS 文本仅保留在 sessionStorage，文件和未确认的麦克风录音不会持久化。
- 声纹库新增浏览器麦克风录音入口，继续复用现有样本上传 API，不新增数据库字段或迁移。远程普通 HTTP 访问仍可能被浏览器拒绝麦克风权限，可继续使用文件上传。

## 升级后验证

```bash
./service.sh doctor
curl -fsS http://127.0.0.1:20810/api/v1/health
.runtime/api/bin/python scripts/smoke_test.py
.runtime/api/bin/python -m pytest -q
corepack pnpm@10.15.1 --dir frontend typecheck
```

若真实模型、Torch、精度或设备路由发生变化，还必须执行真实 ASR、TTS 克隆和说话人分离回归，不能仅依赖 mock 测试。
