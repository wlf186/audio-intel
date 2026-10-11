"""Persistent transfer receipts, library snapshots and deterministic merge plans."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import db as database
from .hotwords import MAX_HOTWORD_LISTS, normalize_hotword_terms, hotword_name_key
from .library_models import LibraryArchive


def migrate(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS library_imports (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL,
            key_hash TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            mode TEXT, preview_json TEXT, result_json TEXT, error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        UPDATE schema_meta SET version=13 WHERE version<13;
    """)


def root(config: Any, identifier: str) -> Path:
    if not identifier or any(c not in '0123456789abcdef' for c in identifier) or len(identifier) != 32:
        raise HTTPException(404, "Library import not found")
    return config.data_dir / "library_imports" / identifier


def get(identifier: str) -> dict[str, Any]:
    with database.connect() as db:
        item = db.execute("SELECT * FROM library_imports WHERE id=?", (identifier,)).fetchone()
    if item is None:
        raise HTTPException(404, "Library import not found")
    result = dict(item)
    for key in ("preview", "result"):
        result[key] = json.loads(result.pop(key + "_json") or "null")
    return result


def update(identifier: str, **values: Any) -> None:
    changes = {k: v for k, v in values.items() if k in {"state", "mode", "preview_json", "result_json", "error"}}
    changes["updated_at"] = database.utcnow()
    with database.connect() as db:
        db.execute("UPDATE library_imports SET " + ",".join(f"{k}=?" for k in changes) + " WHERE id=?", (*changes.values(), identifier))


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(256 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def snapshot(db: sqlite3.Connection) -> dict[str, Any]:
    samples: dict[str, list[dict[str, Any]]] = {}
    for row in db.execute("SELECT * FROM voiceprint_samples ORDER BY id"):
        sample = database._decode_voiceprint_sample(row)
        samples.setdefault(sample["person_id"], []).append(sample)
    aliases: dict[str, list[str]] = {}
    for row in db.execute("SELECT alias_id,person_id FROM voiceprint_aliases ORDER BY alias_id"):
        aliases.setdefault(row["person_id"], []).append(row["alias_id"])
    people = [dict(row) for row in db.execute("SELECT * FROM voiceprint_people ORDER BY id")]
    for person in people:
        person["samples"] = samples.get(person["id"], [])
        person["aliases"] = aliases.get(person["id"], [])
    lists = []
    for row in db.execute("SELECT * FROM asr_hotword_lists WHERE kind='custom' ORDER BY id"):
        item = dict(row)
        item["terms"] = json.loads(item.pop("terms_json"))
        lists.append(item)
    return {"people": people, "hotword_lists": lists}


def counts(data: dict[str, Any]) -> dict[str, int]:
    return {"people": len(data["people"]), "samples": sum(len(p["samples"]) for p in data["people"]), "hotword_lists": len(data["hotword_lists"])}


def revision(data: dict[str, Any]) -> str:
    # Feature-cache writes and their timestamps must not invalidate a preview.
    people = []
    for person in data["people"]:
        people.append({**{k: person.get(k) for k in ("id", "name", "note", "include_in_hotword_library", "aliases")},
                       "samples": [{k: s.get(k) for k in ("id", "name", "state", "language", "audio_path", "transcript", "words", "duration", "error_message")} for s in person["samples"]]})
    return digest({"people": people, "hotword_lists": [{k: item[k] for k in ("id", "name", "terms")} for item in data["hotword_lists"]]})


def require_idle(db: sqlite3.Connection) -> None:
    if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
        raise HTTPException(409, "Wait for unfinished ASR/TTS jobs before replacing the libraries / 请等待 ASR/TTS 任务完成后替换资料")


def require_complete(db: sqlite3.Connection) -> None:
    pending = [row[0] for row in db.execute("SELECT name FROM voiceprint_samples WHERE state='pending'")]
    imports = db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') AND json_extract(request_json,'$.purpose')='voiceprint_import' LIMIT 1").fetchone()
    if pending or imports:
        raise HTTPException(409, "Wait for voiceprint enrollment to finish / 请等待声纹入库完成: " + ", ".join(pending[:20]))


def audio_path(config: Any, value: str) -> Path:
    path = Path(value).resolve()
    if not any(parent.resolve() in path.parents for parent in (config.voiceprints_dir, config.voices_dir)):
        raise HTTPException(409, "Voiceprint audio is outside the library")
    return path


def load_archive(config: Any, identifier: str) -> LibraryArchive:
    return LibraryArchive.model_validate_json((root(config, identifier) / "validated.json").read_text(encoding="utf-8"))


def build_plan(archive: LibraryArchive, current: dict[str, Any], mode: str) -> dict[str, Any]:
    source = archive.model_dump(mode="json")
    destination = current if mode == "merge" else {"people": [], "hotword_lists": []}
    people = {(database.person_name_key(p["name"]), database.person_name_key(p.get("note") or "")): p for p in destination["people"]}
    aliases = {alias: p["id"] for p in destination["people"] for alias in p["aliases"]}
    used_people = {p["id"] for p in destination["people"]} | set(aliases)
    used_samples = {s["id"] for p in destination["people"] for s in p["samples"]}
    used_lists = {item["id"] for item in destination["hotword_lists"]} | {"hotwords_voiceprint_people", "hotwords_voiceprint_people_short"}
    new_people, new_samples, hotwords, new_aliases = [], [], [], []
    mappings: dict[str, dict[str, str]] = {"people": {}, "samples": {}, "hotword_lists": {}}
    stats = {"mode": mode, "preview_revision": digest([archive.model_dump(mode="json"), revision(current), mode]),
             "source": counts(source), "target": counts(current), "added": {"people": 0, "samples": 0, "hotword_lists": 0},
             "removed": counts(current) if mode == "replace" else {"people": 0, "samples": 0, "hotword_lists": 0},
             "merged_people": 0, "merged_hotword_lists": 0, "skipped_samples": 0, "renamed_samples": 0, "warnings": []}

    def identifier(value: str, used: set[str], prefix: str) -> str:
        result = value if value not in used else prefix + uuid.uuid4().hex[:16]
        used.add(result)
        return result

    for person in source["people"]:
        identity = (database.person_name_key(person["name"]), database.person_name_key(person.get("note") or ""))
        existing = people.get(identity)
        person_id = existing["id"] if existing else identifier(person["id"], used_people, "voice_")
        mappings["people"][person["id"]] = person_id
        if existing:
            stats["merged_people"] += 1
        else:
            new_people.append({**person, "id": person_id})
        for alias in dict.fromkeys([person_id, *person["aliases"]]):
            if alias not in aliases:
                aliases[alias] = person_id
                new_aliases.append((alias, person_id))
            elif aliases[alias] != person_id:
                stats["warnings"].append(f"Alias {alias} already belongs to another person; retained the existing alias")
        samples = existing["samples"] if existing else []
        names = {database.person_name_key(s["name"]) for s in samples}
        hashes: dict[str, str] = {}
        failed: dict[str, str] = {}
        for sample in samples:
            if sample.get("audio_path") and Path(sample["audio_path"]).is_file():
                hashes.setdefault(file_digest(Path(sample["audio_path"])), sample["id"])
            elif sample["state"] == "failed":
                failed[digest({k: sample.get(k) for k in ("name", "state", "language", "transcript", "words", "duration", "error_message")})] = sample["id"]
        for sample in person["samples"]:
            fingerprint = sample["audio"]["sha256"] if sample["audio"] else digest({k: sample.get(k) for k in ("name", "state", "language", "transcript", "words", "duration", "error_message")})
            seen = hashes if sample["audio"] else failed
            if fingerprint in seen:
                mappings["samples"][sample["id"]] = seen[fingerprint]
                stats["skipped_samples"] += 1
                continue
            sample_id = identifier(sample["id"], used_samples, "sample_")
            mappings["samples"][sample["id"]] = sample_id
            name = sample["name"]
            suffix = 1
            while database.person_name_key(name) in names:
                suffix += 1
                tail = f" ({suffix})"
                name = sample["name"][:80 - len(tail)].rstrip() + tail
            if name != sample["name"]:
                stats["renamed_samples"] += 1
            names.add(database.person_name_key(name))
            new_samples.append({**sample, "id": sample_id, "person_id": person_id, "name": name})
    current_lists = {hotword_name_key(item["name"]): item for item in destination["hotword_lists"]}
    for item in source["hotword_lists"]:
        existing = current_lists.get(hotword_name_key(item["name"]))
        if existing:
            mappings["hotword_lists"][item["id"]] = existing["id"]
            terms = normalize_hotword_terms([*existing["terms"], *item["terms"]])
            if terms != existing["terms"]:
                hotwords.append({**existing, "terms": terms, "update": True})
            stats["merged_hotword_lists"] += 1
        else:
            new_id = identifier(item["id"], used_lists, "hotwords_")
            mappings["hotword_lists"][item["id"]] = new_id
            hotwords.append({**item, "id": new_id, "update": False})
    new_list_count = sum(not item["update"] for item in hotwords)
    if len(current_lists) + new_list_count > MAX_HOTWORD_LISTS:
        raise ValueError(f"Import exceeds the {MAX_HOTWORD_LISTS} custom hotword list limit")
    stats["added"] = {"people": len(new_people), "samples": len(new_samples), "hotword_lists": new_list_count}
    return {"preview": stats, "people": new_people, "samples": new_samples, "aliases": new_aliases, "hotwords": hotwords, "id_map": mappings}


def commit_plan(db: sqlite3.Connection, plan: dict[str, Any], mode: str) -> None:
    if mode == "replace":
        db.execute("DELETE FROM voiceprint_people")
        db.execute("DELETE FROM asr_hotword_lists WHERE kind='custom'")
    for person in plan["people"]:
        db.execute("""INSERT INTO voiceprint_people(id,name,name_key,note,note_key,include_in_hotword_library,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?)""", (person["id"], person["name"], database.person_name_key(person["name"]), person["note"], database.person_name_key(person["note"] or ""),
                                     int(person["include_in_hotword_library"]), person["created_at"], person["updated_at"]))
    db.executemany("INSERT INTO voiceprint_aliases(alias_id,person_id) VALUES(?,?)", plan["aliases"])
    for sample in plan["samples"]:
        db.execute("""INSERT INTO voiceprint_samples(id,person_id,name,name_key,state,language,audio_path,transcript,words_json,duration,
            embedding,embedding_model,error_message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (sample["id"], sample["person_id"], sample["name"], database.person_name_key(sample["name"]), sample["state"], sample["language"], sample.get("audio_path"),
                    sample["transcript"], json.dumps(sample["words"], ensure_ascii=False), sample["duration"], sample.get("embedding_bytes"),
                    "CAM++/campplus_cn_common" if sample.get("embedding_bytes") else None, sample["error_message"], sample["created_at"], sample["updated_at"]))
    for item in plan["hotwords"]:
        if item["update"]:
            db.execute("UPDATE asr_hotword_lists SET terms_json=?,updated_at=? WHERE id=?", (json.dumps(item["terms"], ensure_ascii=False), database.utcnow(), item["id"]))
        else:
            db.execute("""INSERT INTO asr_hotword_lists(id,name,name_key,terms_json,kind,created_at,updated_at) VALUES(?,?,?,?,'custom',?,?)""",
                       (item["id"], item["name"], hotword_name_key(item["name"]), json.dumps(item["terms"], ensure_ascii=False), item["created_at"], item["updated_at"]))
    database._sync_voiceprint_hotword_lists(db)
