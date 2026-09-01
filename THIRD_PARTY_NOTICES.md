# Third-Party Components and Model Notices

Original code and documentation in this repository are licensed under Apache-2.0. That license applies only to project-owned material. Model weights are downloaded at setup time and are not distributed in this Git repository.

| Component | Upstream | License / terms |
|---|---|---|
| Qwen3-ASR-0.6B | <https://huggingface.co/Qwen/Qwen3-ASR-0.6B> | Apache-2.0 |
| Qwen3-ASR-1.7B | <https://huggingface.co/Qwen/Qwen3-ASR-1.7B> | Apache-2.0 |
| Qwen3-ForcedAligner-0.6B | <https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B> | Apache-2.0 |
| Qwen3-TTS 12Hz 0.6B Base / CustomVoice | <https://huggingface.co/collections/Qwen/qwen3-tts> | Apache-2.0 |
| Qwen3-TTS 12Hz 1.7B Base / CustomVoice / VoiceDesign | <https://huggingface.co/collections/Qwen/qwen3-tts> | Apache-2.0 |
| FSMN-VAD | <https://modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch> | Apache-2.0 model card |
| CAM++ | <https://www.modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common> | Apache-2.0 model card |
| FunASR toolkit | <https://github.com/modelscope/FunASR> | MIT; model weights have separate terms |
| ModelScope SDK | <https://github.com/modelscope/modelscope> | Apache-2.0 |
| PyTorch / torchaudio | <https://github.com/pytorch/pytorch> | BSD-style license |
| qwen-asr / qwen-tts Python packages | <https://pypi.org/project/qwen-asr/>, <https://pypi.org/project/qwen-tts/> | Apache-2.0 |
| Transformers / Hugging Face Hub | <https://github.com/huggingface/transformers>, <https://github.com/huggingface/huggingface_hub> | Apache-2.0 |
| FastAPI / Starlette / Uvicorn | <https://fastapi.tiangolo.com/>, <https://www.starlette.io/>, <https://www.uvicorn.org/> | MIT / BSD-3-Clause |
| React / Vite | <https://react.dev/>, <https://vite.dev/> | MIT |
| Lucide React | <https://github.com/lucide-icons/lucide> | ISC |
| Swagger UI Dist | <https://github.com/swagger-api/swagger-ui> | Apache-2.0 |

Users must review the current upstream model cards and licenses before use or redistribution. Upstream terms control if this notice and an upstream source disagree.

This table identifies the principal direct runtime components; it is not a complete transitive software bill of materials. The exact resolved Python dependency inventory is recorded in `requirements-lock/`, and the exact frontend inventory and integrity hashes are recorded in `frontend/pnpm-lock.yaml`. See `docs/DEPENDENCIES.md` for the maintenance and security-audit policy.

## Distribution boundary

The project currently publishes source-only releases. It does not publish container images, prebuilt Python runtimes, model bundles, or compiled frontend distributions. Dependency lock files describe what the setup process resolves; they are not a statement that every locked package is redistributed by the project.

Any future container, offline installer, prebuilt runtime, appliance image, or other binary bundle requires an artifact-specific software bill of materials, applicable license texts and notices, and review of source-offer or other reciprocal-license obligations before release. Locally built Swagger UI assets are Apache-2.0 software and must retain the corresponding license and notices when redistributed.

## Notable reciprocal runtime dependencies

- `soxr 1.1.0` is published under LGPL-2.1-or-later and includes a patched copy of `libsoxr`. Redistribution in a bundled runtime must satisfy the applicable LGPL requirements.
- `soynlp 0.0.493` has inconsistent published license evidence: its upstream license file states LGPL-3.0, while the PyPI/wheel metadata classifies the release as GPL-3.0. Treat the stricter published metadata as unresolved and do not redistribute this package in a prebuilt runtime until the discrepancy and resulting obligations have been reviewed.

These notes flag known items for release review; they are not a complete legal inventory or SBOM.

Voice cloning must only be used with audio and identities for which the operator has permission. Operators are responsible for consent, disclosure, generated-audio use, and compliance with applicable law.

This is responsible-use guidance, not an additional restriction on Apache-licensed code.

The project is independent and unofficial. See [BRAND_NOTICE.md](BRAND_NOTICE.md) for its status, third-party-rights statement, and the boundary between the project name and the Apache-2.0 grant.
