# Repository Guidelines

## Project Structure & Module Organization

`audio_intel/` contains the FastAPI gateway, SQLite job queue, submission admission control, progress/ETA observability, SSE fan-out, worker supervisors, GPU coordination, model manifest, and cleanup logic. Each ASR/TTS supervisor owns one reusable execution process so a cancelled task can be terminated without stopping its queue; terminal cancellation must only be recorded after the complete task process tree exits. A used executor may be recycled only after its same-kind queue has remained empty for the configured idle window, and its complete process tree must exit before a replacement starts. ASR and TTS pipelines live in `asr/` and `tts/`. The React 19/Vite UI is under `frontend/src/`, browser tests are in `frontend/e2e/`, and backend tests are in `tests/`. Operational scripts belong in `scripts/`; `service.sh` is the supported service entrypoint.

Python is split into four boundaries: `api`, `asr`, `tts`, and the internal `aligner` used by TTS for overlong clone references. Never install qwen-asr into the TTS environment: qwen-asr and qwen-tts require incompatible Transformers versions. Aligner is not a worker or a public service target; `setup tts` owns both environments.

Treat `models/`, `data/`, `cache/`, `tmp/`, `logs/`, `run/`, and `.runtime/` as local runtime state. The `run/` directory contains supervisor PID files and transient executor identity metadata. Never commit model weights, generated audio, databases, process metadata, or credentials.

## Build, Test, and Development Commands

- `./service.sh setup all` installs project-local runtimes and downloads required models.
- `./service.sh start all` starts the API, ASR worker, and TTS worker in the background on port 20810. Use `./service.sh run all` for a Linux foreground/container entrypoint; it is not a Windows action. Use `status`, `logs all`, and `stop all` for operations.
- On native Windows 11, use `service.cmd` with the same background actions and targets except for the Linux-only `run`; successful starts wait for API/worker readiness and stops must remove complete process trees. See `docs/WINDOWS.md`.
- `.runtime/api/bin/python -m pytest -q` runs backend tests.
- `.runtime/api/bin/python scripts/lock_dependencies.py --check` verifies Linux and Windows dependency locks.
- `corepack pnpm@10.15.1 --dir frontend typecheck` checks TypeScript.
- `corepack pnpm@10.15.1 --dir frontend build` creates the production UI in `frontend/dist/`.
- `corepack pnpm@10.15.1 --dir frontend test:e2e` runs Playwright against the local service.
- `AUDIO_INTEL_MOCK_MODE=1 ./service.sh start all` enables fast pipeline smoke testing without real inference.
- `.runtime/api/bin/python -m pytest -q tests/test_service_script.py` checks isolated Linux foreground/background lifecycle behavior without touching the default service instance.
- Native Windows CI runs `tests/test_service_windows.py` for isolated `start`/`restart`/`stop`, PID safety, and process-tree cleanup coverage.

## Coding Style & Naming Conventions

Use four-space indentation, type hints, `snake_case` functions, and `PascalCase` classes in Python. In React, use `PascalCase` components, `camelCase` hooks/helpers, and explicit TypeScript types for API data. Keep API fields in `snake_case` to match Python responses. Prefer small, focused changes and preserve the surrounding no-semicolon frontend style. No repository-wide formatter is configured; avoid unrelated formatting churn.

Direct Python requirements remain human-edited; generated, hashed locks live under `requirements-lock/{linux,windows}`. Regenerate all locks with `scripts/lock_dependencies.py` and commit them together. Keep Python 3.12, uv, pnpm, Torch/CUDA, Qwen packages, and model revisions pinned unless the change explicitly includes compatibility and real-inference validation.

## Testing Guidelines

Name backend tests `test_*.py` and Playwright files `*.spec.ts`. Cover queue state transitions, filesystem cleanup, API error responses, and migration compatibility with pytest/FastAPI `TestClient`. Auth changes must test Bearer clients, browser sessions, same-origin writes, media Range requests, logout, and restart invalidation. UI changes require an interaction assertion, console-error check, and desktop plus 390 px mobile overflow validation. Use mocks for routine tests; real-model inference is mandatory when changing model loading, precision, device routing, runtime separation, or audio pipelines.

Protected business pages must not mount or request protected resources until browser-session authentication succeeds. Represent remote resources with distinct loading, ready, and error states, provide a retry path for recoverable failures, and never render fabricated zero or empty data while a request is unresolved. Use accessible in-app dialogs instead of `window.confirm` or `window.prompt` for destructive and editing workflows. At 390 px, visible interactive controls must remain reachable without horizontal overflow and use touch targets of at least 44 px. Document any change to browser-storage lifetime or draft-clearing behavior.

Worker cancellation changes must verify that the complete executor process tree has exited before the job reaches a terminal state, task temporary files are removed, and the next queued job proceeds without restarting the supervisor. Keep Linux and native Windows process behavior compatible; run real ASR and TTS GPU cancellation smoke tests when process supervision or device cleanup changes.

Single-task acceleration is default-on with an explicit opt-out and must remain quality-neutral: keep model identity, precision, ASR chunking, diarization semantics, and TTS sequential decoder unchanged. Model-specific batch penalties apply after hardware-tier resolution; preserve the model-adjusted task/stage targets, effective stage batches, hardware diagnostics, penalty steps, and OOM fallbacks in task results. Test hardware-tier resolution, ordered batched outputs, OOM fallback to batch 1, the default-on API/UI path, and explicit `accelerate_single_task=false`. Run `scripts/benchmark_single_task_acceleration.py` with real models when changing batch sizing or inference calls. ASR and TTS submissions default to GPU; model-scoped total-memory admission comes from the manifest-backed registry, GPU unavailability must return `503` rather than silently falling back to CPU, and the explicit CPU path must remain tested.

Model identity comes only from `audio_intel/model_manifest.json`. Download, doctor, readiness, and health checks must require `.complete` contents to match the expected revision; existence alone is never sufficient. Keep all model loading offline at runtime and never accept user-supplied model repositories, configs, or checkpoints.

ASR stage-child progress uses immutable, monotonically numbered snapshots so Windows readers never contend with replacement of an open file. The parent must coalesce all published snapshots, drain the final snapshot after stage exit, and clean them up. Progress publication is best-effort and must never fail inference; task input and final output JSON remain strict. Do not fabricate model-load percentages or heartbeats when the backend only exposes start/end boundaries.

TTS controls are model- and voice-mode-specific. The pinned 0.6B models and both Base clone checkpoints reject natural-language instructions; 1.7B CustomVoice preset accepts optional `instruct`/OpenAI `instructions`, and 1.7B VoiceDesign requires native `instruct`. Dedicated numeric speaking-rate/pitch and public sampling controls remain unsupported. Keep `tts.model_capabilities[]` from `GET /api/v1/capabilities` authoritative, never silently ignore unsupported controls, and require updated API/UI capability handling, documentation, contract tests, and real-inference validation for any expansion.

SQLite schema v8 data, historical jobs, queue sequence numbers, stage/timing history, fine-grained progress activity, idempotency records, ASR hotword lists and submitted hotword snapshots, voices, and voiceprint samples are persistent compatibility surfaces. Back up `data/` before migration work. Speaker names and hotword selections in completed jobs are snapshots; later library edits must not rewrite history. Hotword lists apply only to normal ASR and OpenAI-compatible transcription, never clone-reference analysis or voiceprint sample imports. Purges must keep path-containment checks, reject active imports, and clean both files and database records.

The four native asynchronous submission endpoints for ASR, TTS, clone-reference analysis, and voiceprint sample upload require `Idempotency-Key`. Preserve first-accept `202`, same-request replay `200`, conflict `409`, and admission `429` semantics. Tests must cover queue limits, submission reservations, disk protection, `Retry-After`, queue position, ETA warm-up, ETag polling, and SSE reconnect/reconciliation. Global job lists and SSE are summary-only: never read or serialize `result_json`, never treat executor/worker heartbeats or derived wall clocks as semantic changes, and send full request/results only through per-job detail surfaces. Frontends must lazily load and cache details with explicit loading/error states. Admission reservations are process-local; the supported service runs one API process. Do not enable multiple API workers without moving admission coordination to shared durable state.

## Commit & Pull Request Guidelines

Use Conventional Commit subjects such as `feat(jobs): persist GPU device names` or `fix(tts): preserve draft text`. Pull requests should explain behavior changes, list verification commands, call out database/API compatibility, and include before/after screenshots for UI work. Link relevant issues and never include local artifacts or secrets.

## Security & Configuration

Keep the service on trusted networks unless `AUDIO_INTEL_API_KEY` and TLS are configured. `/api/v1/health` is the only intentionally public operational probe; the public TLS bootstrap and root-CA download endpoints may expose only the fixed configured public CA certificate and its fingerprint, never private keys, detailed system data, media, or task data. Browser auth uses an opaque HttpOnly same-origin session cookie and must never place the raw key in storage or URLs. Preserve offline model loading and project-local cache paths; do not add silent cloud fallbacks. Swagger JavaScript, CSS, icons, schemas, and validation must remain locally hosted with no CDN or online-validator fallback. Every public API change must update `/docs`, `/openapi.json`, executable examples, and contract tests together.
