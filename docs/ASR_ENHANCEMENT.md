# Local ASR speech enhancement / 本地 ASR 人声增强

## Independent controls / 独立开关

New submissions accept `speech_enhancement` and `background_denoise` booleans,
both defaulting to `true`. The UI remembers each choice in the existing
localStorage lifetime (schema v5); legacy v4 `auto` maps to both on and `off`
to both off. Content/session draft clearing is unchanged. Saved video uploads
retain their original parameters when resumed.

| Speech gain / 人声增强 | Denoising / 背景降噪 | Processing / 处理 |
| --- | --- | --- |
| false | false | Original signal / 原音 |
| true | false | Continuous upward gain, no denoiser loaded / 仅连续轻声增益 |
| false | true | Denoising without speech gain / 仅降噪 |
| true | true | Denoising followed by continuous gain / 先降噪再增强 |

Policy 5 separates these operations and reports their individual
`enabled`, `status` and `reason` under `audio_enhancement.speech_enhancement`
and `.background_denoise`. Denoiser failure does not block gain; an applied
result can have `reason=partial_fallback`. Always inspect both outcomes.
Capabilities publish availability/defaults for each operation. Gain requires no
extra model. Both operations run on local CPU independently of the ASR device.
Original playback plus at most one processed track are retained.

旧 `audio_enhancement=auto|off` 仍作为双开／双关的组合别名；不能与新字段混用，
否则返回 422。新任务保存组合、策略版本和服务端选定的降噪后端，不能通过请求指定模型。
重放省略字段时继承已保存值；改变开关返回 409。未保存独立开关的历史任务继续走旧处理链，
历史缺少增强字段仍按关闭解释；不迁移既有任务、媒体或声纹。

The legacy alias enables/disables both and cannot be mixed with independent
fields (422). New jobs snapshot the effective controls, policy and server-selected
backend. Replays inherit saved values; changed controls conflict (409). Historical
requests retain their previous path on replay/retry; a missing legacy field means
o enhancement. No persistent migration is performed.

### Stronger-denoiser experiment / 更强降噪实验

The default remains the validated 50% GTCRN mix with policy-4 continuous gain.
Independent switches do not imply stronger suppression. A pinned
[UL-UNAS DNS3](https://github.com/Xiaobin-Rong/ul-unas/tree/00f7c700da43d38347f30a6ccebd86fcbc798e07)
candidate is included with its [MIT license](../asr/licenses/UL-UNAS-MIT.txt).
It uses existing Torch/NumPy on CPU FP32, without extra Python dependencies.
Setup downloads manifest-owned weights and verifies revision and SHA-256;
runtime never downloads them. Native reshapes replace upstream einops and ISTFT
explicitly preserves input length, including the tail.

在 11 个公开控制样本、10 个可信文本参考的配对测试中，UL-UNAS 的 100% 与 75%
配置均使两个噪音样本的 CER 相对当前方案上升，因此未通过“不降低识别准确率”的门槛，
不作为日常默认，也不宣称已实现更强降噪。管理员实验可设置
`AUDIO_INTEL_ASR_DENOISE_BACKEND=ulunas`；恢复 `gtcrn`（默认）即可回退。该变量仅在
首次接受新任务时选择 manifest 模型；刷新 API 和 worker 后生效，已保存请求不改变。
试听必须比较讲话期间的背景声和轻声字尾，不能只看停顿音量或平均响度。

Both 100% and 75% UL-UNAS arms increased CER on two public noisy controls relative
to the current baseline; they fail the no-regression gate and remain experimental.
The administrator-only `AUDIO_INTEL_ASR_DENOISE_BACKEND=gtcrn|ulunas` selects the
manifest model for new policy-5 requests (default `gtcrn`). Refresh service processes
after changing it; persisted requests keep their backend. This is not a public
model selector. Missing/invalid weights never disable base ASR or independent gain.

New audio and video transcription requests default to `audio_enhancement=auto`.
Use `off` to retain the previous recognition input. Automatic enhancement keeps
all conversation speakers: it suppresses environmental noise and lifts quiet
speech, rather than selecting one target speaker. Background conversations and
severe reverberation can remain; missing recorded speech cannot be reconstructed.

新音频、视频转写默认自动判断是否需要增强，支持显式关闭。人声增强分别判断降噪和增益：
较远、较轻的人声可提升局部音量，噪音较大的片段可保守降噪；干净且音量充足的片段直接旁路。
保留全部交谈参与者，不承诺去除其他人的闲聊，也不承诺恢复录音中缺失的内容。

## Offline installation and processing / 离线安装与处理

`./service.sh setup asr` (Windows: `service.cmd setup asr`) downloads the pinned
GTCRN DNS3 checkpoint declared in `audio_intel/model_manifest.json`. Downloads
use an immutable upstream commit and verify the weight checksum. Runtime loading
requires the matching `.complete` revision and checksum and runs locally on CPU,
independently of the selected ASR CPU/GPU device. No new inference environment,
Python dependency, cloud API, or model source is accepted from a request.

The inference source is adapted from GTCRN under the [included MIT license](../asr/licenses/GTCRN-MIT.txt).
Existing Torch, NumPy and Qwen pins are preserved. A missing enhancer does not
disable the base ASR models: automatic mode falls back to original audio and
records that outcome in the job detail.

Models are evaluated in bounded blocks with context. The current conservative
mix retains 50% original signal. Speech gain targets −24 dBFS RMS, is capped at
+24 dB, and uses a cubic soft transition from −33 to −27 dBFS instead of a hard
−30 dBFS switch. Processed blocks have a −1 dBFS peak ceiling; untouched blocks
retain original samples and peaks.
These are internal controls, not public sampling or volume parameters.
Processing policy 4 first produces the existing GTCRN mix, then estimates a
continuous gain curve using the 70th-percentile speech RMS in six-second centred
windows. Estimates update every 20 ms. Peak limits and forward/backward constraints
bound gain changes to 3 dB/s across the whole file, including processing boundaries.
Short gaps hold the previous estimate for two seconds; longer gaps release gently
to unity gain. VAD supplies statistics without directly gating signal volume.
Policy 3 removed policy 2's pause gate but retained per-second gain decisions,
which could still cause substantial within-phrase background-level changes.
A failed second VAD retains base enhancement and the original speech regions.
Adjacent processed blocks crossfade over 50 ms, avoiding a return to raw noise at
each 30-second boundary. Untouched files still bypass sample-exactly.

处理策略 4 保留原有响度目标与 50% GTCRN 混合，将逐秒增益改为跨块连续增益。
6 秒上下文估计和每秒最多 3 dB 的变化限制，减少背景声跟随词句突然起落；保留自然轻重，
不逐词拉齐音量。平均响度和识别误差不能充分反映憋气感，仍需本地、响度匹配的听感对比。
基础混音与最终输出分两遍写入，预检两份 PCM WAV 的临时空间，约每小时 230 MB；
中间文件在完成、取消和失败后清理，最终产物数量不变。能量统计按 20 ms 帧保存并分块
计算上下文，模型与音频读取仍使用有界块。阶段进度累计各遍实际处理的工作量。
The original and enhanced VAD intervals are merged without changing the existing
ASR chunk-combination rules. Speaker clustering and voiceprint embeddings use
original audio. Enhancement preserves sample count and the video time origin.

For a controlled rollout or comparison, administrators may temporarily set
`AUDIO_INTEL_ASR_CONTINUOUS_GAIN=0` and refresh the service processes to retain
policy 3. The default is enabled (policy 4); this is an operational rollback,
not a request parameter. Results report the policy actually used. Local benchmark
`auto` explicitly evaluates policy 4 independently of this service override.
本地试听验收尚未完成时，可用该开关恢复日常服务并暂留策略 3；确认听感后移除覆盖并刷新
相关进程。不会改写既有任务，也不会将未试听的候选标记为听感通过。

## Request and result / 请求与结果

- Audio ASR and `/v1/audio/transcriptions`: multipart field `audio_enhancement=auto|off`.
- Video transcription: JSON property `audio_enhancement` on `/api/v1/asr/video-jobs`.
- `/api/v1/capabilities` reports `asr.audio_enhancement`, including availability,
  default, modes, model revision and CPU execution.
- Job detail/results optionally contain `audio_enhancement`: requested mode,
  `off`, `bypassed`, `applied` or `fallback` status, reason, revision, gain and
  processing time. Applied results include an enhanced playback URL and waveform.
- Original `/playback` and source downloads retain their meaning. `enhanced.wav`
  is a separate authenticated artifact with Range support and normal task purge.
  No extra audio file is retained when the entire task is bypassed.

`gain_seconds` and `denoised_seconds` report the coverage of blocks that applied
each operation, not exact voiced duration. `elapsed_seconds` measures the
enhancement function, excluding subsequent VAD and other task stages. A changed
transcript alone does not establish improved recognition accuracy.

Optional `processing_version=4`, `pause_attenuated_seconds`,
`max_pause_attenuation_db` and `pause_suppression_reason` identify actual processing.
Pause duration counts samples receiving attenuation, not exact non-speech time;
the maximum is a positive dB reduction. Policy 4 reports both attenuation fields
as zero and the reason `disabled_for_naturalness`. Historical policy-2/3 results
remain valid; policy 3 does not imply continuous gain. Missing fields do not imply successful pause suppression. Timing
includes signal processing and excludes VAD.

增强参数纳入幂等比较：同一 key 不能切换增强模式。历史请求缺少该字段时按关闭解释，
省略字段重放旧请求仍返回原任务；旧失败/取消任务重试保留原请求。不会自动改写历史媒体、
声纹样本、克隆参考或已有任务结果。声纹导入和克隆参考分析不启用此处理。

The UI keeps the original audio selected initially and offers enhanced playback
and download when available. Switching tracks preserves position and playback
state, with the waveform following the selected track. The two enhancement preferences
use the existing ASR localStorage lifetime; draft and sessionStorage clearing
rules remain unchanged. Historical results without the field imply no recorded
enhancement, not successful processing.

## Local paired validation / 本地配对验证

For reproducible public controls, first use the separate TTS environment (never
install qwen-tts into ASR or qwen-asr into TTS):

```bash
.runtime/tts/bin/python scripts/create_asr_enhancement_fixtures.py \
  --output-dir testdata/real/asr-enhancement --device gpu
```

The producer uses fixed public phrases, speaker levels, seed, noise mixtures and
transient peaks. Verify the clean transcription before interpreting synthetic
reference scores. Then run in the ASR environment after installing the pinned models:

```bash
.runtime/asr/bin/python scripts/benchmark_asr_enhancement.py \
  --cases-json testdata/real/asr-enhancement/cases.json \
  --device gpu --model qwen3-asr-1.7b \
  --output-dir logs/asr-enhancement-validation
```

The local case file is a JSON list of `{id,path,start?,end?,reference?}`; each
`reference`, when supplied, must be a trusted transcript verified locally.
Use `--job-id ID` to inspect an existing task without changing it, optionally
`--full` for complete audio. The default arms compare original, gain only,
denoise only, and 25%, 50%, 75% enhanced mixes with gain. They use the production VAD union and ordered chunk rules; `--fixed-vad`
keeps the original boundaries when isolating signal processing. Socket connections are blocked during analysis.

For extended controls, reuse public speech without another TTS call:

```bash
.runtime/tts/bin/python scripts/create_asr_enhancement_fixtures.py \
  --speech-dir testdata/real/asr-enhancement --extended \
  --output-dir testdata/real/asr-enhancement-extended
```

Benchmark `auto`, `no_pauses` and `mix50` use policy 4 without pause gating.
`mix60`, `mix65` and `mix75` compare stronger mixing without gating; `pause_gate`
explicitly reproduces retired policy 2, and `legacy_gain` reproduces policy 3's
GTCRN mix with per-second gain. Both are local diagnostics, not public request
modes. Save a frozen
pre-change baseline first; `--baseline-report` reuses its original transcription
only when model, device, cases, durations and normalized audio match. Compare CER
to that baseline where trusted references exist, and match speech loudness for listening.

For timing-specific incidents in a long recording, process each complete audio arm
first and then extract identical sample ranges for listening and ASR comparisons.
Independently enhancing short excerpts resets model context and gain estimates;
those outputs are not a matched baseline for a full-file result.

The historical policy-2 public comparison used the four original controls and seven extended
controls generated above. With Qwen3-ASR 1.7B on GPU, the ten cases with trusted
ordered references retain the v0.1.33 CER at a 50% mix. Steady-noise safe pauses
drop about 5.8–6 dB while speech-region RMS remains unchanged to measurement
precision. Increasing the mix to 60%, 65% or diagnostic 75% adds one character
error in the original noisy control, so production retains 50%. The overlapping
speaker control has no trusted ordered transcript and is not included in CER.
Transient-dominated gaps received little reduction. Subsequent listening feedback
reported audible pumping despite those aggregate measurements. Policy 3 therefore
removed the pause gate; policy 4 additionally replaces per-second gain. The old pause-reduction numbers do not describe the current
default or establish subjective listening quality or noise removal during speech.

Reports, media and transcripts must stay under ignored `logs/`, `tmp/` or
`testdata/real/`. Standard output contains anonymous measurements, never private
transcripts. Cases without trusted references report text agreement, not accuracy.
PESQ/STOI or true SNR require a clean reference and must not be inferred from a
private noisy recording alone. Keep private fixtures and outputs out of Git and CI.

Verify quiet/noisy speech, missed speech, noise-only hallucinations, speaker
preservation, boundaries, clipping, full-length media clocks and added resources.
For clean audio, automatic bypass must preserve the existing decoded samples.
Listen locally to both tracks; improved listening quality does not by itself
establish improved recognition accuracy.
