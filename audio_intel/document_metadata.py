"""Immutable document album names and MP3 metadata (no model dependencies)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def voice_label(request: dict[str, Any]) -> str:
    mode = request.get("voice_mode")
    value = {
        "preset": request.get("speaker"),
        "voiceprint": request.get("voiceprint_person_name"),
        "profile": request.get("voice_profile_name"),
        "inline_clone": "参考音频克隆",
        "voice_design": "声音设计",
    }.get(mode)
    return " ".join(str(value or "克隆音色").split())


def snapshot(db: Any, request: dict[str, Any], created_at: str) -> dict[str, Any]:
    """Allocate inside the caller's write transaction, after idempotency replay."""
    title = " ".join(str(request["document"]["title"]).split())
    voice = voice_label(request)
    stamp = datetime.fromisoformat(created_at).astimezone(timezone.utc).strftime("%y%m%d%H%M")
    base = f"{title} · {voice} · {stamp}"
    album = base
    number = 1
    while db.execute(
        "SELECT 1 FROM jobs WHERE kind='tts' AND "
        "json_extract(request_json,'$.document.audio_metadata.album')=? LIMIT 1", (album,),
    ).fetchone():
        number += 1
        album = f"{base} · {number}"
    return {"version": 1, "album": album, "artist": voice, "album_artist": voice}


def tags(request: dict[str, Any], title: str, index: int | None = None,
         total: int | None = None) -> dict[str, str]:
    metadata = request.get("document", {}).get("audio_metadata")
    if not metadata:
        return {}
    if metadata.get("version") != 1:
        raise ValueError("Unsupported document audio metadata version")
    result = {key: metadata[key] for key in ("album", "artist", "album_artist")}
    result["title"] = title
    if index is not None:
        result["track"] = f"{index}/{total}"
    return result
