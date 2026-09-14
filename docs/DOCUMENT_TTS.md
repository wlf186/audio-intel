# 文档 TTS / Document TTS

短文本接口仍默认限制 50,000 字符。文档入口支持 EPUB、TXT、Markdown、可提取文本的 PDF，以及 DOCX、XLSX、PPTX，可生成超过此限制的完整文档音频。扫描 PDF 需先 OCR；不会调用云服务。

上传后先查看解析警告和正文预览，再选择分段。自动模式优先使用 EPUB 目录、PDF 书签或标题；Markdown 使用最浅的重复标题级别，单个总标题与首章保留。没有可靠结构时按目标字数分段。自然长章节自动模式下保留为一段；选择“按目标字数”可重新拆分。图片、导航及代码块不朗读，纯空白章节并入后续正文（末尾并入前段），保留正文偏移和原始页码。

按字数分段默认目标 N=10,000，可设为 1,000–50,000。在 0.8N–1.2N 内依次找段落、带换行句末、句末、换行；同类取最接近 N 的位置。找不到则向后找至 2N，再尝试窗口内词边界，最终最多 2N 强制切分并显示切分依据。不足 0.2N 的尾段仅在合并后不超过 2N 时并入前段。分段前后规范正文的字符、顺序均保持不变；用户取消选择的分段不合成。PDF 缺少标题样式时使用保守识别，数字列表不会自动充当章节。多栏、页眉页脚及扫描质量仍应通过正文预览检查。

每个任务使用同一模型、语言、设备和音色配置，并进入现有 TTS 队列。所有现有音色能力限制继续适用。服务只持久保存逐段的单声道 96 kbps MP3、文档快照和检查点，不保存长 WAV、完整 MP3 或 ZIP。每段完成后记录大小及 SHA-256；取消、失败或服务重启后手动重试，验证并复用完成段，未完成段重新生成。内存/临时资源不足先执行现有批次降级，再最多自动等待 30 秒、120 秒重试；完整旧执行进程树退出后才启动替代进程。自动重试期间任务保持运行并占有队列位置。用户取消、磁盘空间不足、缺失模型或无效输入不会自动重试。手动重试重置未完成段的自动重试预算。

分段 ZIP 使用 ZIP_STORED 和 ZIP64，完整 MP3 仅重封装现有音频包；两者即时流式传输，背压限制缓冲，不额外写入服务器 SSD。默认同时最多两项批量下载。断开连接释放文件和下载名额，下载中不能删除任务。批量下载不支持 Range，失败后从头下载；单段 MP3 支持 Range。MP3 编码帧可能在章节边界留下少量填充，不承诺无缝播放。反向代理应遵循 `X-Accel-Buffering: no` 并允许长连接。

## API 调用与资源限制 / API workflow and limits

### 下载音频的专辑标签 / Downloaded audio album tags

新建文档任务的 MP3 自动携带 ID3v2.3 标签。专辑名为 `文档标题 · 音色名称或声纹人员姓名 · YYMMDDHHmm`，时间固定为任务创建时的 UTC。同一分钟的同名任务追加 `· 2`、`· 3`。Artist 和 Album Artist 均使用音色名称；Title 使用分段标题；Track 按本任务实际选择的分段顺序写入 `1/N` 至 `N/N`。声音档案使用提交时保存的档案名称；参考音频克隆和声音设计分别标为“参考音频克隆”“声音设计”。

服务端在创建事务内保存可选的 `request.document.audio_metadata`（`version: 1`、`album`、`artist`、`album_artist`），不接受调用方传入这些字段。后续声纹改名、删除、任务重试和幂等重放不会改变专辑。标题沿用保存的文档标题，不自动缩短或改写书名。分段 ZIP 与单段下载包含相同标签；完整 MP3 使用相同专辑，Title 为“文档标题 · 完整音频”，不写分段曲序，仍即时流式重封装。

旧任务默认保持原样。需要定向补写时，先用 `.runtime/api/bin/python scripts/tag_document_audio.py --job-id JOB_ID` 只读预览；停止使用该数据目录的服务后添加 `--apply`。工具校验原始检查点，备份 SQLite 和该任务文件，验证补写前后的编码音频包和解码内容一致，再更新该任务的文件大小及 SHA-256。备份位于 `data/backups/document-metadata/`；停服状态下使用 `--job-id JOB_ID --rollback BACKUP_DIRECTORY` 可恢复。支持 `--data-dir`，不支持全量补写；已经完整写入时重复执行不修改文件。已有客户端文件需要重新下载。

English: Newly submitted document jobs write ID3v2.3 album, artist, album artist, title and track tags. Album names combine the saved document title, voice/person name and creation time in UTC (`YYMMDDHHmm`); same-minute name collisions receive `· 2`, `· 3`, etc. Track numbers run from 1/N through N/N for the selected sections. Profile names are snapshotted; unnamed inline clones and voice designs use fixed mode labels. The server persists an optional `request.document.audio_metadata` snapshot with version 1, album, artist and album_artist; callers cannot supply it. Renaming/removing a voice, retrying or replaying a submission preserves the album. Complete MP3 streams use the same album and a complete-audio title without a track number. Existing jobs stay unchanged unless explicitly processed by the offline, single-job `scripts/tag_document_audio.py` tool. Preview is read-only; `--apply` creates verified backups and updates artifact checksums, while `--rollback BACKUP_DIRECTORY` restores that job. Re-download previously obtained files to receive their new tags. No schema migration, additional model inference, re-encoding or cached complete export is needed for tagging.

可执行下载和标签检查示例 / Executable download and tag inspection:

```bash
.runtime/api/bin/python scripts/tts_document.py manuscript.epub --output sections.zip --inspect-tags
```

API 顺序：

1. `POST /api/v1/tts/document-imports`，multipart `file`，必须带 `Idempotency-Key`；首次 202，重放 200，冲突 409，容量不足 429。
2. 轮询 `GET /api/v1/tts/document-imports/{identifier}` 至 `ready`。
3. `POST .../{identifier}/preview`，JSON `segmentation_mode`、`target_section_chars`。`GET .../{identifier}/text?start=0&limit=4000` 按需读取正文。
4. `POST /api/v1/tts/document-jobs`，multipart `document_import_id`、`preview_revision`、重复的 `section_ids` 字段、相同分段配置及现有音色参数；必须带新的 `Idempotency-Key`。不接受 `text` 或未声明字段，输出固定 MP3。
5. 使用现有任务详情、事件、取消和重试接口。`GET /api/v1/jobs/{job_id}/document/sections` 分页查询检查点；成功后 `/document/download?mode=sections|complete` 直接下载。

完整可执行例子：`.runtime/api/bin/python scripts/tts_document.py manuscript.epub --preview-only`；移除 `--preview-only` 并添加 `--output ./sections.zip` 可生成并下载。认证读取环境变量 `AUDIO_INTEL_API_KEY`。交互式字段及错误契约见本地 `/docs`、`/openapi.json`。

默认限制由 `tts.document_jobs` 能力接口公开：100 MiB 上传、5,000,000 字符、2,000 段。配置项：`AUDIO_INTEL_MAX_DOCUMENT_BYTES`、`AUDIO_INTEL_MAX_DOCUMENT_CHARS`、`AUDIO_INTEL_MAX_DOCUMENT_SECTIONS`、`AUDIO_INTEL_MAX_DOCUMENT_DOWNLOADS`。解析在独立 API Python 子进程串行运行，默认 600 秒、2 GiB RSS、EPUB / Office 压缩包展开 256 MiB，分别由 `AUDIO_INTEL_DOCUMENT_PARSE_SECONDS`、`AUDIO_INTEL_DOCUMENT_PARSE_MEMORY_BYTES`、`AUDIO_INTEL_DOCUMENT_ARCHIVE_BYTES` 配置。

SQLite v11 增加导入与检查点表，保留历史任务、序号和声音库。升级前备份 `data/`。全局任务列表与 SSE 仅含摘要；文档正文保存在任务输入快照，不进入全局事件。浏览器仅在 sessionStorage 保存导入 ID、分段配置和选择，不保存正文或音频；关闭会话、退出登录或移除文档后清除该草稿。声音设置沿用原有偏好保存规则。

English: Document TTS accepts EPUB, TXT, Markdown, text PDFs, DOCX, XLSX and PPTX independently of the ordinary 50,000-character TTS limit. Review extraction warnings, choose structural or length segmentation, select sections, and reuse existing voice controls. Synthesis uses the existing queue and writes incremental section MP3 files with durable verified checkpoints. Manual retries reuse complete sections. ZIP64 and packet-remuxed MP3 downloads stream directly without an additional server export file; batch downloads restart from the beginning, while individual artifacts support Range. The local OpenAPI includes bilingual contracts and the executable Python client above supports preview, submission and bounded streaming downloads. Schema v11 is additive; back up data before upgrading.


## 网页合成工作台 / Browser workspace

语音合成页使用位置固定的“文本合成／文档合成／任务与结果”三个页签。文本和文档共享合成设置：桌面采用内容区与 340 px 设置栏，小于 1200 px 时设置位于内容下方并可展开。提交按钮独占布局空间，不覆盖内容。两种输入页不展示历史音频；试听与下载统一进入“任务与结果”。

文档分段列表宽屏每页 20 项、小屏每页 10 项，选择跨页保留。正文预览和导入管理使用可访问弹窗；正文按页获取，关闭阅读窗口后恢复焦点及列表位置。切换三个页签保留文本草稿、文档选择、分页位置与声音设置。

提交后保留当前编辑页，并选中本次任务；“查看本次任务”定位提交返回的任务 ID。结果页展示最近 5 个合成任务及较早的选中任务，桌面采用列表与详情两栏，小屏采用任务选择器。排队、运行、失败及取消任务展示各自状态，成功后展示结果，不使用其他任务的旧音频替代。后续进度更新不会抢走用户选中的任务；列表与详情提供独立的加载、错误和重试状态。完整历史及取消、重试等操作进入全局任务管理。

结果包含章节选择、播放器、单段下载、完整 MP3 和分段 ZIP，沿用原有流式下载及错误处理。离开结果页暂停播放，返回保留所选任务及章节。恢复默认配置重置合成参数、指令及当前声纹样本的自定义参考区间，不清空文本、文档草稿或切换页签；移除文档使用文档区的明确操作。文档草稿继续使用现有 sessionStorage，TTS 参数沿用现有存储；页签和折叠状态仅保存在当前页面内。

English: The synthesis page has three fixed tabs: Text, Document, and Tasks & results. Both input types share one settings panel (a 340 px desktop sidebar, expandable below the input under 1200 px). Submission controls occupy their own layout space. Document selection persists across pages (20 sections on wide screens, 10 on smaller screens); text previews and import management use accessible dialogs. Switching tabs preserves drafts, section selection, pagination and voice settings. Submitting retains the editor and selects the accepted task; View submitted task opens that exact task. Results show five recent synthesis tasks plus an older selected task, with a mobile task picker. Pending and unsuccessful tasks show their own status rather than another task’s audio. List and detail loading, errors and retries remain distinct. Full history and task management use the existing global Tasks page. Playback and downloads share the existing streaming APIs; leaving results pauses playback and retains the selected section. Restore defaults resets synthesis settings, instructions and the current voiceprint sample’s custom reference range, preserving text, document drafts and the current tab. Existing draft/preference storage lifetimes remain unchanged; navigation and expansion state are page-local.

### 文档来源、分段与阅读

“上传新文档”从本地上传新文件；“已导入文档”打开可复用的导入列表；“取消使用当前文档”仅清空当前文档草稿和分段选择，不删除导入文件。浏览或关闭导入列表不会清空草稿，当前文档标记为“当前使用中”。上传失败或取消上传保留原草稿，服务接受新导入后才切换到新文档；新导入解析失败时可重试。

“优先按文档结构”的“备用分段目标字数”仅用于无可靠结构的全文或章前正文，完整章节不受此目标限制；“按目标字数”尽量在自然边界附近切分，实际长度可超过目标。预览摘要显示已经应用的实际切分方式，尚未点击“重新分段”的参数不会改变摘要。重新分段会重新选中全部分段。每段明确区分“结构分段 · 文档目录／文档标题／工作表／幻灯片／文档文件边界”和“字数分段 · 段落边界／句末边界／换行边界／词边界／剩余正文”，强制切分保留核对提示。

桌面左侧文档卡片与右侧合成设置对齐，分段列表利用剩余高度独立滚动，底部分页保持可达；低屏高或长提示展开时允许外层滚动。小屏维持页面自然纵向滚动。正文预览通过 TAB 切换“原文换行／连续阅读”，通过“上一页／下一页”翻页；默认使用“原文换行”，保留提取文本的换行和空白；PDF 中的短行会如实显示。“连续阅读”仅在显示时合并单换行、保留空行分段，不修改提取文本、分段偏移、任务快照或合成输入。正文仍按最多 4000 字符分页读取。同一阅读弹窗内翻页、切换分段保留显示模式；切换显示模式保留分段和页码、正文滚动回顶部；关闭后恢复默认原文模式，并恢复列表焦点及位置。

导入列表每页 20 条，批量勾选跨页保留；“全选本页可移除文档”仅影响本页，当前合成文档与待移除勾选分别标识。解析完成或失败的文档可移除，等待解析／正在解析的文档不能移除。单项和批量移除均显示文件清单并要求应用内确认；包含当前文档时明确提示将清空其草稿。移除逐项执行并报告已移除、已不存在和失败项，失败项保留供重试，变为解析中的项取消勾选。删除结果与列表刷新错误分别显示。已提交任务及其音频不受导入移除影响。关闭列表会清除批量勾选，不会清空当前合成草稿。

文档草稿继续使用原有 sessionStorage 生命周期；批量勾选和阅读显示模式仅保存在各自弹窗的临时内存中。TTS 参数存储、正文提取与音频合成契约均保持不变。

English: Upload new document imports a local file; Imported documents reuses or removes existing imports; Stop using this document clears only the current draft and section selection. Browsing the library, cancelling an upload, or an upload failure preserves the draft. Structural segmentation labels its character target as a fallback and explains the applied split strategy. Each section identifies both the strategy and the boundary used. Desktop lists fill the available panel height with reachable pagination; mobile uses natural page scrolling. The reader defaults to Original line breaks, with an optional Continuous reading view that collapses single line breaks and retains blank-line paragraphs without changing synthesis text or offsets. Text remains paginated at up to 4000 characters. Display mode persists while navigating within one reader and resets on close. Imported documents support selection across 20-item pages, page-only select-all, and confirmed removal with individual outcomes and retryable failures. Active imports cannot be removed. Only removal of the current import clears its draft; submitted jobs and audio keep independent snapshots. Bulk selection resets when the library closes. Draft sessionStorage lifetime and TTS preference storage remain unchanged; bulk selection and reader mode are temporary in-memory state.

## 办公文档

三种 Office 格式使用同一套原生 API，无需转换为长字符串或绕过普通 TTS 的 50,000 字符限制。示例：

```bash
.runtime/api/bin/python scripts/tts_document.py report.docx --preview-only
.runtime/api/bin/python scripts/tts_document.py data.xlsx --preview-only
.runtime/api/bin/python scripts/tts_document.py slides.pptx --device gpu --output slides.zip
```

- DOCX 按正文顺序读取段落、超链接显示文字和表格；支持自定义标题样式及继承的大纲层级。自动目录、页眉页脚、批注和图片不进入朗读；修订按最终正文读取，删除内容不朗读。过长（超过 100 字）或以中文句号结束的标题样式段落按正文保留并提示，避免误用样式把分析正文切成章节。手动设置的其他标题仍以预览为准。
- XLSX 自动模式以非空可见工作表分段，按工作簿顺序读取。正式表格或多个短文本单元格组成的加粗表头用于“表头：值”的逐行朗读；无可靠表头的区域保留首行，以键值或顺序文本读取。合并单元格只读取锚点，合并表头关联其覆盖列。隐藏工作表、行和列，以及批注和图形不朗读，并给出警告。
- Excel 公式读取最后保存的缓存结果，不重新计算，也不加载外部工作簿。百分比、小数位、千位分隔、前导零及常见日期/时长按格式呈现；不支持的自定义数字格式保留原始值并提示。公式缺少缓存时正文标记“公式结果缺失”，警告定位到工作表和单元格，请在表格软件中重新计算并保存。缓存可能过时，应在导入前确认源文件已更新；正常的空字符串公式结果不会误报缺失。
- PPTX 自动模式以有正文的可见幻灯片分段，按页面从上到下、同行从左到右读取文本框、组合形状和完整表格；使用标题占位符或顶部文字命名，并保留原页码。讲者备注、隐藏页、页码与页脚占位符不朗读；图片、图表和纯图片页提示无法提取正文。
- Markdown 的导出引用控制标记会清除，普通引用文字保留。PDF 只定向归一化康熙部首兼容字符，书签仍优先于保守的标题检测；“数字｜标题”可作为分段依据，连续重复标题不会重复切段。

Office 解析只安装在 API 环境，采用固定版本 python-docx、openpyxl 和 python-pptx；不需要安装 Office 或 LibreOffice。Excel 流式遍历实际存在的单元格，避免稀疏表格的巨大空白范围。压缩包先校验成员数、展开大小、类型、重复成员、路径及 XML DTD/实体，再进入受时间和内存限制的独立解析进程。

DOC/XLS/PPT 等旧版二进制格式需先另存为 DOCX/XLSX/PPTX；加密文档需先解密。图片/OCR、图表含义和宏执行不属于文本提取范围。新导入使用解析版本 2，已有版本 1 文档正文及未受空白合并影响的预览修订、分段 ID 保持稳定；任务请求/结果契约仍为版本 1，SQLite 仍为 v11，本次不迁移数据库。

English: Office files share the native document import, preview, job and streaming download APIs. Word preserves body/table order and custom outline styles while omitting automatic TOC duplication. Excel reads visible sheets and sparse physical cells, pairs headers with row values, and formats saved formula caches; missing results are explicitly marked without recalculation. PowerPoint reads visible slide text/tables in geometric order, excludes speaker notes, and warns about image-only pages. Review warnings and text before synthesis. Legacy binary Office files require conversion; OCR is not included. Existing parsed snapshots and task contracts remain compatible.


## 导入管理、重试和浏览器草稿

导入默认永久保留，管理列表每页 20 项，支持使用旧文档和确认删除。API 列表接受可选 `offset/limit`，仍返回数组；`storage_bytes` 是源文件、解析 JSON 和日志的大小合计。删除进行中的解析或快照复制返回 409；已提交任务持有独立快照。

上传失败保留当次文件及幂等键，重试同一请求；重新加载页面后若文件对象丢失，需要重新选文件或从管理列表使用已导入的文档。失败解析使用 `POST /api/v1/tts/document-imports/{identifier}/retry` 重新排队（202），不要求幂等键；非失败状态和源文件缺失为 409，准入不足为 429。CLI 可用 `--list-imports`、`--import-id ID --retry-parse --preview-only` 管理和复用导入。

新上传在读取正文前鉴权和预留空间，逐块写入数据目录，不经整文件缓存和复制。文件字节上限由能力声明，multipart 附加开销最多 1 MiB；超限返回 413，失败/断连清理临时文件。没有 Content-Length 的上传也受限。

草稿版本 2 继续使用 sessionStorage，保存导入、预览修订、切分参数及包括“全不选”在内的选择。刷新、语言切换和状态查询重试不会恢复为全选；主动重新切分会全选新预览。正文与音频不进入浏览器存储。受空白合并影响的旧修订提交返回 409；历史任务快照不会重写。

任务章节 API 使用稳定的类型化结构：`index` 为原文分段编号，`position` 为所选任务内顺序；`start/end` 及其 `start_offset/end_offset` 别名、`char_count/basis` 来自快照，状态和音频来自检查点。浏览器下载错误在页内显示并可再次点击下载；429 展示等待时间，401 返回登录流程。开始传输后的状态和中断由浏览器下载管理器显示。

文档页面与短文本页面共享设备能力处理：没有可用 GPU 时显示 CPU 及原因，提交使用界面显示的设备；API 客户端显式指定不可用 GPU 仍返回 503，不在后端静默降级。

## 章节波形 / Section waveforms

每章音频完成后预先准备最多 240 个均匀时间峰值，与单段文本和有序序列共用波形计算和播放器。历史章节首次查看时补算。长音频分块读取，缓存每段仅几 KB，不保存额外音频副本；流式 ZIP 和完整 MP3 下载保持原有机制。

结果页只读取当前章节、或进入可见区域的序列项波形。加载或失败时仍可播放、下载，并可重试波形；切换章节不会沿用上一章的图形。浏览器仅使用最多 64 项内存缓存，退出登录时清空，不新增持久化存储。旧章节产物中的按文本块计算的 `waveform` 字段保留兼容；准确的均匀时间波形通过统一音频产物接口读取。

Each section receives up to 240 uniformly timed peaks after audio generation, sharing the algorithm and player with single-text and ordered-sequence synthesis. Historical audio is backfilled on demand using bounded streaming reads. Only visible audio waveforms are fetched; loading or failure does not block playback or download. The browser keeps at most 64 entries in memory, clears them on logout, and adds no persistent storage. Legacy chunk-based artifact peaks remain for compatibility; the shared artifact waveform endpoint supplies accurate time bins.


## 声纹参考区间

文本与文档合成共用声纹参考设置。默认自动使用样本开头，最多 15 秒；选择声纹库样本后，可通过“高级 · 参考区间”打开波形弹窗，从任意位置选择 3～30 秒，试听并查看对应文字。边界向内对齐到完整词，实际有效时长不足 3 秒时需要扩大选区。

“应用区间”按样本保存在当前浏览器；取消不保存。自定义模式下的“改用自动截取（最多前 15 秒）”只清除当前样本的区间。顶部“恢复默认配置”重置当前合成设置及当前使用的声纹样本区间，其他样本的记忆保留。上传/录音参考保持原行为。

API 和 CLI 参数见 [API：手动声纹区间](API.md#manual-voiceprint-reference-ranges)。文档所有章节使用同一份参考快照和区间；任务结果回显实际使用起止时间、时长与文字。

## 异常生成保护

每个实际送入模型的内部文本块都有保守生成预算，模型未自然结束时不发布该块音频。仅异常块以原音色和参考样本单独重试，最多额外 3 次；正文不拆短，明确的目录长点线仅在重试时清理，标题和页码保留。正常章节不因此重新生成。

重试中显示“正在重试异常语音”，结果可显示“已自动恢复 N 处”。次数耗尽时任务失败，当前章节临时 MP3 清理，已完成章节仍可复用；质量重试与原有资源故障重试分别计数。完整 MP3 只使用已完成章节，因此不会混入被保护机制拒绝的音频。保护摘要缺失的旧章节不能视为已检查。

参考音频区间、采样参数和声纹克隆模式保持不变。保护针对异常延长及无效波形，不等同于所有发音、网址读法或听感问题的自动质检。

### 复核正常生成开销

使用 TTS 专用 Python 运行 `scripts/benchmark_tts_generation_guard.py --cases-json /path/cases.json --device gpu`。案例文件是数组，每项使用 `model`、`voice_mode`、`language`、`speaker` 等正常请求字段，并以 `texts` 数组指定同批文本，例如：

```json
[{"name":"中文对照","model":"qwen3-tts-0.6b","voice_mode":"preset","language":"Chinese","speaker":"Vivian","texts":["你好，欢迎收听今天的节目。"]}]
```

脚本预热后交替比较相同种子的原始调用和保护调用，输出音频哈希、耗时、显存及恢复次数；默认写入独立临时目录。GPU 测试共享原有 GPU 锁，检测到生产任务时停止。正式回归应使用至少 30 组配对案例；CPU 可通过 `--device cpu` 单独验证。该工具不向生产队列提交任务，不更换模型或公开采样参数。
