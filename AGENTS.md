# Repository Guidelines

## Project Structure & Module Organization

`audio_intel/` contains the FastAPI gateway, SQLite job queue, workers, GPU coordination, and cleanup logic. ASR and TTS pipelines live in `asr/` and `tts/`. The React 19/Vite UI is under `frontend/src/`, browser tests are in `frontend/e2e/`, and backend tests are in `tests/`. Operational scripts belong in `scripts/`; `service.sh` is the supported service entrypoint.

Treat `models/`, `data/`, `cache/`, `tmp/`, `logs/`, `run/`, and `.runtime/` as local runtime state. Never commit model weights, generated audio, databases, PID files, or credentials.

## Build, Test, and Development Commands

- `./service.sh setup all` installs project-local runtimes and downloads required models.
- `./service.sh start all` starts the API, ASR worker, and TTS worker on port 20810. Use `status`, `logs all`, and `stop all` for operations.
- On native Windows 11, use `service.cmd` with the same actions and targets; see `docs/WINDOWS.md`.
- `.runtime/api/bin/python -m pytest -q` runs backend tests.
- `corepack pnpm@10.15.1 --dir frontend typecheck` checks TypeScript.
- `corepack pnpm@10.15.1 --dir frontend build` creates the production UI in `frontend/dist/`.
- `corepack pnpm@10.15.1 --dir frontend test:e2e` runs Playwright against the local service.
- `AUDIO_INTEL_MOCK_MODE=1 ./service.sh start all` enables fast pipeline smoke testing without real inference.

## Coding Style & Naming Conventions

Use four-space indentation, type hints, `snake_case` functions, and `PascalCase` classes in Python. In React, use `PascalCase` components, `camelCase` hooks/helpers, and explicit TypeScript types for API data. Keep API fields in `snake_case` to match Python responses. Prefer small, focused changes and preserve the surrounding no-semicolon frontend style. No repository-wide formatter is configured; avoid unrelated formatting churn.

## Testing Guidelines

Name backend tests `test_*.py` and Playwright files `*.spec.ts`. Cover queue state transitions, filesystem cleanup, API error responses, and migration compatibility with pytest/FastAPI `TestClient`. UI changes require an interaction assertion, console-error check, and desktop plus 390 px mobile overflow validation. Use mocks for routine tests; run real-model inference only when changing model loading, precision, device routing, or audio pipelines.

## Commit & Pull Request Guidelines

Use Conventional Commit subjects such as `feat(jobs): persist GPU device names` or `fix(tts): preserve draft text`. Pull requests should explain behavior changes, list verification commands, call out database/API compatibility, and include before/after screenshots for UI work. Link relevant issues and never include local artifacts or secrets.

## Security & Configuration

Keep the service on trusted networks unless `AUDIO_INTEL_API_KEY` and external TLS are configured. Preserve offline model loading and project-local cache paths; do not add silent cloud fallbacks.
