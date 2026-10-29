# 表达优化 / Expression optimization

在「系统状态 → 表达优化」配置 Ollama URL，获取模型、选择、验证、保存并启用；选择模型不会自动验证，未保存修改不改变当前生效配置。默认关闭，不会自动处理或发送历史转写。核心语音推理仍在本机运行；表达优化将本批来源正文、候选及任务提交时的热词发送到用户指定的 Ollama，不发送音视频，不提供隐式云端回退。非回环地址显示发送提醒；localhost 指服务端所在机器。

验证检查连接、文本生成能力及实际上下文窗口。最低 16K，默认请求最多 32K（受模型上限和实际窗口约束）；模型上限从服务返回的 model_info 获取。每次生成关闭 thinking，输出预算明确限定为 4096 tokens。辅助复核异常不将已经能生成正文的模型判为不可用。远端服务类型、模型和硬件会影响速度及质量。

## 连续语句成稿

对已完成的普通音频或视频任务选择「表达优化」。按同一人连续发言整理，另一人的插话、超过 15 秒的停顿和画面边界形成新段。阅读段落目标约 240 字、最多 400 字；一幅画面可有多段。仅在有可靠字词对齐时拆分跨画面来源，否则完整片段归属最大重叠画面，不猜测时间。不会根据同名猜测说话人，也不会改变原始转写、字幕或音频时间轴。

| 档位 | API 值 | 效果 |
| --- | --- | --- |
| 自然润色 | `polish` | 清理重复、口头冗余和病句，保留交流语气。 |
| 流畅重写 | `rewrite` | 默认档位，重组句式和表达顺序，形成连贯的对谈稿。 |
| 深度成稿 | `editorial` | 更充分地整理为书面稿，保留事实与有效细节，不生成摘要。 |

三个档位都支持多条来源合并。模型接收本批所属的来源片段，借助任务热词修正明确的听写错误；不向生成请求提供相邻批次或其他人的正文，避免将邻文误当作当前内容。不把当前热词库的后续修改写回历史任务。有语义但不确定的局部词语保留，没有完整语义的截断尾片可省略，原始来源始终可对照；不能凭猜测补全事实、原因、时间或人物关系。原本清晰的短句可以不改；文字变化比例不是质量评分。

长来源组按实际预算分成持久化子批次，目标约 90 字、最多 160 字，优先完整句子，其次逗号边界；预算紧张时进一步缩小。一个批次仍可包含多条原始来源，最终按原顺序组成同一组成稿，不再次整体摘要。真正无自然边界的强制拆分进行有界接缝整理，版本号和小数中的点不作为句界。暂停恢复复用已保存的批次与结果，不重写完成部分或重复拼接。

生成正文失败时进行有界恢复；已有可用改写不会因后续尝试失败而撤销。仍无法得到正文时执行基础整理，并明确记录 `generation_method`，不冒充模型完成了改写。持续连接故障保存进度，恢复服务后可继续；无法安全保存时如实报告运行故障。

生成同时检查少量明确限定词的范围是否丢失（如“不止”“未必”“只能”，接受明确同义表达），必要时复用一次加强机会；不会把普通“不／不是”或合理自我纠正一概认作错误。这是有限保护，不能证明所有事实均正确。

## 统一完成与辅助检查

表达优化是一次完整操作：生成正文 → 自动检查 → 发布。检查完成前本次新稿不能阅读或下载，原文始终可用；重新优化时，同一画面划分下的旧完成稿继续可用，直到新稿整体完成。检查只添加提示，不撤销或回滚正文。

事实复核明确区分 `pass`、`fact`、`uncertain`。通过结论可包含肯定性说明，不因说明非空而判为格式错误。事实疑点需定位原文和成稿证据，引用允许空白与全角字符规范化，不做模糊语义替换。首次疑点最多独立确认一次；矛盾或证据不足记录为不确定，无法完成记录为不可用。

复核结果只是辅助，不能保证绝对正确。疑点、不确定、不可用和基础整理在完成摘要中提供详情，不能将未能检查当作通过。每个子批次最多两次检查请求、每次最多 120 秒；连续三个子批次出现连接或超时故障后，剩余检查记为不可用，仍交付已生成正文。

qwen3.5:4b 与 9b 的本轮实测仍有事实改变、复核漏报和整理不足；完整范围及未达标项见[验证记录](EXPRESSION_OPTIMIZATION_VALIDATION.md)。来源破碎或听写错误较多时，不能将模型结论当作事实认证。

暂停／继续作用于整个流程，恢复复用已保存的正文和检查点，无需单独发起复核。API 重启后未完成工作暂停，用户可继续；关闭功能也暂停未完成工作。删除优化或父任务取消相关请求并清理正文、子批次、检查记录和导出缓存。旧响应不能覆盖新稿或恢复已删除的数据。提示统计可能重叠，不应相加作为总段数。

## 阅读、对照和导出

原文保留原始片段，优化稿按阅读段落显示，对照完整显示每段原文和优化稿。桌面两侧并排，手机上下排列，共用正文滚动区；无需逐段展开原文。来源 ID、时间戳等通过全局「来源明细」开关查看。默认显示改动，大幅改写使用整段提示，可选择详细差异。

「原文／优化稿／对照」和「图文阅读／文本阅读」是两组独立选项，表达优化、画面检测和音频增强入口位置固定。三个版本保留同一时间轴，定位对应画面或阅读段落。搜索仅查当前版本可见正文，对照搜索两侧，提供高亮、前后命中跳转和清除筛选。统计区分原文片段、阅读段落和命中数，不把内部子批次当作阅读段落。

图文模式每幅画面左图右文，对照的右侧为两栏正文。正式应用新检测参数／画面划分或编辑边界，会在同一事务内清理旧优化数据并回到原文，之后可手动重新优化。仅编辑参数、预览、更换截图、改变导出隐藏状态或人物显示名不会清理成稿；应用失败保留旧划分和旧稿。

统一「导出转写」入口长期记忆内容、各内容格式、脱敏及替换规则；不可用选择临时回退，不覆盖偏好。弹窗说明将下载的版本和格式，可选择使用当前阅读版本；重新生成期间导出旧完成稿会明确提示。下载成功触发浏览器下载后关闭弹窗，失败保留选项。优化稿支持 PDF/DOCX/TXT，对照支持横向双栏 PDF/DOCX。段落和来源覆盖与页面一致，具体疑点保留标记，对照附脱敏原因。来源、正文和原因使用相同替换策略，导出不修改持久化原始文件。

## API 与脚本

所有接口沿用服务的 Bearer／浏览器会话认证。下列路径前缀为 `/api/v1`；字段详见本地 `/docs` 与 `/openapi.json`。

| 方法与路径 | 用途 |
| --- | --- |
| GET/PUT `/settings/transcript-optimization` | 读取、保存配置 |
| POST `/settings/transcript-optimization/discover` | 获取模型 |
| POST `/settings/transcript-optimization/verify` | 内置合成样本验证，返回短期 verification_token |
| GET/POST `/jobs/{id}/transcript-optimization` | 状态／创建；GET 支持 ETag |
| POST `/jobs/{id}/transcript-optimization/pause`、`/resume` | `{run_id,phase:"workflow"}`；兼容旧 phase 值，均控制整个未完成流程 |
| GET `/jobs/{id}/transcript-optimization/result?run_id=...` | 正文、来源及复核详情 |
| DELETE `/jobs/{id}/transcript-optimization?purge=true` | 删除全部优化数据 |
| POST `/jobs/{id}/transcript-export` | 使用 text_version、optimization_id 导出 |

创建需 `Idempotency-Key`，正文为 `{regenerate:false,rewrite_mode:"rewrite"}`。首次 202、同请求重放 200、冲突 409、容量不足 429（Retry-After）；更换档位须重新生成，恢复沿用配置快照。原有限制和父任务校验继续适用。

结果 `complete`、`published` 和 `succeeded` 都表示生成与检查整体完成。摘要 `phase` 为 generation/review/complete，`reviewed_parts/total_parts` 表示实际保存的检查进度；疑点和不可用数量独立保留。未发布结果请求返回 409。全局任务列表和 SSE 仍只含摘要，不读取正文统计。

段落 `text` 是 v7 实际成稿；`sources`、`segment_ids` 保留不可变来源，`scene_ids` 最多包含一个归属画面，`merged_source_count` 为来源数。`generation_method` 区分 model/basic_cleanup/mixed；`review_outcome` 区分 pending/pass/fact/uncertain/unavailable。兼容字段 `review_status` 仅 pass 对应 accepted、fact 对应 needs_review，其余为 null；历史 null 不表示通过。旧策略的 candidate_text 保持兼容读取。

```bash
python3 scripts/optimize_transcript.py --url http://localhost:20815 --job JOB_ID --rewrite-mode editorial
python3 scripts/optimize_transcript.py --url http://localhost:20815 --job JOB_ID --resume
```

URL 应使用实际部署地址。客户端密钥通过 `AUDIO_INTEL_API_KEY` 提供，不写入 URL。默认等待整个流程完成后输出 JSON；旧 `--wait-review` 参数仅为命令兼容保留。

## 持久化与升级

策略 v7／SQLite v19 增加统一阶段和检查进度。迁移一次性清理全部旧音频和视频优化正文、候选及检查记录，用户需手动重新优化；不会清理此后新生成的稿件。原始转写、媒体、人物、热词快照和模型配置保留。升级前做 SQLite 一致性备份，无需复制历史媒体；遵循[升级指南](UPGRADE.md)恢复服务并验收后清理临时备份。

档位与导出偏好继续长期保存，刷新、重开、退出登录和资料库替换后保留。阅读方式、搜索、位置、差异显示及新增来源明细开关属于当前任务浏览器会话，刷新保留、退出登录清理，最多保留 200 个任务，不存正文或密钥。差异显示默认开启，已有明确关闭的偏好继续尊重。

English: Policy v7 scopes bounded reading paragraphs to one scene and one consecutive speaker turn. Generation and bounded annotation-only checks form one workflow; only its completed draft can be read or exported. A previous published draft stays available during regeneration. Comparison shows complete source and draft prose; source metadata is optional. Applying scene boundaries invalidates old drafts transactionally. Schema v19 clears all pre-upgrade optimization data once, while preserving original jobs, media and model settings. Take a SQLite-only backup, restore the recorded deployment and verify it before removing the backup.

## 开发质量验证

固定合成／去标识化语料在 `tests/fixtures/optimization_quality.json`。`scripts/evaluate_optimization.py` 直接调用所选服务，不创建应用任务；必须显式指定服务和模型。先验证语料，再串行执行两个模型，避免竞争资源：

```bash
.runtime/api/bin/python scripts/evaluate_optimization.py --validate-corpus
.runtime/api/bin/python scripts/evaluate_optimization.py --base-url http://localhost:11434 --model qwen3.5:4b --output tmp/quality-4b.json --repeats 3
.runtime/api/bin/python scripts/evaluate_optimization.py --base-url http://localhost:11434 --model qwen3.5:9b --output tmp/quality-9b.json --repeats 3
.runtime/api/bin/python scripts/evaluate_optimization.py --base-url http://localhost:11434 --model qwen3.5:4b --corpus tests/fixtures/optimization_quality_long.json --output tmp/quality-long-4b.json --repeats 1
```

Windows 使用 API 环境的对应 Python 路径。工具保存服务版本、模型 digest、策略及语料校验和、逐项输出和耗时；`--resume` 只恢复身份一致的结果。复核用预置候选独立测量，未知／不可用不从分母删除。语言改善和事实保留须对记录逐项独立审读，不能由改字比例自动认证。真实任务评测应复制到隔离测试目录，按来源范围比较并统计来源字数加权结果，不能修改用户任务来做测试。
