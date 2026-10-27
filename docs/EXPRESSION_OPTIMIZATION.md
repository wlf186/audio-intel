# 表达优化 / Expression optimization

在「系统状态 → 表达优化」填写 Ollama URL，按需勾选使用服务端系统代理，获取模型并选择。模型验证会实际运行一段合成文本；验证成功后勾选启用并保存。默认关闭，不会自动发送历史转写。支持本机或受信任网络中的 Ollama，无云端回退。

在已完成的普通音频或视频转写中点击「优化表达」。原文、优化稿、对照使用三个阅读页签，共用搜索和说话人筛选。视频原文内可切换逐句明细。对照在桌面左右并列、窄屏上下排列，标记文字增删和省略；段落筛选默认「全部」，也可选择「有变动」或「待核对」。调整段数不是质量评分。优化稿仅供阅读、API 和简洁导出，不用于声纹或精确字幕。会使用任务提交时保存的热词，而非热词库当前内容。模型可能改错文字，重要内容请对照原文核实。

策略 v4 提供三个改写档位，首次默认「流畅重写」，每次创建或重新优化时选择：

| 档位 | API 值 | 效果 |
|---|---|---|
| 自然润色 | `polish` | 主动清理口头冗余、重复、绕句和病句，保留交流语气。 |
| 流畅重写 | `rewrite` | 充分重组句式和表达顺序，形成顺畅、清晰的对谈稿。 |
| 深度成稿 | `editorial` | 更充分地组织段落，用紧凑书面表达保留全部独立事实和有效细节，不生成摘要或纪要。 |

仍按连续同说话人和视频画面划分来源段，每段最多 800 字，前后各最多 6 段、600 字原文用于理解。允许换词、改句式、合并重复和修顺语法，保持说话人视角、数字、否定、条件、因果和询问；不补背景或推测原因。含糊片段可局部保留，其余内容继续润色。有意义的回应保留；纯占位语需结合上下文确认后才省略。

独立请求复核「信息保真」和「整理充分性」，原样返回也须检查整理效果。复核先核对并引用证据，再给出结论，尤其检查分类、优先级、主宾语和询问含义是否被改写。发现明确事实问题或整理不足时，最多进行一次定向修正或加强；语言不足不等于事实错误。优先保留已通过信息检查的改写，避免后续修正失败时丢失可靠候选。没有可靠候选才逐字保留原文、标记「待核对」，继续整稿；运行故障仍暂停保存进度。复核不保证内容绝对正确，不以改写比例代替质量判断。

至少需要 16K 实际上下文，正文、热词和复核材料均纳入预算；预算不足时需增大窗口并重新验证。关闭 thinking，每次显式设置 `num_predict=4096`。进度统计已保存的来源字符，包括通过检查的改写、确认省略及标记待核对的原文保留。待核对数量不是原文错误数，也不代表其他段落绝对正确。正在处理的段落仅显示生成活动，不制造剩余时间。连接失败、超时、HTTP 429/5xx 最多尝试三次，耗尽后暂停，可在恢复服务后继续；配置、模型或来源变化、存储故障显示错误，不将剩余段落算作完成。暂停取消当前请求，保留已落盘正文及核对标记；API 重启后运行中任务变为暂停。远端取消是否立即释放算力取决于 Ollama。

每个转写保留一份已完成优化稿和至多一份生成候选；重新优化成功才替换旧稿。删除优化会取消请求、删除检查点和草稿并清理导出缓存；删除父任务也级联清理。关闭功能暂停任务，重新启用后可手动继续。模型或原文变化时需要重新生成。

点击统一的「导出转写」入口选择内容、格式、脱敏及替换规则，优先恢复浏览器最近选择的内容，并分别记忆原文、优化稿和对照稿的格式；首次默认当前阅读页签及 PDF。没有完成稿或格式不可用时临时回退，不覆盖已保存偏好。按钮统一为「下载」，文件准备成功并触发浏览器下载后自动关闭弹窗；失败保留选项并可重试。未完成候选不能冒充已完成稿导出；可以切回已完成稿导出上一版。优化稿跳过已省略段，对照版保留该段原文和省略说明。普通优化稿 PDF/DOCX/TXT 不添加待核对提示，保持干净正文；对照版 PDF/DOCX 对保留原文的待核对段落单独显示标记。全部段落保留原文时，网页显示「处理完成，全部保留原文」，仍可导出。优化稿支持 PDF、DOCX、TXT；对照版支持 PDF、DOCX，A4 横向、左原文右改写、等宽同字号并标记文字增删，长段可跨页且重复列标题；画面仅出现一次。脱敏和替换同时应用两侧。只允许导出已完成稿。

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

创建须 `Idempotency-Key`，正文 `{regenerate:false,rewrite_mode:"rewrite"}`；可选 `rewrite_mode` 为 `polish/rewrite/editorial`，省略默认 `rewrite`。档位存入配置快照并返回状态摘要与结果；恢复使用原档位，更换档位须重新生成。幂等指纹包含档位，同键更换档位返回 409；旧键未携带档位的请求仍可重放。首次 202、重放 200、冲突 409、队列上限 429（Retry-After）。独立队列单并发，最多 5 个排队任务。`POST /jobs/{id}/transcript-export` 增加 `text_version` 和非原文必填 `optimization_id`；默认原文兼容旧客户端。列表/SSE 只含优化状态摘要，不包含正文。能力发现见 `asr.transcript_optimization`。

调用示例：`python3 scripts/optimize_transcript.py --url http://localhost:20815 --job JOB_ID`；用 `--rewrite-mode polish|rewrite|editorial` 选择档位，显式重新生成加 `--regenerate`，断点继续加 `--resume`；恢复与档位参数不能同时使用。认证通过 `AUDIO_INTEL_API_KEY` 环境变量。需先在系统页面配置启用。

## 持久化与兼容性

SQLite v14 → v15 仅新增独立设置、优化任务及段落表；不改写历史转写或媒体。升级仅需一致的 SQLite 备份，验收完成后按 UPGRADE.md 清理临时备份。配置保存在服务端 SQLite。阅读切换是当前浏览器工作会话偏好，最多保留 200 个任务，退出登录或删除任务清理；不会保存正文或 API 密钥。模型和依赖锁无需变化。

策略 v3 将 SQLite v15 升至 v16，为优化段落增加核对状态、原因码，为优化任务增加待核对计数；升级前需要一致的 SQLite 备份，不需要复制媒体。历史原文、完成稿及配置快照不改写，历史缺失状态保持未知，不补写为复核通过。新候选将策略版本写入配置快照，不要求重新填写已验证且身份未变的模型配置。旧策略候选的状态摘要返回 `requires_regeneration=true`，继续接口返回 409；明确重新生成后替换旧候选，原已完成稿保留到新稿成功。

策略 v4 沿用 SQLite v16，无额外迁移或历史正文改写。旧稿的 `rewrite_mode=null` 显示为旧版策略，不假定为新档位；旧策略候选须明确重新生成。浏览器 `audio-intel:optimization-preferences:v1` 长期保存最后选择的档位；导出 `audio-intel:transcript-export:v1` 兼容扩展内容与按内容分类的格式，继续保存脱敏和规则。两类偏好在刷新、重开、退出登录及资料库替换后保留，不保存正文或凭据。

结果段落新增 `omitted`：`text=null, omitted=false` 表示尚未完成；`text="", omitted=true` 表示已复核的纯占位语省略；非空 `text` 表示保留正文。旧已完成稿按原文读取，`omitted=false`。来源 ID、时间、说话人和原文始终保留。阅读版本升级为三态，会话内兼容旧布尔偏好；搜索、说话人筛选与阅读位置按任务记录，最多 200 个，退出登录或删除任务清理。

结果段落新增 `review_status`（`accepted` / `needs_review` / `null`）及 `review_reason`（`review_failed` / `invalid_output` / `null`）；状态摘要和结果新增 `needs_review_units`。`accepted` 表示通过当前策略的内容检查（含原样一致性检查），不承诺模型绝对正确。`needs_review` 必须保存与来源完全一致的正文；`null` 表示尚未处理或历史没有记录，结合 `text` 区分。标记和正文同事务保存；完成的保留原文段不阻止 `state=succeeded`、`complete=true` 和发布。全局列表与 SSE 只读摘要。

CLI 完成时输出完整结果 JSON，即使存在 `needs_review_units` 也正常退出；运行故障暂停或失败时返回非零状态，修复后使用 `--resume`。

English: Optional Ollama-only faithful transcript cleanup, with independent durable checkpoints, pause/resume, atomic draft replacement, original/draft comparison, and redacted PDF/DOCX/TXT exports. Minimum allocated context is 16K; each generation explicitly caps output at 4096 tokens and disables thinking. The original transcript and audio linkage remain intact. Schema v16 adds durable review metadata and a summary count. Paragraph-local review failures retain the exact source and continue; operational failures preserve checkpoints for recovery. Only comparison exports include review markers. Historical missing metadata does not imply a passed review. Policy v4 adds polish/rewrite/editorial modes (default rewrite), durable per-run mode snapshots, independent fidelity and editing checks, and side-by-side landscape comparison exports. Browser export choices and the last editing mode persist locally. Configure and enable in System before using the authenticated API example.
