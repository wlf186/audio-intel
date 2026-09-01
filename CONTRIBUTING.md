# Contributing

Thanks for improving Sandevistan Audio. Keep changes focused, preserve Linux and native Windows behavior, and avoid committing local models, task data, generated media, databases, process metadata, or credentials.

## Development setup

Install the API runtime and frontend without downloading inference models:

```bash
./service.sh setup api
```

Use mock mode for routine API, queue, worker, and browser development:

```bash
AUDIO_INTEL_MOCK_MODE=1 ./service.sh start all
.runtime/api/bin/python scripts/smoke_test.py
./service.sh stop all
```

Real-model inference is required when changing model loading, precision, device routing, runtime separation, audio pipelines, process supervision, or batch sizing.

## Repository boundaries

- `audio_intel/` owns the FastAPI gateway, queue, admission, events, observability, workers, GPU coordination, model registry, and cleanup.
- `asr/` and `tts/` own their inference pipelines.
- `frontend/src/` contains the React 19/Vite UI.
- `tests/` and `frontend/e2e/` contain backend and browser coverage.
- `service.sh` and `service.cmd` are the supported Linux and native Windows entrypoints.

Keep the `api`, `asr`, `tts`, and internal `aligner` Python environments separate. Qwen ASR and Qwen TTS require incompatible Transformers versions.

The recommended `full` deployment and opt-in `cpu` deployment use separate ASR, TTS, and aligner locks on both Linux and Windows. Keep profile selection in `.runtime/deployment-profile`; do not hand-mix CUDA and CPU packages or switch profiles by rebuilding one inference environment. Dependency changes must regenerate and validate every platform/profile lock together.

Model identity and revision come only from `audio_intel/model_manifest.json`. Do not add runtime cloud fallbacks or accept user-supplied model repositories, configs, or checkpoints.

## Verification

Run the checks relevant to every code change:

```bash
.runtime/api/bin/python -m pytest -q
.runtime/api/bin/python scripts/lock_dependencies.py --check
corepack pnpm@10.15.1 --dir frontend typecheck
corepack pnpm@10.15.1 --dir frontend build
```

Run browser tests for UI or browser/API interaction changes:

```bash
corepack pnpm@10.15.1 --dir frontend test:e2e
```

Run isolated Linux service lifecycle tests when changing startup, shutdown, readiness, PID handling, TLS, or process trees:

```bash
.runtime/api/bin/python -m pytest -q tests/test_service_script.py
```

Native Windows CI runs `tests/test_service_windows.py` and browser smoke coverage. Do not treat Linux process behavior as proof of Windows compatibility.

### Real inference requirements

- Run real ASR and TTS GPU cancellation smoke tests for process supervision or device cleanup changes.
- Run `scripts/benchmark_single_task_acceleration.py` for batch sizing or inference-call changes.
- Validate affected 0.6B/1.7B, CPU/GPU, clone, diarization, alignment, and OOM paths in proportion to the change.
- Preserve model identity, precision, ASR chunking, diarization semantics, and TTS sequential decoding when changing single-task acceleration.

## API and persistence changes

Public API changes must update the bilingual `/docs`, `/openapi.json`, executable examples, and contract tests together.

SQLite jobs, queue ordering, history, idempotency records, hotwords, voices, voiceprints, and completed-task snapshots are compatibility surfaces. Back up `data/` before migration development and cover migration from the previous schema in tests.

The four native asynchronous submission endpoints must preserve first-accept `202`, same-request replay `200`, conflict `409`, and admission `429` semantics.

## Frontend changes

- Add every user-visible translation key to both `frontend/src/i18n/locales/zh-CN.json` and `en-US.json`; do not reintroduce hard-coded user-facing copy. Run `corepack pnpm@10.15.1 --dir frontend check:i18n` (also included in typecheck and build) to verify key and interpolation parity.
- Do not mount protected business pages or request protected resources before browser-session authentication succeeds.
- Model remote resources as distinct loading, ready, and error states with a retry path.
- Use accessible in-app dialogs instead of `window.confirm` or `window.prompt`.
- At 390 px, keep all visible controls reachable without horizontal overflow and use at least 44 px touch targets.
- Add an interaction assertion, console-error check, and desktop plus 390 px validation for UI changes.
- Document changes to browser-storage lifetime or draft-clearing behavior.

## Pull requests

Use Conventional Commit subjects, for example:

```text
feat(jobs): persist GPU device names
fix(tts): preserve draft text
docs(readme): add product overview and screenshots
```

Pull requests should explain behavior changes, list verification commands and results, call out database/API compatibility, and include before/after screenshots for UI work. Link relevant issues and keep unrelated formatting changes out of the patch.
