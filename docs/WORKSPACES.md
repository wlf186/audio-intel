# 工作台布局 / Workspace layout

PC 浏览器宽度达到 1024 px 时，ASR 音频／视频转写和 TTS 文本／文档合成分别默认 35/65 和 50/50：左侧输入，右侧参数。参数默认展开；参数栏足够宽时自动使用两列，较窄时使用一列并允许内部滚动。复杂的声音克隆和文档配置可能需要滚动，提交按钮固定在参数栏底部。

At browser widths of 1024 px or more, ASR audio/video defaults to 35/65 input/settings panes; TTS text/document creation defaults to 50/50. Settings are expanded, with one or two field columns according to the actual pane width. Complex cloning or document settings may scroll within the pane; submission remains at its bottom.

拖动中间分隔线调整宽度，输入区至少 320 px、参数区至少 400 px。分隔线支持键盘聚焦：左右方向键每次调整 2%，Shift＋方向键调整 10%，Home／End 到达两端限位。双击分隔线或点击底部的“恢复默认布局”按钮回到当前模块默认比例（ASR 35/65，TTS 50/50）。调整布局不会重新挂载输入区，不清空已选文件、正文、视频框选或声纹参考选择。

Drag the divider to resize. Minimum widths are 320 px for input and 400 px for settings. Focus the divider and use Left/Right for 2% steps, Shift+Left/Right for 10%, or Home/End for the limits. Double-click the divider or use its reset button to restore the module default (ASR 35/65, TTS 50/50). Resizing preserves mounted inputs, selected files, text, video regions and voiceprint references.

ASR 和 TTS 比例分别保存在当前站点的 `audio-intel:workbench-layout:v1` localStorage。刷新、切页、关闭浏览器和退出登录后仍保留，不随资料包迁移或跨浏览器同步。恢复参数默认值或替换资料库不清除布局；布局恢复按钮只重置当前工作台。清除浏览器站点数据会删除两种比例。窄屏临时隐藏分隔线、改为上下排列，不改写已记住的桌面比例；浏览器禁用存储时仍可调整当前页面。已保存的旧比例继续生效。输入和草稿保存周期不变，阅读和人员导航状态的保存周期见下文。

ASR and TTS ratios are stored independently in this site's `audio-intel:workbench-layout:v1` localStorage across reloads, navigation, browser restarts and sign-out. They are not transferred in library archives or synchronized across browsers. Parameter resets and library replacement preserve layout; the divider reset affects only the current workbench. Clearing site data removes both ratios. Narrow screens stack the panes and hide the divider without overwriting desktop preferences. Resizing still works for the current page if storage is unavailable. Existing saved ratios remain effective. Input/draft lifetimes are unchanged; reading and person-navigation lifetimes are described below.

五个模块由顶栏主导航标识，内容区不重复显示大标题；读屏仍有唯一一级标题与主区域名称。转写、合成直接展示子页签；资料库的声纹库／热词库页签与资料库工具同行，窄屏允许换行。任务记录保留数量和筛选，系统状态直接展示资源信息。PC 人员列表和样本区分别在可用高度内滚动；仅显示实际有人员的字母索引只滚动人员列表，不切换当前人员。迁移与排序规则见[资料库指南](LIBRARY.md)。

The top navigation identifies all five modules without duplicate visible page headings; a single accessible heading and named main region remain. Transcription and synthesis start with their tabs. Library tabs share a row with Library tools and wrap on narrow screens. Task history retains counts and filters; System status starts with its resources. Desktop people and sample panes scroll within the available height; alphabet navigation scrolls the people list without changing the selected person. See the [library guide](LIBRARY.md) for transfer and ordering rules.

视频转写结果默认显示共用时间刻度的画面缩略图和波形。“画面目录”定位对应时间段的开头；点击波形按准确时间定位，拖动在松开后同步滚动、高亮正文。放大后用横向滚动条或触控板浏览，手机可横向拖动缩略图区；浏览不改变播放时间，“定位播放点”恢复跟随。主动定位逐句明细会清除搜索／说话人筛选，模式切换仍保留各自阅读位置和原有存储期限。PC 截图与文字并排，点击正文截图可放大；阅读模式与画面检测或搜索／筛选共用工具栏。增强状态收在下载旁，点击查看设置强度及实际处理详情；较窄卡片使用“下载音频”菜单。音频结果仍默认展示波形。详见[音视频指南](VIDEO_TRANSCRIPTION.md)。

Video results always show representative thumbnails above the waveform. The scene directory seeks to an interval start; waveform clicks seek to the exact time and reveal/highlight the matching reader content, with drag gestures revealing on release. Persistent timeline scrollbars, trackpads and touch drags over thumbnails browse the zoomed timeline without seeking; Locate playhead restores following. Explicit navigation clears detailed-reader search/speaker filters, while view changes retain independent bookmarks and existing storage lifetimes. Desktop frames sit beside text and open an enlarged view when clicked. Reading mode offers Picture detection; detailed mode offers search/filter controls. Manual section editing is API-only. Processing status opens its details beside audio downloads; narrow cards group both tracks into an audio-download menu. Audio results retain their default waveform. See the [audio/video guide](VIDEO_TRANSCRIPTION.md).

## 分组与导出习惯 / Group and export preferences

桌面声纹库采用等宽的人员管理区与样本详情区，两栏分别滚动。左侧先显示分组筛选与“分组管理”，再显示搜索与“新建人员”；分组新增、重命名及删除集中在管理菜单。列表表头显示当前筛选人数，勾选人员后底部才显示批量移动、删除及清除选择。窄屏继续使用列表与独立人员详情，管理菜单和批量操作均可触达。样本上传参数按详情区宽度排列，调整窗口不会重新挂载表单或播放器。布局整理不新增存储设置，也不改变现有导航、草稿和偏好的保存周期。

English: Desktop voiceprints use equal-width, independently scrolling people and sample panes. Group filtering and Manage groups appear above search and New person; group creation, rename and deletion live in the management menu. The list header shows the filtered count, and selected people reveal a bottom bar for moving, deleting and clearing selection. Narrow screens retain separate list/detail views with reachable actions. Sample controls adapt to the detail pane's width without remounting forms or players. This layout adds no stored settings and preserves existing navigation, draft and preference lifetimes.

声纹库在当前分组内提供批量勾选，切换视图清空勾选；新增人员默认当前筛选分组；全部人员视图默认“未分组”，新建和编辑时均可选择归属。ASR 的“选择分组”默认全部（包含之后新增分组），手动选择只记住指定 ID。加载失败可重试，失效 ID 移除后提示，全部失效时阻止提交，不能扩大到全部。localStorage ASR 偏好 v7 兼容迁移旧版（含正式版 v0.1.33 的 v4）；恢复参数默认和资料库替换恢复全部分组。

转写下载的“脱敏导出”默认开启，规则每行 `原词=替换词`，最多 100 对，错误提示行号。独立 localStorage `audio-intel:transcript-export:v1` 长期保存开关和编辑内容，包括尚未修正的输入；刷新、重开、退出登录、资料库替换均保留。关闭开关停止姓名/备注处理和关键词替换，规则保留；“清空替换规则”仅清空规则、保留脱敏开关，可撤销至清空前的原始输入；继续编辑、离开页面或刷新后撤销失效。正文只替换明确关键词，任务原音和原图保持原样；PDF/DOCX 仍遵循画面隐藏设置。

English: ASR v7 preferences migrate older settings, including published v0.1.33 schema v4, and persist either all future groups or explicit IDs. Invalid choices never widen scope. Library replacement resets group selection to all. New people default to the filtered group, or Ungrouped in the all-people view; creation/editing can choose a group. Export v1 preferences independently persist toggle/editor contents across logout and replacement; Clear replacement rules preserves the redaction toggle and offers exact-text undo until the next rule edit or page unmount. Disabling redaction disables every transformation without losing the rules. Loading/error/retry states remain explicit.

## 处理选项 / Processing options

音频／视频的“转写设置”展开后直接显示“处理选项”，无需再展开“高级设置”。
“单任务加速”和“音频增强”上下排列，标题或说明均可点击切换，也支持键盘空格操作。
单任务加速默认开启，根据设备能力加快处理；悬停或聚焦说明图标可查看完整说明。
单条／文档合成共用相同的加速开关样式，计算方式和请求字段保持不变。

音频增强开启时显示已保存的人声增益和降噪力度；偏离服务端默认值时标记“自定义”，
力度为零时显示“跳过”。点击“调整参数”进入增强高级设置，可分别调整或重置默认值。
关闭后显示“使用原音”，参数入口禁用，之前的力度仍保留。增强组件的可用状态分别展示：
例如降噪模型未就绪时，人声增益仍可用；未知状态不会显示为已就绪。
旧服务缺少力度能力信息时仍可切换增强，但不展示力度摘要或参数入口。

Audio/video transcription settings show Processing options without expanding Advanced settings.
Acceleration and enhancement use stacked, keyboard-accessible switches; clicking either the title
or description toggles its switch. Single-item and document TTS share the acceleration row.
Enhancement shows saved speech/noise strengths, marks custom values, and displays Skip for zero.
Adjust strengths opens the existing dialog with reset-to-server-default support. Disabling enhancement
uses original audio and retains saved strengths. Component availability is reported separately;
unknown capabilities are never presented as ready. Older servers without strength metadata keep
the enhancement switch but hide strength summaries and the adjustment action.
Preference versions, storage lifetime and submission contracts are unchanged; settings apply to new tasks.

## 音频增强与回放 / Audio enhancement and playback

音频／视频设置默认开启一个“音频增强”总开关（`auto`），统一控制背景降噪和人声增益，
取消勾选为 `off`。ASR 偏好迁移到 `audio-intel:asr-preferences:v7` localStorage：
正式版 v0.1.33 的 v4 总开关保留原值并补服务端默认力度；开发版 v5 两项任一开启
则总开关开启，两项均关闭则保持关闭；v6 总开关保留原值并补默认力度。TTS 偏好仍为 v2。
存储期限、退出登录后的保留行为和草稿清理规则不变。

Audio/video forms expose one Audio enhancement switch, enabled by default
(`auto`), for denoising and speech gain; unchecking selects `off`. ASR preferences
migrate to `audio-intel:asr-preferences:v7` localStorage: published v0.1.33 schema v4
keeps its combined value and acquires server-default strengths. Development v5 uses
OR semantics for the independent controls; v6 keeps its master value and acquires defaults.
TTS preferences remain v2.
Storage lifetime, persistence across sign-out and draft-clearing rules are unchanged.

暂停的视频上传继续使用其原参数，包含历史独立组合；旧上传缺少增强字段时按关闭处理。
恢复时参数锁定，需显式清理上传后才能重新选择。结果默认播放原音；实际增强的任务提供
增强音回放和下载，切换保留位置、播放状态并同步波形。波形失败可重试，增益和降噪分别
报告实际状态；降噪未完成时仍可能提供增益后的音频，历史任务不补生成增强音。
详见[音频增强指南](ASR_ENHANCEMENT.md#browser-control--网页总开关)。

Paused video uploads keep their original parameters, including historical
independent combinations; pre-enhancement uploads without the field remain off.
Their settings stay locked until the upload is resumed or explicitly discarded.
Results initially play original audio. Applied results offer enhanced playback and
download, preserving position/play state and updating the waveform. Waveform
failures can be retried. Per-operation results identify partial recovery; gain
can succeed when denoising fails. Historical jobs are not backfilled.

高级设置支持人声增益 0–200%（默认 100%）和 GTCRN 降噪混合 0–100%（默认 50%），
实际默认值来自 capabilities。重置仅恢复两项力度；有效修改即时保存在 ASR v7 localStorage，
v6 偏好保留总开关并补默认强度，存储期限不变。只影响新任务，暂停上传保持原参数。
策略 6 保存力度并在逐项结果返回 `strength`；历史策略 ≤5 不改写、不升级，向旧幂等 key
添加力度返回 409，需新 key。0 跳过对应项；总开关 off 仍关闭两项。

Advanced settings expose independent gain/mix strengths and reset only those values.
Capabilities define ranges/defaults. Valid changes persist immediately in ASR v7;
v6 preferences keep the master value and acquire server defaults without changing lifetime.
Policy 6 snapshots strengths; old requests and paused-upload snapshots retain their semantics.
Adding strengths to a historical idempotency key conflicts (409). Higher values can affect
naturalness or ASR accuracy; safety limits remain fixed and defaults preserve the waveform.

原音／增强音控件将“试听”分段选择与“下载原音／下载增强音”分组，手机上两组分行。
下载原音提供播放器中未增强的 WAV（视频任务同样下载音轨 WAV），不改变试听音轨或进度。
没有增强音时仅提供原音下载；历史任务未保留原音 WAV 时显示不可用，不自动补生成。

Original/enhanced track selection is grouped separately from the two downloads; mobile uses two rows.
Original download serves the saved unenhanced playback WAV, including the audio track of video jobs,
without changing listening state. Jobs without enhanced audio offer only original download;
historical jobs without a saved original WAV show an unavailable explanation and are not backfilled.

## 阅读与人员导航 / Reading and person navigation

窄屏声纹库使用人员列表与独立详情，点击人员进入 `#library/voiceprints/<id>`。返回按钮和浏览器后退恢复筛选、搜索、列表位置及字母索引位置；桌面仍同时显示两栏。详情优先展示已有样本，点击“添加样本”展开上传／录音表单。返回列表保留当前未提交文件或录音；切换人员、离开声纹库或切换分组时提示确认丢弃，正在录音或提交时受到保护。文件内容只保存在当前挂载页面的内存中，刷新不恢复未提交文件。

音视频结果按任务及图文／逐句视图分别记录阅读锚点、位置、已加载数量和逐句搜索／说话人筛选。应用画面检测或更新导出隐藏设置后保留原内容，明确显示刷新状态，读取完成后恢复附近阅读位置。任务被删除后清除对应记录。

这些导航记录保存到当前页签的 `sessionStorage`：刷新与切页保留，关闭页签、退出登录或鉴权失效清除。资料库替换只清除人员导航；导出设置、ASR 参数和桌面比例仍沿用各自长期 `localStorage` 周期。浏览器禁用存储时，当前页面仍可使用导航。

English: Narrow screens use a separate person detail route, with Back/browser history restoring group, search and both list/index scroll positions. Desktop retains two panes. Existing samples appear first; Add sample expands upload/recording. Returning to the list retains an unsubmitted in-memory draft; changing people/groups or leaving the library asks before discarding it. Refresh does not restore file bytes. Readers save an anchor and loaded count per job/view, plus segment filters; refreshing sections keeps the old content visible until current data is ready. Deleted jobs remove bookmarks. Navigation uses current-tab sessionStorage across reload/navigation, cleared on tab closure, logout or authentication invalidation. Library replacement clears person navigation only. Existing localStorage export, parameter and layout preferences retain their independent lifetimes.

结果页的增强状态与本次保存的力度合并为紧凑摘要，“处理详情”展开人声与降噪各自状态；部分回退始终在摘要提示。历史任务未记录的力度不补默认值。

任务记录显示每项任务文件占用，勾选与删除确认显示已知项合计及未知项数量；零字节、统计中与统计失败分开显示，可手动刷新。统计任务专属输入、输出、临时文件，排除共享资料库、模型和数据库；活动任务数据最多缓存5秒，终态30秒。删除后的实际释放可能因数据库压缩、正在写入文件而不同。窄屏在任务名称下显示占用。

English: Enhancement status and saved strengths occupy one compact summary; details show each operation, with partial fallback always visible. History shows per-task owned disk usage and a selection subtotal with unknown counts. Loading/errors never imply zero. Refresh remeasures owned inputs/outputs/temp files, excluding shared libraries/models/database; actual reclaimed space may differ. Clear replacement rules preserves redaction and offers transient exact-text undo; preference storage lifetime is unchanged.

视频结果的「画面检测」弹窗支持变化比例与保持时间，先预览、再应用，详见[视频转写](VIDEO_TRANSCRIPTION.md#结果页画面检测--picture-detection-in-results)。成功应用后记住本浏览器后续视频参数，当前任务仍保留自己的设置；默认值重置只修改待预览值。弹窗关闭丢弃草稿，不占用主阅读区高度。

The video result's Picture detection dialog previews area/duration controls before applying. Applied settings are remembered for future videos in this browser; each existing task keeps its own settings. Reset affects pending controls only. Closing discards the draft and the dialog adds no height to the reading area. See [video transcription](VIDEO_TRANSCRIPTION.md#结果页画面检测--picture-detection-in-results).

画面检测弹窗打开即显示当前画面；滚动自动加载，底部操作始终可达。导出隐藏标记随任务保存在服务端，不使用浏览器存储；应用变化后的检测参数时重置。检测参数的浏览器默认值生命周期保持不变。

English: Picture detection immediately shows current pictures, loads more on scroll, and keeps footer actions reachable. Export visibility is server-owned task metadata, not browser storage, and resets when changed detection settings are applied. Browser detection-default preferences retain their existing lifetime.

音视频任务的桌面“简洁导出”菜单提供 PDF（固定排版）和 Word（DOCX，可编辑），明细与字幕入口保留。手机在原导出弹窗选择 DOCX，默认 PDF；不新增格式偏好存储。

English: The desktop Concise export menu offers PDF (fixed layout) and Word (DOCX, editable), alongside the existing details/subtitles menu. Mobile uses the existing format selector, defaulting to PDF, without new format-preference storage.
