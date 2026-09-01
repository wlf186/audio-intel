<p align="center">
  <img src="frontend/public/sandevistan-audio.svg" width="104" alt="Sandevistan Audio logo">
</p>

<h1 align="center">Sandevistan Audio</h1>

<p align="center">
  <strong>A private, offline-first workstation for speech recognition and synthesis.</strong>
</p>

<p align="center">
  Turn one Linux or Windows machine into a private speech workstation: transcribe and diarize audio with word-level timestamps, manage voiceprints and hotwords, and synthesize or clone speech through a local Web UI or API.
</p>

<p align="center">
  After model setup, inference runs offline and task data stays on your machine.
</p>

<p align="center">
  <a href="README_CN.md">简体中文</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#api-and-integrations">API</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="https://github.com/wlf186/audio-intel/releases">Releases</a>
</p>

<p align="center">
  <a href="https://github.com/wlf186/audio-intel/actions/workflows/linux.yml"><img alt="Linux quality gates" src="https://github.com/wlf186/audio-intel/actions/workflows/linux.yml/badge.svg"></a>
  <a href="https://github.com/wlf186/audio-intel/actions/workflows/windows.yml"><img alt="Native Windows smoke" src="https://github.com/wlf186/audio-intel/actions/workflows/windows.yml/badge.svg"></a>
  <a href="https://github.com/wlf186/audio-intel/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/wlf186/audio-intel"></a>
  <a href="LICENSE"><img alt="Code license: Apache 2.0" src="https://img.shields.io/badge/code%20license-Apache--2.0-blue"></a>
</p>

![Sandevistan Audio local ASR workspace showing a speaker-separated transcript and export controls](docs/assets/readme/asr-workspace.webp)

> [!NOTE]
> The Web UI supports Simplified Chinese and English. Use the language selector in the header or sign-in dialog; the choice is stored locally in the browser. The local Swagger API guide is also bilingual.

## What it does

| Area | Capabilities |
| --- | --- |
| **Speech recognition** | Qwen3-ASR 0.6B/1.7B, FSMN-VAD, CAM++ speaker diarization, sentence and word timestamps, hotwords, voiceprint naming, and JSON/SRT/VTT/TXT export |
| **Speech synthesis** | Qwen3-TTS 0.6B/1.7B, preset voices, reference-based voice cloning, 1.7B VoiceDesign, and WAV/FLAC/MP3 output |
| **Local operations** | Persistent SQLite job queues, upload and inference progress, local ETA history, SSE updates, cancellation, retry, task history, and safe purge |
| **Integration** | Native asynchronous APIs, OpenAI-compatible transcription and speech endpoints, local Web UI, CPU FP32, and NVIDIA GPU BF16 |

### Built for private, repeatable workflows

- **Local after setup.** Model revisions are pinned and runtime loading is forced offline; inputs, results, voices, the database, and logs stay in project-controlled directories.
- **One workstation, complete workflow.** Transcribe meetings, produce subtitles, identify known speakers, maintain scenario hotwords, synthesize speech, and clone voices without assembling separate services.
- **Observable long-running jobs.** Queue position, stage progress, activity, ETA warm-up, ETags, and SSE are available without putting full task results into global list or event payloads.
- **Careful process isolation.** ASR and TTS use separate, incompatible Python environments and reusable supervised executors. Cancellation reaches a terminal state only after the complete task process tree exits.
- **Model-aware controls.** The UI and API expose only controls supported by the selected Qwen checkpoint and voice mode instead of silently ignoring unsupported input.

### Typical uses

- Turn multi-speaker meetings into named transcripts and subtitle files.
- Run a private local voice studio with preset voices, cloning, and 1.7B voice design.
- Add a durable speech backend to local tools through asynchronous or OpenAI-compatible APIs.

<p align="center">
  <img src="docs/assets/readme/tts-workspace.webp" width="49%" alt="Sandevistan Audio TTS workspace with preset voice synthesis">
  <img src="docs/assets/readme/job-history.webp" width="49%" alt="Sandevistan Audio persistent ASR and TTS job history">
</p>

## Quick Start

> [!IMPORTANT]
> A complete ASR + TTS installation uses about **43 GiB** for pinned models, isolated runtimes, and installation caches. Reserve at least **55 GiB** of free disk space; **70 GiB** is recommended. Start with **16 GB RAM**; **32 GB** is more comfortable. An NVIDIA GPU is optional—every capability also has an explicit CPU path.

### Ubuntu 22.04 / 24.04 x86_64

Install Git, curl, tar, Node.js 22.20+ (Node.js 24 LTS recommended), and Corepack. Python 3.12 and pinned project runtimes are installed inside the repository.

```bash
git clone https://github.com/wlf186/audio-intel.git
cd audio-intel

./service.sh doctor
./service.sh setup all
./service.sh start all

curl -fsS http://127.0.0.1:20810/api/v1/health
```

### Native Windows 11 x64

Use a short local NTFS path and Node.js 24 LTS:

```powershell
git clone https://github.com/wlf186/audio-intel.git C:\ai\audio-intel
Set-Location C:\ai\audio-intel

.\service.cmd doctor
.\service.cmd setup all
.\service.cmd start all

Invoke-RestMethod http://127.0.0.1:20810/api/v1/health
```

Open <http://127.0.0.1:20810>. The bilingual interactive API guide is served at <http://127.0.0.1:20810/docs>, and the machine-readable contract is at `/openapi.json`. Swagger assets and validation are hosted locally.

Install or start only one pipeline when you do not need the complete model set:

```bash
./service.sh setup asr   # or: tts / api
./service.sh start asr   # or: tts / api
./service.sh status
./service.sh logs all
./service.sh stop all
```

See the [Linux installation guide](docs/INSTALL.md) or [native Windows guide](docs/WINDOWS.md) for prerequisites, proxies, partial installations, foreground/container operation, and upgrades.

## Compatibility and hardware

| Item | Supported baseline |
| --- | --- |
| Operating systems | Ubuntu 22.04/24.04 x86_64; native Windows 11 x64 |
| CPU | All ASR and TTS capabilities, FP32 |
| NVIDIA GPU | BF16; `nvidia-smi` must work and the driver must support the pinned PyTorch CUDA runtime |
| GPU admission | 0.6B models: 3840 MiB total VRAM; 1.7B models: 7936 MiB total VRAM |
| Memory | 16 GB minimum for full setup; 32 GB recommended |
| Disk | 55 GB free minimum; 70 GB recommended for models, data, and upgrades |

GPU admission uses total reported VRAM, not current free VRAM. Other GPU processes can still cause an out-of-memory failure. Explicit API requests for an unavailable GPU return `503` instead of silently switching to CPU; the Web UI explains the reason and selects CPU for that submission.

macOS and ARM are not validated. There is no official container image. Linux foreground mode can be used as an OCI container entrypoint, but the caller remains responsible for building the runtimes and models into the image or mounting them. Native Windows lifecycle behavior is covered by CI; real-model Windows GPU inference has not yet been validated.

## How it works

```text
20810 FastAPI + local React Web UI
        │
        ├── SQLite WAL ASR queue ── ASR supervisor
        │       └── reusable task executor
        │           VAD → diarization → Qwen3-ASR → forced alignment
        │           └── JSON / SRT / VTT / TXT
        │
        └── SQLite WAL TTS queue ── TTS supervisor
                └── reusable task executor
                    Qwen3-TTS preset / clone / VoiceDesign
                    └── WAV / FLAC / MP3
```

ASR, TTS, and the internal long-reference aligner use separate Python environments because Qwen ASR and Qwen TTS require incompatible Transformers versions. ASR and TTS GPU jobs share a project-local lock so only one large model occupies the GPU at a time. Used executors stay warm briefly for burst traffic and are recycled only after their same-kind queue remains empty and the old process tree has exited.

The default-on single-task acceleration increases internal batch sizes according to hardware and model size without changing model identity, precision, diarization semantics, ASR chunking, or the sequential TTS decoder. OOM retries step down to batch 1 inside the same task. See [Architecture and capabilities](docs/ARCHITECTURE.md) for the full execution, cancellation, model, progress, and capability contracts.

## API and integrations

The four native asynchronous submission surfaces—ASR, TTS, clone-reference analysis, and voiceprint sample upload—require an 8–128 character `Idempotency-Key`. First acceptance returns `202`; a same-request replay returns `200`; reusing a key with different input returns `409`.

Minimal native ASR submission using the CPU path:

```bash
BASE_URL=${AUDIO_INTEL_BASE_URL:-http://127.0.0.1:20810}
ASR_KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')

curl --fail-with-body -sS \
  -H "Idempotency-Key: $ASR_KEY" \
  -F file=@meeting.wav \
  -F language=Auto \
  -F diarize=true \
  -F align=true \
  -F compute_device=cpu \
  "$BASE_URL/api/v1/asr/jobs"
```

OpenAI-compatible synchronous transcription:

```bash
curl --fail-with-body -sS \
  -F file=@meeting.wav \
  -F model=qwen3-asr-0.6b \
  -F compute_device=cpu \
  -F response_format=verbose_json \
  "$BASE_URL/v1/audio/transcriptions"
```

Add `Authorization: Bearer $AUDIO_INTEL_API_KEY` when authentication is configured. Use the native asynchronous APIs for long tasks; they expose queue state, progress, ETA, ETag polling, and SSE. See the [API guide](docs/API.md) for TTS, cloning, hotwords, voiceprints, cancellation, retries, artifacts, Range requests, and event reconciliation.

## Documentation

| Guide | Contents |
| --- | --- |
| [Installation](docs/INSTALL.md) | Linux prerequisites, full and partial setup, proxy, directories, and service modes |
| [Native Windows](docs/WINDOWS.md) | Windows setup, lifecycle behavior, firewall, and troubleshooting |
| [API](docs/API.md) | Native asynchronous and OpenAI-compatible usage contracts |
| [Architecture and capabilities](docs/ARCHITECTURE.md) | Pipelines, models, devices, acceleration, queues, progress, and cancellation |
| [Local HTTPS](docs/HTTPS.md) | Project CA, certificate trust, SAN renewal, and fingerprint verification |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | GPU, models, uploads, queues, progress, and process cleanup |
| [Upgrade](docs/UPGRADE.md) | Data backup, schema compatibility, and upgrade steps |
| [Dependency maintenance](docs/DEPENDENCIES.md) | Runtime separation, pins, locks, and security-audit notes |
| [Contributing](CONTRIBUTING.md) | Development setup, verification, and pull-request expectations |

## Security and data ownership

Authentication is disabled by default for trusted local networks. Before exposing the service beyond a trusted machine or LAN, configure a strong `AUDIO_INTEL_API_KEY` and TLS:

```bash
AUDIO_INTEL_API_KEY='replace-with-a-long-random-value' ./service.sh start all
```

Browser login exchanges the key for an opaque HttpOnly same-origin session cookie; the raw key is not stored in browser storage or URLs. `/api/v1/health` remains a deliberately minimal public probe. Detailed system information, media, models, tasks, and results remain protected.

For microphone recording over a LAN IP, browsers usually require HTTPS. The project includes an offline, project-specific `mkcert` helper; follow the [local HTTPS guide](docs/HTTPS.md). Do not expose port 20810 directly to the public internet without authentication and trusted TLS.

Models, task inputs, generated outputs, the SQLite database, voices, voiceprints, caches, logs, and runtimes stay in project-local directories by default. Inputs and results persist until explicitly purged.

## Support and contributing

- Report reproducible bugs or request features through [GitHub Issues](https://github.com/wlf186/audio-intel/issues).
- Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing runtime boundaries, model loading, device routing, process supervision, or public APIs.
- Review [Releases](https://github.com/wlf186/audio-intel/releases) and the [upgrade guide](docs/UPGRADE.md) before updating an existing installation.

## License

Project-owned code is licensed under the [Apache License 2.0](LICENSE). Downloaded model weights are not included in the repository and remain subject to their upstream licenses; see [third-party and model notices](THIRD_PARTY_NOTICES.md).

Sandevistan Audio is an unofficial cyberpunk-styled interface and is not affiliated with or endorsed by any related game, trademark owner, or rights holder.
