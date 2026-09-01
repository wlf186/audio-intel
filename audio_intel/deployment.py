from __future__ import annotations

from pathlib import Path
from typing import Literal


DeploymentProfile = Literal["full", "cpu"]
DEFAULT_DEPLOYMENT_PROFILE: DeploymentProfile = "full"
PROFILE_FILENAME = "deployment-profile"


def deployment_profile_path(root: Path) -> Path:
    return root / ".runtime" / PROFILE_FILENAME


def read_deployment_profile(root: Path) -> DeploymentProfile:
    path = deployment_profile_path(root)
    if not path.is_file():
        return DEFAULT_DEPLOYMENT_PROFILE
    value = path.read_text(encoding="utf-8").strip().lower()
    if value not in {"full", "cpu"}:
        raise RuntimeError(
            f"Invalid deployment profile in {path}: expected full or cpu"
        )
    return value  # type: ignore[return-value]


def default_compute_device(profile: DeploymentProfile) -> Literal["cpu", "gpu"]:
    return "cpu" if profile == "cpu" else "gpu"


def deployment_metadata(profile: DeploymentProfile) -> dict[str, str | bool]:
    return {
        "profile": profile,
        "default_compute_device": default_compute_device(profile),
        "gpu_runtime_installed": profile == "full",
    }
