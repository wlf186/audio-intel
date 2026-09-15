# 升级指南

## 升级前

先阅读当前版本到目标版本之间的全部升级说明，按[升级备份范围](#upgrade-backup-scope)确定是否需要备份。确认没有未完成任务或导入，记录实际启动配置，再停止全部服务。`data/` 包含 SQLite 队列、历史输入输出、声音档案和声纹库；升级应保留这些数据。先阅读下方的本机部署收尾要求。

```bash
set -e
# 若部署依赖 .env，先加载它；service.sh 不会自动读取环境文件。
if [[ -f .env ]]; then set -a; source .env; set +a; fi
if [[ -f .env.local-deploy ]]; then set -a; source .env.local-deploy; set +a; fi
./service.sh stop all
# 需要备份时，在此按下文执行；备份失败则停止升级。
git pull --ff-only
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
| 影响范围广泛，或无法确认范围 | 完整备份实际数据目录 |

每个 Release 应写明“数据迁移：无／仅数据库／涉及文件”、适用的起始版本及备份范围。跨版本升级必须覆盖中间版本；不能仅凭目标版本号小、schema 未变化或最新一条说明判断。新增任务正常生成文件不属于这里的历史文件迁移。日常数据备份按数据重要性单独安排，不绑定每次发版。

需要备份时，先停止使用该数据目录的全部服务，确认进程树退出，在新版本首次启动或迁移操作前完成备份。数据库和关联文件必须对应同一停服时点。本节升级备份放在项目内被 Git 忽略的 `backups/`，不得放入待复制的数据目录内部。沿用 `AUDIO_INTEL_DATA_DIR` 的实际配置，不要误备份默认路径。

### 按需备份示例（Linux / Windows）

仅当上表要求备份时执行。将以下标准库示例保存为项目内的 `tmp/backup-upgrade.py`（先创建 `tmp/`），从项目根目录使用现有 API Python 运行，无需安装依赖。`database` 只备份 SQLite；`full` 复制完整数据目录。数据库快照使用 Backup API，包含尚在 WAL 中的已提交数据，不能用单独复制主数据库文件代替。

```python
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

scope = sys.argv[1] if len(sys.argv) == 2 else ""
if scope not in {"database", "full"}:
    raise SystemExit("Usage: backup-upgrade.py database|full")
root = Path.cwd().resolve()
data = Path(os.environ.get("AUDIO_INTEL_DATA_DIR", "data")).resolve(strict=True)
backups = (root / "backups").resolve()
if not backups.is_relative_to(root) or backups.is_relative_to(data):
    raise SystemExit("Backups must stay inside the project and outside the data directory")
# mode=ro prevents a missing source database from being silently created.
with closing(sqlite3.connect((data / "audio_intel.sqlite3").as_uri() + "?mode=ro", uri=True)) as source:
    backups.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="upgrade-" + scope + "-", dir=backups))
    if scope == "database":
        saved = destination / "audio_intel.sqlite3"
        with closing(sqlite3.connect(saved)) as target:
            source.backup(target)
    else:
        shutil.copytree(data, destination / "data")
        saved = destination / "data" / "audio_intel.sqlite3"
with closing(sqlite3.connect(saved.as_uri() + "?mode=ro", uri=True)) as check:
    if check.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise SystemExit("Backup integrity check failed; do not continue the upgrade")
print("Verified backup:", destination)
```

Linux：

```bash
.runtime/api/bin/python tmp/backup-upgrade.py database
# 完整备份时，将 database 改为 full；不要把两种模式都执行一遍。
```

Windows PowerShell：

```powershell
.\.runtime\api\Scripts\python.exe tmp\backup-upgrade.py database
if ($LASTEXITCODE -ne 0) { throw "Backup failed; stop the upgrade" }
# 完整备份时，将 database 改为 full。
```

只在命令成功并输出 `Verified backup:` 后继续；失败时可能留下不完整的备份目录，不得用于回退。涉及文件的升级，在数据库快照成功后，按升级说明将受影响文件以原相对路径复制到同一备份目录的 `data/` 下并核对副本；全部完成前保持停服。专用迁移工具已有数据库及受影响文件备份机制时，按该工具说明操作，无需再复制整个目录。

回退必须停止新版本，使用同一时点的数据库和受影响文件备份。单独恢复数据库的前提是关联文件仍与该快照兼容；新版本已经处理任务或修改文件后，不能直接用旧数据库覆盖当前状态。既有备份保留，不在升级中自动清理。

## Local development and deployment

### 单目录工作约定

本机开发期间可以停止日常服务；继续使用同一份源码、运行环境和模型，无需创建第二套部署。以下流程适用于本机代码更新和开发/发版收尾，纯只读检查不触发启停。

1. **记录现场。** 在停服前记录实际端口、host、数据及运行目录、启用的服务、full/cpu profile、mock 状态和 TLS 配置来源，以及 API、ASR/TTS supervisor、executor 和子进程的 PID 与创建时间。非敏感启动参数可以放在被忽略的 `.env.local-deploy` 中；鉴权密钥从原有安全来源加载，不写入日志或部署记录。`service.sh` 不自动读取环境文件，需如上显式加载；Windows 在 PowerShell 中设置对应环境变量，`service.cmd` 不解析 Bash 环境文件。
2. **停止后再修改。** 先检查 queued/running/cancelling 任务、排队或执行中的文档导入等操作；有未完成工作时等待或报告，不擅自取消。进入停服窗口时可先 `stop api` 关闭新提交入口，再检查一次队列，确认空闲后 `stop all`。如出现新任务，恢复原 API 并等待，避免 `stop all` 中断它。确认记录的旧进程树已退出，再编辑运行代码或安装依赖。
3. **按变更验证。** 测试沿用现有环境，临时实例使用独立的数据、PID、日志、缓存和临时目录。正式升级和迁移开发均按[升级备份范围](#upgrade-backup-scope)选择备份，记录选择依据及实际路径；明确无数据迁移的升级无需额外复制数据。依赖锁变化才同步对应环境，前端源码/版本/依赖变化才重建前端；模型变化按 manifest 准备。纯 Python 修改不需要每次执行 `setup all`。测试进程要在收尾时退出，不能把 mock 服务留作日常实例。
4. **最终代码确定后恢复。** 完成最后一次修改、测试及相关 tag 操作，记录目标 SHA、预期应用版本和源码就绪时间。正式 release 部署要求已验证的干净提交；本地未提交修改要另外记录 dirty 状态和 diff 摘要，不能宣称部署了干净的发布版本。清理残留测试/服务进程，按记录的配置和组件重新启动。原本停止的组件保持停止，除非用户要求启动；用户要求继续停服时记录待部署状态。收尾后再次修改运行代码，必须重新完成这一轮。
5. **验收后才算完成。** 按下表检查，并在被忽略的 `logs/local-deploy.json` 中记录部署状态、目标 SHA/dirty 状态、预期及实际版本、源码就绪时间、进程身份、检查结果和失败原因。开始维护时也记录原运行组件及配置文件位置，让后续会话能够继续收尾；不要保存凭据。GitHub Release 成功、本机部署成功分别报告。失败时保留诊断和待办，不能把发布成功当作部署成功；回退如涉及数据库迁移，必须配套使用升级前备份，不能只回退源码。

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
- API 启动时自动将 SQLite 迁移到当前 schema v11。v8 新增的声纹人名系统词表在 v9 更名为“声纹库人名（全名）”，稳定 ID 不变；同时新增“声纹库人名（去姓）”，为已开启热词同步的既有人员回填可靠提取的两字中文名或英文首名。若升级前已有词表占用新系统名称，会保留内容并追加“原自定义”后缀；历史任务仍保留提交时的词表名称和内容。上述数据迁移仅涉及数据库，需在启动新版本前创建 SQLite 快照；已经使用当前 schema 且明确无其他数据迁移的更新无需额外备份。完整范围按[升级备份规则](#upgrade-backup-scope)判断。
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

```bash
./service.sh doctor
BASE_URL=${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}
curl -fsS "$BASE_URL/api/v1/health"
.runtime/api/bin/python scripts/smoke_test.py
.runtime/api/bin/python -m pytest -q
corepack pnpm@10.15.1 --dir frontend typecheck
```

若真实模型、Torch、精度或设备路由发生变化，还必须执行真实 ASR、TTS 克隆和说话人分离回归，不能仅依赖 mock 测试。
