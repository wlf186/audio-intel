# 表达优化 / Expression optimization

在「系统状态 → 表达优化」填写 Ollama URL，按需勾选使用服务端系统代理，获取模型并选择。模型验证会实际运行一段合成文本；验证成功后勾选启用并保存。默认关闭，不会自动发送历史转写。支持本机或受信任网络中的 Ollama，无云端回退。

在已完成的普通音频或视频转写中点击「优化表达」。原文、优化稿和视频逐句明细共用阅读切换栏；「对比」在桌面打开双栏，在窄屏上下排列。优化稿仅供阅读、API 和简洁导出，不用于声纹或精确字幕。会使用任务提交时保存的热词，而非热词库当前内容。模型可能改错文字，重要内容请对照原文核实。

处理按连续同说话人和视频画面分段，再组成最多约 2400 字的批次，携带相邻原文作参考。每段最多 800 字，最多 32 段一批。要求实际上下文至少 16K，关闭 thinking，每次显式设置 `num_predict=4096`；预算预留输出与安全余量。模型验证会显示实际采用的窗口，并非只看模型标称最大窗口。无需手动配置 Ollama 全局输出窗口。

进度表示已验证并保存的正文字符比例；当前批次仅显示生成活动，不制造剩余时间。输出必须完整覆盖输入段落，截断或格式错误会拆小批次重试；网络失败有限次退避重试。暂停取消当前请求，保留已完成批次；继续从检查点开始。API 重启后运行中任务变为暂停。远端取消是否立即释放算力取决于 Ollama。

每个转写保留一份已完成优化稿和至多一份生成候选；重新优化成功才替换旧稿。删除优化会取消请求、删除检查点和草稿并清理导出缓存；删除父任务也级联清理。关闭功能暂停任务，重新启用后可手动继续。模型或原文变化时需要重新生成。

导出选择原文、优化稿或对照版。优化稿支持 PDF、DOCX、TXT；对照版支持 PDF、DOCX，A4 纵向，上方优化稿、下方灰色小字原文，画面仅出现一次。脱敏和替换同时应用两侧。只允许导出已完成稿。

## API

沿用服务 Bearer/API 浏览器认证。OpenAPI 和本地 `/docs` 提供请求结构。

| 方法 | 路径（前缀 `/api/v1`） | 用途 |
|---|---|---|
| GET / PUT | `/settings/transcript-optimization` | 读取、保存配置 |
| POST | `/settings/transcript-optimization/discover` | `{base_url,use_proxy}` 获取模型 |
| POST | `/settings/transcript-optimization/verify` | 加 `model` 验证；返回短期 verification_token |
| GET / POST | `/jobs/{id}/transcript-optimization` | 状态（ETag）/创建 |
| POST | `/jobs/{id}/transcript-optimization/pause`、`/resume` | `{run_id}` 暂停/继续 |
| GET | `/jobs/{id}/transcript-optimization/result?run_id=...` | 段落与原文、来源、画面 |
| DELETE | `/jobs/{id}/transcript-optimization?purge=true` | 清理所有优化数据 |

创建须 `Idempotency-Key`，正文 `{regenerate:false}`；首次 202、重放 200、冲突 409、队列上限 429（Retry-After）。独立队列单并发，最多 5 个排队任务。`POST /jobs/{id}/transcript-export` 增加 `text_version` 和非原文必填 `optimization_id`；默认原文兼容旧客户端。列表/SSE 只含优化状态摘要，不包含正文。能力发现见 `asr.transcript_optimization`。

调用示例：`python3 scripts/optimize_transcript.py --url http://localhost:20815 --job JOB_ID`，认证通过 `AUDIO_INTEL_API_KEY` 环境变量。需先在系统页面配置启用。

## 持久化与兼容性

SQLite v14 → v15 仅新增独立设置、优化任务及段落表；不改写历史转写或媒体。升级仅需一致的 SQLite 备份，验收完成后按 UPGRADE.md 清理临时备份。配置保存在服务端 SQLite。阅读切换是当前浏览器工作会话偏好，最多保留 200 个任务，退出登录或删除任务清理；不会保存正文或 API 密钥。模型和依赖锁无需变化。

English: Optional Ollama-only faithful transcript cleanup, with independent durable checkpoints, pause/resume, atomic draft replacement, original/draft comparison, and redacted PDF/DOCX/TXT exports. Minimum allocated context is 16K; each generation explicitly caps output at 4096 tokens and disables thinking. The original transcript and audio linkage remain intact. Schema v15 adds independent tables only. Configure and enable in System before using the authenticated API example.
