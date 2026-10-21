"""Download names only; persistent transcript artifacts keep their original names."""
from pathlib import PurePosixPath

from .utils import safe_filename
from .video_uploads import AUDIO_FORMATS, VIDEO_FORMATS


def transcript_filename(display_name: str, job_id: str, format: str) -> str:
    name = PurePosixPath(display_name.replace("\\", "/")).name
    suffix = PurePosixPath(name).suffix
    if suffix.lower().lstrip(".") in (*AUDIO_FORMATS, *VIDEO_FORMATS):
        name = name[:-len(suffix)]
    return safe_filename(name, f"transcript-{job_id}") + "." + format
