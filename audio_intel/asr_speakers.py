"""Task-local speaker identities and persistence of edited ASR results."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import unicodedata
from typing import Any, Iterator

from . import db


_registry_lock = threading.Lock()
_update_locks: dict[str, tuple[threading.Lock, int]] = {}


def speaker_identity(speaker: dict[str, Any]) -> tuple[str, str | None]:
    """Read active identity fields, with explicit handling of historical snapshots."""
    if speaker.get("name") is not None:
        name, note = str(speaker["name"]), speaker.get("note")
    else:
        match = speaker.get("voiceprint_match") or {}
        if speaker.get("label_source") == "voiceprint" and match.get("name"):
            name, note = str(match["name"]), match.get("note")
        else:
            name, note = str(speaker.get("label") or speaker["id"].replace("_", " ")), speaker.get("note")
    # Historical notes keep their length; only new submitted notes are limited to 20.
    clean_note = " ".join(unicodedata.normalize("NFKC", note).split()) if note is not None else ""
    return name, clean_note or None


def speaker_label(name: str, note: str | None) -> str:
    return f"{name}（{note}）" if note else name


def merge_speakers(
    result: dict[str, Any], *, preferred_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Merge named identities while retaining all segment and word boundaries."""
    merged = deepcopy(result)
    speakers: list[dict[str, Any]] = []
    identities: dict[tuple[str, str], dict[str, Any]] = {}
    mapping: dict[str, str] = {}
    for speaker in merged.get("speakers", []):
        name, note = speaker_identity(speaker)
        speaker.update(name=name, note=note)
        source = speaker.get("label_source")
        named = source in {"manual", "voiceprint"} or (
            source != "default" and not re.fullmatch(r"Speaker[ _]\d+", name, re.IGNORECASE)
        )
        key = (db.person_name_key(name), db.person_name_key(note or ""))
        canonical = identities.get(key) if named else None
        if canonical is None:
            canonical = speaker
            speakers.append(canonical)
            if named:
                identities[key] = canonical
        elif speaker["id"] == preferred_id:
            canonical.update(name=name, note=note, label_source=speaker.get("label_source"))
        elif source == "manual":
            canonical["label_source"] = "manual"
        mapping[speaker["id"]] = canonical["id"]
    for speaker in speakers:
        speaker["label"] = speaker_label(speaker["name"], speaker["note"])
    labels = {speaker["id"]: speaker["label"] for speaker in speakers}
    for segment in merged.get("segments", []):
        segment["speaker"] = mapping.get(segment["speaker"], segment["speaker"])
        if segment["speaker"] in labels:
            segment["speaker_label"] = labels[segment["speaker"]]
        for word in segment.get("words", []):
            if "speaker" in word:
                word["speaker"] = mapping.get(word["speaker"], word["speaker"])
    merged["speakers"] = speakers
    return merged, mapping


@contextmanager
def speaker_update_lock(job_id: str) -> Iterator[None]:
    """Serialize edits in the supported single API process without leaking locks."""
    with _registry_lock:
        lock, users = _update_locks.get(job_id, (threading.Lock(), 0))
        _update_locks[job_id] = lock, users + 1
    try:
        with lock:
            yield
    finally:
        with _registry_lock:
            _, users = _update_locks[job_id]
            if users == 1:
                del _update_locks[job_id]
            else:
                _update_locks[job_id] = lock, users - 1


def persist_speaker_result(
    job_id: str, result: dict[str, Any], formats: list[str], output: Path,
) -> dict[str, Any]:
    """Stage exports, then commit files and SQLite together; restore on failure."""
    from asr.pipeline import write_asr_exports

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".speaker-update-", dir=output.parent) as temporary:
        staging = Path(temporary) / "new"
        backup = Path(temporary) / "old"
        backup.mkdir()
        saved = write_asr_exports(job_id, deepcopy(result), formats, output_dir=staging)
        exports = saved["artifacts"]
        export_names = {artifact["name"] for artifact in exports}
        saved["artifacts"] = exports + [
            deepcopy(artifact) for artifact in result.get("artifacts", [])
            if artifact["name"] not in export_names
        ]
        for artifact in exports:
            name = artifact["name"]
            target = output / name
            if target.exists():
                shutil.copy2(target, backup / name)
            artifact["path"] = str(target)
        installed: list[str] = []
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                changed = connection.execute(
                    "UPDATE jobs SET result_json=?,updated_at=? WHERE id=? AND kind='asr' AND state='succeeded'",
                    (json.dumps(saved, ensure_ascii=False), db.utcnow(), job_id),
                )
                if changed.rowcount != 1:
                    raise ValueError("Only completed ASR speakers can be renamed")
                for artifact in exports:
                    name = artifact["name"]
                    os.replace(staging / name, output / name)
                    installed.append(name)
                connection.execute("COMMIT")
            except BaseException:
                try:
                    connection.execute("ROLLBACK")
                finally:
                    for name in reversed(installed):
                        if (backup / name).exists():
                            os.replace(backup / name, output / name)
                        else:
                            (output / name).unlink(missing_ok=True)
                raise
    return saved
