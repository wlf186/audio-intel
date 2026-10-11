# 工作台布局 / Workspace layout

PC 浏览器宽度达到 1024 px 时，ASR 音频／视频转写和 TTS 文本／文档合成都默认左右均分：左侧输入，右侧参数。参数默认展开；参数栏足够宽时自动使用两列，较窄时使用一列并允许内部滚动。复杂的声音克隆和文档配置可能需要滚动，提交按钮固定在参数栏底部。

At browser widths of 1024 px or more, ASR audio/video and TTS text/document creation default to equal input and settings panes. Settings are expanded, with one or two field columns according to the actual pane width. Complex cloning or document settings may scroll within the pane; submission remains at its bottom.

拖动中间分隔线调整宽度，输入区至少 320 px、参数区至少 400 px。分隔线支持键盘聚焦：左右方向键每次调整 2%，Shift＋方向键调整 10%，Home／End 到达两端限位。双击分隔线或点击底部的“恢复左右均分”按钮回到 50/50。调整布局不会重新挂载输入区，不清空已选文件、正文、视频框选或声纹参考选择。

Drag the divider to resize. Minimum widths are 320 px for input and 400 px for settings. Focus the divider and use Left/Right for 2% steps, Shift+Left/Right for 10%, or Home/End for the limits. Double-click the divider or use its reset button to restore equal panes. Resizing preserves mounted inputs, selected files, text, video regions and voiceprint references.

ASR 和 TTS 比例分别保存在当前站点的 `audio-intel:workbench-layout:v1` localStorage。刷新、切页、关闭浏览器和退出登录后仍保留，不随资料包迁移或跨浏览器同步。恢复参数默认值或替换资料库不清除布局；布局恢复按钮只重置当前工作台。清除浏览器站点数据会删除两种比例。窄屏临时隐藏分隔线、改为上下排列，不改写已记住的桌面比例；浏览器禁用存储时仍可调整当前页面。原有输入和草稿保存周期不变。

ASR and TTS ratios are stored independently in this site's `audio-intel:workbench-layout:v1` localStorage across reloads, navigation, browser restarts and sign-out. They are not transferred in library archives or synchronized across browsers. Parameter resets and library replacement preserve layout; the divider reset affects only the current workbench. Clearing site data removes both ratios. Narrow screens stack the panes and hide the divider without overwriting desktop preferences. Resizing still works for the current page if storage is unavailable. Existing input/draft storage lifetimes are unchanged.

资料库只保留一个页面标题，声纹库／热词库由子页签标识。PC 人员列表和样本区分别在可用高度内滚动；A–Z 索引只滚动人员列表，不切换当前人员。迁移与排序规则见[资料库指南](LIBRARY.md)。

The Library uses a single page heading and Voiceprints/Hotwords tabs. Desktop people and sample panes scroll within the available height; alphabet navigation scrolls the people list without changing the selected person. See the [library guide](LIBRARY.md) for transfer and ordering rules.

视频转写结果默认使用紧凑播放进度条，波形可按需展开。PC 截图与对应文字并排，点击截图可放大；“图文阅读／逐句明细”和分段编辑操作共用阅读工具栏。音频结果仍默认展示波形。详见[音视频指南](VIDEO_TRANSCRIPTION.md)。

Video results use a compact seek bar with an optional waveform. Desktop frames sit beside their matching text and open an enlarged view when clicked. Reading mode and scene editing share a toolbar. Audio results retain their default waveform. See the [audio/video guide](VIDEO_TRANSCRIPTION.md).

## 分组与导出习惯 / Group and export preferences

声纹库在当前分组内提供批量勾选，切换视图清空勾选；新增人员进入“未分组”。ASR 的“选择分组”默认全部（包含之后新增分组），手动选择只记住指定 ID。加载失败可重试，失效 ID 移除后提示，全部失效时阻止提交，不能扩大到全部。localStorage ASR 偏好 v4 兼容迁移 v3/v2/v1；恢复参数默认和资料库替换恢复全部分组。

转写下载的“脱敏导出”默认开启，规则每行 `原词=替换词`，最多 100 对，错误提示行号。独立 localStorage `audio-intel:transcript-export:v1` 长期保存开关和编辑内容，包括尚未修正的输入；刷新、重开、退出登录、资料库替换均保留。关闭开关停止姓名/备注处理和关键词替换，规则保留；“恢复默认导出设置”开启并清空规则。正文只替换明确关键词，音频和画面保持原样。

English: ASR v4 preferences migrate older settings and persist either all future groups or explicit IDs. Invalid choices never widen scope. Library replacement resets group selection to all. Export v1 preferences independently persist toggle/editor contents across logout and replacement; reset enables redaction and clears rules. Disabling redaction disables every transformation without losing the rules. Loading/error/retry states remain explicit.
