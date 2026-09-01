# Architecture and Capabilities

This document describes the runtime boundaries and behavioral contracts behind the compact overview in the README. The generated `/openapi.json` remains authoritative for HTTP schemas, and `audio_intel/model_manifest.json` is the only source of model identity and revision.

## Runtime boundaries

The project deliberately uses four Python environments:

| Boundary | Responsibility |
| --- | --- |
| `api` | FastAPI gateway, React assets, SQLite queues, admission, SSE, capabilities, and cleanup |
| `asr` | VAD, speaker diarization, Qwen3-ASR, and ASR worker execution |
| `tts` | Qwen3-TTS preset, clone, and VoiceDesign execution |
| `aligner` | Internal forced alignment for overlong TTS clone references |

Qwen ASR and Qwen TTS require incompatible Transformers versions and must never be installed into the same environment. The aligner is an internal TTS dependency, not a public worker or service target; `setup tts` owns both TTS environments.

The API is the only HTTP process. ASR and TTS each have a persistent FIFO queue and one supervisor. A supervisor owns one reusable same-kind executor at a time.

```text
FastAPI + Web UI
    │
    ├── SQLite WAL ASR queue ── ASR supervisor ── ASR executor ── stage children
    │
    └── SQLite WAL TTS queue ── TTS supervisor ── TTS executor ── optional aligner
```

## Queue and executor lifecycle

- Jobs and queue sequence numbers are durable in SQLite; admission reservations are process-local.
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
  → CAM++ speaker embeddings and clustering on CPU
  → Qwen3-ASR 0.6B or 1.7B on CPU/GPU
  → ForcedAligner on the ASR device when supported
  → speaker turns + JSON/SRT/VTT/TXT
```

### Languages and timestamps

The public explicit-language list is `Auto` plus Chinese, English, Cantonese, French, German, Italian, Japanese, Korean, Portuguese, Russian, and Spanish. These eleven explicit languages support forced word/character alignment.

`Auto` can detect another Qwen-supported language. The task still succeeds, but returns sentence/segment timestamps without fabricated word alignment. Explicit unsupported language input returns `422`.

ASR merges VAD speech regions into roughly 20–60 second chunks. When word timestamps are available, large chunks are split into continuous speaker turns. Multi-speaker tasks may align internally even when the caller requests sentence-level output, then hide the word detail in the public result.

### Diarization and voiceprints

Short audio bypasses the upstream low-window single-speaker fallback. A known speaker count uses KMeans; automatic count uses conservative short-audio cosine clustering and an additional whole-segment review for weak candidate splits. CAM++ operates in single-active-speaker mode, so true overlapping speech is still assigned to one speaker.

The voiceprint library runs after diarization. It can name matched speakers but does not change clustering, speaker IDs, or segment boundaries. Names and notes written into completed task results are immutable snapshots.

### Hotwords

Custom scenario lists and the two system-generated voiceprint-name lists are selected per normal ASR task. They are converted into a Qwen vocabulary context and stored as an immutable request snapshot. Hotwords never apply to clone-reference analysis or voiceprint sample imports.

## TTS pipeline

| Model group | Preset / CustomVoice | Clone / Base | VoiceDesign |
| --- | --- | --- | --- |
| Qwen3-TTS 0.6B | Supported; no natural-language instruction | Supported; no instruction | Not available |
| Qwen3-TTS 1.7B | Supported; optional `instruct` | Supported; no instruction | Supported; `instruct` required |

Preset voices load the selected CustomVoice checkpoint. Voice cloning uses the Base ICL path with `x_vector_only_mode=False` and requires clean reference audio plus accurate reference text. References longer than 15 seconds are truncated on complete word boundaries after internal alignment.

VoiceDesign is native to the 1.7B checkpoint and accepts a natural-language description of voice, pace, pitch, prosody, and emotion. The project does not expose separate numeric speed/pitch controls or public sampling controls because the pinned upstream public interfaces do not support those contracts consistently.

The output language defaults to `Auto` and supports Chinese, English, Japanese, Korean, German, French, Russian, Portuguese, Spanish, and Italian. Prefer the preset speaker's native language reported by `GET /api/v1/capabilities` when possible.

A CPU TTS executor can retain one checkpoint during its warm window. Switching checkpoints or moving to GPU clears the old CPU checkpoint first. GPU checkpoints are released after each task.

## Devices and GPU coordination

ASR and TTS default to GPU at submission time. Every supported path can explicitly select CPU:

- CPU uses FP32.
- GPU uses BF16 without quantization.
- ASR VAD and CAM++ always remain on CPU; the main ASR model and ForcedAligner follow the selected device.

GPU qualification is model-scoped and based on total memory reported by `nvidia-smi`, with a 256 MiB tolerance for nominal hardware/driver reserve:

| Model size | Admission threshold |
| --- | --- |
| 0.6B | 3840 MiB total VRAM |
| 1.7B | 7936 MiB total VRAM |

Admission does not guarantee current free memory. External GPU processes can still produce a runtime OOM. Explicit API GPU requests that fail capability checks return `503`; they never silently fall back to CPU.

ASR and TTS share a project-local GPU file lock, so only one service task loads a large GPU model at a time. The lock coordinates this project only and cannot prevent external CUDA use.

## Single-task acceleration

`accelerate_single_task` is supported by native and OpenAI-compatible ASR/TTS submissions and defaults to `true`. It changes only internal batch sizing:

- GPU hardware tiers target batches `2/4/6/8/12/16` for `<8/8/12/16/24/32+ GB`.
- CPU tiers target `2/4/6/8` when physical cores and available memory meet the paired thresholds.
- 1.7B ASR and TTS models apply a two-step model penalty to the hardware target.
- Disabling acceleration fixes all stages at batch 1.
- OOM retry follows `16 → 12 → 8 → 6 → 4 → 2 → 1` within the same task.

Acceleration does not change model identity, precision, ASR chunking, diarization semantics, or the TTS decoder's sequential block order. Task results record the model-adjusted targets, effective stage batches, hardware diagnostics, penalty steps, and OOM fallback count.

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
.runtime/     isolated api/asr/tts/aligner Python environments
```

Inputs and results persist by default. Permanent deletion applies path-containment checks, rejects unsafe active imports, removes files and database rows, then performs database cleanup. Back up `data/` before migrations or manual recovery.

Model installation, doctor, readiness, and health checks require each `.complete` file to contain the exact revision declared in `audio_intel/model_manifest.json`; file existence alone is insufficient. Runtime model loading is offline and never accepts user-supplied repositories, configs, or checkpoints.
