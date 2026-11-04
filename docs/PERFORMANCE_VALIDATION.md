# Performance and I/O validation / 性能与 I/O 验收

This report covers the five second-batch I/O optimizations and the preview-progress
boundary fix. It does not benchmark inference or claim an SSD lifetime improvement.

## Data boundary / 数据边界

Real audio/video, transcripts, subtitles, extracted frames and other content derived
from user media must never enter Git, Git LFS, CI artifacts/logs, PR attachments or
Releases. This benchmark generates tones and geometric video itself; `--suite all`
rejects `--source`. Generated benchmark media and raw measurement JSON also stay
local. Only source, tests, reproduction instructions and aggregate measurements
belong in the repository. Do not attach a local `logs/` directory to a GitHub report.

原始音视频及其转写、字幕、截图等衍生内容严禁提交 GitHub。本报告仅使用脚本生成的
正弦音和几何画面；合成基准媒体与原始测量 JSON 同样只保留本地。提交内容仅限代码、
测试、复现说明及脱敏汇总，不包含私有路径、文件名、任务标识或逐项输出。

## Reproduction / 复现

Run from a clean source checkout with the API runtime already installed:

```bash
AUDIO_INTEL_LOAD_ENV=0 .runtime/api/bin/python scripts/benchmark_io_efficiency.py \
  --suite all --baseline 1c4a537 --candidate f0e0d61 \
  --repeat 3 --output logs/synthetic-io-validation.json
```

On Windows, set `AUDIO_INTEL_LOAD_ENV=0` and use
`.runtime\api\Scripts\python.exe` with the same arguments. The complete performance
suite is a local measurement tool, not a timed CI gate. Routine CI runs focused
synthetic regressions; it does not run or upload the full benchmark. Existing
`--source ... --output ... --repeat ...` chunk-only usage remains supported for
local diagnostics, but its inputs and output must remain local too.

The driver resolves both Git commits and exports separate source archives. Fresh
children use the same interpreter and dependencies with isolated data, temporary,
cache, run and log directories. No deployment database is opened. The candidate
is the frozen runtime-code commit; this report is committed separately so adding
results cannot change the code being measured.

Fixtures are 16 kHz mono PCM16 440 Hz tones at amplitude 0.35, lasting 10, 300 and
3,600 seconds; a six-second 320 × 180 H.264 geometric video at 10 fps; and flat
resource trees of 100 or 10,000 sixteen-byte files. Chunk intervals are 45 seconds
with batch size one. Frame requests cover sparse/dense targets, duplicate targets,
null images and one reusable image. Waveform cases cover full-track cache misses
and hits, plus a 17,003-sample window with 1,000 bins and its cache hit. Progress
cases cover ordinary updates, duplicates, fast phase changes and unread backlogs.

Each case records an initial observation separately, one warmup per version, and
three paired rounds with alternating order. Processes are fresh each time;
application cache hits are explicitly primed before the measured call. OS caches
are neither cleared nor controlled: the first observation is not a cold-disk claim.
Operation counters run in a separate instrumented pass and are not used as timing
samples. Correctness comparisons and operation-count targets are hard assertions.

Process-tree RSS is sampled every 10 ms, including initial and final observations;
it is a sampled high-water mark, not an exact instantaneous peak. Timing or RSS
regressions trigger three additional paired rounds when the median grows both
more than 10% and more than 5 ms or 16 MiB, respectively. These are investigation
thresholds for this machine, not cross-machine CI requirements.

## Results / 结果

Measured on 2026-11-04, comparing first-batch-complete `1c4a5371f5e16ee87376c26ce60a6d059af938d2`
with runtime candidate `f0e0d6141ef730778dd9da6cef9e7f774f388138`.
Environment: Linux x86_64, 32 logical CPUs, 30.9 GiB RAM; Python 3.12.14,
PyAV 18.1.0, NumPy 2.5.2, SoundFile 0.14.0 / libsndfile 1.2.2.
Both versions used these same installed dependencies; no models were loaded.

All 22 cases passed exact output and operation-count checks. No case triggered
the additional-pair threshold. The following are medians of three measured runs;
the separate initial observations, warmups and counter passes are excluded.
RSS cells show initial / sampled peak process-tree MiB for the measured operation.

| Case | Before ms | After ms | Before RSS MiB | After RSS MiB |
| --- | ---: | ---: | ---: | ---: |
| `chunks:10` | 10.571 | 1.448 | 69.0 / 69.4 | 69.1 / 69.3 |
| `chunks:300` | 68.399 | 27.073 | 69.0 / 74.8 | 69.1 / 72.9 |
| `chunks:3600` | 677.429 | 265.306 | 69.1 / 77.7 | 69.1 / 74.8 |
| `resources:100` | 2.679 | 0.640 | 76.5 / 76.6 | 76.6 / 76.6 |
| `resources:10000` | 271.153 | 47.609 | 76.5 / 80.1 | 76.5 / 76.6 |
| `progress:asr` | 40.211 | 12.880 | 69.0 / 69.9 | 69.1 / 69.4 |
| `progress:ordinary` | 2.682 | 3.361 | 79.6 / 79.8 | 79.6 / 79.8 |
| `progress:boundaries` | 0.459 | 0.698 | 79.7 / 79.7 | 79.6 / 79.6 |
| `frames:sparse` | 14.215 | 8.697 | 78.4 / 78.4 | 78.5 / 78.5 |
| `frames:dense` | 140.206 | 98.983 | 78.3 / 79.0 | 78.4 / 78.6 |
| `wave_full:10` | 19.241 | 2.294 | 69.1 / 72.8 | 69.1 / 69.5 |
| `wave_full:300` | 127.939 | 20.707 | 69.0 / 73.0 | 69.1 / 69.5 |
| `wave_full:3600` | 1340.501 | 176.611 | 69.0 / 73.9 | 69.2 / 69.6 |
| `wave_full_hit:10` | 0.079 | 0.116 | 72.7 / 72.7 | 69.5 / 69.5 |
| `wave_full_hit:300` | 0.114 | 0.091 | 72.8 / 72.8 | 69.5 / 69.5 |
| `wave_full_hit:3600` | 0.122 | 0.085 | 73.5 / 73.6 | 69.4 / 69.4 |
| `wave_window:10` | 13.714 | 3.397 | 69.0 / 69.4 | 69.1 / 69.4 |
| `wave_window:300` | 13.869 | 3.404 | 69.0 / 69.4 | 69.0 / 69.4 |
| `wave_window:3600` | 14.319 | 3.429 | 69.0 / 69.4 | 69.0 / 69.4 |
| `wave_window_hit:10` | 0.014 | 0.014 | 69.3 / 69.4 | 69.5 / 69.5 |
| `wave_window_hit:300` | 0.014 | 0.013 | 69.4 / 69.4 | 69.5 / 69.5 |
| `wave_window_hit:3600` | 0.015 | 0.017 | 69.4 / 69.4 | 69.4 / 69.5 |

Representative logical counters (separate instrumented passes):

| Case | Before → after |
| --- | --- |
| `chunks:3600` | logical write bytes: 115,203,520 → 0 |
| `resources:10000` | directory scans: 20 → 6; metadata calls: 150,020 → 30,012; disk free calls: 10 → 3 |
| `progress:asr` | snapshot files: 600 → 200; logical write bytes: 93,870 → 25,490; json parses: 600 → 1 |
| `progress:ordinary` | snapshot files: 80 → 80; logical write bytes: 4,040 → 3,640; json parses: 81 → 2 |
| `progress:boundaries` | snapshot files: 5 → 8; logical write bytes: 254 → 364; json parses: 6 → 2 |
| `frames:sparse` | container opens: 3 → 1 |
| `frames:dense` | container opens: 50 → 1 |
| `wave_full:3600` | container opens: 2 → 0; pcm opens: 0 → 1; PCM block reads: N/A → 7,032 |
| `wave_window:10` | block reads: 1,000 → 3 |

Full PCM16 waveform processing changes from two complete AV decoding passes to
one PCM pass; the 60-minute candidate uses 7,032 bounded block reads. Cache hits
perform zero media opens/reads on both versions. AV block-read counts are N/A:
the SoundFile read counter does not instrument AV decoder internals.

Preview parse counts include one request JSON parse plus snapshot consumption.
The rapid-boundary case changes from five to eight snapshots, preserving all four
phase starts and completions. Its median grows by 0.239 ms; ordinary preview
publication grows by 0.679 ms with atomic immutable publication. Both are below
the 5 ms investigation threshold. These small costs buy the required boundary
and publication semantics rather than representing an inference regression.

中文：22 个用例的结果与操作次数校验全部通过，未触发复测阈值。60 分钟音频不再
写临时分块 WAV；波形由双遍解码改为单遍 PCM 读取；进度积压只解析最新有效状态。
快速预览阶段多保留 3 个必要边界快照，小幅发布开销已如实列出。以上结果只代表
本机合成样本的局部 I/O 路径，不代表整段转写耗时、物理盘写入量或 SSD 寿命。

## Interpretation and limits / 解释与限制

Chunk bytes are logical temporary WAV bytes, not physical disk traffic or NAND
writes. Directory scans and metadata calls count Python-level operations, not all
kernel system calls. Container opens and PCM block reads describe the exercised
code paths; they are not storage-device read counts. Unrelated metrics are N/A.

Preserving rapid preview phase boundaries can deliberately add snapshots. Ordinary
same-phase updates retain their 0.25-second throttle; duplicates are suppressed.
First observations, phase changes and real completions publish immediately. Normal
and catchable error exits flush only observed progress. Forced process termination
cannot guarantee a final publication, and polling readers may coalesce short phases.
Publication failure is retryable and must not fail media processing.

The full inference matrix from the two implementation batches remains separate.
These measurements do not change model identity, precision, ASR chunking, diarization
or TTS decoding, and cannot establish end-to-end transcription speed or SSD endurance.
No public API fields, database schema or historical media are changed by this closeout.
