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

### Parallel CI and early Windows feedback

Linux and native Windows each run independent `backend` and `browser` jobs. Both jobs install the locked API runtime and build the frontend in separate runners. Backend jobs run the complete pytest suite, lock verification and dependency audits; browser jobs run service lifecycle/smoke checks and the complete Playwright suite. Linux retains its scheduled model-runtime audit. The stable `linux` and `windows` summary checks require **both** jobs to succeed: failure, cancellation, a skipped job or a missing result cannot pass. A backend failure does not prevent browser diagnostics.

The complete workflows run for `main`, `v*` tags, pull requests, and `ci/**` development branches. Start native Windows feedback with the first reviewable development change, before release preparation. When PR creation is unavailable, pushing a `ci/**` branch provides the same full checks without changing token permissions. Prefer an ordinary feature branch for PR-only validation to avoid duplicate branch/PR runs. All exact-SHA main and tag release gates remain required.

The optional **Run workflow → stability_check** input adds five executions of every `@timing` browser test after the full suite. Alternatively, push the candidate to a `ci/stability/<topic>` branch to run the same extra checks on both platforms. This also works with credentials that permit workflow-file pushes but not Actions dispatch or PR creation. These runs supplement full coverage; they never replace the main/tag gates. A failure in any repetition fails the job; automatic test retries remain disabled.

```bash
# Against a separately started, isolated mock service; never the normal deployment.
AUDIO_INTEL_TEST_BASE_URL=http://127.0.0.1:20961 corepack pnpm@10.15.1 --dir frontend test:e2e --grep '@timing' --repeat-each=5
# Optional remote stability run, after committing the candidate:
git push origin HEAD:ci/stability/my-change
```

Each job always uploads JUnit statistics as `<platform>-<job>-reports`; failed jobs additionally upload `<platform>-<job>-logs`, including available Playwright screenshots, traces and error contexts. Backend results are under `logs/ci/backend.xml`; browser results are `browser.xml`, with optional `stability.xml` alongside them. Stability traces use `logs/playwright-stability/` so they do not erase full-suite traces. Browser installation and test execution are separate steps. Compare job/step durations and test/skip counts; report queue wait separately. Parallel jobs add a small duplicate setup cost and require runner capacity, so measured wall-clock improvement is not a fixed guarantee.

### Deterministic asynchronous browser checks

Use the shared `responseGate` fixture for controlled delays: create a gate, `await gate.hold()` in the intended route handler, wait for `gate.arrived` and the pending UI, then `gate.release()` at the precise point required by the scenario. Shared teardown releases gates before route-draining hooks, including after an assertion fails. Match the request method, path, parameters/body as appropriate; an unchanged row count or closed dialog alone cannot establish completion of a new request.

Register response waits **before** the action, then require response completion and the resulting UI state. For response-order tests, hold the old response until the new result renders, release it, and assert the result remains correct. For polling, pause the browser clock before navigation, explicitly disable SSE in the fixture, advance an entire polling interval, and verify a poll actually completed before checking persistence. Keep real-time observation windows when elapsed time is the behavior under test (for example, the 5.2-second SSE idle assertion); explain such waits rather than replacing them with an immediate negative assertion.

Tag readiness, response-order and polling regressions with `@timing`, including desktop and 390 px cases where applicable. Keep console-error, overflow, original deadline and request-count assertions. SQLite snapshot readers must close their connections before deleting files; a committed library-import receipt precedes retired-file cleanup and release of the write gate. Keep the existing backup, cancellation and held-cleanup regressions in the full backend suite. Repetitions provide additional evidence; controlled timing and explicit resource ownership address the cause.

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

SQLite jobs, queue ordering, history, idempotency records, hotwords, voices, voiceprints, and completed-task snapshots are compatibility surfaces. Before migration development, follow [upgrade backup scope](docs/UPGRADE.md#upgrade-backup-scope): snapshot SQLite for database-only changes, include affected files for file rewrites or deletions, and copy the full data directory for broad or uncertain changes. Confirmed updates without data migration require no additional upgrade backup. Use the smallest necessary scope; schema changes alone never justify copying all media. After complete deployment acceptance, promptly remove the recorded temporary upgrade backups and failed copies and record cleanup; retain them while acceptance fails or rollback remains in progress. Daily and library-replacement backups have separate retention. Cover migration from the previous schema in tests.

The eight native asynchronous submission endpoints (ASR, video transcription, single-item TTS, ordered TTS sequences, clone-reference analysis, voiceprint sample upload, document import, and document TTS) require `Idempotency-Key` and must preserve first-accept `202`, same-request replay `200`, conflict `409`, and admission `429` semantics.

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

A request to publish authorizes diagnosis, fixes for clear release-blocking code/test/version/documentation defects, relevant regressions, commits/pushes, revalidation and completion of that release. Continue this work without asking for approval after each failure. Keep the chosen version until publication; a failed check alone does not consume a release number. Ask only for necessary missing credentials/information, an unresolved material product decision, or work beyond the authorized scope, after completing independent diagnosis/preparation. Read-only inspection or preparation alone does not authorize publication.

1. Synchronize remote tags with `git fetch origin --tags --prune` and identify the exact candidate SHA in a clean worktree. For a new attempt, confirm the version is unused locally/remotely; for a retry, identify the pending tag from the current attempt and record its actual object ID, peeled commit and publication history. Reconcile conflicting refs instead of force-fetching over them blindly.
2. Set `RELEASE_VERSION` in `audio_intel/version.py` and the version in `frontend/package.json` to the same `X.Y.Z` in the candidate. Require successful Linux and native Windows **main** workflows for that exact SHA before creating or updating the release tag.
3. Temporarily point the local target tag at that SHA, run tag-specific version tests and a production frontend build, and verify `/api/v1/health`, `/api/v1/system` and OpenAPI `info.version` report exactly `X.Y.Z`. Record any original local tag object and guarantee cleanup on success or failure: remove a newly created temporary tag, or restore the exact original object for a retry. Confirm cleanup, SHA and worktree; repeat the gate if source changed and reconcile unexpected ref changes without overwriting them.
4. Re-read main results, create/push the unused `vX.Y.Z` tag or update the current pending tag using [unpublished tag recovery](#unpublished-tag-recovery). Verify local/remote tags resolve to the candidate. Wait for successful Linux and native Windows **tag** workflows for that tag and SHA before publishing a GitHub Release. A main run alone is insufficient.
5. If validation fails, record the evidence, diagnose and fix clear defects, and continue under the same authorization/version. Each changed SHA must pass its own candidate and tag gates. Never publish while a required result is pending, cancelled, skipped or failed, and never weaken assertions or bypass checks to finish.
6. Publish and read back the Release, confirming the tag/SHA, draft/prerelease state and latest status. Include workflow links and upgrade/validation notes. State the starting version, data migration category (none / database only / includes files), affected file paths when applicable, and required backup scope; cover all intervening changes for upgrades that skip versions. Do not infer data compatibility from the schema number alone. Keep authentication process-scoped; never persist tokens in remotes, Git configuration or release artifacts.

GitHub publication and local deployment are separate completion results. For local development/release work, finish [local deployment acceptance](docs/UPGRADE.md#local-development-and-deployment) after the final source and tag changes, restoring the recorded service configuration and components. Do not treat an API version check as proof that the ASR/TTS supervisors and executors were refreshed. A remote-only inspection does not start or restart a local service; explicitly report a skipped, pending or failed local deployment instead of implying it succeeded.

Between releases, source checkouts append local SemVer build metadata derived from `git describe`. This lookup is offline; do not add a runtime GitHub request to resolve the version. README release badges should remain dynamic rather than hard-coding a release number.

### Unpublished tag recovery

A tag that has appeared in a published GitHub Release is immutable, including prerelease tags. A draft is not published. Absence of a Release alone does not make a historical tag reusable: recovery applies only to a never-published pending tag known to belong to the current authorized attempt. Previously retained failed tags, including `v0.1.27` and `v0.1.28`, keep their original references; this policy does not reclaim historical version gaps.

Preserve the old tag object ID and peeled commit, failed run URLs, diagnosis and replacement SHA in the maintenance record. After the corrected SHA passes both main workflows and local tag/version validation, immediately recheck the tag's publication state with authenticated reads. An API permission/network error is not proof that no Release exists.

Replace only the intended remote tag using an explicit lease on its recorded **object ID**, which may differ from the peeled commit for an annotated tag. With `release_tag`, `previous_tag_oid` and `candidate_sha` set from those verified values, use:

```bash
git push --force-with-lease="refs/tags/${release_tag}:${previous_tag_oid}" origin "${candidate_sha}:refs/tags/${release_tag}"
```

Use normal Git authentication; native PowerShell supports the same arguments with its corresponding variables. Never use unconditional `--force` or delete/recreate the remote tag. If the lease fails, inspect the concurrent reference change and release state before further action; do not overwrite it blindly. Reconcile the local tag with the successful remote update, verify both resolve to the candidate, and require fresh tag workflows for that exact tag/SHA. Keep the version unchanged and record each failed/corrected attempt; old CI successes cannot validate the replacement. Missing permissions, conflicting publication history or an unreconciled concurrent change require reporting the specific blocker rather than repeated rejected mutations.

### CI failure handling

Record the workflow URL, commit SHA, failing step and relevant error or test before deciding how to recover. Distinguish application/platform defects, test timing assumptions, missing checkout content and runner/network failures. A run cancelled by a newer commit is superseded, not evidence that the new candidate failed; it also cannot satisfy a success gate.

For code or test defects, reproduce the failure where practical, correct the cause and run focused regression checks before submitting the corrected candidate to the full workflows. During an authorized release, do this directly and continue publication under the same version. Rerun the same SHA when evidence points to a transient infrastructure failure and permissions allow it; repeated runs alone do not resolve a known defect. Preserve assertions and required checks instead of skipping failures or increasing timeouts without a diagnosed need. Any changed SHA must pass its own release gates. Recover the current pending tag using the rules above; published tags remain immutable.

### Transcription CI lessons

| Failure and evidence | Diagnosis and regression |
| --- | --- |
| [Linux original/enhanced download cleanup timeout](https://github.com/wlf186/audio-intel/actions/runs/39005964233) at `f0516df52cbfc21d9d5added74247c25de5d40c6` | Desktop/390 px traces completed the UI and byte assertions in about four seconds, then timed out in fixture teardown. Chromium retained media/preconnect sockets, so `server.close()` waited indefinitely. A deterministic retained-TCP-socket experiment reproduced the wait. Call `server.closeAllConnections()` after registering the close callback, and label cleanup as a test step. Both cases passed five local repetitions; [Linux](https://github.com/wlf186/audio-intel/actions/runs/39006688504) and [Windows](https://github.com/wlf186/audio-intel/actions/runs/39006688443) full suites passed at corrective SHA `cd3766b2da092150be8d57482cf8c19888074543`. Preserve listening-state, download-byte, console/overflow assertions and the original deadlines. |
| [Windows rename-to-clone readiness failure](https://github.com/wlf186/audio-intel/actions/runs/38224341304) at `525f3357f97b356e9ce6101ae64dc242e9f75362` | The sample-name fixture had changed and the rename dialog had closed, but the people/group refresh was still pending. Clicking TTS triggered the existing busy navigation protection, so the TTS settings never mounted. Hold the people refresh deliberately at desktop/390 px, require navigation to remain blocked during the operation, then release it, require the renamed control to be enabled and the TTS URL to load. Preserve conflict, clone-name, console/overflow assertions and the original deadlines. |
| [Linux completed-import cleanup timing failure](https://github.com/wlf186/audio-intel/actions/runs/38126368523) at `1ab560ccc8dcc3fab946665a9a31a108c0cb3dc8` | A durable `completed` receipt is committed before retired-file cleanup, while the application still protects writes with `library_maintenance` 409. Wait for the manager's write gate to clear before the next test mutation. A held-cleanup regression requires the committed receipt and retained old file during 409, followed by cleaned old audio, usable replacement audio, staging DELETE 204 and a downloadable backup. Keep crash recovery ordering, busy protections and existing deadlines. |
| [Windows Swagger readiness failure](https://github.com/wlf186/audio-intel/actions/runs/38195192034) at `c311884918aeb437b2ec35c931e30f0b8086e49a` | The Quick start assertion expired before rendering, while the failure screenshot and DOM snapshot already contained the heading. The trace shows the real OpenAPI response took about 1.9 seconds; the Swagger root alone did not establish schema/render readiness. Hold that request until the loading root is visible, then release it, require a successful completed schema response and the existing rendered-content, offline-network, desktop/mobile and executable-health assertions. Repeat five times without changing the 30-second test or 5-second content deadline. Production documentation is unchanged. |
| Local divider keyboard focus regression during workspace validation | Home observed the previous 60% width (820.8 px), rather than the 320 px minimum. Pending authentication/navigation focus could redirect keys after the divider received focus. Preserve already focused business controls when deferred focus runs. A paused animation clock keeps the callbacks pending until the divider is focused, then advances them and requires focus to stay on the divider and Home to reach 320 px. Keep the existing keyboard, cancellation, resize and original-deadline assertions. |
| [Workspace Windows stability failure](https://github.com/wlf186/audio-intel/actions/runs/38194071864) at `7b791be2e70baffa09db9feeb8e726dac5369352` | Full browser coverage passed, but one of five repetitions retained 120 rows after clearing transcript search, where 40 were required. Reader cleanup could save the new filter with the old loaded count before an effect read that bookmark. Reset the filter bookmark and count together in the input action, suspend capture while deferred search is pending, and ignore observer callbacks from previous filters. Desktop/390 px checks preserve deep anchors across reload and then require search/clear to reset to 40 rows at the top. Keep the original deadline and five repetitions; this is an ordering defect exposed on Windows, not evidence that Windows alone is affected. |
| [Windows detail-loading timing failure](https://github.com/wlf186/audio-intel/actions/runs/38125151250) at `ce8ff70d839eae83e047706c4b6a30a1607199dc` | A fixture returned 503 after 250 ms, so the transient loading state could disappear before the browser assertion observed it. Hold the first detail response until the loading heading is visible, then return 503 and require retry success. Run at desktop/390 px while preserving single detail caching, the 5.2-second SSE idle check, console assertions and existing deadlines; production loading behavior is unchanged. |
| [Windows history-filter timing failure](https://github.com/wlf186/audio-intel/actions/runs/38123101501) at `e3f7ed40570232c2b92263ea07a565765953d7bc` | Selecting TTS kept the existing one-row search result visible. The row-count assertion completed 53 ms before the filtered request began, so it could not establish server filtering. Register a wait for the exact kind/query response before clicking, require response completion and `aria-busy=false`, and retain the row and request assertions. Controlled held-response checks run at desktop/390 px; deadlines and production filtering remain unchanged. |
| [Windows backup/cancellation checks](https://github.com/wlf186/audio-intel/actions/runs/38121337253) | SQLite connection context managers commit/roll back without closing handles. Close test snapshot readers before deletion; Windows refuses unlinking an open DB. Enqueue the next job before timing cancellation so unrelated submission I/O is excluded, while retaining the 3-second bound, process-exit, temporary-file cleanup and queue-continuation assertions. |
| [Linux video preview failure](https://github.com/wlf186/audio-intel/actions/runs/38012743915), fixed by `1199cbc` | A browser container extension is not a decoder guarantee. Generate WebM VP8/Opus browser fixtures while retaining backend MP4 coverage; wait for `videoWidth > 0` before crop interaction. Test unsupported-preview fallback and selecting another file. |
| [Windows CPU readiness failure](https://github.com/wlf186/audio-intel/actions/runs/38014197054), addressed by `699bfa0` | Navigation completion does not prove capabilities have loaded or responsive controls are expanded. A controlled delayed capabilities response reproduces the timing assumption. Register response waits before navigation, wait for completion and visible controls, and cover both normal and held responses at desktop/390 px. The original run lacked browser trace evidence, so distinguish the observed failure from the controlled reproduction. |
| Long busy-worker IDs overflowed the 390 px system grid, fixed by `699bfa0` | Idle workers hid the defect. Supply deterministic busy-worker IDs, scroll to the worker panel and assert document width. Allow grid children to shrink with `min-width: 0`; retain the long input in the regression fixture. |
| [v0.1.30 candidate Linux](https://github.com/wlf186/audio-intel/actions/runs/38110999035) and [Windows](https://github.com/wlf186/audio-intel/actions/runs/38110999036) at `39af1a13a8b9bc90ec1c9add0d06f3f32e7b953d` needed ASR settings scrolling at 1280×720 and 1366×768 | Both runners lacked a GPU, unlike the local machine. Duplicate ASR fallback notices and device hints consumed extra height. Show one accessible warning beside the device, retain a notice when mobile settings are folded, and place ASR/TTS warnings in available desktop grid cells. Layout fixtures now control both capabilities and system GPU state, covering available/unavailable GPUs at five desktop sizes plus mobile. Preserve the no-scroll, visibility, console and overflow assertions and original deadlines. |
| [v0.1.30 Windows reader-height failure](https://github.com/wlf186/audio-intel/actions/runs/38111884907) at `a5751082eb220520a9ff5c575c1f576e787e502c` observed 266.25 px while playback metadata was still loading | Loaded scenes do not establish audio readiness. The screenshot retained the temporary loading-status row. Hold the playback response deliberately, assert loading and visible scene text, then require audio `readyState >= 1` and removal of the loading status before measuring the steady reading surface. Keep the 280 px minimum, seeking/modal checks, console/overflow assertions and original deadline; the same controlled flow runs in both locales. |
| Browser artifacts previously escaped the CI upload directory, fixed by `699bfa0` | `frontend/playwright.config.ts` now resolves output to repository `logs/playwright/`; both workflows upload `logs/` on failure. Keep failure screenshots, traces and error contexts there, using `testInfo.outputPath()` for test evidence. |
| [Windows recycle-test teardown failure](https://github.com/wlf186/audio-intel/actions/runs/38022798294) at `f4bcf82` | The lifecycle assertions passed, but one bounded termination call returned a survivor during teardown. The helper explicitly returns remaining PIDs, and production shutdown drains them. Test teardown now follows that contract with a bounded drain and a strict no-survivors assertion; deterministic tests cover both eventual exit and persistent failure. The original log does not establish why that process took longer to exit. Queue/recycle deadlines and production process handling remain unchanged. |
| [v0.1.27 Windows browser failure](https://github.com/wlf186/audio-intel/actions/runs/38032730038) at `be2051b02cc34ec1ec37f433224b2e6bbe4d77b2` | After reload, the ASR speaker-notes fixture's capabilities `route.fetch` failed with `socket hang up`; the speaker filter assertion then failed. A local Linux reference-range test showed the same transport error, so Windows-specific failure and keep-alive expiry are not established causes. UI fixture handlers now deep-copy worker-scoped live capabilities/system snapshots, read through short-lived request contexts, rather than fetching again on every navigation. Retain live browser capability checks, delayed/error/retry coverage, and repeated desktop/mobile reload regressions; no whole-test retries or timeout increases. |
| Video enrollment passed focused checks but failed automatic loading in the local full suite | The fixture returned full request/results from the summary-only job list. A polling response after enrollment replaced the detail result, triggered another scene read and reset the reader before the scroll assertion. Return summaries from the list and full data only from job detail. A controlled 5-second poll verifies the wire shape, unchanged scene-read count and scroll position, then automatic loading at desktop/390 px. Production behavior and polling intervals are unchanged. |
| Repeated mobile segment checks observed 40 rows followed by 110, rather than a stable 80-row intermediate state | Near-bottom prefetch can advance through multiple batches before the assertion observes the DOM. Keep the initial 40-row check, require the next batch to appear, and check the complete ordered prefix for duplicates, omissions and the total upper bound. Do not require a transient intermediate render or change production prefetch behavior to satisfy the test. |
| [Windows cancellation-test timing failure](https://github.com/wlf186/audio-intel/actions/runs/38039371601) at `133593ebf8f734a549fc93c08cc908992563b59d` | The cancelled job reached its terminal state within the existing 3-second wait, but the elapsed-time assertion ran after the next task completed and counted replacement startup/inference too (6.703 seconds combined). Check cancellation latency, executor exit and temporary-file removal immediately after observing cancellation, then independently require the next queued job to succeed under the original deadline. Production cancellation and all deadlines are unchanged. |
| [Windows restore-dialog timing failure](https://github.com/wlf186/audio-intel/actions/runs/38041151889) on `v0.1.28` / `c87b92cf00499904c464cadfde65c92d06821af9` | The frame PATCH succeeded, but the test clicked restore while the scene GET was still pending; no restore dialog or restore PATCH followed. Dialog closure does not establish refreshed scene readiness. Wait for the updated frame to render before opening restore, and assert each save closes its dialog. A held scene-response regression checks loading/disabled restore and successful frame-save-to-restore flow at desktop/390 px. The real video API workflow uses independent worker media files, waits for server completion, and advances a paused browser clock deliberately; its capabilities fixture disables SSE while dedicated SSE/polling tests retain that coverage. Deadlines were preserved; this historical failed tag was retained without a Release under the previous policy. |

Record workflow URL, exact SHA, observed error, reproduction and correction together. Do not substitute a Linux emulation for native Windows feedback, or weaken assertions/increase timeouts to conceal an unobserved readiness dependency. Existing successful regression evidence: [v0.1.25 Linux](https://github.com/wlf186/audio-intel/actions/runs/38017263353), [v0.1.25 Windows](https://github.com/wlf186/audio-intel/actions/runs/38017263365). A failed tag check does not constitute a published Release; pending-tag recovery and published-tag immutability follow the release rules above.

From an authenticated GitHub CLI session, inspect a failing run and download its artifacts (replace the run ID with the run being diagnosed):

```bash
gh run view RUN_ID --repo wlf186/audio-intel --log-failed
gh run download RUN_ID --repo wlf186/audio-intel --name windows-browser-logs --dir tmp/ci-evidence
# Current artifacts: linux-browser-logs / windows-browser-logs. Older runs used linux-logs / windows-logs.
corepack pnpm@10.15.1 --dir frontend exec playwright show-trace ../tmp/ci-evidence/playwright/TEST_DIRECTORY/trace.zip
```

Use the actual downloaded test directory in the last command. Native Windows PowerShell accepts these same `gh` and `corepack` commands. Artifact retention may expire, so preserve a concise diagnosis and regression reference in the committed documentation rather than depending only on an external trace.

中文：浏览器媒体测试同时验证编码支持与解码就绪；能力接口需等待响应及控件展开；布局使用繁忙状态和长 ID 夹具；失败截图、trace、错误上下文统一进入 `logs/playwright/`。记录事实、受控复现及推断的边界，保留原生 Windows 验证。一次发布授权覆盖明确问题的修复和继续发布；当前未发布标签可按旧引用校验更新，已发布标签不可变。

### Browser fixture snapshots and GitHub permissions

Import `test` and the snapshot helpers from `frontend/e2e/fixtures.ts` when building capabilities or system UI fixtures. Snapshots are lazy, worker-scoped reads of the running test service; every handler gets a deep copy, including nested models and devices. An unsuccessful snapshot read fails the test instead of supplying fabricated data. Keep real API/browser contract tests unmocked, and continue using the real endpoints for integration workflows. `capabilities-readiness.spec.ts` covers live responses plus controlled loading, HTTP errors, disconnects and manual recovery at desktop and 390 px.

Fine-grained GitHub token permissions are independent: `Workflows` permits workflow-file changes, while re-running failed jobs through the API requires repository `Actions: write` ([GitHub endpoint requirements](https://docs.github.com/en/rest/actions/workflow-runs#re-run-failed-jobs-from-a-workflow-run)). `Administration` does not substitute for that permission. A rerun returning `403 Resource not accessible by personal access token` is a publication-tool authorization issue, not evidence of a test defect. Normal authorized pushes still trigger the configured workflows; do not change workflow `GITHUB_TOKEN` permissions to address a personal-token permission error.

中文：构造 capabilities/system 页面夹具时使用共享快照，每个 worker 按需读取真实服务，每次响应独立深拷贝；读取失败直接报告，不返回虚构能力。真实接口验证仍走浏览器网络。GitHub 的 Workflows、Administration 与 Actions 权限独立，API 重跑需 Actions 写权限；推送触发新 CI 不依赖重跑权限。

## Pull requests

Use Conventional Commit subjects, for example:

```text
feat(jobs): persist GPU device names
fix(tts): preserve draft text
docs(readme): add product overview and screenshots
```

Pull requests should explain behavior changes, list verification commands and results, call out database/API compatibility, and include before/after screenshots for UI work. Link relevant issues and keep unrelated formatting changes out of the patch.
