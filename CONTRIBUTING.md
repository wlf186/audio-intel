# Contributing

Thanks for improving Sandevistan Audio. Keep changes focused, preserve Linux and native Windows behavior, and avoid committing local models, task data, generated media, databases, process metadata, or credentials.

## Development setup

If this checkout hosts your normal service, use the [single-directory development and deployment workflow](docs/UPGRADE.md#local-development-and-deployment). Record its configuration and running components, stop it before editing runtime code or installing dependencies, and restore it only after the final changes and validation. A second checkout or duplicate model installation is not required. Test instances must use separate data, PID, log and temporary paths, even while the normal service is stopped. Service commands automatically load the project-root `.env`; exported variables take precedence. Set `AUDIO_INTEL_LOAD_ENV=0` for isolated tests, or stage a fixture checkout with its own file. Never overwrite the normal `.env` in tests. File parsing must remain compatible with Bash and native Windows PowerShell before Python setup.

Install the API runtime and frontend without downloading inference models:

```bash
./service.sh setup api
```

Use mock mode for routine API, queue, worker, and browser development:

```bash
(
  export AUDIO_INTEL_LOAD_ENV=0 AUDIO_INTEL_MOCK_MODE=1 AUDIO_INTEL_PORT=20910
  export AUDIO_INTEL_DATA_DIR=tmp/dev-smoke/data AUDIO_INTEL_RUN_DIR=tmp/dev-smoke/run
  export AUDIO_INTEL_LOG_DIR=tmp/dev-smoke/logs AUDIO_INTEL_TEMP_DIR=tmp/dev-smoke/tmp
  export AUDIO_INTEL_CACHE_DIR=tmp/dev-smoke/cache AUDIO_INTEL_MODELS_DIR=tmp/dev-smoke/models
  trap './service.sh stop all' EXIT
  ./service.sh start all
  AUDIO_INTEL_URL=http://127.0.0.1:20910 .runtime/api/bin/python scripts/smoke_test.py
)
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

### Cross-platform development

For changes involving text files, filesystem operations, process lifecycle, service scripts or asynchronous browser behavior, use native Windows CI early in the authorized development/PR workflow. The existing workflows support pull requests; use that feedback before release preparation. PR results complement the exact-commit main and tag gates below. Windows CI covers native service behavior and mock pipelines; it does not establish real Windows GPU inference quality or performance.

- Specify the encoding when reading and writing project-owned text formats, including JSON journals, benchmark reports and test fixtures. Use UTF-8 consistently on both sides; preserve format-specific decoding for imported documents. The album rollback failure came from reading a UTF-8 journal with the system default encoding. Its regression tests emulate a non-UTF-8 default on Linux while preserving strict snapshot comparisons.
- Check file access modes, open-handle lifetimes and replacement behavior on Windows. MP3 checkpoint synchronization requires a writable descriptor for `fsync`; the regression test emulates that requirement on Linux. File replacement and process cleanup still need native coverage where a local simulation cannot reproduce Windows behavior.
- Wait for the observable state an assertion depends on. Loaded waveform data does not guarantee a painted canvas. For virtual-clock tests, pause time before navigation and advance it deliberately after the required UI state is ready. Preserve meaningful assertions rather than relying on fixed sleeps or machine speed.
- Turn demonstrated platform defects into focused regression cases, using local emulation where practical and native Windows checks for the remaining behavior. See the [document TTS validation record](docs/DOCUMENT_TTS_VALIDATION.md) for the checkpoint, session-clock, canvas and rollback incidents and their validation limits.

### Clean-checkout validation

Validate affected documentation and build outputs from a clean checkout or source archive of the candidate, with test state and build artifacts isolated from the running service. A clean `git status` alone does not rule out ignored files masking missing release content. Public document links must resolve to files shipped in Git and valid section anchors; the existing documentation tests check file existence and Git inclusion, so also check anchors when adding or changing them. An earlier release candidate linked to an ignored local skill file that existed only in the development workspace. Keep public guidance self-contained and let local tooling reference it.

### Real inference requirements

- Run real ASR and TTS GPU cancellation smoke tests for process supervision or device cleanup changes.
- Run `scripts/benchmark_single_task_acceleration.py` for batch sizing or inference-call changes.
- For sequence TTS inference or batching changes, also run `scripts/benchmark_tts_sequence.py` against real models; see [the sequence benchmark contract](docs/ARCHITECTURE.md#tts-pipeline). Verify ordered per-item artifacts and snapshotted voiceprint references.
- Validate affected 0.6B/1.7B, CPU/GPU, clone, diarization, alignment, and OOM paths in proportion to the change.
- Run the generation-guard tests in the TTS runtime as well as API tests: the API-only environment skips inference-dependent tests. For guard changes, use `scripts/benchmark_tts_generation_guard.py` with fresh isolated output directories and matched seeds; compare raw audio, false retries, duration, latency and peak memory. See [validation evidence and limits](docs/DOCUMENT_TTS_VALIDATION.md#v0114-reference-ranges-albums-and-generation-protection).
- Preserve model identity, precision, ASR chunking, diarization semantics, and TTS sequential decoding when changing single-task acceleration.

## API and persistence changes

Public API changes must update the bilingual `/docs`, `/openapi.json`, executable examples, and contract tests together.

SQLite jobs, queue ordering, history, idempotency records, hotwords, voices, voiceprints, and completed-task snapshots are compatibility surfaces. Back up `data/` before migration development and cover migration from the previous schema in tests.

The seven native asynchronous submission endpoints (ASR, single-item TTS, ordered TTS sequences, clone-reference analysis, voiceprint sample upload, document import, and document TTS) require `Idempotency-Key` and must preserve first-accept `202`, same-request replay `200`, conflict `409`, and admission `429` semantics.

## Frontend changes

- Add every user-visible translation key to both `frontend/src/i18n/locales/zh-CN.json` and `en-US.json`; do not reintroduce hard-coded user-facing copy. Run `corepack pnpm@10.15.1 --dir frontend check:i18n` (also included in typecheck and build) to verify key and interpolation parity.
- Do not mount protected business pages or request protected resources before browser-session authentication succeeds.
- Model remote resources as distinct loading, ready, and error states with a retry path.
- Use accessible in-app dialogs instead of `window.confirm` or `window.prompt`.
- At 390 px, keep all visible controls reachable without horizontal overflow and use at least 44 px touch targets.
- Add an interaction assertion, console-error check, and desktop plus 390 px validation for UI changes.
- Document changes to browser-storage lifetime or draft-clearing behavior.

## Licensing and provenance

- Unless explicitly stated otherwise, contributions intentionally submitted to this repository are provided under Apache-2.0 in accordance with Section 5 of that license. Contributors must have the rights needed to submit their work.
- Identify third-party code, fonts, icons, samples, and other assets with their source, version or revision, and license. Preserve required notices and do not commit model weights, generated media, or material whose redistribution rights are unclear.
- Update `audio_intel/model_manifest.json`, `THIRD_PARTY_NOTICES.md`, and related documentation when a change alters model identity, dependency provenance, or redistributed notices.
- A prebuilt container, offline installer, runtime bundle, or appliance image requires an artifact-specific SBOM, license-text bundle, and reciprocal-license/source-offer review before release.
- Brand references must remain consistent with [BRAND_NOTICE.md](BRAND_NOTICE.md); do not imply authorization, sponsorship, approval, endorsement, or affiliation by a third-party rights holder.

## Releases

Before the version/tag gates, review the candidate changes against [cross-platform development](#cross-platform-development) and [clean-checkout validation](#clean-checkout-validation). Confirm relevant regression coverage and inspect the current workflows for the checks actually run. Match checks to the affected behavior; documentation-only edits do not require real-model inference.

1. Synchronize remote tags with `git fetch origin --tags --prune`, confirm the next version is unused locally/remotely, and identify the exact candidate SHA in a clean worktree.
2. Set `RELEASE_VERSION` in `audio_intel/version.py` and the version in `frontend/package.json` to the same `X.Y.Z` in the candidate. Require successful Linux and native Windows **main** workflows for that exact SHA before any official tag.
3. Temporarily tag that SHA locally, run tag-specific version tests and a production frontend build, and verify `/api/v1/health`, `/api/v1/system` and OpenAPI `info.version` report exactly `X.Y.Z`. Remove the temporary local tag and verify cleanup. Recheck SHA and worktree; repeat the gate if either changes.
4. Create and push the official `vX.Y.Z` tag once. Wait for both Linux and native Windows **tag** workflows for that tag and SHA to pass before publishing a GitHub Release. A main run alone is insufficient.
5. If tag validation fails, preserve the immutable tag as audit history and stop publication. Never move/reuse the tag or invent another version to bypass a failed release.
6. Publish and read back the Release, confirming the tag/SHA, draft/prerelease state and latest status. Include workflow links and upgrade/validation notes. Keep authentication process-scoped; never persist tokens in remotes, Git configuration or release artifacts.

GitHub publication and local deployment are separate completion results. For local development/release work, finish [local deployment acceptance](docs/UPGRADE.md#local-development-and-deployment) after the final source and tag changes, restoring the recorded service configuration and components. Do not treat an API version check as proof that the ASR/TTS supervisors and executors were refreshed. A remote-only inspection does not start or restart a local service; explicitly report a skipped, pending or failed local deployment instead of implying it succeeded.

Between releases, source checkouts append local SemVer build metadata derived from `git describe`. This lookup is offline; do not add a runtime GitHub request to resolve the version. README release badges should remain dynamic rather than hard-coding a release number.

### CI failure handling

Record the workflow URL, commit SHA, failing step and relevant error or test before deciding how to recover. Distinguish application/platform defects, test timing assumptions, missing checkout content and runner/network failures. A run cancelled by a newer commit is superseded, not evidence that the new candidate failed; it also cannot satisfy a success gate.

For code or test defects, reproduce the failure where practical, correct the cause and run focused regression checks before submitting the corrected candidate to the full workflows. Rerun the same SHA when evidence points to a transient infrastructure failure; repeated runs alone do not resolve a known defect. Preserve assertions and required checks instead of skipping failures or increasing timeouts without a diagnosed need. Any changed SHA must pass its own release gates. A failed official tag remains subject to the immutable-tag rules above.

## Pull requests

Use Conventional Commit subjects, for example:

```text
feat(jobs): persist GPU device names
fix(tts): preserve draft text
docs(readme): add product overview and screenshots
```

Pull requests should explain behavior changes, list verification commands and results, call out database/API compatibility, and include before/after screenshots for UI work. Link relevant issues and keep unrelated formatting changes out of the patch.
