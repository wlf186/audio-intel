# 表达优化 / Expression optimization

在「系统状态 → 表达优化」配置 Ollama URL，获取模型、验证、启用并保存。默认关闭，不会自动处理或发送历史转写。核心语音推理仍在本机运行；表达优化将本批来源正文、候选及任务提交时的热词发送到用户指定的 Ollama，不发送音视频，不提供隐式云端回退。非回环地址显示发送提醒；localhost 指服务端所在机器。

验证检查连接、文本生成能力及实际上下文窗口。最低 16K，默认请求最多 32K（受模型上限和实际窗口约束）；模型上限从服务返回的 model_info 获取。每次生成关闭 thinking，输出预算明确限定为 4096 tokens。辅助复核异常不将已经能生成正文的模型判为不可用。远端服务类型、模型和硬件会影响速度及质量。

## 连续语句成稿

对已完成的普通音频或视频任务选择「优化表达」。按同一人连续发言构建来源组，可跨画面边界；另一人的插话及超过 15 秒的停顿形成新组。不会根据同名猜测说话人，也不会改变原始转写、字幕或音频时间轴。

| 档位 | API 值 | 效果 |
| --- | --- | --- |
| 自然润色 | `polish` | 清理重复、口头冗余和病句，保留交流语气。 |
| 流畅重写 | `rewrite` | 默认档位，重组句式和表达顺序，形成连贯的对谈稿。 |
| 深度成稿 | `editorial` | 更充分地整理为书面稿，保留事实与有效细节，不生成摘要。 |

三个档位都支持多条来源合并。模型接收本批所属的来源片段，借助任务热词修正明确的听写错误；不向生成请求提供相邻批次或其他人的正文，避免将邻文误当作当前内容。不把当前热词库的后续修改写回历史任务。有语义但不确定的局部词语保留，没有完整语义的截断尾片可省略，原始来源始终可对照；不能凭猜测补全事实、原因、时间或人物关系。原本清晰的短句可以不改；文字变化比例不是质量评分。

长来源组按实际预算分成持久化子批次，目标约 90 字、最多 160 字，优先完整句子，其次逗号边界；预算紧张时进一步缩小。一个批次仍可包含多条原始来源，最终按原顺序组成同一组成稿，不再次整体摘要。真正无自然边界的强制拆分进行有界接缝整理，版本号和小数中的点不作为句界。暂停恢复复用已保存的批次与结果，不重写完成部分或重复拼接。

生成正文失败时进行有界恢复；已有可用改写不会因后续尝试失败而撤销。仍无法得到正文时执行基础整理，并明确记录 `generation_method`，不冒充模型完成了改写。持续连接故障保存进度，恢复服务后可继续；无法安全保存时如实报告运行故障。

生成同时检查少量明确限定词的范围是否丢失（如“不止”“未必”“只能”，接受明确同义表达），必要时复用一次加强机会；不会把普通“不／不是”或合理自我纠正一概认作错误。这是有限保护，不能证明所有事实均正确。

## 稿件就绪与后台复核

正文生成和复核是两个独立阶段。正文全部完成后即发布，允许阅读和下载；复核在后台运行。一个远端请求槽优先处理生成，避免旧稿复核阻塞新稿。复核只更新提示，不修改已经发布的正文。

事实复核明确区分 `pass`、`fact`、`uncertain`。通过结论可包含肯定性说明，不因说明非空而判为格式错误。事实疑点需定位原文和成稿证据，引用允许空白与全角字符规范化，不做模糊语义替换。首次疑点最多独立确认一次；矛盾或证据不足记录为不确定，无法完成记录为不可用。

复核结果只是辅助，不能保证绝对正确。疑点、不确定、不可用和基础整理数量分别展示，不能将未完成复核当作通过。后台复核不可用不影响成稿的成功状态。

qwen3.5:4b 与 9b 的本轮实测仍有事实改变、复核漏报和整理不足；完整范围及未达标项见[验证记录](EXPRESSION_OPTIMIZATION_VALIDATION.md)。来源破碎或听写错误较多时，不能将模型结论当作事实认证。

可分别暂停／继续生成和复核。复核阶段结束但仍有未能复核部分时，可仅重试这些子批次，保留其他结论和正文。“未能复核”按包含不可用子批次的来源组计数，可能与事实疑点组重叠，不应将这些计数相加作为总组数。API 重启后未完成生成暂停，意外中断的后台复核重新排队，主动暂停保持暂停。关闭功能暂停两类工作。删除优化或父任务会取消相关请求，并清理正文、子批次、复核记录和导出缓存。重新优化时旧完成稿保留到新稿就绪；旧响应不能覆盖新稿。

## 阅读、对照和导出

原文保持逐条来源；优化稿按来源组显示连贯成稿；对照默认阅读优先，桌面左侧多条原文、右侧一组成稿，共用滚动区。长来源默认显示前三条，可展开全部；逐字差异通过「显示改动」开启。手机按组上下排列。

原文时间戳定位对应片段，成稿回听定位整组。搜索命中折叠来源会展开；版本切换按来源 ID 和时间范围定位，不把位于合并组内部的时间跳到下一组。后台复核刷新保留阅读位置。图文／文本方式独立于原文／优化稿／对照。

跨画面的成稿只展示一次，位于首个关联画面，并关联其他画面。画面编辑后重新计算引用，不改正文。对照导出同样保留逐条来源和时间，不伪造逐词音频对齐。

统一「导出转写」入口记忆内容、各内容格式、脱敏及替换规则；不可用选择临时回退，不覆盖偏好。下载成功触发浏览器下载后关闭弹窗，失败保留选项。优化稿支持 PDF/DOCX/TXT，对照支持横向双栏 PDF/DOCX。复核未完成也可下载并如实说明；具体疑点保留标记，对照附脱敏后的原因。来源、正文和原因使用相同替换策略，导出不修改持久化原始文件。

## API 与脚本

所有接口沿用服务的 Bearer／浏览器会话认证。下列路径前缀为 `/api/v1`；字段详见本地 `/docs` 与 `/openapi.json`。

| 方法与路径 | 用途 |
| --- | --- |
| GET/PUT `/settings/transcript-optimization` | 读取、保存配置 |
| POST `/settings/transcript-optimization/discover` | 获取模型 |
| POST `/settings/transcript-optimization/verify` | 内置合成样本验证，返回短期 verification_token |
| GET/POST `/jobs/{id}/transcript-optimization` | 状态／创建；GET 支持 ETag |
| POST `/jobs/{id}/transcript-optimization/pause`、`/resume` | `{run_id,phase:"generation"或"review"}`；phase 默认 generation |
| GET `/jobs/{id}/transcript-optimization/result?run_id=...` | 正文、来源及复核详情 |
| DELETE `/jobs/{id}/transcript-optimization?purge=true` | 删除全部优化数据 |
| POST `/jobs/{id}/transcript-export` | 使用 text_version、optimization_id 导出 |

创建需 `Idempotency-Key`，正文为 `{regenerate:false,rewrite_mode:"rewrite"}`。首次 202、同请求重放 200、冲突 409、容量不足 429（Retry-After）；更换档位须重新生成，恢复沿用配置快照。原有限制和父任务校验继续适用。

结果 `complete` 代表正文已经发布，不代表复核结束。`review_state` 独立表示 queued/running/paused/completed/unavailable；历史没有记录时为 null。摘要包含 reviewed_units、review_total_units、uncertain_units、unavailable_units、basic_cleanup_units，进度按真实保存结果更新。全局任务列表和 SSE 只含摘要，不读取正文统计。

段落 `text` 是 v6 实际成稿；`sources`、`segment_ids` 保留不可变来源，`scene_ids` 为关联画面，`merged_source_count` 为来源数。`generation_method` 区分 model/basic_cleanup/mixed；`review_outcome` 区分 pending/pass/fact/uncertain/unavailable。兼容字段 `review_status` 仅 pass 对应 accepted、fact 对应 needs_review，其余为 null；历史 null 不表示通过。旧策略的 candidate_text 保持兼容读取。

```bash
python3 scripts/optimize_transcript.py --url http://localhost:20815 --job JOB_ID --rewrite-mode editorial
python3 scripts/optimize_transcript.py --url http://localhost:20815 --job JOB_ID --resume
python3 scripts/optimize_transcript.py --url http://localhost:20815 --job JOB_ID --resume --phase review --wait-review
```

URL 应使用实际部署地址。客户端密钥通过 `AUDIO_INTEL_API_KEY` 提供，不写入 URL。默认在正文就绪后输出 JSON；`--wait-review` 额外等待后台复核终态或暂停，复核不可用不撤销正文成功。

## 持久化与升级

策略 v6／SQLite v18 增加独立复核元数据及内部批次检查点。升级前做 SQLite 一致性备份，无需复制历史媒体；遵循 [升级指南](UPGRADE.md) 完成服务恢复和历史数据验收后清理临时备份。已完成历史稿不自动重算，旧策略未完成候选须明确重新生成。

原有模型身份、配置和任务热词快照保留。v3/v4 的核对记录、v5 的候选正文按旧含义读取；缺失信息不补造为已复核。浏览器档位与导出偏好继续长期保存，刷新、重开、退出登录和资料库替换后保留。阅读方式、筛选、位置及差异显示属于当前任务会话，刷新保留、退出登录清理，最多保留 200 个任务，不存正文或密钥。

English: Policy v6 combines consecutive same-speaker source spans across pictures into readable drafts. Generation batches and seam checkpoints are durable. Published content is available before independent background review finishes; review never changes published text. Structured verdicts distinguish pass, factual concerns and uncertainty, and review failures do not invalidate a usable draft. Comparison presents multiple timed sources against one reading group. PDF/DOCX/TXT exports preserve pending-review notices, factual markers and redacted reasons. Schema v18 adds metadata and checkpoints without rewriting historical text. Use a SQLite-only backup and complete deployment acceptance before cleanup. Native API documentation and the CLI expose independent generation/review resume and optional waiting for review.

## 开发质量验证

固定合成／去标识化语料在 `tests/fixtures/optimization_quality.json`。`scripts/evaluate_optimization.py` 直接调用所选服务，不创建应用任务；必须显式指定服务和模型。先验证语料，再串行执行两个模型，避免竞争资源：

```bash
.runtime/api/bin/python scripts/evaluate_optimization.py --validate-corpus
.runtime/api/bin/python scripts/evaluate_optimization.py --base-url http://localhost:11434 --model qwen3.5:4b --output tmp/quality-4b.json --repeats 3
.runtime/api/bin/python scripts/evaluate_optimization.py --base-url http://localhost:11434 --model qwen3.5:9b --output tmp/quality-9b.json --repeats 3
.runtime/api/bin/python scripts/evaluate_optimization.py --base-url http://localhost:11434 --model qwen3.5:4b --corpus tests/fixtures/optimization_quality_long.json --output tmp/quality-long-4b.json --repeats 1
```

Windows 使用 API 环境的对应 Python 路径。工具保存服务版本、模型 digest、策略及语料校验和、逐项输出和耗时；`--resume` 只恢复身份一致的结果。复核用预置候选独立测量，未知／不可用不从分母删除。语言改善和事实保留须对记录逐项独立审读，不能由改字比例自动认证。真实任务评测应复制到隔离测试目录，按来源范围比较并统计来源字数加权结果，不能修改用户任务来做测试。
