# Document TTS validation — 2026-09-11

Validation used isolated Linux instances (real inference on port 20816, mock on 20817), project-local pinned runtimes, and an NVIDIA RTX A1000 Laptop GPU with 4096 MiB. Existing data was backed up before the additive schema v11 migration. Test instances were stopped after validation.

Automated checks:

- `.runtime/api/bin/python -m pytest -q`: **241 passed, 6 skipped**. Includes migration compatibility, import parsing/failure/idempotency/auth, immutable voiceprint snapshots, replay after source-import deletion, exact text partitioning, restart/manual resume, complete executor-tree retirement before automatic resource retries, and original queue/API regressions.
- `corepack pnpm@10.15.1 --dir frontend typecheck` and `build`: passed.
- Playwright suite against the isolated mock service: **52 passed**. Document upload → preview → change split rules → select sections → synthesis → single-player playback → native ZIP download passed. Desktop and 390 px overflow checks, minimum 44 px document controls, and console-error checks passed. Browser plugin was not available; repository Playwright with Chromium was used. Evidence screenshots: `/tmp/document-desktop.png`, `/tmp/document-mobile.png` (local, not committed).
- `.runtime/api/bin/python scripts/lock_dependencies.py --check`: passed for Linux/Windows full and CPU locks.
- A 1,000,000-character mock synthesis exercised thousands of model chunks while limiting tracked Python allocations below 80 MiB; no long WAV or export copy was written. This allocation check excludes model weight memory and is not a claim about total process RSS.
- A sparse file larger than 4 GiB streamed through ZIP_STORED with ZIP64 records and bounded chunks, without storing a ZIP. Live HTTP tests held two slow streams, verified the third returned 429 and purge returned 409, then disconnected both and verified cleanup allowed purge.
- MP3 remux tests decoded ordered 330 Hz / 880 Hz sections and checked duration with the documented encoder-padding allowance. Batch MP3/ZIP headers and individual artifact Range responses passed.

Real inference:

| Configuration | Result |
| --- | --- |
| 0.6B preset, GPU, acceleration enabled | Two ordered section MP3 files, successful streaming exports |
| 0.6B preset, CPU, acceleration disabled | Passed |
| 1.7B preset with instruction, CPU | Passed |
| 1.7B VoiceDesign, CPU | Passed |
| 0.6B and 1.7B inline cloning, CPU | Passed |
| 0.6B document with multiple model chunks, GPU | 91.46 s audio; generation batch 2, sequential decoder batch 1 |
| 1.7B GPU request on 4 GiB hardware | Correct 503 admission rejection |

Real document cancellation reaped the old TTS executor, preserved a completed section byte-for-byte and with unchanged modification time, allowed the next job to finish, and successfully resumed the original document. Real ASR cancellation during GPU transcription reaped both the executor and its stage child before terminal cancellation; the next ASR job succeeded.

The existing real-model benchmark scripts also passed functional validation: `benchmark_tts_sequence.py` (two items, one repetition, no warmup) and `benchmark_single_task_acceleration.py tts --device gpu --repeat 1`. These short runs verify compatibility and batching, not a general performance claim.

Every supported supplied sample was fully parsed and its canonical text reconstructed exactly from preview slices:

| Sample | Canonical characters | Auto sections | Length sections (N=10,000) |
| --- | ---: | ---: | ---: |
| 孩子如何学习 EPUB | 141,175 | 15 | 14 |
| Gödel, Escher, Bach PDF | 1,967,442 | 80 | 201 |
| I Am a Strange Loop EPUB | 1,072,914 | 37 | 107 |
| deepseek_harness_cn PDF | 6,625 | 15 | 1 |
| Zen and the Art of Motorcycle Maintenance EPUB | 809,927 | 36 | 81 |
| bitcoin PDF | 21,908 | 3 | 3 |

Image-only EPUB chapter-title pages retain TOC anchors. A lone Markdown wrapper title falls back to length segmentation. PDF extraction preserves page boundaries and uses conservative heading recognition; its reading order and header/footer quality remain source-dependent, so preview and length mode are available. Scanned PDFs require external OCR.

Native Windows and 1.7B GPU inference were not run on this host. The six pytest skips include unavailable platform-specific coverage. Runtime pins and Windows dependency locks remain intact.

## Office format extension — 2026-09-11

The supplied `~/share/books/office-test` files were parsed through the native HTTP API, previewed in the browser, and synthesized in full with the mock worker on isolated port 20819. Every preview reconstructed its canonical text exactly. Both ZIP and complete MP3 downloads were decoded, and ZIP artifact counts matched the selected sections.

| Sample format | Canonical characters | Automatic sections | Checked details |
| --- | ---: | ---: | --- |
| Markdown | 12,921 | 6 | Exported citation controls removed; ordinary reference text retained |
| DOCX | 13,169 | 15 | Automatic TOC excluded, custom outlines and 8 body tables retained, prose heading styles warned |
| XLSX | 8,330 | 10 | Workbook order, cover key/value rows, six cached formulas, percentage/price formatting; annotations warned |
| PDF | 8,372 | 7 | Repeated numbered-bar headings grouped; page 2 has no extractable text |
| PPTX | 8,411 | 12 | Original 13-page order, image-only page 2 warned, slide notes excluded |

Real GPU inference used an isolated authenticated service on port 20820 with the pinned Qwen3-TTS 0.6B preset model and Vivian voice. A complete short natural section from each new format was submitted through the document API; both download modes decoded successfully:

| Format | Selected characters | Decoded section audio |
| --- | ---: | ---: |
| DOCX | 41 | 9.84 s |
| XLSX | 435 | 123.30 s |
| PPTX | 316 | 72.58 s |

These are real-inference integration checks, not a claim that every word or numeric pronunciation in the full reports was manually assessed. Full-document runs used mock inference.

The Office test module covers custom/inherited Word outline styles, TOC fields, inserted/deleted text, merged cells, more than 50,000 characters, sparse Excel row 1,048,576, declared and styled headers, hidden content, cached/missing/empty-string formula results, leading zeroes, percentages, 1904 dates, durations, unsupported format warnings, PowerPoint geometry/group ordering, notes, hidden/empty pages, archive validation, parser failure cleanup, v1 preview identity, Bearer authentication, idempotency, native job submission and streaming exports. The three executable CLI Office preview examples passed.

Browser verification used repository Playwright/Chromium because the Browser plugin was unavailable: `/#tts` → upload each sample → inspect warnings and text → check desktop and 390 px mobile layout. All six document browser tests passed, including synthesis/playback/download for the existing document flow; title/route, nonblank UI, absence of a Vite overlay, console errors, horizontal overflow and 44 px document buttons were checked. Screenshots remain local at `/tmp/office-{md,pdf,docx,xlsx,pptx}-{desktop,mobile}.png`.

API-only Office dependencies and their transitive dependencies are pinned in Linux/Windows locks. Dependency-lock verification, frontend typecheck/build and API contracts passed. No database migration or historical snapshot rewrite was required. Native Windows execution and OCR were not tested or added in this extension.

Final verification: `.runtime/api/bin/python -m pytest -q` **263 passed, 6 skipped**; the focused document/API contract run passed **41 tests**, and the final document Playwright run passed **6 tests**. The earlier large PDF samples were also re-extracted without changing their section counts: Gödel, Escher, Bach 1,967,442 characters / 80 sections; bitcoin 21,908 / 3; deepseek_harness_cn 6,625 / 15. Their preview slices reconstructed the complete canonical text.

The normal local API on port 20810 was restarted after confirming no active jobs/imports; live capabilities and OpenAPI now advertise DOCX, XLSX and PPTX. The isolated test services were stopped after validation.

## v0.1.11 review corrections — 2026-09-11

The nine review findings are covered by scoped form parsing, pre-body upload admission and direct streamed persistence, versioned empty-selection drafts, operation-specific retries, typed stable section responses, native downloads with in-app errors, lossless blank-section merging, retained-import management, and synchronized public documentation.

Local validation on the release implementation:

- Complete backend suite: **276 passed, 6 skipped** (the skips are platform-specific). Includes v10→v11 historical-job/queue/idempotency preservation, 1,000/1,001/2,000 section submissions in both form encodings, pre-body auth/admission, cumulative disk reservations, chunked uploads and disconnect cleanup, parser retry, deletion/snapshot exclusion, stable section DTOs, and blank PDF bookmarks.
- Complete browser suite: **54 passed, 5 skipped** (optional external samples). A separate document run with the supplied Office directory passed **8/8**, including all five sample formats, explicit empty selections across refresh, language changes, same-key upload retry, parser retry, retained-import reuse/deletion, and download errors without navigation. Desktop and 390 px mobile controls, console/page errors, screenshots and interaction assertions were checked using repository Playwright because the Browser plugin was unavailable.
- All **12** supported document samples under `~/share/books` parsed successfully. Both segmentation modes reconstructed every canonical text exactly, with no blank-only output sections. The largest PDF contained **1,967,442 characters**, producing 80 structural or 201 length sections.
- A **500 MiB** synthetic streamed ZIP remained below **16 MiB** of traced Python allocation and created no export file. Existing ZIP/MP3 decode, concurrency, lease and purge checks passed.
- Real pinned 0.6B GPU/Vivian synthesis succeeded for DOCX (41 characters), XLSX (435) and PPTX (316); complete MP3 streams decoded successfully. A two-section real job was cancelled after its first checkpoint, retried using the original persisted request, and completed with the first section's SHA-256 unchanged. Full-book text validation does not imply full-book real synthesis or word-by-word listening review.
- Frontend typecheck/production build, dependency-lock verification, mock ASR/TTS smoke, frontend dependency audit and Linux API dependency audit passed. Native Windows execution is gated by the candidate and tag workflows before publication.

Tests used isolated data/service instances on ports 20811 and 20812. The normal data directory was backed up with SQLite's backup API before migration regression work. Test media, source samples, screenshots and runtime data are local artifacts and are not included in the release.

Candidate CI exposed two additional platform differences before tagging: Windows requires a writable descriptor for MP3 checkpoint `fsync`, and a document page on a GPU-less host must share the ordinary TTS page's visible CPU selection and explanation. These are corrected; explicit unavailable-GPU API requests still return 503. Chinese text fixtures now always specify UTF-8. The session-expiry browser check waits for the ready sample to render before advancing its virtual polling clock.

The follow-up document/Office backend run passed 48 tests, including an emulation of Windows' writable-descriptor requirement. The focused device/session browser checks passed, and real GPU synthesis after the checkpoint fix succeeded. Additional checks completed full mock synthesis and decoded both download modes for all five Office samples, real inline-clone document synthesis, and real ASR GPU cancellation with complete process-tree exit followed by another successful task under the same supervisor.
