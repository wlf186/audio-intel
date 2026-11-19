# 工作台设计与主题

Sandevistan-Audio 的工作台沿用深色黄青工业风。切角用于导航和关键操作，青色标识媒体位置、焦点或当前模式，绿色、橙色和红色标识状态。状态同时提供文字或图标。正文使用稳定底色；动作区保留一个亮黄主操作，其余功能使用普通按钮、菜单或对话框。

## 从页面样板开始

| 页面任务 | 桌面参考 | 手机参考 | 新功能放置位置 |
| --- | --- | --- | --- |
| 阅读转写 | [视频结果](../frontend/e2e/design-reference.spec.ts-snapshots/reader-1440-linux.png) | [视频结果](../frontend/e2e/design-reference.spec.ts-snapshots/reader-390-linux.png) | 播放与搜索保持可达；诊断、导出选项进入现有工具栏或弹层。 |
| 编辑与提交 | [合成编辑](../frontend/e2e/design-reference.spec.ts-snapshots/editor-1440-linux.png) | [合成编辑](../frontend/e2e/design-reference.spec.ts-snapshots/editor-390-linux.png) | 输入和设置各自成组；窄屏折叠设置，保持草稿、参考区间与预览播放器。 |
| 列表与详情 | [人员与样本](../frontend/e2e/design-reference.spec.ts-snapshots/library-1440-linux.png) | [人员列表](../frontend/e2e/design-reference.spec.ts-snapshots/library-390-linux.png) | 筛选跟随列表，编辑跟随详情，批量操作仅在选中后出现。返回恢复列表位置。 |

运行 `corepack pnpm@10.34.5 --dir frontend design`，在本机 5174 端口查看三组样板的 390、1024 和 1440 px 图，并操作真实的设置行、确认框、菜单和错误重试控件。预览入口只在开发服务器提供。

## 组件与布局

- 按钮、链接和输入负责自身尺寸、焦点、禁用和错误状态；面板负责内边距与滚动，工具栏负责同级操作的对齐与收纳。分栏内的表单按面板可用宽度重排。
- 设置使用 `SettingToggleRow`。辅助说明和开关分别占据固定列。浮层沿用 `Modal`、`ConfirmDialog` 和 `ActionPopover`，关闭后焦点返回触发控件。
- 列表区分首次加载、成功为空、首次失败和刷新失败；刷新失败保留已加载数据并提供原位重试。
- 桌面高度至少 720 px 时，阅读正文与视口及所有裁剪祖先的交集至少 360 px，同时达到结果面板高度的 60%。目录与正文分别滚动。390 px 宽时，实际命中范围至少 44×44 px，并检查邻近控件和长标签。

## 集中换主题

编辑 [theme.css](../audio_intel/assets/theme.css)。工作台、Canvas、浏览器主题色、Swagger、独立转写稿和有声书共用该源文件。Word 使用其中的印刷颜色。品牌图形使用 [本地图标](../frontend/public/sandevistan-audio.svg)。替换字体、强调色和装饰时，先在预览样板上确认层级、焦点与选中态，再查看完整业务页面。

工作台使用 surface、text、action、status、waveform 等角色；独立阅读和打印使用 reader 系列。用于浏览器主题色和 Word 的颜色保持六位十六进制。控制尺寸、字号、动效和关键按钮切角也在主题文件中定义。构建前端后按 [升级指南](UPGRADE.md) 刷新本机服务。已经导出的独立文件保留导出时的资源。

## 回归与评审

在隔离的 mock 服务上设置 `AUDIO_INTEL_TEST_BASE_URL`，运行：

```sh
corepack pnpm@10.34.5 --dir frontend typecheck
corepack pnpm@10.34.5 --dir frontend build
corepack pnpm@10.34.5 --dir frontend test:e2e
```

`design-system.spec.ts` 验证窄桌面断点两侧的实际正文高度，以及任务记录首次失败后的恢复。`design-reference.spec.ts` 在各平台生成三类样板截图。基准来自 Debian 13、仓库锁定的 Playwright 1.62.1 Chromium 构建 1234、DejaVu 与 Noto CJK 系统字体、设备像素比 1。在相同渲染环境中，设置 `AUDIO_INTEL_VISUAL_BASELINE=1` 执行该用例做像素比较；仅在评审确认有意变更后使用 `--update-snapshots`。Windows 运行交互和几何断言。

UI 变更说明使用哪个样板、动作放在哪里、变窄时如何收纳。附桌面和手机前后图，并检查键盘操作、控制台、中英文长标签、警告与恢复状态。涉及导出时同时检查离线打开和打印。
