"""Bounded portable ZIPs and recoverable file/database library application."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import zipfile
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from fastapi import HTTPException
from fastapi.responses import FileResponse

from . import __version__, db as database, library_store as store
from .document_download import Sink, reader_lease, deletion_guard
from .library_models import ArchiveEmbedding, LibraryArchive
from .model_registry import model_manifest, model_installation

MANIFEST_LIMIT = 16 * 1024**2
ENTRY_LIMIT = 100_000
BLOCK = 256 * 1024


class VoiceprintFileResponse(FileResponse):
    """Keep the deletion lease for the entire Range/full response, including disconnects."""

    def __init__(self, path: Path, sample_id: str):
        super().__init__(path, media_type="audio/wav")
        self.sample_id = sample_id

    async def __call__(self, scope, receive, send):
        with reader_lease("voiceprint:" + self.sample_id):
            await super().__call__(scope, receive, send)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, allow_nan=False)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def current_snapshot() -> dict[str, Any]:
    with database.connect() as db:
        db.execute("BEGIN")
        return store.snapshot(db)


def cam_revision() -> str:
    return str(next(model for model in model_manifest() if model["name"] == "CAM++")["revision"])


def prepare_export(config: Any) -> tuple[ExitStack, bytes, list[tuple[str, Path]]]:
    stack = ExitStack()
    try:
        with database.connect() as db:
            db.execute("BEGIN")
            store.require_complete(db)
            snapshot = store.snapshot(db)
        model = next(model for model in model_manifest() if model["name"] == "CAM++")
        cache_compatible = model_installation(config.models_dir, model)["installed"]
        files = []
        people = []
        for person in snapshot["people"]:
            samples = []
            for sample in person["samples"]:
                stack.enter_context(reader_lease("voiceprint:" + sample["id"]))
                item = {k: sample.get(k) for k in ("id", "name", "state", "language", "transcript", "words", "duration", "error_message", "created_at", "updated_at")}
                if sample.get("audio_path"):
                    path = store.audio_path(config, sample["audio_path"])
                    if not path.is_file():
                        if sample["state"] == "ready":
                            raise HTTPException(409, "Ready voiceprint audio is missing / 可用声纹样本音频缺失: " + sample["name"])
                    else:
                        suffix = path.suffix.lower() if re.fullmatch(r"\.[a-z0-9]{1,10}", path.suffix.lower()) else ".wav"
                        name = f"audio/{sample['id']}{suffix}"
                        files.append((name, path))
                        item["audio"] = {"path": name, "sha256": store.file_digest(path), "size_bytes": path.stat().st_size}
                if cache_compatible and sample.get("embedding") and sample.get("embedding_model") == "CAM++/campplus_cn_common":
                    try:
                        embedding = ArchiveEmbedding(model=sample["embedding_model"], revision=cam_revision(), data=base64.b64encode(sample["embedding"]).decode("ascii"))
                        item["embedding"] = embedding.model_dump()
                    except ValueError:
                        pass  # An invalid optional feature cache is rebuilt by existing ASR behavior.
                samples.append(item)
            people.append({**{k: person.get(k) for k in ("id", "name", "note", "aliases", "created_at", "updated_at")},
                           "include_in_hotword_library": bool(person["include_in_hotword_library"]), "samples": samples})
        hotwords = [{k: item[k] for k in ("id", "name", "terms", "created_at", "updated_at")} for item in snapshot["hotword_lists"]]
        archive = LibraryArchive(exported_at=database.utcnow(), source_version=__version__, people=people, hotword_lists=hotwords)
        manifest = archive.model_dump_json(exclude_none=True).encode("utf-8")
        # Include streaming ZIP64 headers, data descriptors and the central directory.
        overhead = 256 + sum(256 + 2 * len(name.encode("utf-8")) for name, _ in files)
        if len(manifest) > MANIFEST_LIMIT or len(files) + 1 > ENTRY_LIMIT or len(manifest) + overhead + sum(path.stat().st_size for _, path in files) > config.max_upload_bytes:
            raise HTTPException(413, "Library archive exceeds transfer limits")
        return stack, manifest, files
    except BaseException:
        stack.close()
        raise


def export_stream(stack: ExitStack, manifest: bytes, files: list[tuple[str, Path]]) -> Iterator[bytes]:
    try:
        sink = Sink()
        with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.writestr("manifest.json", manifest)
            yield sink.drain()
            for name, path in files:
                info = zipfile.ZipInfo(name)
                info.file_size = path.stat().st_size
                with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                    while chunk := source.read(BLOCK):
                        target.write(chunk)
                        yield sink.drain()
                if sink.pending:
                    yield sink.drain()
        if sink.pending:
            yield sink.drain()
    finally:
        stack.close()


def validate(config: Any, identifier: str) -> None:
    task_root = store.root(config, identifier)
    store.update(identifier, state="validating", error=None)
    try:
        with zipfile.ZipFile(task_root / "source.zip") as archive:
            entries = archive.infolist()
            if len(entries) > ENTRY_LIMIT or sum(info.file_size for info in entries) > config.max_upload_bytes:
                raise ValueError("Archive exceeds expanded size or entry limits")
            names: set[str] = set()
            for info in entries:
                path = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                key = info.filename.casefold()
                if (not info.filename or path.is_absolute() or any(p in (".", "..") for p in info.filename.split("/"))
                        or any(c in info.filename for c in ("\\", ":", "\x00")) or info.is_dir()
                        or stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                        or info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or key in names):
                    raise ValueError("Unsafe, duplicate, encrypted or unsupported archive entry")
                names.add(key)
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > MANIFEST_LIMIT:
                raise ValueError("Library manifest exceeds 16 MiB")
            parsed = LibraryArchive.model_validate_json(archive.read(manifest_info))
            people_ids, identities, sample_ids, aliases, list_ids, list_names = set(), set(), set(), set(), set(), set()
            declared: dict[str, Any] = {}
            for person in parsed.people:
                identity = (database.person_name_key(person.name), database.person_name_key(person.note or ""))
                if person.id in people_ids or identity in identities:
                    raise ValueError("Duplicate person ID or normalized name/note")
                people_ids.add(person.id); identities.add(identity)
                for alias in dict.fromkeys([person.id, *person.aliases]):
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", alias) or alias in aliases:
                        raise ValueError("Invalid or ambiguous voice alias")
                    aliases.add(alias)
                sample_names = set()
                for sample in person.samples:
                    name = database.person_name_key(sample.name)
                    if sample.id in sample_ids or name in sample_names:
                        raise ValueError("Duplicate sample ID or name")
                    sample_ids.add(sample.id); sample_names.add(name)
                    if sample.audio:
                        name = sample.audio.path
                        if not re.fullmatch(r"audio/[A-Za-z0-9_-]+\.[a-z0-9]{1,10}", name) or name in declared:
                            raise ValueError("Invalid or duplicate audio reference")
                        declared[name] = sample.audio
            for item in parsed.hotword_lists:
                name = hotword_key = item.name.casefold()
                if item.id in list_ids or hotword_key in list_names or item.id in {"hotwords_voiceprint_people", "hotwords_voiceprint_people_short"}:
                    raise ValueError("Duplicate or reserved hotword list ID/name")
                list_ids.add(item.id); list_names.add(name)
            if len(list_ids) > 100 or {info.filename for info in entries} != {"manifest.json", *declared}:
                raise ValueError("Unexpected or missing archive files, or too many hotword lists")
            required = sum(info.file_size for info in entries)
            if shutil.disk_usage(config.data_dir).free - required < config.min_free_disk_bytes:
                raise ValueError("Insufficient disk space for expanded library archive")
            for name, audio in declared.items():
                info = archive.getinfo(name)
                if info.file_size != audio.size_bytes:
                    raise ValueError("Audio size does not match manifest")
                path = task_root / "assets" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, path.open("wb") as target:
                    shutil.copyfileobj(source, target, BLOCK)
                if store.file_digest(path) != audio.sha256:
                    raise ValueError("Audio checksum does not match manifest")
                import av
                with av.open(str(path), options={"format_whitelist": "wav,mp3,flac,ogg,mov,aac", "protocol_whitelist": "file"}) as media:
                    if not media.streams.audio:
                        raise ValueError("Sample does not contain audio")
                    if next(media.decode(audio=0), None) is None:
                        raise ValueError("Sample has no decodable audio frames")
            atomic_json(task_root / "validated.json", parsed.model_dump(mode="json"))
        store.update(identifier, state="ready")
    except Exception as exc:
        shutil.rmtree(task_root / "assets", ignore_errors=True)
        (task_root / "source.zip").unlink(missing_ok=True)
        store.update(identifier, state="failed", error=str(exc)[:2000])


def backup_path(config: Any, identifier: str) -> Path:
    return config.data_dir / "library_backups" / f"{identifier}.zip"


def write_backup(config: Any, identifier: str) -> Path:
    target = backup_path(config, identifier)
    target.parent.mkdir(parents=True, exist_ok=True)
    stack, manifest, files = prepare_export(config)
    partial = target.with_suffix(".partial")
    stream = export_stream(stack, manifest, files)
    try:
        with partial.open("wb") as output:
            for chunk in stream:
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        with zipfile.ZipFile(partial) as archive:
            if archive.testzip() is not None:
                raise ValueError("Replacement backup checksum failed")
        partial.replace(target)
        return target
    finally:
        stream.close()
        stack.close()
        partial.unlink(missing_ok=True)


def unlink_audio(config: Any, paths: list[str]) -> bool:
    success = True
    for value in paths:
        try:
            path = store.audio_path(config, value)
            from .waveforms import cache_path
            path.unlink(missing_ok=True)
            cache_path(path, path.parent / "waveforms").unlink(missing_ok=True)
        except (OSError, HTTPException):
            success = False
    return success


def cleanup(config: Any, identifier: str, completed: bool) -> None:
    task_root = store.root(config, identifier)
    journal_path = task_root / "files.json"
    if journal_path.exists():
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if unlink_audio(config, journal["old_paths"] if completed else journal["new_paths"]):
            journal_path.unlink()
    if completed:
        shutil.rmtree(task_root / "assets", ignore_errors=True)
        (task_root / "source.zip").unlink(missing_ok=True)
        (task_root / "validated.json").unlink(missing_ok=True)


def apply(config: Any, identifier: str) -> None:
    item = store.get(identifier)
    mode = item["mode"]
    task_root = store.root(config, identifier)
    committed = False
    try:
        cleanup(config, identifier, False)
        if (task_root / "files.json").exists():
            raise HTTPException(409, "File rollback is pending; retry after its handles close")
        parsed = store.load_archive(config, identifier)
        current = current_snapshot()
        plan = store.build_plan(parsed, current, mode)
        if plan["preview"]["preview_revision"] != item["preview"]["preview_revision"]:
            raise HTTPException(409, "Library changed; refresh the preview / 资料库已变化，请刷新预览")
        old_paths = [s["audio_path"] for p in current["people"] for s in p["samples"] if s.get("audio_path")] if mode == "replace" else []
        size = sum(s["audio"]["size_bytes"] for s in plan["samples"] if s["audio"])
        backup_size = sum(Path(path).stat().st_size for path in old_paths if Path(path).is_file())
        if shutil.disk_usage(config.data_dir).free - size - backup_size - MANIFEST_LIMIT < config.min_free_disk_bytes:
            raise HTTPException(429, "Insufficient disk space for import and replacement backup")
        if mode == "replace":
            with database.connect() as db:
                store.require_idle(db)
            write_backup(config, identifier)
        with ExitStack() as guards:
            if mode == "replace":
                for p in current["people"]:
                    for sample in p["samples"]:
                        if not guards.enter_context(deletion_guard("voiceprint:" + sample["id"])):
                            raise HTTPException(409, "Voiceprint audio is being read; retry shortly")
            for sample in plan["samples"]:
                if sample["audio"]:
                    suffix = Path(sample["audio"]["path"]).suffix
                    target = config.voiceprints_dir / sample["person_id"] / f"{sample['id']}-{identifier}{suffix}"
                    sample["audio_path"] = str(target)
                embedding = sample.get("embedding")
                if embedding and embedding["revision"] == cam_revision():
                    sample["embedding_bytes"] = base64.b64decode(embedding["data"], validate=True)
            new_paths = [s["audio_path"] for s in plan["samples"] if s.get("audio_path")]
            atomic_json(task_root / "files.json", {"new_paths": new_paths, "old_paths": old_paths})
            for sample in plan["samples"]:
                if sample.get("audio_path"):
                    target = Path(sample["audio_path"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(task_root / "assets" / sample["audio"]["path"], target)
                    with target.open("r+b") as durable:
                        os.fsync(durable.fileno())
                    if store.file_digest(target) != sample["audio"]["sha256"]:
                        raise ValueError("Staged audio changed; upload the archive again")
            result = {**plan["preview"], "id_map": plan["id_map"]}
            with database.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if store.revision(store.snapshot(db)) != store.revision(current):
                    raise HTTPException(409, "Library changed; refresh the preview")
                if mode == "replace":
                    store.require_idle(db)
                store.commit_plan(db, plan, mode)
                db.execute("UPDATE library_imports SET state='completed',result_json=?,error=NULL,updated_at=? WHERE id=?",
                           (json.dumps(result, ensure_ascii=False), database.utcnow(), identifier))
                db.execute("COMMIT")
                committed = True
            cleanup(config, identifier, True)
    except Exception as exc:
        if committed:
            # The durable receipt distinguishes a completed transaction from cleanup.
            store.update(identifier, error="Import completed; retired file cleanup will retry on restart")
        else:
            cleanup(config, identifier, False)
            store.update(identifier, state="ready", error=(str(exc.detail) if isinstance(exc, HTTPException) else str(exc))[:2000])


def recover(config: Any) -> None:
    with database.connect() as db:
        items = [dict(row) for row in db.execute("SELECT id,state FROM library_imports")]
    for item in items:
        identifier, state = item["id"], item["state"]
        cleanup(config, identifier, state == "completed")
        if state == "applying":
            store.update(identifier, state="ready", error="Interrupted import rolled back; review the preview again")
        elif state == "validating":
            shutil.rmtree(store.root(config, identifier) / "assets", ignore_errors=True)
            store.update(identifier, state="queued")
