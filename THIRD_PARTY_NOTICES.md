# Third-Party Components and Model Notices

Sandevistan-Audio source code is licensed under Apache-2.0. That license applies only to this repository's original code and documentation. Model weights are downloaded at setup time and are not distributed in this Git repository.

| Component | Upstream | License / terms |
|---|---|---|
| Qwen3-ASR-0.6B | <https://huggingface.co/Qwen/Qwen3-ASR-0.6B> | Apache-2.0 |
| Qwen3-ForcedAligner-0.6B | <https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B> | Apache-2.0 |
| Qwen3-TTS Base / CustomVoice | <https://github.com/QwenLM/Qwen3-TTS> | Apache-2.0 |
| FSMN-VAD | <https://modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch> | Apache-2.0 model card |
| CAM++ | <https://www.modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common> | Apache-2.0 model card |
| FunASR toolkit | <https://github.com/modelscope/FunASR> | MIT; model weights have separate terms |
| ModelScope SDK | <https://github.com/modelscope/modelscope> | Apache-2.0 |
| PyTorch / torchaudio | <https://github.com/pytorch/pytorch> | BSD-style license |
| qwen-asr / qwen-tts Python packages | <https://pypi.org/project/qwen-asr/>, <https://pypi.org/project/qwen-tts/> | Apache-2.0 |
| Transformers / Hugging Face Hub | <https://github.com/huggingface/transformers>, <https://github.com/huggingface/huggingface_hub> | Apache-2.0 |
| FastAPI / Starlette / Uvicorn | <https://fastapi.tiangolo.com/>, <https://www.starlette.io/>, <https://www.uvicorn.org/> | MIT / BSD-3-Clause |
| React / Vite | <https://react.dev/>, <https://vite.dev/> | MIT |

Users must review the current upstream model cards and licenses before use or redistribution. Upstream terms control if this notice and an upstream source disagree.

This table identifies the principal direct runtime components; it is not a complete transitive software bill of materials. The exact resolved Python dependency inventory is recorded in `requirements-lock/`, and the exact frontend inventory and integrity hashes are recorded in `frontend/pnpm-lock.yaml`. See `docs/DEPENDENCIES.md` for the maintenance and security-audit policy.

Voice cloning must only be used with audio and identities for which the operator has permission. Operators are responsible for consent, disclosure, generated-audio use, and compliance with applicable law.

The interface uses an original cyberpunk-inspired visual treatment. References to games, fictional organizations, product names, or trademarks belong to their respective owners. This project is unofficial and is not affiliated with or endorsed by those owners.
