# 资料库迁移 / Library transfer

在主导航打开“资料库”，用“声纹库 / 热词库”子页管理资料。声纹人员和各模块的人员下拉框按完整拼音或拉丁字母排序，同名按备注、ID 稳定排序；中文姓氏采用常见姓氏读音（如单 shan、曾 zeng、区 ou），数字和符号放到最后的 `#` 组。人员列表右侧 A–Z / # 索引只滚动人员列表，不改变当前选中人员。旧 `#voiceprints`、`#hotwords` 链接会转到对应资料库子页。

The Library navigation contains Voiceprints and Hotwords tabs. Person lists and pickers share server-side full-pinyin/Latin ordering, common surname readings and stable name/note/ID ties. Numbers and symbols belong to the final `#` group. The alphabet rail scrolls the person list without changing the selected person. Legacy hashes redirect to the matching tab.

## 从 A 服务器恢复到 B / Restore on another server

1. 等待 A 的声纹入库完成，在资料库点击“导出资料库”。浏览器直接下载 ZIP，无需把整个包放入浏览器内存。正在入库或可用音频缺失时导出返回 `409`，请先完成入库或修复该样本。
2. 在 B 打开资料库，点击“导入资料库”，选择 A 导出的 ZIP。上传后在后台离线检查格式、文件清单、SHA-256、音频和容量；不用 GPU。
3. 查看预览，选择默认的“合并”或“替换全部资料”。只有校验成功且预览仍对应当前资料时才能应用；库发生变化时重新预览。
4. 应用后查看结果数量。替换前备份可在同一导入记录下载，使用相同导入流程恢复。刷新页面可重新打开最近的记录继续查看。

Export on A after enrollment finishes, upload the ZIP on B, inspect the preview, then apply merge or replacement. Validation is offline and does not use a GPU. The browser downloads streamed archives directly. Receipts survive reloads and service restarts; results and downloadable replacement backups remain available.

## 合并与替换 / Merge and replace

| 行为 / Behavior | 合并 / Merge | 替换 / Replace |
| --- | --- | --- |
| 人员 / People | 规范化姓名＋备注相同则合并，保留 B 的姓名、备注和热词同步开关 / Match normalized name + note, retain B metadata and hotword opt-in | 完全使用包中的人员 / Use archive people |
| 样本 / Samples | 同一人员下相同音频 SHA-256 跳过；新音频同名追加 `(2)` 等后缀 / Skip audio already present for that person, suffix new sample name conflicts | 恢复包中的所有稳定样本 / Restore all stable archive samples |
| 自定义热词 / Custom hotwords | 同名规范化后合并去重，B 的词在前 / Union same-name lists, B terms first | 使用包中的自定义词表 / Use archive custom lists |
| 系统人名词表 / System name lists | 依据合并后的人员重新生成 / Regenerate from final people | 依据导入人员重新生成 / Regenerate from imported people |
| 任务要求 / Job requirements | 无进行中的提交或资料编辑 / No in-flight submissions or library edits | 所有 ASR/TTS 任务、提交和入库完成 / All ASR/TTS jobs, submissions and enrollment finished |
| 备份 / Backup | 无自动全库备份 / No automatic full-library backup | 确认后先完成可下载 ZIP 备份，再替换 / Confirm, create verified downloadable ZIP backup, then replace |

ID 没有冲突时保留；有冲突则分配新 ID，结果 `id_map` 给出映射。有歧义的旧声音别名保留 B 的归属并在预览报告。导入不会创建来源服务器的入库任务，失败样本保留状态和错误；可用样本的音频、语言、转写、字词时间和时长随包迁移。兼容的 CAM++ 特征缓存可以复用，其他情况由已有识别流程按需重建。

Preserve IDs when available; conflicts receive new IDs reported in `result.id_map`. Ambiguous legacy aliases retain B ownership with a preview warning. Enrollment jobs are not imported. Failed samples retain their state/error; ready samples retain audio, language, transcript, alignment and duration. Compatible CAM++ embeddings can be reused; other caches are rebuilt lazily by the existing recognition flow.

历史任务、结果、请求和参考音频快照保持原样。替换成功后清理 B 浏览器中的热词勾选、克隆人员/样本选择、每样本参考区间和热词编辑草稿；保留 ASR 上传和 TTS 文本等其他草稿。合并保留上述偏好。浏览器仅在当前标签页 sessionStorage 中临时记录导入 ID 来继续跟踪，不保存音频、资料包或密钥。

替换后，历史失败/取消的声纹入库任务只有仍绑定原样本时才能重试；已移除或恢复成独立样本时重试返回 `409`，请重新上传，避免旧任务修改恢复后的资料。

Historical job requests, results and reference snapshots remain unchanged. Successful replacement clears browser hotword selections, clone person/sample selections, per-sample reference intervals and the hotword editor draft, preserving ASR upload and TTS text drafts. Merge preserves preferences. A temporary import ID in per-tab sessionStorage resumes monitoring; archives, audio and keys are not stored there.

Historical failed/cancelled enrollment retries require their original bound sample. A removed or independently restored sample returns `409`; upload again to keep old enrollment work from modifying restored data.

## 格式、限制与存放 / Format, limits and storage

- 当前导出 ZIP v2：`manifest.json` 中 `format="audio-intel-library"`、`contract_version=2`，包含分组及人员归属；仍接受 ZIP v1 导入，以及声明的 `audio/<sample-id>.<extension>`。不含数据库绝对路径、历史任务、模型权重、密钥或推理环境。
- 严格校验未知字段、重复 ID/姓名/路径、路径穿越、符号链接、加密和异常音频。仅支持 stored/deflated ZIP；每个音频须与声明的大小和 SHA-256 一致。
- 上传及解压总量均受 `limits.max_upload_bytes`（默认 4 GiB）约束；manifest 最大 16 MiB、文件最多 100,000 个，并保持既有热词数量/长度和磁盘保护。合并后超过限制须先调整词表。
- 临时上传位于 `data/library_imports/<id>/`；未应用的 ready/failed 包 24 小时后清理，记录变为 expired。可在 API 中主动删除暂存包。已完成包清理暂存文件并保留结果。
- 替换备份位于 `data/library_backups/<id>.zip`，受鉴权保护且不自动删除。日常数据备份也应包含这些目录；空间不足须先整理或调整部署存储。
- 音频写入独立新路径，数据库事务成功后才清理旧音频；中断时根据持久化文件日志回滚或完成清理。正在读取的音频不被删除。
- `completed` 回执表示数据库事务已提交；旧文件仍在收尾清理时，写操作和暂存包删除可能返回 `409 library_maintenance`，应稍后重试。不要据此重新应用资料包或改动恢复日志。

The v2 ZIP (v1 imports supported) contains a strict manifest and declared audio files, without absolute DB paths, history, model weights, credentials or runtimes. Upload and expanded sizes share the configured audio-upload limit, with a 16 MiB manifest and 100,000-entry ceiling. Staging uses `data/library_imports/`; unapplied ready/failed archives expire after 24 hours. Completed results remain. Protected replacement backups in `data/library_backups/` are never automatically deleted. File journals recover interrupted operations; old audio is retired only after the database transaction commits and active readers release it.

A `completed` receipt records the committed database transaction. While retired-file cleanup finishes, writes and staging removal can still return `409 library_maintenance`; retry shortly without reapplying the archive or changing its recovery journal.

接口和可执行示例见 [API guide](API.md#library-export-and-import)。迁移 B 的运行环境仍须在 B 按正常安装流程 setup，不要复制 A 的 `.runtime/`。

## 人员分组 / Person groups

每人只属于一个分组；新增及升级前已有人员默认“未分组”。可新增、重命名自建分组，并在“全部人员”或分组视图中勾选、全选当前列表、批量移动或确认后永久删除。批量移动失败不部分生效；批量删除逐人报告失败并保留勾选，活动样本导入和音频读取仍受保护。删除分组只将人员移回“未分组”，不删除人员或样本；内置组不可重命名或删除。拼音索引只作用于当前人员列表。

ZIP v2 包含分组及归属，仍接受 v1。v1 新人员进入“未分组”；v2 合并按规范化组名匹配，已有同身份人员保留目标端分组并提示差异，替换恢复包内归属。分组变化使旧预览失效，需重新预览。导入统计中的分组数只计自建组。资料库替换备份与升级临时 SQLite 备份分别管理。

ASR 默认使用全部分组，手动多选会长期记忆，提交时固定人员 ID；之后移动或新增人员不改变排队任务。资料库替换及恢复 ASR 默认配置恢复全部分组。已完成识别、TTS 参考及历史请求快照保持原样。

English: Each person belongs to one group. Ungrouped is built-in and is the default for existing/new people. Filter and select people for atomic batch moves or confirmed per-person deletes. Group deletion moves members to Ungrouped without removing samples. ZIP v2 preserves groups, with v1 imports supported. Merge matches group names, retains existing target memberships with warnings, and replacement restores source membership; changed groups invalidate previews. Group counts exclude Ungrouped. ASR scope is snapshotted at acceptance. Persistent browser choices reset to all on library replacement; historical tasks remain unchanged.
