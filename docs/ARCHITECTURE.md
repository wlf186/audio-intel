# Architecture and Capabilities

This document describes the runtime boundaries and behavioral contracts behind the compact overview in the README. The generated `/openapi.json` remains authoritative for HTTP schemas, and `audio_intel/model_manifest.json` is the only source of model identity and revision.

## Runtime boundaries

`service.sh` (Linux) and `service.cmd` (native Windows) are the supported lifecycle entrypoints. They load the project-root `.env` before resolving directories and TLS, then pass the effective environment to the API and workers. Exported variables take precedence over file values; missing settings use their existing defaults. Only `.env` is automatic, while `.env.local-deploy` is an explicitly selected maintenance snapshot. `AUDIO_INTEL_LOAD_ENV=0` skips the file. Loading does not modify the caller's shell or configure independent API clients, and direct Python module invocations do not load `.env`. See the [configuration rules](INSTALL.md#4-代理与配置).

The project deliberately uses four Python environments:

| Boundary | Responsibility |
| --- | --- |
| `api` | FastAPI gateway, React assets, SQLite queues, admission, SSE, capabilities, and cleanup |
| `asr` | VAD, CPU speech enhancement, speaker diarization, Qwen3-ASR, and ASR worker execution |
| `tts` | Qwen3-TTS preset, clone, and VoiceDesign execution |
| `aligner` | Internal forced alignment for automatic and manual TTS reference ranges |

Qwen ASR and Qwen TTS require incompatible Transformers versions and must never be installed into the same environment. The aligner is an internal TTS dependency, not a public worker or service target; `setup tts` owns both TTS environments.

The recommended `full` deployment installs CUDA-capable inference environments that can run either CPU or GPU work. The opt-in `cpu` deployment installs CPU-only Torch environments without CUDA, NVIDIA, or Triton packages. The selected profile is persisted in `.runtime/deployment-profile`; setup chooses matching hashed locks, and service startup rejects mixed or mismatched ASR, TTS, and aligner environments. Profile changes require a stopped service, no nonterminal jobs, and `setup all --profile full|cpu`.

The API is the only HTTP process. ASR and TTS each have a persistent FIFO queue and one supervisor. A supervisor owns one reusable same-kind executor at a time.

```text
FastAPI + Web UI
    │
    ├── SQLite WAL ASR queue ── ASR supervisor ── ASR executor ── stage children
    │
    └── SQLite WAL TTS queue ── TTS supervisor ── TTS executor ── optional aligner
```

## Queue and executor lifecycle

- Jobs and queue sequence numbers are durable in SQLite; admission reservations are process-local. Retrying a failed or cancelled job reserves capacity just like a new submission; its state is rechecked inside the SQLite write transaction before it moves to the queue tail.
- The supported service topology uses one API process. Multiple API workers would require shared durable admission coordination.
- Each supervisor processes its queue in FIFO order and survives individual executor recycling.
- A used executor can stay warm after its queue drains. A new same-kind job cancels the idle timer and reuses it.
- After the configured idle window, the supervisor waits for the complete executor process tree to exit before starting a clean replacement.
- `AUDIO_INTEL_EXECUTOR_IDLE_SECONDS` defaults to 60; `0` recycles immediately after the queue drains.

Cancellation first offers a short cooperative exit window. If required, the worker terminates the current task process and all descendants. The job becomes terminally cancelled only after the process tree has exited, GPU/file locks are released, and task temporary files are cleaned. The next queued job proceeds without restarting the supervisor.

## ASR pipeline

```text
audio input
  → FSMN-VAD on CPU
  → optional GTCRN denoising + quiet-speech gain on CPU
  → union of original and enhanced VAD regions
  ├─ original audio → CAM++ speaker embeddings and clustering on CPU
  └─ enhanced audio when applied, otherwise original
       → Qwen3-ASR 0.6B or 1.7B on CPU/GPU
       → ForcedAligner on the ASR device when supported
  → speaker turns + JSON/SRT/VTT/TXT
```

### Speech enhancement

The browser and recommended API use one `audio_enhancement=auto|off` control.
Policy 6 retains compatible independent `speech_enhancement` and `background_denoise`
booleans, both on by default. It executes denoising before continuous gain and
recovers each operation independently. Gain-only skips model loading. Results
record both outcomes; aggregate `applied` can include a `partial_fallback`.
Submission snapshots include effective controls, numeric strengths, policy and manifest-selected
backend; policy-6 ETA cohorts distinguish strengths while historical cohorts retain their keys. Historical requests without
these controls retain the legacy path and fingerprints. The original audio still
supplies speaker clustering and voiceprints. Browser preferences migrate to v7 with server-default strengths; v6 master values survive, and v5 migrates by combining either enabled control into `auto` while preserving both-off as
`off`, storage lifetime and paused-upload snapshots. There is no database migration.

The validated default remains GTCRN 50%. A pinned CPU FP32 UL-UNAS candidate
has an independent optional manifest role and uses existing Torch/NumPy. Both
UL-UNAS mix candidates failed public CER validation and are experimental; the
administrator backend override does not modify persisted requests. Both denoisers
are optional for base ASR readiness, with exact revision/checksum availability
reported separately. No runtime network download or user model path is accepted.


New audio, video and compatible transcription submissions default to
`audio_enhancement=auto`; `off` preserves the former recognition input. The pinned
GTCRN-DNS3 checkpoint runs in a separate CPU FP32 stage child within the existing
ASR process tree, using bounded blocks and context. It uses the ASR environment,
with no additional worker or package. Each operation reports its outcome:
unavailable denoising does not block gain. Partial success produces an enhanced
track with `applied` / `partial_fallback`; only no usable enhanced output falls
back to original audio. Inspect both operation statuses.

Default automatic processing retains 50% original signal and smooths upward gain for quiet
speech. Clean, audible blocks bypass processing. The VAD union uses existing chunk
rules; original audio supplies CAM++ and voiceprints. Sample count, video time
origin and ASR model/precision are preserved. Clone-reference analysis and
voiceprint imports are excluded.

Current policy 6 retains the continuous gain introduced by historical policy 4,
separating GTCRN mix (default 0.5, configurable 0–1) from file-wide dB gain
(default multiplier 1, configurable 0–2). Zero skips the operation; master off disables both.
The gain multiplier precedes fixed peak/slope limits. Default signal output is unchanged.
Historical policies ≤5 keep their snapshots; adding strengths to their idempotency keys
conflicts. Per-operation results add optional strength; global summaries do not.
Six-second centred speech RMS estimates, a soft activation knee and peak-safe
forward/backward limits bound gain changes to 3 dB/s. Short gaps hold estimates;
long gaps release gently to unity. Compact 20 ms statistics and tiled quantiles
avoid full-waveform or full-spectrogram buffering. The two WAV passes preflight
temporary space and clean all intermediates on success, failure or cancellation.
Policy 3 removed policy 2's pause gate but retained per-second gain decisions.
The second VAD union for ASR remains. A second-VAD failure
retains base enhancement and original speech regions. Adjacent processed blocks
crossfade over 50 ms. The final
waveform is generated after all signal changes. Optional policy/attenuation
diagnostics report zero pause attenuation and `disabled_for_naturalness`; historical
policy-2/3 results stay readable. Global summaries and SSE stay unchanged.

Applied results declare a separate authenticated `enhanced.wav` and waveform.
Original playback remains the default; track switching preserves position and play
state. Entirely bypassed tasks retain no additional WAV. Historical requests
without the field remain off on replay/retry; existing media/results are not
rewritten. See the [enhancement contract](ASR_ENHANCEMENT.md).

### Languages and timestamps

The public explicit-language list is `Auto` plus Chinese, English, Cantonese, French, German, Italian, Japanese, Korean, Portuguese, Russian, and Spanish. These eleven explicit languages support forced word/character alignment.

`Auto` can detect another Qwen-supported language. The task still succeeds, but returns sentence/segment timestamps without fabricated word alignment. Explicit unsupported language input returns `422`.

ASR merges VAD speech regions into roughly 20–60 second chunks. When word timestamps are available, large chunks are split into continuous speaker turns. Multi-speaker tasks may align internally even when the caller requests sentence-level output, then hide the word detail in the public result.

### Diarization and voiceprints

Short audio bypasses the upstream low-window single-speaker fallback. A known speaker count uses KMeans; automatic count uses conservative short-audio cosine clustering and an additional whole-segment review for weak candidate splits. CAM++ operates in single-active-speaker mode, so true overlapping speech is still assigned to one speaker.

The voiceprint library runs after diarization. Each speaker independently selects its best person match above the fixed cosine threshold, allowing multiple clusters to match one person. Result assembly then merges named speakers by normalized name plus note, keeping the first ID in the speaker list and remapping segment and word speaker references. Clustering, segment boundaries, text and timestamps are preserved. Different notes and unnamed default speakers stay separate. Names and notes are task snapshots: later library edits do not rewrite them.

Completed ASR results support explicit task-local name/note editing. The browser can copy an existing library person's name and note into editable fields, reusing the authenticated library resource and its loading/error states. Selection is dialog-local; only the current name/note inputs are submitted as a task snapshot. Names are trimmed and retained in full. An omitted note preserves the snapshot, while null or blank clears it; notes follow the library's 20-character normalized limit. Manual saves merge all duplicate named identities within that task and update every segment of the merged person. Exports are staged before replacement and SQLite commit, with file and database rollback on save failure; saves are serialized per job in the single API process and hold a lease against task deletion. Historical results remain readable without new fields and are normalized only on explicit save. Existing voiceprint samples retain their original source-speaker snapshots; enrollment uniqueness continues to use job plus segment ID.

### Hotwords

Custom scenario lists and the two system-generated voiceprint-name lists are selected per normal ASR task. They are converted into a Qwen vocabulary context and stored as an immutable request snapshot. Hotwords never apply to clone-reference analysis or voiceprint sample imports.

## TTS pipeline

| Model group | Preset / CustomVoice | Clone / Base | VoiceDesign |
| --- | --- | --- | --- |
| Qwen3-TTS 0.6B | Supported; no natural-language instruction | Supported; no instruction | Not available |
| Qwen3-TTS 1.7B | Supported; optional `instruct` | Supported; no instruction | Supported; `instruct` required |

Preset voices load the selected CustomVoice checkpoint. Voice cloning uses the Base ICL path with `x_vector_only_mode=False` and requires clean reference audio plus accurate reference text. Automatic selection uses at most the first 15 seconds on complete word boundaries. Library voiceprint samples may instead specify a 3–30 second interval anywhere in the original recording. Audio and transcript are cropped together from immutable submission snapshots; absent or invalid alignment is recomputed once in the separate offline aligner, then fails explicitly if still invalid. Manual ranges require at least 3 seconds after inward word-boundary alignment; automatic short references retain their existing behavior. The original library audio and alignment are not rewritten.

VoiceDesign is native to the 1.7B checkpoint and accepts a natural-language description of voice, pace, pitch, prosody, and emotion. The project does not expose separate numeric speed/pitch controls or public sampling controls because the pinned upstream public interfaces do not support those contracts consistently.

The output language defaults to `Auto` and supports Chinese, English, Japanese, Korean, German, French, Russian, Portuguese, Spanish, and Italian. Prefer the preset speaker's native language reported by `GET /api/v1/capabilities` when possible.

A CPU TTS executor can retain one checkpoint during its warm window. Switching checkpoints or moving to GPU clears the old CPU checkpoint first. GPU checkpoints are released after each task.

Ordered sequence jobs reuse one loaded checkpoint across all items, preserve item and chunk order, and emit one WAV artifact per input item. They deliberately require a single model, device, language, and voice mode so execution and retry semantics stay deterministic; per-item preset speakers/instructions or voiceprint samples remain supported. The capability contract is additive under `tts.sequence_jobs`, allowing older clients to continue submitting single-item jobs.

A sequence occupies one position in the existing TTS queue. Submission copies each distinct voiceprint reference into the job input and snapshots its transcript and metadata; subsequent library edits do not change that accepted request. Synthesis progress counts text chunks across the sequence, not completed item downloads. Results become available only after the whole job succeeds. Retrying a failed or cancelled sequence requeues its complete persisted request and regenerates all items; there is no per-item resume.

Run `scripts/benchmark_tts_sequence.py` against a real service to compare the same ordered script as sequential single-item jobs and one sequence job. It performs a warm-up, alternates execution order across two repetitions, validates every WAV, reports median timings and the provider's actual generation batch size, and exits nonzero unless the sequence path is at least 15% faster. Pass `--items-json` with a JSON list of `{id,text,speaker}` objects for a representative script; benchmark jobs are purged by default.

## Devices and GPU coordination

ASR and TTS default to GPU at submission time in the recommended full deployment and default to CPU in the CPU-only deployment. Every full-deployment path can explicitly select CPU:

- CPU uses FP32.
- GPU uses BF16 without quantization.
- ASR VAD, GTCRN enhancement and CAM++ always remain on CPU; the main ASR model and ForcedAligner follow the selected device.

GPU qualification is model-scoped and based on total memory reported by `nvidia-smi`, with a 256 MiB tolerance for nominal hardware/driver reserve:

| Model size | Admission threshold |
| --- | --- |
| 0.6B | 3840 MiB total VRAM |
| 1.7B | 7936 MiB total VRAM |

Admission does not guarantee current free memory. External GPU processes can still produce a runtime OOM. Explicit API GPU requests that fail capability checks return `503`; they never silently fall back to CPU. A CPU-only deployment reports `gpu_runtime_installed=false`, removes GPU from every model-scoped capability, and returns `503 gpu_runtime_not_installed` for an explicit GPU request before accepting a job or upload. Physical GPU telemetry may still be visible under `/api/v1/system` and does not imply that a GPU inference runtime is installed.

ASR and TTS share a project-local GPU file lock, so only one service task loads a large GPU model at a time. The lock coordinates this project only and cannot prevent external CUDA use.

## Single-task acceleration

`accelerate_single_task` is supported by native and OpenAI-compatible ASR/TTS submissions and defaults to `true`. It changes only internal batch sizing:

- GPU hardware tiers target batches `2/4/6/8/12/16` for `<8/8/12/16/24/32+ GB`.
- CPU tiers target `2/4/6/8` when physical cores and available memory meet the paired thresholds.
- 1.7B ASR and TTS models apply a two-step model penalty to the hardware target.
- Disabling acceleration fixes all stages at batch 1.
- OOM retry follows `16 → 12 → 8 → 6 → 4 → 2 → 1` within the same task.

Acceleration does not change model identity, precision, ASR chunking, diarization semantics, or the TTS decoder's sequential block order. Task results record the model-adjusted targets, effective stage batches, hardware diagnostics, penalty steps, and OOM fallback count.

Batch OOM recovery only reduces the effective batch within the same task. For ordinary ASR, single-item TTS and ordered sequences, a model-load OOM or an OOM that remains at batch 1 is terminal: the task fails without CPU fallback or automatic retry, captures before/after memory and visible-GPU-process diagnostics, and the supervisor waits for the complete executor process tree to exit before starting a clean replacement.

## Progress, ETA, and events

Job state is durable. Progress snapshots are monotonic and best effort:

- Model loading publishes start and completion boundaries only.
- Fine-grained ASR/TTS activity is persisted at a limited cadence when the backend exposes real units.
- Estimated totals are labelled through `progress_detail.basis`; they are not precise remaining-work commitments.
- Stage-child ASR progress uses immutable, monotonically numbered snapshot files so Windows readers never replace an open file.

ETA uses local historical tasks with the same model, device, mode, and similar task features. It starts returning an interval after at least five suitable samples. 0.6B and 1.7B histories warm independently, and the estimate is guidance rather than an SLA.

The global job list and global SSE stream are summary-only. They never read or serialize full result JSON. Consumers fetch full request and result data through a per-job detail surface only when needed.

## Persistence and cleanup

Default project-local runtime state:

```text
models/       pinned model weights
data/         SQLite, task inputs/results, voices, and voiceprints
tmp/          per-task temporary files
cache/        uv, pip, Hugging Face, ModelScope, and Torch caches
logs/         API, ASR, and TTS logs
run/          supervisor PIDs, executor identities, and GPU lock
.runtime/     deployment profile and isolated api/asr/tts/aligner Python environments
```

Inputs and results persist by default. Permanent deletion applies path-containment checks, rejects unsafe active imports, removes files and database rows, then performs database cleanup. Before migrations, choose the database/file backup scope using the [upgrade guide](UPGRADE.md#upgrade-backup-scope). Before manual recovery, preserve the affected state; use a full data backup when the impact is uncertain.

Model installation, doctor, readiness, and health checks require each `.complete` file to contain the exact revision declared in `audio_intel/model_manifest.json`; file existence alone is insufficient. Runtime model loading is offline and never accepts user-supplied repositories, configs, or checkpoints.


## Voiceprint metadata compatibility

Schema v10 identifies people by stable IDs with a unique normalized `(name_key, note_key)` pair; absent notes map to the empty key. Sample IDs and media paths remain stable, with unique `(person_id, name_key)` sample names. Source-derived names are allocated under a write transaction; manual renames fail on collisions. Existing samples retain the library numbering seen at migration, persisted as names. The parent-table migration disables foreign-key actions outside its transaction, preserves children and aliases, validates foreign keys, and commits the schema version only on success.

System full-name and surname-free lists deduplicate names independently of people. One person's removal or opt-out cannot remove another opted-in person's shared term. Neither notes nor sample names become ASR hotwords.

TTS requests snapshot person names, notes, and sample names with reference audio. Same-request replays compare normalized caller parameters and sample IDs against the accepted request, including older idempotency records, instead of mutable library names or `updated_at`. Renaming changes metadata only; it neither modifies the audio pipeline nor rewrites historical jobs or sequence contract v1 results.


## TTS generation protection

A document section, an internal model text chunk, and a generated codec step are different units. A section may contain many internal calls. The internal budget is `min(8192, max(750, ceil(13.5 * target_text_tokens)))`; tokenizer failures use explicitly recorded character counts. The 8192 ceiling is a generation limit, not the document character target or an input-context diagnosis. Budgets are conservative limits, not exact duration predictions.

The guard observes natural EOS before padding on each batch row and decodes valid rows sequentially. Missing EOS, empty audio and non-finite waveforms cause singleton recovery of that row only, up to three additional calls with the same voice/reference and existing sampling parameters. Recovery changes the random trajectory while preserving the surrounding RNG state. Clearly identified TOC dot leaders may be removed only during recovery; normal input splitting and sampling defaults remain intact. Naturally ended speech can still repeat or mispronounce content.

Per-chunk journals under each job’s `diagnostics/tts-generation/<attempt>/` retain retry allowances through document resource recovery. Exhaustion raises `TtsGenerationGuardError`; document cleanup removes the incomplete section, retaining verified completed sections. A user-initiated job retry starts a new attempt; sequences replay the complete persisted request. Optional `generation_guard` counters describe checked original chunks in this attempt, excluding reused historical sections. Missing counters do not imply coverage. `tts_chunk_retry` reports attempts while overall progress stays monotonic; global lists/SSE stay summary-only. Journals and failure diagnostics are local task data, purged with the task.

## Document imports and long TTS

Schema v11 adds `document_imports` and `document_sections` without replacing historical task, library or idempotency data. The API runtime owns a serial, offline parser child with a time/RSS limit; its five queued/running slots include uploads still being received. Authentication, idempotency validation, shared submission concurrency and cumulative disk reservations run before receiving new uploads. Files stream to a caller-owned partial source on the data volume and are atomically renamed. Identical replays hash without another file or parser slot. Source archives have independent expanded-size and member limits; Office dependencies stay in the API environment.

A ready import holds canonical text and structural metadata. Versioned previews partition it without dropping characters; blank-only intervals merge into adjacent spoken text. Snapshot copies serialize with import deletion. Document jobs enter the normal TTS queue with independent source/text/section snapshots and one common voice configuration. Incremental MP3 encoding bounds waveform memory; per-section hashes and checkpoints preserve completed work across cancellation and retry. The existing executor process-tree retirement contract remains authoritative. Document jobs additionally allow two resource-recovery waits (30 and 120 seconds) after retiring the executor; they retain their queue position and reuse verified sections. Cancellation, disk exhaustion, invalid input and missing models do not trigger this automatic retry.

Global job lists/SSE remain summary-only. Typed per-document APIs expose paginated stable section metadata and lazy text. Imports are retained until manual deletion; browser sessionStorage stores a versioned selection draft, including explicit empty selections, not the document text. Language changes do not reload or reset the draft. The import manager UI supports reuse, parser retry and cross-page bulk selection. Removal calls the existing single-import DELETE sequentially, reports each outcome, and retains failed selections for retry; active imports cannot be removed. Reader display modes and bulk selections are dialog-local memory. Continuous reading changes presentation only, preserving canonical text and offsets.

ZIP_STORED ZIP64 export and packet-remuxed complete MP3 run on demand with bounded chunks and backpressure. Export files are not persisted. New document requests snapshot immutable album metadata inside the submission transaction: document title, voice/person name and UTC creation time, with a short suffix for collisions. Selected sections receive ID3v2.3 track order and the same album; complete MP3 carries that album without a section track number. Historical jobs remain unchanged unless explicitly processed by the offline single-job tagging tool. Download leases limit concurrency and exclude task purge; disconnects close iterators and release leases. Native browser downloads use same-origin hidden frames so structured pre-stream failures stay in the app; a begun transfer is owned by the browser download manager. No complete-file Blob is created.

## Audio artifact waveforms

Single-item, ordered-sequence and document TTS prepare up to 240 uniformly timed normalized peaks after encoding. Optional waveform preparation must not fail synthesis. Historical audio is decoded in bounded blocks on first access through the authenticated artifact waveform endpoint; only declared artifacts of successful jobs are eligible. Existing request/result snapshots and legacy inline peaks remain compatible.

Each job owns `waveforms/<sha256(artifact_name)>.json` sidecars containing a cache version, source size/mtime fingerprint and waveform response. Atomic replacement prevents partial JSON reads; stale fingerprints trigger regeneration. These few-KB files contain no duplicate audio and are removed with the job. Same-file computations coalesce, with at most two cold computations in the API process; saturation returns 429 with Retry-After. A reader lease prevents purge during waveform reads/computation without consuming the separate batch-download slots. Interrupted temporary waveform files are removed during task cleanup.

## Browser workspaces and state

ASR uses fixed create/results tabs; TTS uses fixed text/document/results tabs. Opening results selects a task explicitly and loads successful-job details lazily with separate loading, error and retry states. Background completions do not replace the selected task; recent lists retain an explicitly selected older task. Full history and management remain on the global Tasks page. Leaving results pauses playback.

ASR file objects live only in page memory: tab switches retain them, successful submission clears them, and refresh or leaving the ASR page discards them. Existing ASR/TTS parameter preferences keep their localStorage lifetime; document selection drafts retain sessionStorage semantics. Workspace navigation and expansion state are page-local. Returning to the same ASR result preserves search, speaker filters and reading position; changing tasks resets them.

Waveforms are fetched only for visible players. The browser limits the shared cache to 64 memory entries, clears it on logout and adds no persistent storage. Loading or failing to fetch a waveform does not block audio playback or download. Result-detail caching remains separate from the summary-only job list and SSE.

Library reference ranges use `audio-intel:tts-reference-ranges:v1` localStorage, keyed by sample ID, surviving refresh, navigation and logout. Applying stores the interval; cancelling does not. Automatic selection and Restore defaults clear only the current sample’s saved interval. These browser preferences never become defaults for independent API clients. Sample waveforms use the authenticated sample endpoint, the same bounded peak computation and reader leases, and are removed with the sample.


## Video transcription and reading exports

Video uploads use durable sequential offsets and chunk hashes (schema v12), with one append-only input file and same-filesystem rename on submission. Unfinished upload reservations reduce shared disk admission; they expire independently of completed tasks. `purpose=video_transcription` runs in the existing ASR supervisor/executor, preserving cancellation and queue ownership. Video PTS and audio PTS share a time origin. Sampled frames stay in memory; only representative JPEGs are retained, alongside the original normalized playback WAV and a separate enhanced WAV only when applied. Manual scene revisions and speaker edits share the task's edit lock and reader lease. Full scene/result data never enters global job summaries or SSE.

Concise PDF is a protected, on-demand API export, with a bounded in-memory cache and one renderer. It uses the same paragraph/scene mapping as the browser's illustrated reading view. See [video contracts, resource limits and storage lifetime](VIDEO_TRANSCRIPTION.md).

### Transcription recovery and binary contracts

Scene-edit dialogs snapshot their opening revision and bounds independently of refreshed job details; a changed revision disables saving until explicit discard/reload. Server revision checks remain authoritative. The browser verifies confirmed upload chunks with a separate `verifying` progress phase before resuming network transfer. PDF export uses a cancellable single POST with normal session handling and Retry-After feedback; successful binary responses are represented accurately in OpenAPI, and HEAD describes headers only.

The executable video client atomically persists the accepted job ID in its existing UTF-8 journal and resumes polling/download without another submission. A requested PDF is written to a sibling temporary file, closed and atomically replaced before journal removal; a failed download preserves any complete destination. This is client-local recovery, with no database or historical-result migration. Browser sessionStorage/localStorage lifetimes remain unchanged.

## Voiceprint grouping and export copies

SQLite v14 adds independent group and single-membership tables, with a default-membership trigger covering normal creation, legacy voice creation and archive imports. Existing person rows and historical jobs remain unchanged. Batch movement validates all identities inside one immediate transaction; group deletion moves members before deleting the group. Portable ZIP v2 snapshots groups/membership, accepts legacy v1, and includes group edits in preview revisions.

ASR first acceptance snapshots selected groups and eligible person IDs. Workers read only eligible surviving IDs; names/ready samples remain execution-time data. Idempotency compares caller selection while excluding derived membership snapshots, and retries preserve the persisted request. Legacy jobs keep their former matching semantics. Group snapshots never enter global summaries/SSE.

Synchronous transcript export holds the task reader and speaker-update lock, transforms a copy and formats it without writing persistent artifacts. Uppercase name initials share one collision map across formats; note masking counts Unicode characters. Escaped literal rules apply longest-first in one pass and merge matched timed-word spans. Redacted JSON whitelists transcript/timing/model fields and omits auxiliary original identities and paths. PDF cache identity includes result revision and policy; size/concurrency budgets remain. This is text redaction, with original audio/video pixels unchanged. Browser export preference v1 is independent of ASR v4 and survives logout/library replacement.

Temporary upgrade backups have a recorded purpose and exact path. Scope follows migration impact, never schema number alone; complete local acceptance precedes mandatory exact-directory cleanup. Separate daily/library backups are not part of upgrade cleanup.

## On-demand job metadata and video navigation

Job-owned storage uses an authenticated batch POST separate from summary queries and SSE. It selects only ID/state/update metadata, never `result_json`, scans owned job/temp paths without following symlinks or Windows reparse points, and reports per-item unknown states separately from real zero. A bounded 512-entry cache retains active measurements for 5 seconds and terminal measurements for 30 seconds; at most two scans run concurrently. Shared libraries, models and SQLite are excluded, so deletion's reclaimed space is a separate measurement.

Video navigation uses one revision-aware scenes resource for both the illustrated reader and the timeline. Existing representative frames form a proportional scene track, with visible tiles loaded lazily and a keyboard-accessible scene selector for dense timelines. The waveform is the seek control; zoom/pan browse the time window, with debounced reads and stale-response cancellation. Playback ticks only update the playhead. The window waveform endpoint seeks saved mono PCM16 WAV in bounded blocks, caches up to 128 windows in memory, and shares the existing two-calculation budget and purge reader leases. It does not create sidecars or backfill media; the older artifact endpoint remains capped at 240 peaks. Enhancement summaries use persisted outcomes/strengths and keep fallback visible without changing processing.

### Result-side picture detection / 结果页画面检测

Video presentation-v2 separates low-resolution feature sampling, temporal candidate confirmation and representative JPEG generation. Chunked XOR/ZIP feature caches are versioned and bound to source size/mtime, ROI and sampling shape. They are job-owned optional state (512 MiB budget), streamed in bounded chunks and rebuilt on mismatch/corruption. Initial ASR/video decoding preserves the existing audio path; legacy retry uses presentation-v1.

视频 v2 将特征抽样、候选持续时间判断和正式截图分离；分块差分压缩缓存绑定源文件、ROI 和特征版本，按块读取，失配或损坏时重建。缓存可选且计入任务空间，旧任务重试保留 v1，音轨解码路径不变。

API-managed disposable previews use one bounded CPU subprocess, immutable progress snapshots and complete process-tree cleanup. Preview receipts/images are temporary and expire on restart; successful feature caches survive. Reader leases protect active source/cache access from deletion. Applying a ready preview uses the existing speaker/result lock plus revision and updated_at checks, installs images before committing the new video section metadata, and preserves transcript and first-section snapshots. The ASR/TTS queues, job summary/SSE contract and SQLite schema are unchanged.

预览由 API 管理独立 CPU 子进程，使用不可变进度文件和完整进程树回收；重启丢弃未应用预览、保留完整缓存。读取租约阻止处理中删除任务；应用使用结果锁及修订检查，先准备图片再提交分段元数据。队列、任务摘要/SSE 和数据库结构保持兼容。


Video export visibility is optional task-result metadata (`video.export_hidden_scene_ids`) with no schema migration or historical backfill. Revision-checked writes share the job result lock and deletion lease. Automatic/original scene snapshots and media files remain intact. Applying changed detection settings clears markers; otherwise markers are matched by interval, frame time and image, never by reused scene ID alone. PDF renders placeholders without opening hidden image files, and its result-based cache identity includes the markers. JSON exports carry the metadata; reading and timeline continue to show source pictures.

中文：视频导出隐藏状态仅记录任务结果中的画面 ID，不迁移数据库、不回填历史任务。修订锁和读取租约保护写入。参数变化后应用清空标记，否则通过时间范围、截图时间与图片匹配继承，不能仅凭重用的编号继承。PDF 不读取隐藏图片，缓存包含标记；JSON 携带标记，阅读和时间轴继续显示原图。
