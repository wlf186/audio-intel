# Local ASR speech enhancement / 本地 ASR 人声增强

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
mix retains 50% original signal. Speech gain targets −24 dBFS RMS, activates only
below −30 dBFS, is capped at +24 dB, and uses smoothing plus a local −1 dBFS peak
ceiling on processed blocks. Untouched blocks retain original samples and peaks.
These are internal controls, not public sampling or volume parameters.
Processing policy 2 keeps these gain controls and protects the union of original
and enhanced VAD regions before reducing noisy pauses. Speech has a 200 ms head
and 400 ms tail margin. Remaining gaps shorter than 300 ms are untouched; eligible
gaps inside denoised blocks receive 100 ms fades and at most 6 dB attenuation,
never hard muting. No reliable speech or no noise means no pause attenuation.
A failed second VAD retains base enhancement and records `vad_unavailable`.
Adjacent processed blocks crossfade over 50 ms, avoiding a return to raw noise at
each 30-second boundary. Untouched files still bypass sample-exactly.

处理策略 2 保留现有增益目标，只在两次人声检测并集及起止保护区间之外平滑压低较长
噪声间隙；保留轻声、辅音和交谈参与者。边界和 VAD 误差仍需本地对比验证，间隙音量
下降不能视为说话期间真实信噪比提高，也不能单独证明识别准确率提高。
The original and enhanced VAD intervals are merged without changing the existing
ASR chunk-combination rules. Speaker clustering and voiceprint embeddings use
original audio. Enhancement preserves sample count and the video time origin.

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

Optional `processing_version=2`, `pause_attenuated_seconds`,
`max_pause_attenuation_db` and `pause_suppression_reason` identify actual processing.
Pause duration counts samples receiving attenuation, not exact non-speech time;
the maximum is a positive dB reduction. Old results lacking these fields do not
imply successful pause suppression. Timing includes the signal pass and excludes VAD.

增强参数纳入幂等比较：同一 key 不能切换增强模式。历史请求缺少该字段时按关闭解释，
省略字段重放旧请求仍返回原任务；旧失败/取消任务重试保留原请求。不会自动改写历史媒体、
声纹样本、克隆参考或已有任务结果。声纹导入和克隆参考分析不启用此处理。

The UI keeps the original audio selected initially and offers enhanced playback
and download when available. Switching tracks preserves position and playback
state, with the waveform following the selected track. The enhancement preference
uses the existing ASR localStorage lifetime; draft and sessionStorage clearing
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

For policy-2 calibration, generate extended public controls without another TTS call:

```bash
.runtime/tts/bin/python scripts/create_asr_enhancement_fixtures.py \
  --speech-dir testdata/real/asr-enhancement --extended \
  --output-dir testdata/real/asr-enhancement-extended
```

Benchmark modes `no_pauses`, `mix50`, `mix60`, `mix65` and diagnostic `mix75`
separate base enhancement, pause suppression and stronger mixing. Save a frozen
pre-change baseline first; `--baseline-report` reuses its original transcription
only when model, device, cases, durations and normalized audio match. Compare CER
to that baseline where trusted references exist, and match speech loudness for listening.

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
