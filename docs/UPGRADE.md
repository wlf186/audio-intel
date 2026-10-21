# 升级指南

## 升级前

先阅读当前版本到目标版本之间的全部升级说明，按[升级备份范围](#upgrade-backup-scope)确定是否需要备份。确认没有未完成任务或导入，记录实际启动配置，再停止全部服务。`data/` 包含 SQLite 队列、历史输入输出、声音档案和声纹库；升级应保留这些数据。先阅读下方的本机部署收尾要求。

如果升级前的旧版入口尚不支持自动加载 `.env`，先在原 shell 中执行一次 `if [[ -f .env ]]; then set -a; source .env; set +a; fi`，再按下方流程停服，确保仍使用原端口和 PID 目录。更新后的入口自动读取 `.env`，日常启停无需该步骤。维护快照 `.env.local-deploy` 仅在明确需要恢复其配置时显式加载；不要仅因文件存在就自动叠加，以免旧快照覆盖新配置。

```bash
set -e
# 当前版本的入口会自动加载 .env；外部显式环境变量仍优先。
./service.sh stop all
git pull --ff-only
# 仅更新源码不会迁移数据库；可取得新版备份工具。
# 在 setup/start 或任何数据迁移前完成下文要求的备份；失败则停止。
./service.sh setup all
./service.sh start all
```

setup 会自动沿用 `.runtime/deployment-profile` 中的 full/cpu 配置；默认配置仍为 full。不要在升级时仅重建单个推理环境来切换配置。需要切换时，先停止服务并清空非终态任务，再执行 `./service.sh setup all --profile cpu` 或 `--profile full`。Windows 使用同名 `service.cmd` 命令。

Windows 使用 `service.cmd`，备份范围遵循同一规则；命令失败时不要继续执行后续步骤。

## Upgrade backup scope

### 按升级影响选择备份

| 当前版本到目标版本之间的升级影响 | 升级前备份要求 |
| --- | --- |
| 明确无数据库迁移，也不改写或删除历史文件 | 不强制创建升级备份；普通调试和重启同样无需重复复制数据 |
| 仅迁移数据库，历史文件保持不变 | 创建 SQLite 数据库一致快照 |
| 改写或删除历史音频、声纹等持久化文件 | 备份数据库及升级说明列出的受影响文件 |
| 确需保护大量历史文件，或核实后仍无法确认影响且必须继续升级 | 完整备份实际数据目录 |

每个 Release 应写明“数据迁移：无／仅数据库／涉及文件”、适用的起始版本及备份范围。跨版本升级必须覆盖中间版本；不能仅凭目标版本号小、schema 未变化或最新一条说明判断。新增任务正常生成文件不属于这里的历史文件迁移。日常数据备份按数据重要性单独安排，不绑定每次发版。

需要备份时，先停止使用该数据目录的全部服务，确认进程树退出，在新版本首次启动或迁移操作前完成备份。数据库和关联文件必须对应同一停服时点。本节升级备份放在项目内被 Git 忽略的 `backups/`，不得放入待复制的数据目录内部。沿用 `AUDIO_INTEL_DATA_DIR` 的实际配置，不要误备份默认路径。

### 按需备份与验收后清理（Linux / Windows）

**不要仅因 schema 变化就全量复制数据。** 先核实跨版本迁移影响；没有持久化数据迁移时不创建额外升级备份，仅数据库迁移只创建 SQLite 一致快照，不复制视频、音频和任务文件。涉及历史文件时只补充受影响文件；确实需要时才选择 `full`。不要为了升级每次复制几十 GB 的媒体。日常备份与临时升级备份分别管理。

服务入口不会自动创建或删除升级备份。使用项目自带的标准库工具，必须明确指定实际数据目录和范围。工具不从客户端 shell 推断服务的 `.env` 配置。先记录实际目录、确认无未完成工作、停服并确认进程树退出。旧版本尚未包含工具时，先仅执行 `git pull --ff-only` 获取源码，再创建备份；源码更新本身不会迁移数据库。必要备份须在新版本 `setup`、`start` 或其他迁移操作前成功完成：

Linux：

```bash
# 按实际部署配置填写；以下 data 仅适用于默认数据目录。
.runtime/api/bin/python scripts/upgrade_backup.py create --data-dir data --scope database
```

Windows PowerShell：

```powershell
.\.runtime\api\Scripts\python.exe scripts\upgrade_backup.py create --data-dir data --scope database
if ($LASTEXITCODE -ne 0) { throw "Backup failed; stop the upgrade" }
```

仅在确需完整目录备份时将 `database` 改为 `full`，不要同时执行两种范围。SQLite 使用 Backup API，包含尚在 WAL 中的已提交数据，并验证完整性和外键；不能只复制主数据库文件。记下 `Verified backup:` 输出的准确目录、备份范围和大小。涉及文件的升级将指定受影响文件以原相对路径补充到同一备份目录的 `data/` 下，完成前保持停服。失败可能留下标记为 creating 的不完整目录，不得用于回退。

**完整升级验收通过后，必须及时清理本次临时升级备份及同次失败副本。** 验收包括数据库完整性、历史任务和关联文件可读性、相关功能，以及 API、supervisor 和 executor 来自最终代码的检查；不能仅以健康探测、进程存活或 GitHub 发布成功作为依据。执行升级的人或代理负责清理和记录结果。未完成验收、验收失败或仍在回退时保留恢复资料。

验收通过后，针对本次记录的每个具体目录分别执行：

```bash
.runtime/api/bin/python scripts/upgrade_backup.py cleanup backups/upgrade-database-实际目录后缀 --verified
```

```powershell
.\.runtime\api\Scripts\python.exe scripts\upgrade_backup.py cleanup backups\upgrade-database-实际目录后缀 --verified
if ($LASTEXITCODE -ne 0) { throw "Backup cleanup failed; resolve before closing maintenance" }
```

将示例路径替换为实际记录，不能传空路径、`backups/` 根目录、数据目录或其他备份。工具核对升级备份标记及目录范围，拒绝符号链接和越界删除；`--verified` 表示执行者确认完整验收已通过，不会替代验收。清理成功后记录目录、时间和释放字节数；失败时报告并处理，不能把备份长期留存当作升级完成。不要使用通配符清空备份根目录。既有未受此工具管理的临时升级备份，需核实来源及已通过的验收后精确清理；日常备份、资料库替换备份和历史专项备份遵循各自保留规则。

回退必须停服，并使用同一时点的数据库及受影响文件。新版本已处理任务或修改文件后，不能直接用旧数据库覆盖当前状态。验收通过并清理临时备份后，该临时回退点不再存在；长期恢复由独立日常备份承担。

English: Choose the smallest necessary upgrade backup scope: no extra backup without persistent migration, a consistent SQLite-only snapshot for database changes, and affected files only for file migrations. Do not copy large media merely because the schema changes. Use `scripts/upgrade_backup.py create --data-dir <actual-directory> --scope database|full` after stopping all users of that directory. If the old checkout lacks this tool, first pull only the new source, then back up before setup, startup or any migration; pulling source does not migrate data. Record its exact output path. After complete local deployment acceptance—including database integrity, history/files, functionality, and final API/supervisor/executor identities—**promptly remove every temporary backup from this upgrade**, including failed attempts, using `cleanup <recorded-directory> --verified`. Failed or incomplete acceptance retains rollback material. The tool validates purpose/path markers and refuses root, outside and symlink directories. Daily and library-replacement backups remain separately managed. Resolve cleanup failures before closing maintenance.

## Local development and deployment

### 单目录工作约定

本机开发期间可以停止日常服务；继续使用同一份源码、运行环境和模型，无需创建第二套部署。以下流程适用于本机代码更新和开发/发版收尾，纯只读检查不触发启停。

1. **记录现场。** 在停服前记录实际端口、host、数据及运行目录、启用的服务、full/cpu profile、mock 状态和 TLS 配置来源，以及 API、ASR/TTS supervisor、executor 和子进程的 PID 与创建时间。非敏感启动参数可以放在被忽略的 `.env.local-deploy` 中；鉴权密钥从原有安全来源加载，不写入日志或部署记录。服务入口自动读取 `.env`，但不自动读取维护快照；需要恢复 `.env.local-deploy` 时仍显式加载。Windows 的 `.env` 使用相同键值语法，维护快照中的 Bash 语句不能在 PowerShell 中执行，应设置对应环境变量。
2. **停止后再修改。** 先检查 queued/running/cancelling 任务、排队或执行中的文档导入等操作；有未完成工作时等待或报告，不擅自取消。进入停服窗口时可先 `stop api` 关闭新提交入口，再检查一次队列，确认空闲后 `stop all`。如出现新任务，恢复原 API 并等待，避免 `stop all` 中断它。确认记录的旧进程树已退出，再编辑运行代码或安装依赖。
3. **按变更验证。** 测试沿用现有环境，临时实例使用独立的数据、PID、日志、缓存和临时目录。正式升级和迁移开发均按[升级备份范围](#upgrade-backup-scope)选择备份，记录选择依据及实际路径；明确无数据迁移的升级无需额外复制数据。依赖锁变化才同步对应环境，前端源码/版本/依赖变化才重建前端；模型变化按 manifest 准备。纯 Python 修改不需要每次执行 `setup all`。测试进程要在收尾时退出，不能把 mock 服务留作日常实例。
4. **最终代码确定后恢复。** 完成最后一次修改、测试及相关 tag 操作，记录目标 SHA、预期应用版本和源码就绪时间。正式 release 部署要求已验证的干净提交；本地未提交修改要另外记录 dirty 状态和 diff 摘要，不能宣称部署了干净的发布版本。清理残留测试/服务进程，按记录的配置和组件重新启动。原本停止的组件保持停止，除非用户要求启动；用户要求继续停服时记录待部署状态。收尾后再次修改运行代码，必须重新完成这一轮。
5. **验收并清理后才算完成。** 按下表检查，并在被忽略的 `logs/local-deploy.json` 中记录部署状态、目标 SHA/dirty 状态、预期及实际版本、源码就绪时间、进程身份、检查结果和失败原因。开始维护时也记录原运行组件及配置文件位置，让后续会话能够继续收尾；不要保存凭据。GitHub Release 成功、本机部署成功分别报告。完整验收通过后按上文精确清理本次临时升级备份及同次失败副本，记录释放空间；清理失败需处理并报告。失败时保留诊断和待办，不能把发布成功当作部署成功；回退如涉及数据库迁移，必须配套使用升级前备份，不能只回退源码。

| 验收项 | 判断依据 |
| --- | --- |
| 实例与配置 | 实际监听端口/协议、数据路径、profile 和启用组件符合维护前记录；不要因新终端没有环境变量而回到默认端口或启用 mock |
| API 版本 | 从真实运行实例读取 `/api/v1/health`，与最终源码解析出的预期版本一致；受保护信息继续使用正常鉴权 |
| 旧进程退出 | 维护前记录的 PID **及创建时间**对应的进程均已退出；不能仅凭 PID 不同判断 |
| 全部新进程 | API、启用的 ASR/TTS supervisor 和各自 executor 均来自目标目录/运行环境，并在源码就绪时间之后创建；executor 元数据与实际父子关系匹配 |
| 功能与历史 | worker 已注册并可接任务，历史任务可读；按变更选择短任务验收，文档合成相关事故应覆盖文档 TTS 入口 |

`status` 和启动就绪检查主要确认进程存活及注册状态，现有 worker 没有独立公开代码 SHA。上述验收通过“最终代码固定后启动全部新进程”的顺序确认装载一致性；API 的版本号不能代表仍在运行的其他旧进程。

### 为什么发布后很久仍可能运行旧代码

Python 会缓存已经导入的模块，首次使用时才导入的模块则可能从更新后的磁盘加载。同一进程因此可能同时使用旧管线和新文档模块，直到之后的任务才暴露不兼容。提交代码、打 tag 或发布 GitHub Release 都不会自动替换本机进程。

默认 60 秒空闲回收用于释放**用过的**执行器资源，不是定时检查或部署新版本。新建但尚未执行任务的 executor 可以长期待命，supervisor 和 API 也不会因此重启。不要通过等待、只重启 API 或缩短空闲窗口来代替完整部署收尾。

English: A commit or GitHub Release does not refresh a running local process. Stop the existing instance before changing runtime code/dependencies, retain its configuration and original component state, then restore and verify it after the final code/tag changes. Verify the API version and the identities, creation times and paths of supervisors and executors; API health alone is insufficient. Keep local maintenance evidence so interrupted work can resume. Idle recycling manages used-executor resources, not deployment. Read-only release inspection does not authorize local lifecycle changes.

## v0.1.37 稳定截图修复 / Stable picture confirmation

从 v0.1.36 升级：**数据迁移：无**，无历史媒体批量改写，无需额外升级备份；更早版本须覆盖中间版本的迁移要求。新视频使用 presentation-v3、视频结果契约 2，客户端需支持成对为空的 image/frame_time 和为空的预览 preview_url。旧结果保持原样，v1/v2 重试保留原算法；历史任务通过“预览检测 → 应用预览”显式启用修复。无达标截图的分段显示“无稳定画面”，文字与时间范围保留；PDF/DOCX 同样使用占位。保持时间输入支持 0.1 秒步进，范围和浏览器偏好键、寿命不变，既有特征缓存继续复用。

English: From v0.1.36, no database or historical-file migration and no extra upgrade backup is required; earlier upgrades must cover intervening migration requirements. New videos use presentation-v3 and video contract 2, including paired nullable image/frame_time and nullable preview_url. Clients must handle no-picture sections. Historical results and v1/v2 retry semantics remain intact; explicit preview and apply enables the fix. PDF/DOCX retain text with placeholders. Duration input supports 0.1-second steps within the existing range; browser preference keys/lifetimes and raw feature-cache identity are unchanged. Dependencies and models are unchanged. Follow the maintenance procedure: stop the recorded idle service, rebuild the frontend, and restore and verify the API, supervisors and executors. Refresh browser pages after upgrading.

## v0.1.36 转写任务切换修复

从 v0.1.35 升级：**数据迁移：无**。不修改数据库、历史请求/结果、媒体文件或浏览器存储契约，无需额外升级备份；更早版本仍须覆盖中间版本的迁移要求。

修复“任务与结果”中音频、视频任务反复切换后，旧视频缩略图和波形残留、重复叠加的问题。时间轴和图文阅读组件使用独立的任务标识，保留原有播放、定位及阅读位置逻辑。新增桌面和 390 px 手机端的缓存任务切换回归，覆盖音视频往返、不同视频互切、定位和阅读模式切换。

按本页维护流程记录配置并等待空闲，停止原服务后更新源码，运行 `corepack pnpm@10.15.1 --dir frontend build` 重建前端，然后恢复原配置并验证 API、ASR/TTS supervisor、executor 和历史任务。依赖与模型未变化，无需重新下载模型或同步推理环境。刷新已打开的浏览器页面以加载修复。

English: From v0.1.35 there is no database or historical-file migration and no extra upgrade backup is required. Repeated switching between cached audio/video results now removes the previous timeline instead of retaining or duplicating thumbnails and waveforms. Playback, reader bookmarks and browser-storage lifetimes are unchanged. Desktop and 390 px regression tests cover audio/video and video/video switching, seeking and reading modes. Stop the recorded service after it becomes idle, update source, rebuild the frontend with `corepack pnpm@10.15.1 --dir frontend build`, and restore and verify the original API, supervisors and executors. Dependencies and models are unchanged. Refresh browser pages; earlier upgrades must cover intervening migration requirements.

## v0.1.35 画面检测、阅读体验与 Word 导出

从 v0.1.34 升级：**数据迁移：无**，schema v14、历史请求/结果与媒体不批量改写，无需额外升级备份。更早版本仍须覆盖中间版本的迁移要求。

- 音视频简洁导出支持 PDF 与可编辑 DOCX，复用已有 python-docx；文本脱敏、关键词替换和画面隐藏保持一致。DOCX 不嵌入字体，不同 Word/WPS 字体环境可能产生分页差异。
- 画面检测 v2 提供最小变化比例与最短保持时间，可预览后应用而无需重新转写。画面预览自动加载，支持批量隐藏/恢复导出图片；图文阅读显示标记，导出使用占位图并保留文字。手工分段仅保留 API。
- 时间轴横向滚动、点击波形/缩略图定位阅读、紧凑处理状态及导航改善结果页空间；任务列表显示单项与勾选空间合计，清空关键词规则保留脱敏开关。
- 新增只读任务占用和窗口波形接口；全局列表/SSE 保持摘要契约。画面检测实际参数、最近自动分段、隐藏画面 ID 为可选结果字段；历史缺失不代表使用了新默认参数。
- API Pillow 升至 13.0.0；各环境 certifi 升至 2026.10.19，模型锁中 hydra-core/urllib3/Werkzeug 同步安全补丁；模型环境 Pillow 约束及 VEX 见[依赖维护](DEPENDENCIES.md#模型环境-pillow-vex--model-runtime-pillow-vex)。前端含 Vite 7.3.7 安全更新。

按本页维护流程记录配置并等待空闲，停止全部原进程后更新源码；执行 `./service.sh setup all` 同步本次受影响的四个隔离环境和前端，复用已有模型及部署 profile，不切换 CPU/GPU 配置。恢复原 API、ASR/TTS supervisor 和 executor，并验证最终版本、全部新进程及历史任务。

画面检测新增独立 localStorage 偏好，仅成功应用后保存，退出登录保留；弹窗草稿不持久化。导出偏好键与寿命不变，DOCX 格式选择不持久化，默认 PDF；清空规则的撤销仅在当前页面下一次编辑前有效。新视频使用 v2，历史重试保留原检测版本；特征缓存仅按需生成，不在启动时扫描全部视频。

English: From v0.1.34 there is no database/file migration or extra upgrade backup. This release adds editable DOCX exports, tunable stable picture detection, export-hidden picture markers/placeholders, automatic preview loading, timeline-to-reader navigation and per-job/selected storage totals. Text remains intact when pictures are hidden, independently of text redaction. Manual section editing remains API-only. Historical jobs keep their saved semantics and optional metadata is not backfilled.

API Pillow becomes 13.0.0 and all runtimes use certifi 2026.10.19, with targeted hydra-core/urllib3/Werkzeug security updates in model locks; model-runtime Pillow remains constrained with documented, narrowly scoped VEX assessments. Vite is patched to 7.3.7. Stop the recorded instance, update source and run `./service.sh setup all` to sync affected environments/build the UI while preserving the existing profile and models, then restore and verify all original components. Earlier upgrades must cover intervening migrations. Picture detection preferences persist after successful apply and survive sign-out; dialog drafts and DOCX format selection do not persist. Export preference lifetime is unchanged and PDF remains the default. DOCX uses installed fonts, so pagination can vary across Word/WPS systems.

## v0.1.34 音频增强、原音下载与处理选项

本轮还支持下载播放器中的原音 WAV（视频任务同样下载音轨）；试听切换与原音／增强音下载分组，手机分行显示。复用保存的文件，不补生成历史音轨；默认回放接口行为保持不变，仅新增 `download=true` 附件模式。

Original playback WAV downloads are also available for audio/video jobs. Listening and download controls are grouped separately, stacking on mobile. Existing files are reused without historical backfill; the playback endpoint adds optional `download=true` while retaining its default behavior.

从 v0.1.33 升级：**数据迁移：无**，无需额外升级备份。Qwen ASR/TTS 模型、
隔离环境和依赖锁不变；新增的实验性 UL-UNAS 模型见下文，GTCRN 仍为默认。
历史任务、请求、媒体及声纹不改写，重放／重试沿用保存的语义。更早版本升级仍须覆盖
中间迁移要求；跨 v0.1.30 的 v13 → v14 迁移只需停服后的 SQLite 一致快照，无需复制媒体。

网页音频／视频使用一个默认开启的“音频增强”总开关，API 推荐同一
`audio_enhancement=auto|off`，不再标为废弃；独立参数继续兼容，不能与总开关混用。
正式版 v0.1.33 的 ASR 浏览器偏好由 v4 迁移到 v7：保留原总开关的 `auto`/`off`，
补入服务端默认力度。开发版 v5 的两项开关取任一开启为 `auto`、均关为 `off`；
开发版 v6 保留总开关并补默认力度。存储期限、退出登录后的保留行为和草稿清理不变；
TTS 偏好仍为 v2。暂停上传保留原参数，包括旧独立组合。

当前新任务保存策略 6、两项有效开关、数值力度及服务端选定的降噪后端；保留 GTCRN 50% 混合和
策略 4 引入的跨块连续增益（6 秒上下文、每秒最多变化 3 dB），不增加降噪强度。
策略 3 已撤下策略 2 的间隙门控；策略 5 不再应用间隙门控，衰减诊断为零，原因为
`disabled_for_naturalness`。降噪和增益分别恢复，降噪失败后仍可应用增益；检查逐项状态，
不能把总状态 `applied` 与 `partial_fallback` 视为两项全部成功。

`setup asr/all` 还会下载固定 revision/checksum 的实验性 UL-UNAS-DNS3。其 100% 和
75% 混合未通过公开样本的识别准确率验证，不作为日常默认。管理员实验设置
`AUDIO_INTEL_ASR_DENOISE_BACKEND=ulunas`；恢复 `gtcrn` 并刷新全部进程后影响新提交任务，
不会改写已保存的请求。旧 `AUDIO_INTEL_ASR_CONTINUOUS_GAIN=0` 仅选择旧处理链的
策略 3，不会把策略 5/6 任务回退为策略 3。

按本页维护流程等待队列空闲，保留原配置，停止后更新；前端来源或版本变更时重建前端，
仅文档元数据变更不需要重新 setup。刷新原来运行的 API、supervisor 与 executor，并
验收真实端点、OpenAPI 和历史任务。两份临时 PCM WAV 约每小时 230 MB，完成、失败
和取消后清理中间文件；最终仍保留原音及至多一份增强音。增强进度累计各遍处理量，
不能当作录音时长；读取 `result.duration` 获取实际时长。

English: Upgrading from v0.1.33 requires no database/media migration or extra
upgrade backup. Qwen ASR/TTS checkpoints, environments and dependency locks remain
unchanged; setup adds the experimental UL-UNAS checkpoint described below, with
GTCRN still the default. Historical requests/results/media retain their saved semantics.
Earlier upgrades must cover intervening migrations, including the v13 → v14
database-only migration across v0.1.30: take a stopped SQLite snapshot, without copying media.

Audio/video forms and recommended API examples use one combined
`audio_enhancement=auto|off` control, enabled by default and no longer deprecated.
Independent fields remain compatible and cannot be mixed with it. ASR localStorage
migrates from published v0.1.33 schema v4 to v7, preserving the combined value and
adding server-default strengths. Development v5 merges independent controls with
OR semantics; v6 preserves the master value and acquires default strengths. Storage
lifetime, sign-out persistence and draft clearing are unchanged. TTS preferences
remain v2; paused uploads keep their saved parameters, including mixed combinations.

New tasks use policy 6 with snapshotted controls, strengths and denoiser. The validated GTCRN
50% mix and policy-4 continuous gain remain unchanged (six-second context, at most
3 dB/s). Denoising and gain recover independently; inspect each operation when
aggregate status is `applied` with `partial_fallback`. Pause attenuation remains
zero with `disabled_for_naturalness`. The old continuous-gain override applies only
to legacy requests and does not downgrade policy-5/6 jobs.

Setup downloads the pinned experimental UL-UNAS checkpoint, but its 100%/75%
candidates failed public CER validation; GTCRN remains the default. Administrator
backend changes require refreshed API/supervisors/executors and affect new jobs
only. Follow the maintenance/acceptance process above. Rebuild the frontend for
frontend source/version changes; metadata-only edits need no setup. Allow about
230 MB per audio hour for two temporary PCM WAVs; intermediates are cleaned and
retained artifact count is unchanged. Enhancement progress accumulates work across
passes; recording duration is `result.duration`.

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

处理选项直接展示上下排列的“单任务加速”和“音频增强”，支持点击标题、说明或键盘操作；
显示已保存力度、自定义／跳过状态及逐项可用性，调整参数可恢复默认。TTS 文本／文档
共用同一加速样式和偏好，未改变加速计算或质量设置。

Processing options now show stacked acceleration/enhancement switches, saved strengths,
custom/skip states and per-operation availability, with an adjustment/reset dialog.
Text/document TTS share the acceleration row and preference. Acceleration behavior
and quality settings remain unchanged. See [workspace behavior](WORKSPACES.md#处理选项--processing-options).

## v0.1.33 本地 ASR 人声增强

从 v0.1.32 升级本次增强变更：**数据迁移：无**，无需额外升级备份。历史任务、请求、媒体和声纹不改写；新增任务默认自动增强，旧请求缺少字段时继续按关闭处理。新增可选 GTCRN DNS3 模型由 manifest 固定 revision 和 checksum，运行时 CPU 离线处理；原 ASR/TTS、Python 和依赖锁保持不变。

运行 `./service.sh setup asr`（Windows：`service.cmd setup asr`）准备增强模型，重建前端，然后按本页维护流程刷新原来运行的 API、supervisor 和 executor。模型暂未安装时基础 ASR 仍可用，自动模式记录回退。增强回放额外保存一份 16 kHz PCM WAV，约每小时 115 MB；纯旁路任务不增加该文件，随任务清理。ASR 设置沿用现有 localStorage 生命周期，草稿与 sessionStorage 清理规则不变。详见[人声增强契约](ASR_ENHANCEMENT.md)。

从已部署的增强开发源码升级同样无持久化迁移。跨 v0.1.30 升级仍需完成 v13 → v14 的数据库迁移：停服后创建 SQLite 一致快照，无需复制媒体；更早版本覆盖中间升级要求，完整验收后清理本次临时升级备份。

English: From v0.1.32 or the enhancement development source, no data migration,
historical rewrite or additional upgrade backup is required. Install the optional
pinned enhancer with `setup asr`, rebuild the frontend and refresh all previously
running components. New jobs default to automatic processing; historical missing
fields preserve disabled behavior. Applied jobs retain a separate enhanced WAV
(about 115 MB/hour); bypassed jobs do not. Existing storage lifetimes and dependency
pins remain. Upgrading across v0.1.30 still requires the v13 → v14 database-only
migration and a stopped SQLite snapshot; earlier installations must cover all
intervening requirements. Clean temporary upgrade backups after complete acceptance.

## v0.1.32 声纹库布局与工作台交互优化

从正式发布的 v0.1.31 或已部署的后续工作台开发源码升级：**数据迁移：无**，无需额外升级备份，不改写数据库、历史任务、音视频或声纹样本。模型、依赖锁和公共 API 契约不变。重建前端，并按上面的本地部署流程刷新原运行的 API、ASR/TTS supervisor 和 executor。更早版本必须覆盖中间升级要求；从 v0.1.30 跨版本升级仍需下节的 schema v13 → v14 SQLite 一致快照，无需复制媒体文件。

桌面声纹库改为等宽人员管理与样本详情两栏，分别滚动。分组操作集中在“分组管理”菜单，搜索和新建人员单独成行，勾选后才显示底部批量操作；样本表单按详情区宽度排列，调整窗口保留表单和播放器。窄屏保留人员列表与独立详情，返回时恢复筛选和位置；未提交样本保存在页面内存，离开或切换人员时受到丢弃确认保护，刷新不恢复文件。新建人员默认当前筛选分组，全部人员视图默认“未分组”。

本版同时交付 v0.1.31 之后的工作台修复：ASR 新用户桌面布局默认 35/65，已有比例继续生效；音视频阅读恢复任务、视图、位置、加载数量及逐句筛选，编辑刷新后保留附近阅读位置；窄屏导出统一到弹窗，时间输入与视频声纹入库步骤更清晰。新增 `audio-intel:workspace-session:v1` sessionStorage 保存当前页签的导航记录，刷新和切页保留，关闭页签、退出登录或鉴权失效清除；资料库替换只清除人员导航。原有 localStorage 布局、导出设置和 ASR 参数的保存周期不变。详见[工作台指南](WORKSPACES.md)和[资料库指南](LIBRARY.md)。

English: From released v0.1.31 or subsequent workspace-development source, **no data migration or additional upgrade backup** is required. Historical database records, jobs and media are not rewritten; models, dependency locks and public API contracts are unchanged. Rebuild the frontend and refresh all originally running API, supervisor and executor processes. Earlier upgrades must cover intervening requirements; from v0.1.30, the v13 → v14 database-only migration below still requires a consistent SQLite snapshot without copying media. Desktop voiceprints use equal, independently scrolling panes with grouped management actions and selection-only batch controls; responsive sample forms preserve mounted inputs and players. Narrow screens restore person navigation and protect in-memory drafts, whose file bytes do not survive reload. New people default to the filtered group, or Ungrouped in the all-people view. This release also delivers workspace/reader, mobile export, time-input and enrollment improvements since v0.1.31. New current-tab `audio-intel:workspace-session:v1` sessionStorage retains navigation across reloads, clears on tab closure/logout/auth invalidation, and clears only person navigation on library replacement. Existing localStorage preference lifetimes and saved desktop ratios remain effective; new ASR layouts default to 35/65.

## v0.1.31 声纹分组与脱敏导出

从 v0.1.30 升级：**数据迁移：仅数据库**，schema v13 → v14 新增声纹分组及人员归属表，既有人员进入“未分组”。首次启动前仅需 SQLite 一致快照，不复制视频、音频或历史任务文件；完整验收通过后必须清理本次临时升级备份。更早版本需覆盖中间升级要求。模型和依赖锁不变，重建前端并刷新原运行的全部服务及执行器。

声纹库支持分组及批量移动/删除；ASR 音频、视频和兼容提交支持分组范围，接受请求时固定人员 ID，旧任务保持原行为。资料库导出使用 ZIP v2，导入兼容 v1；合并保留已有人员目标端分组，替换恢复包内分组。历史请求、结果和文件不自动改写。

全部转写格式支持默认开启的按需脱敏，关闭后导出原文。姓名缩写、备注遮盖和最多 100 组字面关键词替换只作用于下载副本；音频和视频画面保持原样。ASR 偏好迁移至 localStorage v4；分组选择长期记忆，资料库替换恢复全部。独立 `audio-intel:transcript-export:v1` 长期保存脱敏开关和规则，退出登录及资料库替换后保留，恢复默认导出设置时开启并清空规则。

English: From v0.1.30 this is a database-only v13 → v14 migration; take a consistent SQLite snapshot without copying media, and remove this upgrade's temporary backup after complete acceptance. Existing people enter Ungrouped. Eligible voiceprint IDs are snapshotted on submission. ZIP v2 retains groups while imports accept v1. No historical jobs or files are rewritten. Default-on redaction transforms download copies across PDF/JSON/TXT/SRT/VTT; audio/images remain unchanged. ASR preferences migrate to localStorage v4. Export policy uses independent persistent localStorage v1, retained on logout/library replacement and cleared by its own reset. Models and dependency locks are unchanged.

## v0.1.30 资料库迁移、拼音索引与 PC 工作台

从正式发布的 v0.1.29 升级：**数据迁移：仅数据库**，首次启动前停止服务并创建 SQLite 一致快照，具体见下文。已完成资料库开发版 schema v13 迁移的实例无新增数据迁移，无需重复升级备份。更早版本须覆盖中间版本要求。同步 API 依赖锁、重建前端，并刷新原运行的 API、ASR/TTS supervisor 和 executor。

English: From released v0.1.29, this is a **database-only migration**: stop and snapshot SQLite before first startup as described below. Library-development installations already at schema v13 require no additional migration or upgrade backup. Earlier installations must cover intervening requirements. Synchronize API locks, rebuild the UI and refresh all originally running API, supervisor and executor processes.

### PC 工作台布局 / Desktop workspaces

布局调整本身不增加 API、数据库或模型变更。桌面 ASR/TTS 新建页面默认 50/50、可拖动且参数展开，资料库移除重复标题，视频阅读采用截图与文字并排。

新增 `audio-intel:workbench-layout:v1` localStorage，分别长期保存 ASR/TTS 分栏比例，退出登录和资料库替换后保留。恢复参数默认不清除布局；双击分隔线或其恢复按钮只重置当前工作台。原有草稿周期不变。操作和存储说明见[工作台布局](WORKSPACES.md)。

English: The layout changes themselves add no API, database or model changes. Desktop creation panes default to 50/50 with expanded settings. The new localStorage key preserves independent ASR/TTS ratios across sign-out and library replacement. Parameter resets preserve layout; divider resets affect only the current workbench. Existing draft lifetimes are unchanged. See the workspace guide.

### 资料库迁移与拼音索引 / Library transfer and ordering

**数据迁移：仅数据库**，schema v12 → v13 新增独立资料导入记录表，不改写既有音频、历史任务或热词快照。首次启动新代码前停服并按上面的升级备份规则创建一致 SQLite 快照。新增 API 离线拼音依赖 pypinyin 0.55.0，需同步 API 环境的 Linux/Windows 哈希锁并重建前端；ASR/TTS/aligner 依赖和模型不变。

主导航声纹库与热词库合并为“资料库”，旧 hash 链接仍兼容；所有人员下拉框按完整拼音排序。资料包导出/校验/合并不迁移历史；用户主动选择替换时才备份并替换库。替换备份持久存放于 data/library_backups，非升级 SQLite 备份。格式、限制、浏览器偏好清理和 A → B 步骤见 [资料库指南](LIBRARY.md)。

English: Database-only v12 → v13 migration adds persisted library transfer receipts, preserving existing files/history. Stop and snapshot SQLite before first startup. Synchronize API dependency locks for pypinyin 0.55.0, rebuild the UI and refresh the previously running processes. The new Library navigation preserves legacy hashes. Explicit replacement creates a separate portable backup and clears library-related browser selections, preserving other drafts. See the library guide for transfer contracts.

## v0.1.29 视频编辑就绪回归与发布恢复

从已部署的 v0.1.28、v0.1.27 源码或正式发布的 v0.1.26 升级：**数据迁移：无**，无需额外升级备份，不改写数据库、历史任务、视频、音频或声纹样本。模型、依赖、推理设置和浏览器存储生命周期不变。重建前端并按本地部署流程刷新 API、ASR/TTS supervisor 和 executor；更早版本须覆盖中间版本迁移要求。

修复视频编辑集成测试的就绪判断：保存截图后等待新画面实际显示，再恢复自动分段；每次保存均确认弹窗关闭。新增延迟画面响应的桌面/390 px 回归，确认加载期间恢复按钮禁用、读取完成后可以恢复。真实视频 API 测试使用各 worker 独立素材，等待任务完成并明确推进暂停的浏览器时钟；专门的 SSE/轮询测试继续覆盖实时更新。保留原有超时和断言，不增加整套测试重试。CI 证据与处理经验见[贡献指南](../CONTRIBUTING.md#transcription-ci-lessons)。

本版本交付下文 v0.1.27 的视频声纹入库与自动加载，以及 v0.1.28 的浏览器快照、摘要列表和取消测试计时修复。v0.1.27、v0.1.28 的正式标签因 Windows 检查失败保留为未发布的审计记录，不移动或复用；对应说明应与本版本一并阅读。

English: Upgrading from deployed v0.1.28/v0.1.27 source or released v0.1.26 requires **no data migration or additional upgrade backup**. No database records, historical media or voiceprint samples are rewritten. Rebuild the frontend and refresh all previously running API, supervisor and executor processes. Models, dependencies, inference settings and browser-storage lifetime are unchanged. Video editing tests wait for refreshed frames and closed dialogs; controlled delayed responses cover desktop/390 px readiness. Live API tests use worker-specific media and deliberately advanced browser clocks, with dedicated SSE/polling coverage retained. Original deadlines and zero whole-test retries remain. This release delivers the video features and CI fixes below; v0.1.27/v0.1.28 remain immutable unpublished audit tags. Earlier upgrades must cover intervening migration requirements.

## v0.1.28 浏览器测试快照与发布恢复（未发布标签）

从已部署的 v0.1.27 源码或正式发布的 v0.1.26 升级：**数据迁移：无**，无需额外升级备份，不改写数据库、历史任务、音频或声纹样本。模型、依赖、推理设置和浏览器存储生命周期不变。重建前端并按本地部署流程刷新 API、ASR/TTS supervisor 和 executor；更早版本仍须覆盖中间版本迁移要求。

页面测试改用来自真实服务的 capabilities/system 共享快照，按 worker 缓存、按响应深拷贝，避免刷新时的测试夹具 HTTP 断连。保留真实浏览器接口检查，新增加载、503、断网和手动恢复回归，维持零整套测试重试及原有超时。证据与权限说明见[贡献指南](../CONTRIBUTING.md#browser-fixture-snapshots-and-github-permissions)。

视频任务列表夹具同时遵循真实 API 的仅摘要契约，完整结果只从任务详情读取；受控轮询回归确认入库后不会因列表刷新重载画面或重置阅读位置。生产轮询和自动加载行为未改。

本候选版本包含下文 v0.1.27 的视频声纹入库、原始语句试听选择和画面自动加载。v0.1.27、v0.1.28 标签保留为审计记录：其 Windows 浏览器检查失败，未创建 GitHub Release，标签不移动或复用；正式交付见 v0.1.29。

English: Upgrading from the deployed v0.1.27 source or released v0.1.26 requires **no data migration or additional upgrade backup**. Historical jobs, files and samples are not rewritten. Rebuild the frontend and refresh all previously running service components. Models, dependencies, inference settings and browser-storage lifetime are unchanged. Browser fixtures use deep copies of worker-scoped live snapshots, with real capability and controlled failure/recovery checks retained. This candidate includes the video features below; v0.1.27/v0.1.28 remain immutable unpublished audit tags, with delivery in v0.1.29. Earlier upgrades must cover intervening migration requirements.

## v0.1.27 视频声纹入库与自动加载

从 v0.1.26 升级：**数据迁移：无**，无需额外升级备份，不改写历史任务或样本。重建前端并刷新原运行组件；模型、依赖和识别参数未变。更早版本须覆盖中间版本升级要求。

图文阅读新增说话人入库入口，可试听并选择原始语句，随后选择已有或新建人员；每句独立成样本，已入库语句禁选。选择只在当前弹窗内保留，不写浏览器存储。图文列表每批 20 个画面自动加载，并保留手动按钮；逐句明细切换后仍支持自动加载。修正图文时间按钮的秒数定位。

视频入库复用现有 from-asr 接口，从任务已保存的 playback.wav 截取，以保持视频起始偏移、延迟音轨与转写时间一致。缺失音频返回 404，不重建历史文件；旧样本不会自动修复或替换。如需重新采样，应由用户明确删除对应旧样本后再次选择入库。

English: From v0.1.26, **no data migration or additional upgrade backup** is required. Rebuild the frontend and refresh the original service components; models, dependencies and inference settings are unchanged. Video reading now provides sentence preview/selection and voiceprint enrollment, with temporary dialog-only selection. Scenes automatically expand in batches of 20 with a manual fallback. Video enrollment uses the saved playback WAV on the transcription timeline and returns 404 if missing; historical tasks and samples are not rewritten. Earlier upgrades must cover intervening migration requirements.

## v0.1.26 转写恢复、编辑冲突与文档一致性

从 v0.1.25 升级：**数据迁移：无**，无需额外升级备份；schema v12、历史任务和文件保持不变。更早版本须覆盖中间版本迁移要求。同步源码并重建前端，按本地部署流程刷新 API、ASR/TTS supervisor 和 executor，无需重新安装模型或同步未变化的依赖。

分段编辑固定使用打开弹窗时的修订号，后台更新或 409 会保留输入并要求放弃本次编辑后重新加载。所有分段修改按钮统一进入编辑模式后显示。视频续传新增独立的本地校验进度，sessionStorage 生命周期不变。PDF 使用单次 GET 下载，区分繁忙、超限、登录失效和断网，并遵循 Retry-After 手动重试。

视频命令行示例兼容旧续传记录，新增已接受任务 ID；使用 --pdf 时，仅在完整下载并原子替换目标文件后清除记录，中断重跑继续原任务。未指定 --pdf 时仍以成功提交任务结束。OpenAPI 修正 PDF/JPEG 和 HEAD 元数据、二进制示例及视频请求示例，实际 API 字段和状态码契约保持兼容。CI 经验见 [贡献指南](../CONTRIBUTING.md#transcription-ci-lessons)。

English: Upgrading from v0.1.25 requires **no data migration or additional upgrade backup**. Earlier upgrades must cover intervening requirements. Schema v12 and historical files remain unchanged. Rebuild the frontend and refresh all original API/supervisor/executor processes; model and dependency installation is unchanged. Scene dialogs retain their starting revision and require explicit reload on conflict. Resume verification has a separate progress phase. PDF export uses a single GET with actionable retry/auth/size feedback. The CLI retains accepted job IDs until requested PDFs are atomically saved and reads legacy journals. Browser storage lifetime and wire contracts remain compatible; OpenAPI now documents binary responses, HEAD headers and valid video examples correctly.

## v0.1.25 视频转写正式发布与跨平台加载验证

从 v0.1.23 升级：**数据迁移：仅数据库**，按下节说明备份 SQLite 后升级 schema v11 → v12；不重写历史文件。更早版本需合并阅读中间升级要求。已经运行 v0.1.24 源码且完成 v12 迁移的实例，本次**无新增数据迁移**，无需重复升级备份。

本版发布下节的视频续传、画面识别及简洁 PDF 功能。v0.1.24 标签因 Windows CPU-only 浏览器测试在部署能力加载完成前查找设备控件而失败，保留标签但未发布 Release。本版按接口完成和界面就绪状态验证，增加正常／受控延迟加载的桌面与 390 px 回归，保留 CPU 默认值及 GPU 禁用断言。同时修复工作进程显示长任务 ID 时系统页的移动端横向溢出，使用固定繁忙进程数据验证。失败截图、trace 与页面错误上下文统一存放于 `logs/playwright/`，由现有 CI 失败日志上传步骤收集。

模型、推理参数、API 和 schema v12 不变。从 v0.1.23 升级仍需同步 API/ASR 依赖；所有实例重建前端并按本地部署流程刷新原有服务进程。

English: From v0.1.23, take a consistent SQLite backup for the **database-only** v11 → v12 migration described below; historical files are not rewritten. An instance already migrated by v0.1.24 needs **no additional data migration or upgrade backup**. The failed v0.1.24 tag remains without a Release. This version delivers its video/PDF features and fixes the CPU-only browser test to wait for capabilities and visible controls, with normal and controlled delayed-response coverage on desktop and mobile. System-page grid sections can now shrink around long active job IDs without mobile overflow, covered by fixed busy-worker data. CI retains failure screenshots, traces and error context under `logs/playwright/`. Models, inference settings, APIs and schema v12 stay unchanged; rebuild the frontend and refresh all original processes, synchronizing API/ASR dependencies when upgrading from v0.1.23.

## v0.1.24 视频转写与简洁 PDF / Video transcription and concise PDF

从 v0.1.23 升级：**数据迁移：仅数据库**，需要 SQLite 一致快照；更早版本须合并阅读中间版本升级要求。数据库从 v11 增量升级至 **v12**，只新增续传上传和分片校验记录，不重写历史任务或文件。首次启动新代码前按备份规则停止服务并创建 SQLite 快照。回退须使用该快照，不应由旧代码写入 v12 数据库。已完成音频任务可直接导出简洁 PDF，旧视频任务不会自动补做画面分析。

Upgrading from v0.1.23 requires a **database-only migration** and a consistent SQLite backup; earlier versions must also follow intervening upgrade notes. Schema **v12** adds upload/checksum tables without rewriting history or files. Stop the service and take a SQLite snapshot before first startup. Rollback requires the matching backup. Completed historical audio tasks can export concise PDF immediately; old video inputs do not receive automatic presentation backfills.

同步 API/ASR 依赖锁并重建前端；新增 ReportLab 与本地字体、显式 Pillow 依赖，模型及推理参数不变。视频上限由独立的 `AUDIO_INTEL_MAX_VIDEO_UPLOAD_BYTES` 控制，默认 25 GiB，不影响原音频 4 GiB。续传元数据使用 sessionStorage，刷新保留、关闭页签/退出登录清除；服务器未完成文件按 24 小时闲置/7 天总寿命清理。见[视频指南](VIDEO_TRANSCRIPTION.md)。按本地部署流程刷新全部原有服务进程。

Sync API/ASR locks and rebuild the UI, then refresh all original service processes. Video has an independent 25 GiB default; audio keeps 4 GiB. Browser resume metadata is session-scoped; server upload expiry is independent. See the [video guide](VIDEO_TRANSCRIPTION.md).

## v0.1.23 转写人员备注、合并与声纹库选择

从 v0.1.21 升级：**数据迁移：无**，无需额外升级备份。更早版本须合并阅读中间版本的升级要求。本版不在启动或读取时批量改写历史任务。

转写结果重命名支持可选备注，以及从声纹库选择人员后继续编辑姓名、备注。姓名完整保留，备注规范化后最多 20 字；API 省略备注保留原值，null 或空白清除。自动声纹匹配允许多个簇命中同一人员；新结果和手动保存按规范化姓名＋备注合并，同名不同备注保持独立。选择声纹人员仅复制任务快照，不建立随库更新的绑定。

手动保存会更新所选任务的结果及实际数据目录下 `jobs/<job_id>/output/transcript.{json,txt,srt,vtt}` 中已选格式的导出文件；其他任务、既有声纹样本、播放音频和段落文字／时间戳不改写。每组合并保留结果列表的第一个说话人 ID，客户端应使用返回结果中的 ID；旧结果兼容缺少 `name`、`note`。浏览器选择仅在弹窗内存保留，不改变现有存储或草稿期限。详见 [API 契约与示例](API.md#asr-speaker-names-and-notes)。

界面示例使用合成资料，展示桌面和 390 px 重命名弹窗：

| 视口 | v0.1.21 | v0.1.23 |
| --- | --- | --- |
| 桌面 | [更新前](assets/releases/v0.1.23/speaker-rename-before-1440.png) | [更新后](assets/releases/v0.1.23/speaker-rename-after-1440.png) |
| 390 px | [更新前](assets/releases/v0.1.23/speaker-rename-before-390.png) | [更新后](assets/releases/v0.1.23/speaker-rename-after-390.png) |

PDF 解析依赖由 pypdf 6.18.0 更新到 6.19.0，前端间接依赖 source-map-js 由 1.2.1 更新到 1.2.2，以修复依赖审计报告的安全公告；模型、推理运行时依赖、默认精度和 SQLite schema v11 保持不变。运行 `./service.sh setup api`（Windows：`service.cmd setup api`）同步 API 依赖并重建前端，再按本地部署流程刷新 API、ASR/TTS supervisor 和 executor。

v0.1.22 标签指向仍内置 0.1.21 版本的旧提交，Linux 和 Windows 标签检查均失败，未创建 GitHub Release；原标签保留作为审计记录。本版在新提交中统一前后端 0.1.23 版本，并重新通过完整发布门槛。

English: Upgrading from v0.1.21 requires **no data migration or additional upgrade backup**. Earlier upgrades must account for intervening versions. Speaker renaming adds optional notes and editable library-person selection. Automatic matches and manual saves merge task-local identities by normalized name plus note; different notes remain distinct. Manual saves update only the selected task and its transcript exports, retaining segment IDs, text, timing and playback audio. Use the surviving speaker IDs returned by the API. Historical jobs are not automatically rewritten, and library selections remain snapshots. Browser-storage lifetimes are unchanged. Update the API environment and rebuild the UI with `setup api`, then refresh all service processes. pypdf is pinned to 6.19.0 and the frontend's transitive source-map-js to 1.2.2 for dependency-audit fixes; model/runtime pins and schema v11 remain unchanged. The failed v0.1.22 tag is retained without a Release because its embedded version was still 0.1.21.

## v0.1.21 ASR 播放修复与跨平台验证

从 v0.1.19 或已部署的 v0.1.20 源码升级：**数据迁移：无**，无需额外升级备份，不改写历史任务或音频。更早版本仍须检查中间版本的升级要求。

本版正式交付下节介绍的 ASR 播放时间轴修复，新增任务的播放 WAV 每小时约占 115 MB；旧任务不回填，受影响录音需重新提交。v0.1.20 因 Windows 浏览器测试失败未创建 GitHub Release，其标签保留作为审计记录。本版将上传进度测试改为显式推进事件和受控时钟，保留取消、幂等重试及移动端断言，生产上传行为不变。

模型、依赖锁和 SQLite schema v11 不变。更新后重建前端，按本地部署流程刷新 API、ASR/TTS supervisor 和 executor。

English: Upgrading from v0.1.19 or the deployed v0.1.20 source requires no data migration or additional upgrade backup. This release delivers the ASR playback fix described below; historical jobs are not rewritten or backfilled. The v0.1.20 tag is retained for audit history and has no GitHub Release because its Windows browser checks failed. Upload tests now advance events and time explicitly without changing production upload behavior. Models, dependency pins and schema v11 are unchanged. Rebuild the frontend and refresh all service processes. Earlier upgrades must account for intervening versions.

## v0.1.20 ASR 片段播放时间轴

从 v0.1.19 升级：**数据迁移：无**，不要求额外升级备份；常规数据备份可照常进行。更早版本升级还须合并阅读中间版本的备份要求。

新 ASR 任务保留识别用的 16 kHz 单声道 PCM16 WAV，网页的片段、字词和波形定位统一使用该音频，修复 VBR MP3 跳转时混入上一句的问题。结果新增可选 `playback_url`，对应受保护且支持 Range 的 `GET /api/v1/jobs/{job_id}/playback`。原始文件仍由 `/source` 提供。播放失败可在页面重新加载。

已有任务、时间戳、声纹和音频文件不改写，也不自动补生成播放 WAV；受影响的旧录音需要重新提交。新文件位于 `data/jobs/<job_id>/output/playback.wav`（以实际数据目录为准），每小时约增加 115 MB，随任务删除。模型、推理参数及数据库 schema v11 不变。完成源码和前端更新后，按上面的本地部署流程刷新 API、supervisor 和 executor。

English: Upgrading from v0.1.19 requires no data migration or additional upgrade backup. New ASR jobs retain a 16 kHz mono PCM16 playback WAV on the recognition timeline, fixing inaccurate VBR MP3 seeks. The optional result field `playback_url` points to the authenticated, Range-capable `/api/v1/jobs/{job_id}/playback` endpoint. Original downloads remain available through `/source`. Historical jobs are not rewritten or backfilled; resubmit affected recordings. Playback files add about 115 MB per audio hour and are removed with the job. Earlier upgrades must also follow intervening versions' backup notes. Rebuild the frontend and refresh all service processes after updating.

## v0.1.19 服务配置自动加载

从 v0.1.17 或 v0.1.18 升级：**数据迁移：无**。本版不迁移数据库、不改写或删除历史文件，无需额外创建升级备份；保留现有数据目录即可。从更早版本升级须同时检查中间版本的迁移说明，按实际影响选择备份范围。模型、依赖锁、推理行为和 HTTP API 请求/响应契约保持不变。

Linux `service.sh` 与 Windows `service.cmd` 现在自动读取仓库根目录的 `.env`，外部已导出的环境变量优先，文件不存在时继续使用默认值。不自动加载 `.env.local-deploy`。设置 `AUDIO_INTEL_PORT=20815` 后直接启动即可监听该端口；修改配置后需 `restart` 才能替换已有进程。

`.env` 按键值数据解析，支持引号、注释及变量引用，不执行 shell 命令；原文件若包含命令替换或其他 shell 语句，须先改成静态配置。完整语法、优先级和 `AUDIO_INTEL_LOAD_ENV=0` 排查方式见[安装配置](INSTALL.md)。升级前停止旧入口时仍应显式加载原配置，以确保使用原 PID 目录。

`status` 显示配置来源、实际端点和下次监听地址；`doctor` 根据有效 host/port 检查端口，并修复 Windows 对 `0.0.0.0` 的检查。API 客户端仍需单独配置访问地址与凭据，服务读取 `.env` 不会修改调用者的 shell 环境。

## v0.1.17 本机部署收尾

本版统一前后端版本号，补充单目录开发、停服维护、配置保留和本机部署验收规范。GitHub Release 发布成功与本机部署成功分别确认；验收覆盖 API、ASR/TTS supervisor 和 executor，避免发布后仍由旧进程处理任务。数据备份示例统一放在被 Git 忽略的 `backups/` 下。

本版不增加自动重载或运行时代码版本检测。现有安装完成更新后仍需按上述流程停止并启动全部原运行组件；仅等待空闲回收或调用 `start` 不能保证替换旧进程。模型、依赖锁、推理行为、HTTP API 和 SQLite v11 均保持兼容，历史任务与音频无需迁移或重新生成。本地技能和部署配置不包含在发布源码中，公开流程不依赖这些文件。

## v0.1.15 跨平台开发与发布规范

本版整理 Windows CI 与发布经验，并同步前后端发布版本号。功能、API 请求与响应结构、SQLite v11、模型、依赖锁和推理行为均与 v0.1.14 相同；历史任务和音频无需迁移或重新生成。

贡献指南新增跨平台开发、干净源码验证与 CI 失败处理规范，涵盖明确文本编码、Windows 文件句柄差异、异步页面就绪条件和文档交付检查。AGENTS 在开发阶段引用这些规则，发布继续要求准确提交的 Linux/Windows main 与 tag 检查通过。详见 [贡献指南](../CONTRIBUTING.md#cross-platform-development)。

## v0.1.14 参考区间、文档体验与异常生成保护

本版无数据库迁移，SQLite 仍为 v11。模型、依赖锁、默认精度、采样参数和序列契约 v1 保持不变。升级保留历史请求、结果、文档检查点及声纹样本；历史音频不会自动重新生成或补写专辑。

- 声纹库参考支持成对的 `reference_start_seconds` / `reference_end_seconds`：单条及文档为表单字段，序列为每个 item 的字段；区间长 3–30 秒，可从样本任意位置选取。省略两端继续默认最多前 15 秒。参数属于幂等身份，结果新增可选的实际区间/文字字段，旧请求保持兼容。能力及错误说明见 [API](API.md#manual-voiceprint-reference-ranges)。
- 新增受保护的声纹样本波形接口；前端可拖动、输入端点及试听。需要时由独立 aligner 对齐参考，API/TTS 环境边界保持不变。
- 新文档任务保存专辑名称快照，分段与完整 MP3 带一致 ID3v2.3 标签。名称时间固定 UTC，已提交任务不随声纹改名变化。历史补写必须显式指定单个任务，使用文档指南中的离线备份工具。回滚按 UTF-8 读取备份，修复 Windows 默认编码下中文记录被误判为任务变化的问题。
- 文档导入支持跨页多选移除，明确区分上传新文件和复用导入；结构模式说明备用字数，分段显示来源；正文“原文换行／连续阅读”使用 TAB，“上一页／下一页”逐页阅读。阅读模式、批量勾选仅在弹窗内存保留。
- TTS 内部块默认启用异常生成保护，每个异常块最多额外重试 3 次，保持正常块和已完成文档段。结果新增可选 `generation_guard`，进度可能出现 `tts_chunk_retry`；严格客户端应允许新增字段/阶段，旧结果缺失统计仍表示未记录。保护不承诺覆盖所有重复、误读或噪音。详见 [保护契约](API.md#tts-generation-guard--语音生成保护)及[验证记录](DOCUMENT_TTS_VALIDATION.md#v0114-reference-ranges-albums-and-generation-protection)。

浏览器新增 `audio-intel:tts-reference-ranges:v1` localStorage，按样本 ID 保存起止秒数，刷新、切页、关闭浏览器或退出登录后仍保留，不保存音频或转写文本。此记忆不跨浏览器同步，也不改变服务端默认值。样本级“改用自动截取”清除当前样本记录；顶部恢复默认仅清除当前正在使用的声纹样本区间，保留其他样本记录。原有正文及文档草稿保存周期不变。波形仍仅在浏览器内存缓存，退出登录时清除。

## v0.1.13 工作台与音频波形

ASR 改为固定的“新建转写 / 任务与结果”页签，TTS 改为“文本合成 / 文档合成 / 任务与结果”。配置不再挤压结果区域；提交后保留编辑页，通过“查看本次任务”打开对应结果。完整历史仍在全局任务记录中。切换离开结果页暂停播放，后台任务完成不会抢占当前阅读的任务。

新增鉴权接口 `GET /api/v1/jobs/{job_id}/artifacts/{name}/waveform`，支持单段、序列和文档章节。新音频生成后准备波形；历史音频首次查看时补算并保存每段几 KB 的缓存，不复制音频。补算繁忙返回 429 和 Retry-After，读取期间删除任务返回 409；波形失败不阻止播放与下载。

本次无数据库迁移，SQLite 仍为 v11，历史输入/结果、检查点和序列契约 v1 保持兼容。ZIP 和完整 MP3 继续按需流式下载。浏览器波形仅使用内存缓存，退出登录清除；原有参数 localStorage 与文档草稿 sessionStorage 保存周期不变。ASR 文件仅保存在页面内存中，页签切换保留，成功提交、刷新或离开 ASR 页面后清空。

中英文 README、工作台截图、文档 API 流程、资源限制和排障指南同步更新。安装与运行环境、模型版本及推理参数不变。

## v0.1.12 文档 TTS

从 schema v10 升级时，数据迁移仅涉及数据库：停止服务并创建 SQLite 快照，历史文件无需复制；从更旧版本升级还需覆盖中间迁移的[备份要求](#upgrade-backup-scope)。首次启动执行 v10→v11 的增量迁移，新增导入与章节检查点表。已有任务、队列序号、幂等记录、声音库和提交快照保留。回退使用升级前备份，勿让旧程序写入 v11 数据库。运行 setup api 以安装固定版本的离线文档解析依赖。

新增 EPUB/TXT/Markdown/文本 PDF/DOCX/XLSX/PPTX 导入及长文档合成。导入默认保留，可在管理列表复用和手动删除；关闭页面或退出登录只清除浏览器草稿，不删除服务器文档。草稿继续使用 sessionStorage，并保存明确的空章节选择；语言切换不重置配置。

上传缺少 Idempotency-Key 返回统一的 400，超过限制返回 413，准入拒绝返回 429。解析重试独立于上传重试。章节接口现在始终返回稳定字段及偏移兼容别名。受纯空白章节合并影响的旧预览必须重新预览（409），已提交任务快照不改写；含空白分段的历史任务应重新预览并创建任务。

分段 ZIP 和完整 MP3 按需流式下载，既不预生成也不缓存整个导出；批量下载不支持 Range。详细契约和命令行示例见 [文档 TTS](DOCUMENT_TTS.md)。

## 自动迁移与兼容性

- TTS 序列请求及每个 item 现在拒绝未声明字段，返回 `422`；旧客户端必须移除此前被静默忽略的 `speed`、`pitch`、采样参数和 `response_format`，序列仍固定输出 WAV。`/v1/audio/speech` 同样执行 `limits.max_tts_chars` 的去首尾空白文本上限。这些校验只作用于新提交，不改写已保存的请求和结果。
- 任务重试现在执行与新提交相同的队列容量、提交并发和磁盘准入限制；客户端需处理 `429` 和 `Retry-After`。重试仍不要求 `Idempotency-Key`，不再允许并发请求将已运行的任务重置为排队状态，无数据库迁移。
- 修复声纹序列的单条批次和尾批推理；Web UI 现在按顺序提供全部序列音频的播放与下载。待处理声纹轮询在会话失效后停止，重新登录后恢复；浏览器存储与草稿生命周期不变。
- 从 schema v9 升级到 v10 前先停止服务并创建 SQLite 快照；该迁移仅涉及数据库，历史文件无需复制。从更旧版本升级需合并中间迁移的[备份要求](#upgrade-backup-scope)。v10 事务内重建人员组合唯一约束（规范化姓名＋备注），为样本增加人员内唯一的持久化名称，并校验外键；失败整体回滚。既有样本按升级前声纹库列表编号保存为“样本 N”，后续增删不会重新编号。任务、别名、音频路径与历史快照保持不变；回退须使用升级前备份，不能让旧版直接操作 v10 数据库。
- 样本上传新增可选 `name` 基名、样本响应新增 `name`，重命名使用样本 PATCH；所有状态均可改名。新建时名称冲突自动追加序号，手动改名冲突返回 `409`。人员组合重复同样返回 `409`，旧声音档案创建遇到同名多人时返回 `409`。TTS 新请求增加人员备注和样本名称快照，兼容旧请求、幂等记录及序列契约 v1。
- 人员和样本编辑错误现在显示在对应弹窗内；编辑草稿仅留在当前弹窗状态，关闭或刷新即丢弃。现有浏览器偏好和 ASR/TTS 内容草稿的保存周期不变。
- API 启动时自动将 SQLite 增量迁移到当前代码所需 schema（v0.1.31 为 v14）。v8 新增的声纹人名系统词表在 v9 更名为“声纹库人名（全名）”，稳定 ID 不变；同时新增“声纹库人名（去姓）”，为已开启热词同步的既有人员回填可靠提取的两字中文名或英文首名。若升级前已有词表占用新系统名称，会保留内容并追加“原自定义”后缀；历史任务仍保留提交时的词表名称和内容。上述数据迁移仅涉及数据库，需在启动新版本前创建 SQLite 快照；已经使用当前 schema 且明确无其他数据迁移的更新无需额外备份。完整范围按[升级备份规则](#upgrade-backup-scope)判断。
- 历史 ASR/TTS 任务、旧声音档案和既有声纹样本保持可读；人员名字、备注及开关变化不会回写历史任务或已提交热词快照。
- 浏览器鉴权改用进程内会话 Cookie，升级或重启后需要重新输入 API Key。
- `/api/v1/health` 现在是公开最小探针；原详细结构迁移到受保护的 `/api/v1/system`。监控脚本如依赖硬件、worker、模型或路径字段必须切换端点并增加 Bearer Header。
- `/api/v1/health`、`/api/v1/system` 和 OpenAPI `info.version` 现在统一报告 release-aware 版本，不再长期固定为 `0.1.0`。正式 tag 返回纯发布号，tag 后源码构建使用 `0.1.8+3.g8adfe46` 一类 SemVer build metadata，存在已跟踪本地修改时追加 `.dirty`；字段类型和响应结构不变。
- `.complete` 必须包含固定模型 revision。旧的空 marker 会在 setup 时被判定为无效并修复。
- TTS 安装现在同时创建独立 aligner 环境；不要复用旧 TTS 环境中的 qwen-asr。
- ASR/TTS worker 现在由监督器管理可重启执行器，`setup all` 会将进程树管理所需的 `psutil` 同步到两个模型环境。启动时会校验并清理可信的遗留执行器元数据，再恢复中断任务。
- ASR/TTS 执行器现在只在同类队列有连续任务时保持热状态；队列排空并默认空闲 60 秒后会安全重建，以归还 VAD、CAM++、TTS CPU checkpoint 和 CUDA context 的进程高水位。可用 `AUDIO_INTEL_EXECUTOR_IDLE_SECONDS` 调整，`0` 表示立即回收。监督器、FIFO、任务状态、API、数据库和浏览器会话均不变。
- Linux `service.sh` 的 `start` 现在将各组件放入独立会话和进程组，记录真实服务 PID，可在普通终端、调用脚本或其进程组退出后继续后台运行；容器或平台按 cgroup 管理生命周期时仍应使用 `run` 前台动作。`restart` 会先预检，再清理旧的完整进程树，停止失败时返回非零且不启动新实例。启动就绪检查、PID 身份校验和目录覆盖行为保持兼容；不涉及 HTTP API、数据库或原生 Windows 行为变更。
- Windows 安装入口修复了未提供额外选项时转发空参数、导致 `Unknown setup option` 的问题。升级源码后可直接重试 `.\service.cmd setup all`、`setup asr` 或 `setup tts`；不需要添加占位参数，已有 `--profile` 用法保持不变。
- 原生 Windows `service.cmd` 的动作保持不变；`start`/`restart` 现在等待 API 与 worker 真正就绪，`stop` 校验 PID 身份并清理完整进程树。已有 `AUDIO_INTEL_*_DIR` 覆盖也会用于日志和 PID 等生命周期状态，不涉及 HTTP API 或数据库迁移。
- 服务脚本新增可选的单端口 HTTPS 模式和项目本地 CA 助手。配置 `AUDIO_INTEL_PROTOCOL=https`、证书与私钥后，`start`/`run`/`restart` 会启用 TLS 并在停止旧服务前验证证书；`status` 显示实际协议。新增公开的 `/api/v1/tls/bootstrap` 与根证书下载端点仅用于登录前建立信任，不返回私钥或详细系统数据。HTTP 仍是默认值，不涉及数据库迁移。
- 项目管理的 HTTPS 现在可通过 `tls enable` 持久化到 `<AUDIO_INTEL_DATA_DIR>/tls/service-profile.json`（默认 `data/tls/service-profile.json`）；新终端中的普通 `start`/`restart` 会自动沿用，`tls disable` 无损切回 HTTP。`tls enable|disable --restart` 都会执行完整的 `restart all`。旧的环境变量配置仍优先且保持兼容。
- 原生 ASR/TTS API、OpenAI 兼容音频端点和提交页现在默认启用 `accelerate_single_task`。依赖旧版 batch 1 默认行为的客户端必须显式传入 `false`；模型、精度、ASR 分块与说话人语义不变。
- ASR/TTS 新提交在 full 配置中默认使用 GPU，在 CPU-only 配置中默认使用 CPU；TTS 输出语种默认由 `Chinese` 改为 `Auto`。已有浏览器偏好保持不变；full 配置中不使用 GPU 的 API 消费方需显式传 `compute_device=cpu`，依赖固定中文默认值的消费方需显式传 `language=Chinese`。
- 一次性 TTS 克隆参考新增 `/api/v1/tts/clone-references` 分析端点和 `reference_job_id` 提交方式。分析任务会保留在 ASR 任务记录中，旧的 `reference_audio` + `reference_text` 请求继续兼容。
- ASR 页面、声纹样本入库和 Capabilities 现在统一公开 `Auto + 11` 种支持字词级对齐的语言。原生 ASR、OpenAI 转写和声纹入库显式传入清单外语言时由运行期失败或透传改为同步 `422`；`Auto` 检测到其他模型语种时仍成功返回句段级时间戳。
- TTS 新增 `qwen3-tts-1.7b` 模型组，可按任务选择 CustomVoice、Base 或 VoiceDesign checkpoint；默认仍为 0.6B。`setup tts/all` 会下载新增的三个固定 revision。浏览器 TTS 偏好从 v1 自动迁移到包含 `model` 的 v2，旧客户端省略 `model` 时行为不变。
- 原生 `instruct` 和 OpenAI 兼容 `instructions` 现在可用于 1.7B 预置音色；原生 1.7B VoiceDesign 必须提供 `instruct`。0.6B 和 Base 克隆仍拒绝非空指令。没有独立数值语速/音高或公共采样参数；客户端应按 `GET /api/v1/capabilities` 返回的 `tts.model_capabilities[]` 动态显示控制项，而不是只读取代表默认模型的 `tts.controls`。
- TTS GPU 准入与 ASR 一致，0.6B/1.7B 使用 3840/7936 MiB 总显存门槛；Capabilities 与 TTS 结果新增模型组、checkpoint 和指令信息，均为兼容性扩展。此项不涉及数据库迁移。
- 新增 `POST /api/v1/tts/sequence-jobs` 与 `tts.sequence_jobs` 能力标记。序列任务共享模型、设备、语言和音色模式，按输入顺序为每条文本返回独立 WAV；旧客户端与单条 `/api/v1/tts/jobs` 保持兼容，不涉及数据库迁移。
- 受保护的 `/api/v1/system` GPU 快照新增可选的 `memory_free_mib` 和 `memory_system_reserved_mib`；后者是按 `max(total-used-free, 0)` 计算的系统保留估算。终态 CUDA OOM 现在记录前后显存与可见 GPU 进程，并在完整进程树退出后重建执行器。严格响应模型需允许这两个兼容性字段；此项不涉及数据库迁移。
- **不兼容变更：** 原生异步 ASR、TTS、TTS 克隆参考分析和声纹样本上传现在强制要求 `Idempotency-Key`。现有客户端必须为每次逻辑提交生成 8–128 字符的键，并在超时、断线或 `429` 后复用；相同键改变请求会返回 `409`。`429` 的分类和恢复步骤见 [故障排查](TROUBLESHOOTING.md#api-提交返回-429)。
- **不兼容变更：** `GET /api/v1/jobs` 与全局 `/api/v1/events` 现在只返回任务摘要，不再包含 `request`/`result`。全局 SSE 首帧仍为 `snapshot`，后续改为 `update`（仅变更任务、`removed_job_ids`、当前 worker）和空闲 `heartbeat`；Capabilities 以 `events.global_mode=summary_delta` 标识。依赖列表内完整结果、或把每个全局事件都按完整快照覆盖的客户端，必须改为按增量合并，并在确需详情时读取 `GET /api/v1/jobs/{job_id}`。单任务状态接口和单任务 SSE 仍返回完整契约。此变更不迁移数据库，也不改写历史任务。
- 新增同类队列位置、稳定阶段/细粒度进度、本机历史 ETA 区间、`GET /api/v1/queue`、单任务 SSE 和 ETag 条件轮询。TTS 解码与 ASR 推理的顶层 `progress` 现在会持续变化；`progress_detail.basis=estimated` 时百分比是最佳估算，`activity` 提供当前调用的模型活动。新增响应字段是兼容性扩展，但使用严格反序列化模型的客户端需要先允许这些字段。ETA 是热身后才出现的建议区间，不是 SLA。
- Windows 上的 ASR 子进程进度通信改为不可变编号快照，修复父进程读取进度时覆盖同一路径可能触发的 `PermissionError: [WinError 5]`。进度频率、API 和识别结果不变；TTS 仍直接写入任务进度，不使用该文件通信机制。
- ASR/TTS 页面参数现在分别保存在浏览器 localStorage，并提供页面级“恢复默认配置”；清除站点数据后会恢复默认。ASR 偏好升级到 v3，自动迁移 v2 设置并将热词表勾选作为长期偏好保存；刷新、切页、重开浏览器和成功提交后继续沿用，恢复 ASR 默认配置时清空。TTS 偏好仍为包含 `model` 的 v2，未保存模型时仍使用 0.6B。TTS 文本、`instruct`、参考文本和分析引用仅保留在 sessionStorage；热词库未保存草稿同样只在当前标签页会话保留，保存或“取消并清空”后删除。文件和未确认的麦克风录音不会持久化。
- Web UI 新增简体中文/英文切换。显式选择保存在 localStorage 的 `audio-intel:ui-locale:v1`，跨刷新和重开浏览器保留；未选择时按浏览器语言检测，无法检测时使用简体中文。清除站点数据会同时清除该选择；不改变 API、任务数据或原有草稿生命周期。
- ASR 新增 1.7B 模型选择和热词库。默认仍是 0.6B；旧客户端省略 `model` 和 `hotword_list_ids` 时行为不变。`setup asr/all` 会额外下载固定 revision、当前约 4.4 GiB 的 1.7B 权重。GPU 按标称 4/8 GiB 档位并扣除 256 MiB 报告容差判断，因此 0.6B/1.7B 的实际门槛分别是 3840/7936 MiB，判断口径是报告的总显存而非当前空闲显存。门槛只决定准入，不保证其他 GPU 程序不会造成运行期 OOM。
- Capabilities 新增 `asr.default_model`、`asr.models[]` 和 `asr.hotword_library`，ASR 结果新增模型身份与 `hotword_context`。这些都是兼容性扩展；严格反序列化客户端应先允许新字段，并按 `asr.models[].compute_devices` 判断所选模型，而不是继续使用只代表默认模型的顶层 `asr.compute_devices`。
- 声纹库新增浏览器麦克风录音入口，继续复用现有样本上传 API，不新增数据库字段或迁移。远程普通 HTTP 访问仍可能被浏览器拒绝麦克风权限，可继续使用文件上传。
- Web UI 改进长转写渐进加载、可键盘操作的波形定位、失败任务本地化详情、资源加载失败隔离、模型状态分组和页面导航滚动复位；不改变 API、数据库或浏览器存储生命周期。
- Web UI 的 API 文档和 HTTPS 证书帮助现在位于全局右上角，系统状态页不再重复相同入口。ASR、TTS、克隆参考和声纹样本上传会显示浏览器到服务端的上传进度，区分上传与服务端创建任务，允许在创建前取消并保留文件重试；旧浏览器缺少 `crypto.randomUUID()` 时改用 Web Crypto 安全随机数生成兼容 UUID。上述均不改变任务 API、幂等语义或数据库。

## 升级后验证

先查看 `status` 的实际端点，再给客户端设置 `AUDIO_INTEL_BASE_URL`（例如 `http://127.0.0.1:20815`，HTTPS 部署使用实际 HTTPS 地址）及需要的 `AUDIO_INTEL_API_KEY`。服务加载 `.env` 不会把这些值导出回当前 shell。下方为 smoke 客户端显式传递它现有的 `AUDIO_INTEL_URL` 参数；默认 `20810` 只适用于未改端口的实例。

```bash
./service.sh doctor
BASE_URL=${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}
curl -fsS "$BASE_URL/api/v1/health"
AUDIO_INTEL_URL="$BASE_URL" .runtime/api/bin/python scripts/smoke_test.py
.runtime/api/bin/python -m pytest -q
corepack pnpm@10.15.1 --dir frontend typecheck
```

若真实模型、Torch、精度或设备路由发生变化，还必须执行真实 ASR、TTS 克隆和说话人分离回归，不能仅依赖 mock 测试。

### v0.1.35：结果摘要、任务占用与视频时间轴

从 v0.1.34 更新：数据迁移无；SQLite schema v14、历史请求／结果和媒体均不改写，无额外升级备份要求。新增只读任务占用和窗口波形接口，原任务列表／SSE及产物波形契约保持兼容。需重建前端并重启原有API与工作进程，再按本文完成部署验收。导出偏好的存储键与周期不变；清空规则保留脱敏开关，撤销仅在当前页面下一次编辑之前有效。

## 画面检测 v2 / Picture detection v2

本次无数据库结构迁移，不批量改写历史结果或媒体，无需额外升级备份。新视频使用 presentation-v2；旧任务重试沿用 v1，用户可在结果页显式预览并应用 v2。最近一次自动分段和实际参数是新增可选结果字段，首次自动快照保留。任务目录新增可重建特征缓存；旧任务按需生成，不在启动时扫描全部视频。浏览器新增独立的画面检测 localStorage 偏好，成功应用后保存，退出登录仍保留；弹窗草稿不持久化。更新后按本页维护流程重新构建前端并刷新 API、supervisor 和 executor。

No schema migration, automatic historical rewrite or extra upgrade backup is required. New videos use presentation-v2; legacy retries retain v1, with explicit result-side preview/apply available. Applied settings/latest automatic sections are optional result additions and the initial snapshot remains intact. Rebuildable feature caches are created per job on demand, not by scanning all videos at startup. A separate browser localStorage preference persists successful choices across sign-out; dialog drafts are not persisted. Rebuild the frontend and refresh API, supervisors and executors using the maintenance procedure above.
