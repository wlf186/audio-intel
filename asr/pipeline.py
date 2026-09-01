from __future__ import annotations

import json
import html
import logging
import math
import os
import subprocess
import time
import sys
import wave
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from audio_intel.config import settings
from audio_intel.db import (
    get_voiceprint_sample,
    list_voiceprint_people,
    update_voiceprint_sample,
)
from audio_intel.gpu import compute_device_name, gpu_lease
from audio_intel.model_registry import model_installation, resolve_asr_model
from audio_intel.performance import resolve_acceleration
from audio_intel.progress import progress_snapshot_paths
from audio_intel.utils import atomic_json, timecode, waveform_peaks
from audio_intel.worker import JobContext


ALIGNER_LANGUAGES = {"Chinese", "English", "Cantonese", "French", "German", "Italian", "Japanese", "Korean", "Portuguese", "Russian", "Spanish"}
GPU_DIARIZATION_BATCH_SIZE = 16
GPU_DIARIZATION_MIN_CPUS = 8
SHORT_DIARIZATION_WINDOW_LIMIT = 20
SHORT_DIARIZATION_COSINE_THRESHOLD = 0.4
MAX_AUTO_SPEAKERS = 15
VOICEPRINT_COSINE_THRESHOLD = 0.31
VOICEPRINT_EMBEDDING_MODEL = "CAM++/campplus_cn_common"
AUTO_REFINE_LOW_SUPPORT_TURNS = 2
AUTO_REFINE_STABLE_TURNS = 3
AUTO_REFINE_MIN_TURN_SECONDS = 1.0
AUTO_REFINE_MAX_TURN_SECONDS = 12.0
AUTO_REFINE_WINDOW_COSINE = 0.50
AUTO_REFINE_TURN_COSINE = 0.535
AUTO_REFINE_WINDOW_MARGIN = 0.08
AUTO_REFINE_TURN_MARGIN = 0.04


logger = logging.getLogger(__name__)


def decode_audio(source: Path, output: Path) -> tuple[Any, int]:
    import av
    import numpy as np

    samples: list[Any] = []
    with av.open(str(source)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        for frame in container.decode(stream):
            converted = resampler.resample(frame)
            for audio_frame in converted:
                samples.append(audio_frame.to_ndarray().reshape(-1))
        for audio_frame in resampler.resample(None):
            samples.append(audio_frame.to_ndarray().reshape(-1))
    if not samples:
        raise ValueError("Audio contains no decodable samples")
    audio = np.concatenate(samples).astype(np.float32)
    peak = float(np.max(np.abs(audio)))
    if peak > 1.0:
        audio /= peak
    import soundfile as sf
    sf.write(output, audio, 16000, subtype="PCM_16")
    return audio, 16000


def _mock_audio(source: Path, output: Path) -> tuple[list[float], int]:
    try:
        with wave.open(str(source), "rb") as handle:
            duration = handle.getnframes() / max(handle.getframerate(), 1)
    except (wave.Error, EOFError):
        duration = 18.0
    duration = max(2.0, duration)
    rate = 16000
    audio = [0.06 * math.sin(2 * math.pi * 180 * i / rate) for i in range(int(duration * rate))]
    with wave.open(str(output), "wb") as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(rate)
        handle.writeframes(b"".join(int(max(-1, min(1, x)) * 32767).to_bytes(2, "little", signed=True) for x in audio))
    return audio, rate


def run_vad(audio: Any, sample_rate: int) -> list[dict[str, float]]:
    from funasr import AutoModel

    model = AutoModel(model=str(settings.models_dir / "FSMN-VAD"), device="cpu", disable_update=True)
    result = model.generate(input=audio, fs=sample_rate)
    raw = (result[0].get("value") or result[0].get("timestamp") or []) if result else []
    segments = [{"start": float(item[0]) / 1000, "end": float(item[1]) / 1000} for item in raw]
    return segments


def combine_vad(segments: list[dict[str, float]], duration: float, target: float = 45.0) -> list[dict[str, float]]:
    if not segments:
        return [{"start": 0.0, "end": duration}] if duration else []
    chunks: list[dict[str, float]] = []
    current = dict(segments[0])
    for segment in segments[1:]:
        if segment["end"] - current["start"] <= target and segment["start"] - current["end"] < 2.0:
            current["end"] = segment["end"]
        else:
            chunks.append(current); current = dict(segment)
    chunks.append(current)
    split: list[dict[str, float]] = []
    for chunk in chunks:
        start = chunk["start"]
        while chunk["end"] - start > 60:
            split.append({"start": start, "end": start + 55}); start += 55
        split.append({"start": start, "end": chunk["end"]})
    return split


def write_chunks(audio: Any, rate: int, chunks: list[dict[str, float]], directory: Path) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    output = []
    for index, chunk in enumerate(chunks):
        path = directory / f"chunk-{index:04d}.wav"
        samples = audio[int(chunk["start"] * rate):int(chunk["end"] * rate)]
        if settings.mock_mode:
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(rate)
                handle.writeframes(b"".join(int(max(-1, min(1, x)) * 32767).to_bytes(2, "little", signed=True) for x in samples))
        else:
            import soundfile as sf
            sf.write(path, samples, rate, subtype="PCM_16")
        output.append({"index": index, "path": str(path), **chunk})
    return output


def diarize(
    audio: Any,
    vad: list[dict[str, float]],
    speakers: int | None,
    batch_size: int = 1,
    voiceprint_people: list[dict[str, Any]] | None = None,
    return_metadata: bool = False,
) -> Any:
    import torch
    from funasr import AutoModel
    from funasr.models.campplus.cluster_backend import ClusterBackend
    from funasr.models.campplus.utils import postprocess, sv_chunk

    model = AutoModel(
        model=str(settings.models_dir / "CAM++"),
        device="cpu",
        disable_update=True,
        disable_pbar=True,
    )
    vad_with_audio = [[item["start"], item["end"], audio[int(item["start"] * 16000):int(item["end"] * 16000)]] for item in vad]
    chunks = sv_chunk(vad_with_audio)
    if not chunks:
        diarized = [{**item, "speaker": "Speaker_0"} for item in vad]
        return (diarized, {}, {"status": "empty_audio", "indexed_samples": 0}) if return_metadata else diarized
    embeddings = model.generate(
        input=[chunk[2] for chunk in chunks],
        cache={},
        is_final=True,
        batch_size=batch_size,
    )
    vectors = torch.cat([item["spk_embedding"] for item in embeddings], dim=0).detach().cpu().numpy()
    cluster = ClusterBackend(merge_thr=0.78).cpu()
    labels = _cluster_speakers(vectors, speakers, cluster)
    if speakers is None:
        try:
            labels = _refine_auto_speaker_labels(
                audio, chunks, vad_with_audio, labels, vectors, model, batch_size,
            )
        except Exception as exc:
            logger.warning("Automatic speaker refinement skipped: %s", exc)
    matches: dict[str, dict[str, Any]] = {}
    voiceprint_status = {"status": "disabled", "indexed_samples": 0}
    speaker_centers = None
    try:
        diarized, speaker_centers = postprocess(
            chunks, vad_with_audio, labels, vectors, return_spk_center=True,
        )
        result = [{"start": float(item[0]), "end": float(item[1]), "speaker": f"Speaker_{int(item[2])}"} for item in diarized]
    except Exception:
        result = [{"start": float(chunk[0]), "end": float(chunk[1]), "speaker": f"Speaker_{int(label)}"} for chunk, label in zip(chunks, labels)]
    if voiceprint_people is not None:
        if speaker_centers is None:
            voiceprint_status = {"status": "degraded", "indexed_samples": 0}
        else:
            try:
                matches, voiceprint_status = _match_voiceprints(model, speaker_centers, voiceprint_people)
            except Exception:
                voiceprint_status = {"status": "degraded", "indexed_samples": 0}
    return (result, matches, voiceprint_status) if return_metadata else result


def _extract_embedding(model: Any, audio_path: str) -> Any:
    import numpy as np

    output = model.generate(input=audio_path, cache={}, is_final=True, batch_size=1)
    if not output or "spk_embedding" not in output[0]:
        raise ValueError("CAM++ returned no speaker embedding")
    vector = output[0]["spk_embedding"][0].detach().cpu().numpy().astype(np.float32, copy=False)
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("CAM++ returned an invalid speaker embedding")
    return vector / norm


def _match_voiceprints(
    model: Any,
    speaker_centers: Any,
    people: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    person_ids: list[str] = []
    person_names: list[str] = []
    person_notes: list[str | None] = []
    person_vectors: list[Any] = []
    indexed_samples = 0
    skipped_samples = 0
    for person in people:
        sample_vectors = []
        for sample in person.get("samples", []):
            if sample.get("state") != "ready" or not sample.get("audio_path"):
                continue
            vector = None
            raw = sample.get("embedding")
            if raw:
                decoded = np.frombuffer(raw, dtype=np.float32)
                if decoded.size == int(speaker_centers.shape[1]):
                    norm = float(np.linalg.norm(decoded))
                    if norm > 0:
                        vector = decoded / norm
            if vector is None:
                try:
                    vector = _extract_embedding(model, sample["audio_path"])
                    update_voiceprint_sample(
                        sample["id"], embedding=vector.astype(np.float32).tobytes(),
                        embedding_model=VOICEPRINT_EMBEDDING_MODEL, embedding_error=None,
                    )
                except Exception as exc:
                    skipped_samples += 1
                    update_voiceprint_sample(sample["id"], embedding_error=str(exc)[:500])
                    continue
            indexed_samples += 1
            sample_vectors.append(vector)
        if sample_vectors:
            center = np.mean(np.stack(sample_vectors), axis=0)
            center /= max(float(np.linalg.norm(center)), np.finfo(np.float32).eps)
            person_ids.append(person["id"])
            person_names.append(person["name"])
            person_notes.append(person.get("note"))
            person_vectors.append(center)
    if not person_vectors:
        status = "empty" if not skipped_samples else "degraded"
        return {}, {"status": status, "indexed_samples": indexed_samples, "skipped_samples": skipped_samples}

    current = np.asarray(speaker_centers, dtype=np.float32)
    current /= np.maximum(np.linalg.norm(current, axis=1, keepdims=True), np.finfo(np.float32).eps)
    scores = current @ np.stack(person_vectors).T
    rows, columns = linear_sum_assignment(-scores)
    matches = {}
    for row, column in zip(rows, columns):
        score = float(scores[row, column])
        if score >= VOICEPRINT_COSINE_THRESHOLD:
            match = {
                "person_id": person_ids[column], "name": person_names[column],
                "score": round(score, 4),
            }
            if person_notes[column]:
                match["note"] = person_notes[column]
            matches[f"Speaker_{int(row)}"] = match
    return matches, {
        "status": "matched" if matches else "no_match", "indexed_samples": indexed_samples,
        "skipped_samples": skipped_samples, "matched_speakers": len(matches),
    }


def _cluster_speakers(vectors: Any, speakers: int | None, cluster: Any) -> Any:
    """Avoid FunASR's unconditional single-speaker fallback for short recordings."""
    import numpy as np

    window_count = int(vectors.shape[0])
    if window_count >= SHORT_DIARIZATION_WINDOW_LIMIT:
        return cluster(vectors, oracle_num=speakers)
    if window_count <= 1 or speakers == 1:
        return np.zeros(window_count, dtype="int")
    if speakers is not None:
        return cluster.kmeans_cluster(vectors, min(speakers, window_count))

    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    normalized = vectors / np.maximum(norms, np.finfo(vectors.dtype).eps)
    distances = np.clip(1.0 - normalized @ normalized.T, 0.0, 2.0)
    distances = (distances + distances.T) / 2
    np.fill_diagonal(distances, 0.0)
    tree = linkage(squareform(distances, checks=False), method="average")
    labels = fcluster(tree, 1.0 - SHORT_DIARIZATION_COSINE_THRESHOLD, criterion="distance") - 1
    if len(np.unique(labels)) > MAX_AUTO_SPEAKERS:
        labels = fcluster(tree, MAX_AUTO_SPEAKERS, criterion="maxclust") - 1
    return labels.astype("int", copy=False)


def _canonical_labels(labels: Any) -> Any:
    import numpy as np

    mapping: dict[int, int] = {}
    canonical = []
    for value in labels:
        key = int(value)
        mapping.setdefault(key, len(mapping))
        canonical.append(mapping[key])
    return np.asarray(canonical, dtype="int")


def _cosine_centers(vectors: Any, labels: Any) -> Any:
    import numpy as np

    centers = np.stack([vectors[labels == index].mean(axis=0) for index in range(int(labels.max()) + 1)])
    norms = np.linalg.norm(centers, axis=1, keepdims=True)
    return centers / np.maximum(norms, np.finfo(centers.dtype).eps)


def _mutual_nearest(similarities: Any) -> Any:
    import numpy as np

    values = similarities.copy()
    np.fill_diagonal(values, -np.inf)
    return np.argmax(values, axis=1)


def _pair_margin(similarities: Any, first: int, second: int) -> float:
    alternatives = [
        float(similarities[member, other])
        for member in (first, second)
        for other in range(len(similarities))
        if other not in (first, second)
    ]
    return float(similarities[first, second]) - max(alternatives, default=-1.0)


def _candidate_auto_merges(labels: Any, vectors: Any, turns: list[Any]) -> list[tuple[int, int]]:
    counts = Counter(int(item[2]) for item in turns)
    if not any(count <= AUTO_REFINE_LOW_SUPPORT_TURNS for count in counts.values()):
        return []
    centers = _cosine_centers(vectors, labels)
    similarities = centers @ centers.T
    nearest = _mutual_nearest(similarities)
    candidates = []
    for low, low_count in counts.items():
        if low_count > AUTO_REFINE_LOW_SUPPORT_TURNS:
            continue
        stable = int(nearest[low])
        if counts.get(stable, 0) < AUTO_REFINE_STABLE_TURNS or int(nearest[stable]) != low:
            continue
        score = float(similarities[low, stable])
        margin = _pair_margin(similarities, low, stable)
        if score >= AUTO_REFINE_WINDOW_COSINE and margin >= AUTO_REFINE_WINDOW_MARGIN:
            candidates.append((low, stable))
    return candidates


def _turn_cluster_centers(
    audio: Any,
    turns: list[Any],
    candidate_clusters: set[int],
    model: Any,
    batch_size: int,
) -> dict[int, Any]:
    import numpy as np
    import torch

    selected: list[tuple[int, float, float]] = []
    speaker_ids = sorted({int(item[2]) for item in turns})
    for speaker_id in speaker_ids:
        limit = 3 if speaker_id in candidate_clusters else 1
        eligible = sorted(
            (
                (float(item[1]) - float(item[0]), float(item[0]), float(item[1]))
                for item in turns
                if int(item[2]) == speaker_id and float(item[1]) - float(item[0]) >= AUTO_REFINE_MIN_TURN_SECONDS
            ),
            reverse=True,
        )[:limit]
        for duration, start, end in eligible:
            if duration > AUTO_REFINE_MAX_TURN_SECONDS:
                midpoint = (start + end) / 2
                start = midpoint - AUTO_REFINE_MAX_TURN_SECONDS / 2
                end = midpoint + AUTO_REFINE_MAX_TURN_SECONDS / 2
            selected.append((speaker_id, start, end))
    if not selected:
        return {}
    output = model.generate(
        input=[audio[int(start * 16000):int(end * 16000)] for _, start, end in selected],
        cache={},
        is_final=True,
        batch_size=batch_size,
    )
    vectors = torch.cat([item["spk_embedding"] for item in output], dim=0).detach().cpu().numpy()
    if len(vectors) != len(selected):
        raise ValueError("CAM++ returned an unexpected number of turn embeddings")
    centers = {}
    for speaker_id in speaker_ids:
        indices = [index for index, item in enumerate(selected) if item[0] == speaker_id]
        if not indices:
            continue
        center = vectors[indices].mean(axis=0)
        norm = float(np.linalg.norm(center))
        if math.isfinite(norm) and norm > 0:
            centers[speaker_id] = center / norm
    return centers


def _accepted_auto_merges(
    candidates: list[tuple[int, int]],
    turn_centers: dict[int, Any],
) -> tuple[dict[int, int], list[tuple[int, int, float, float]]]:
    import numpy as np

    ordered_ids = sorted(turn_centers)
    if len(ordered_ids) < 2:
        return {}, []
    turn_matrix = np.stack([turn_centers[speaker_id] for speaker_id in ordered_ids])
    similarities = turn_matrix @ turn_matrix.T
    nearest = _mutual_nearest(similarities)
    positions = {speaker_id: index for index, speaker_id in enumerate(ordered_ids)}
    merges: dict[int, int] = {}
    accepted = []
    for low, stable in candidates:
        if low not in positions or stable not in positions:
            continue
        first, second = positions[low], positions[stable]
        if int(nearest[first]) != second or int(nearest[second]) != first:
            continue
        score = float(similarities[first, second])
        margin = _pair_margin(similarities, first, second)
        if score < AUTO_REFINE_TURN_COSINE or margin < AUTO_REFINE_TURN_MARGIN:
            continue
        merges[low] = stable
        accepted.append((low, stable, round(score, 4), round(margin, 4)))
    return merges, accepted


def _refine_auto_speaker_labels(
    audio: Any,
    chunks: list[Any],
    vad_with_audio: list[Any],
    labels: Any,
    vectors: Any,
    model: Any,
    batch_size: int,
) -> Any:
    import numpy as np
    from funasr.models.campplus.utils import postprocess

    labels = _canonical_labels(labels)
    if labels.size <= 1 or int(labels.max()) == 0:
        return labels
    preliminary = postprocess(chunks, vad_with_audio, labels, vectors)
    candidates = _candidate_auto_merges(labels, vectors, preliminary)
    if not candidates:
        return labels
    candidate_clusters = {speaker_id for pair in candidates for speaker_id in pair}
    turn_centers = _turn_cluster_centers(audio, preliminary, candidate_clusters, model, batch_size)
    merges, accepted = _accepted_auto_merges(candidates, turn_centers)
    if not merges:
        return labels
    refined = np.asarray([merges.get(int(label), int(label)) for label in labels], dtype="int")
    refined = _canonical_labels(refined)
    logger.info(
        "Automatic speaker refinement merged %d -> %d clusters: %s",
        int(labels.max()) + 1,
        int(refined.max()) + 1,
        accepted,
    )
    return refined


def _stop_stage_process(process: subprocess.Popen[Any]) -> None:
    try:
        import psutil
        root = psutil.Process(process.pid)
        targets = list(reversed(root.children(recursive=True))) + [root]
        for target in targets:
            try:
                target.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        _, alive = psutil.wait_procs(targets, timeout=0.75)
        for target in alive:
            try:
                target.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        psutil.wait_procs(alive, timeout=0.5)
    except Exception:
        process.kill()
    process.wait(timeout=2)


def _drain_stage_progress(
    progress_path: Path,
    progress_callback: Any | None,
    last_sequence: int,
) -> int:
    latest: tuple[int, dict[str, Any]] | None = None
    consumed: list[Path] = []
    for sequence, snapshot_path in progress_snapshot_paths(progress_path):
        if sequence <= last_sequence:
            consumed.append(snapshot_path)
            continue
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except OSError:
            continue
        except ValueError:
            consumed.append(snapshot_path)
            continue
        consumed.append(snapshot_path)
        if (
            isinstance(snapshot, dict)
            and {"stage", "completed", "total"}.issubset(snapshot)
        ):
            latest = (sequence, snapshot)

    if latest is not None:
        sequence, snapshot = latest
        if progress_callback is not None:
            progress_callback(snapshot)
        last_sequence = sequence

    for snapshot_path in consumed:
        try:
            snapshot_path.unlink(missing_ok=True)
        except OSError:
            pass
    return last_sequence


def run_stage(
    operation: str,
    payload: dict[str, Any],
    directory: Path,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    input_path, output_path = directory / f"{operation}-input.json", directory / f"{operation}-output.json"
    progress_path = directory / f"{operation}-progress.json"
    payload["progress_path"] = str(progress_path)
    atomic_json(input_path, payload)
    environment = os.environ.copy()
    environment.update({"PYTHONPATH": str(settings.root), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    process = subprocess.Popen(
        [sys.executable, "-m", "asr.stage", operation, str(input_path), str(output_path)],
        env=environment,
    )
    last_progress_sequence = 0
    try:
        while process.poll() is None:
            last_progress_sequence = _drain_stage_progress(
                progress_path, progress_callback, last_progress_sequence,
            )
            time.sleep(0.2)
        _drain_stage_progress(progress_path, progress_callback, last_progress_sequence)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, process.args)
    except BaseException:
        if process.poll() is None:
            _stop_stage_process(process)
        raise
    return json.loads(output_path.read_text(encoding="utf-8"))


def run_model_stage(
    context: JobContext,
    operation: str,
    payload: dict[str, Any],
    directory: Path,
    compute_device: str,
    progress: float,
) -> dict[str, Any]:
    payload["compute_device"] = compute_device
    end_progress = 0.68 if operation == "transcribe" else 0.88
    def report(snapshot: dict[str, Any]) -> None:
        completed = int(snapshot["completed"])
        total = int(snapshot["total"])
        ratio = snapshot.get("stage_progress")
        ratio = float(ratio) if ratio is not None else completed / total if total else 1.0
        context.progress(
            progress + (end_progress - progress) * ratio,
            f"qwen3_{'asr' if operation == 'transcribe' else 'forced_alignment'}_{compute_device}",
            completed, total,
            stage_progress=ratio, unit=str(snapshot.get("unit") or "audio_chunk"),
            basis=str(snapshot.get("basis") or "observed"),
            activity=snapshot.get("activity"),
        )
    if compute_device == "cpu":
        return run_stage(operation, payload, directory, report)
    with gpu_lease(lambda: context.progress(progress, "waiting_for_gpu")):
        return run_stage(operation, payload, directory, report)


def _parallel_diarization_enabled(compute_device: str) -> bool:
    return compute_device == "gpu" and (os.cpu_count() or 1) >= GPU_DIARIZATION_MIN_CPUS


def speaker_at(start: float, end: float, diarization: list[dict[str, Any]]) -> str:
    midpoint = (start + end) / 2
    overlap_by_speaker: dict[str, float] = {}
    midpoint_speakers = set()
    first_index: dict[str, int] = {}
    for index, item in enumerate(diarization):
        speaker = item["speaker"]
        overlap_by_speaker[speaker] = overlap_by_speaker.get(speaker, 0.0) + max(
            0.0, min(end, item["end"]) - max(start, item["start"]),
        )
        first_index.setdefault(speaker, index)
        if item["start"] <= midpoint <= item["end"]:
            midpoint_speakers.add(speaker)
    if overlap_by_speaker and max(overlap_by_speaker.values()) > 0:
        return max(
            overlap_by_speaker,
            key=lambda speaker: (
                overlap_by_speaker[speaker],
                speaker in midpoint_speakers,
                -first_index[speaker],
            ),
        )
    if diarization:
        nearest = min(
            enumerate(diarization),
            key=lambda pair: (
                max(pair[1]["start"] - end, start - pair[1]["end"], 0.0),
                pair[0],
            ),
        )[1]
        return nearest["speaker"]
    return "Speaker_0"


def _aligned_word_offsets(text: str, words: list[dict[str, Any]]) -> list[int] | None:
    offsets = []
    cursor = 0
    for word in words:
        token = str(word.get("text", ""))
        if not token:
            offsets.append(cursor)
            continue
        offset = text.find(token, cursor)
        if offset < 0:
            return None
        offsets.append(offset)
        cursor = offset + len(token)
    return offsets


def _segment_payload(
    start: float,
    end: float,
    speaker: str,
    text: str,
    words: list[dict[str, Any]],
    expose_words: bool,
) -> dict[str, Any]:
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "speaker": speaker,
        "speaker_label": speaker.replace("_", " "),
        "text": text,
        "words": words if expose_words else [],
    }


def _speaker_turns(
    chunk: dict[str, Any],
    diarization: list[dict[str, Any]],
    expose_words: bool,
) -> tuple[list[dict[str, Any]], set[str]]:
    words = chunk.get("words") or []
    fallback_speaker = speaker_at(chunk["start"], chunk["end"], diarization)
    if not words:
        return [
            _segment_payload(
                chunk["start"], chunk["end"], fallback_speaker,
                chunk.get("text", ""), [], expose_words,
            )
        ], {fallback_speaker}

    word_items = [
        {**word, "speaker": speaker_at(word["start"], word["end"], diarization)}
        for word in words
    ]
    observed_speakers = {word["speaker"] for word in word_items}
    offsets = _aligned_word_offsets(chunk.get("text", ""), word_items)
    if offsets is None:
        speaker = Counter(word["speaker"] for word in word_items).most_common(1)[0][0]
        return [
            _segment_payload(
                chunk["start"], chunk["end"], speaker,
                chunk.get("text", ""), word_items, expose_words,
            )
        ], observed_speakers

    runs: list[dict[str, Any]] = []
    for index, word in enumerate(word_items):
        if runs and (not word.get("text") or runs[-1]["speaker"] == word["speaker"]):
            runs[-1]["last"] = index
            runs[-1]["words"].append(word)
        else:
            runs.append({"speaker": word["speaker"], "first": index, "last": index, "words": [word]})

    boundaries = [float(chunk["start"])]
    for previous, current in zip(runs, runs[1:]):
        previous_end = float(previous["words"][-1]["end"])
        current_start = float(current["words"][0]["start"])
        boundary = max(boundaries[-1], min(float(chunk["end"]), (previous_end + current_start) / 2))
        boundaries.append(boundary)
    boundaries.append(float(chunk["end"]))

    segments = []
    chunk_text = chunk.get("text", "")
    for index, run in enumerate(runs):
        text_start = 0 if index == 0 else offsets[run["first"]]
        text_end = len(chunk_text) if index + 1 == len(runs) else offsets[runs[index + 1]["first"]]
        segments.append(
            _segment_payload(
                boundaries[index], boundaries[index + 1], run["speaker"],
                chunk_text[text_start:text_end], run["words"], expose_words,
            )
        )
    return segments, observed_speakers


def assemble(
    chunks: list[dict[str, Any]],
    diarization: list[dict[str, Any]],
    duration: float,
    aligned: bool,
    expose_words: bool | None = None,
    voiceprint_matches: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expose_words = aligned if expose_words is None else expose_words
    segments: list[dict[str, Any]] = []
    speaker_ids: set[str] = set()
    for chunk in chunks:
        turns, observed = _speaker_turns(chunk, diarization, expose_words)
        speaker_ids.update(observed)
        for turn in turns:
            turn["id"] = len(segments)
            segments.append(turn)
    ordered_speakers = sorted(speaker_ids or {"Speaker_0"})
    matches = voiceprint_matches or {}
    speakers = []
    labels = {}
    for speaker_id in ordered_speakers:
        match = matches.get(speaker_id)
        label = (
            f"{match['name']}（{match['note']}）" if match and match.get("note")
            else match["name"] if match
            else speaker_id.replace("_", " ")
        )
        labels[speaker_id] = label
        payload = {
            "id": speaker_id, "label": label,
            "label_source": "voiceprint" if match else "default",
        }
        if match:
            payload["voiceprint_match"] = match
        speakers.append(payload)
    for segment in segments:
        segment["speaker_label"] = labels.get(segment["speaker"], segment["speaker_label"])
    language = Counter(chunk.get("language", "Unknown") for chunk in chunks).most_common(1)[0][0] if chunks else "Unknown"
    return {
        "text": "".join(chunk.get("text", "") for chunk in chunks), "language": language,
        "duration": round(duration, 3), "timestamp_precision": "word_or_character" if expose_words else "segment",
        "diarization_mode": "single_active_speaker", "speakers": speakers,
        "segments": segments,
    }


def write_asr_exports(job_id: str, result: dict[str, Any], formats: list[str]) -> dict[str, Any]:
    output = settings.jobs_dir / job_id / "output"
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    def add(name: str, content: str, mime: str) -> None:
        path = output / name; path.write_text(content, encoding="utf-8")
        artifacts.append({"name": name, "path": str(path), "mime_type": mime, "size_bytes": path.stat().st_size})
    if "json" in formats:
        payload = {key: value for key, value in result.items() if key != "artifacts"}
        add("transcript.json", json.dumps(payload, ensure_ascii=False, indent=2), "application/json")
    if "txt" in formats:
        add("transcript.txt", "\n".join(f"[{timecode(x['start'])} - {timecode(x['end'])}] {x['speaker_label']}: {x['text']}" for x in result["segments"]), "text/plain")
    if "srt" in formats:
        add("transcript.srt", "\n\n".join(f"{i}\n{timecode(x['start'], ',')} --> {timecode(x['end'], ',')}\n{x['speaker_label']}: {x['text']}" for i, x in enumerate(result["segments"], 1)), "application/x-subrip")
    if "vtt" in formats:
        add("transcript.vtt", "WEBVTT\n\n" + "\n\n".join(
            f"{timecode(x['start'])} --> {timecode(x['end'])}\n"
            f"<v {html.escape(x['speaker_label'], quote=False)}>{x['text']}"
            for x in result["segments"]
        ), "text/vtt")
    result["artifacts"] = artifacts
    return result


def _mock_transcription_examples(requested_language: str | None) -> tuple[str, list[str]]:
    if requested_language == "English":
        return "English", [
            "Welcome to your fully local speech intelligence workstation.",
            "Transcripts, speakers, and timestamps stay inside this project.",
        ]
    return "Chinese", [
        "欢迎使用完全本地化的语音智能工作台。",
        "识别、说话人和时间戳都会保存在当前项目中。",
    ]


def process_job(context: JobContext) -> dict[str, Any]:
    request = context.job["request"]
    compute_device = request.get("compute_device", "gpu")
    asr_model = resolve_asr_model(request.get("model"))
    if asr_model is None:
        raise ValueError("Unknown ASR model in persisted job request")
    installation = model_installation(settings.models_dir, asr_model)
    if not installation["installed"] and not settings.mock_mode:
        raise RuntimeError(
            f"{asr_model['name']} is not installed at revision {asr_model['revision']}"
        )
    source = Path(request["input_path"])
    normalized = context.work_dir / "normalized.wav"
    context.progress(0.04, "decoding_audio")
    audio, rate = _mock_audio(source, normalized) if settings.mock_mode else decode_audio(source, normalized)
    duration = len(audio) / rate
    context.set_input_duration(duration)
    context.progress(0.10, "voice_activity_detection")
    if settings.mock_mode:
        vad = [{"start": 0.0, "end": duration}]
    else:
        vad = run_vad(audio, rate)
    chunks = write_chunks(audio, rate, combine_vad(vad, duration), context.work_dir / "chunks")
    acceleration_enabled = bool(request.get("accelerate_single_task", False))
    hardware_acceleration = resolve_acceleration(acceleration_enabled, compute_device)
    acceleration = resolve_acceleration(
        acceleration_enabled,
        compute_device,
        int(asr_model.get("batch_penalty_steps") or 0),
    )
    target_batch_size = int(acceleration["target_batch_size"])
    alignment_batch_size = int(hardware_acceleration["target_batch_size"])
    diarization_batch_size = (
        GPU_DIARIZATION_BATCH_SIZE
        if _parallel_diarization_enabled(compute_device)
        else min(GPU_DIARIZATION_BATCH_SIZE, alignment_batch_size * 4)
        if acceleration["requested"]
        else 1
    )
    context.progress(0.20, "speaker_diarization")
    voiceprint_matches: dict[str, dict[str, Any]] = {}
    use_voiceprints = bool(request.get("use_voiceprint_library", True) and request.get("diarize"))
    voiceprint_status: dict[str, Any] = {
        "enabled": use_voiceprints, "status": "disabled" if not use_voiceprints else "empty",
        "indexed_samples": 0,
    }
    voiceprint_people: list[dict[str, Any]] = []
    if use_voiceprints:
        try:
            voiceprint_people = list_voiceprint_people()
        except Exception:
            voiceprint_status["status"] = "degraded"
    diarization_executor = None
    diarization_future = None
    if request.get("diarize") and not settings.mock_mode and _parallel_diarization_enabled(compute_device):
        diarization_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr-diarization")
        if voiceprint_people:
            diarization_future = diarization_executor.submit(
                diarize, audio, vad, request.get("speaker_count"), diarization_batch_size,
                voiceprint_people, True,
            )
        else:
            diarization_future = diarization_executor.submit(
                diarize, audio, vad, request.get("speaker_count"), diarization_batch_size,
            )
        diarization = []
    elif request.get("diarize") and not settings.mock_mode:
        if voiceprint_people:
            diarization, voiceprint_matches, match_status = diarize(
                audio, vad, request.get("speaker_count"), batch_size=diarization_batch_size,
                voiceprint_people=voiceprint_people, return_metadata=True,
            )
            voiceprint_status.update(match_status)
        else:
            diarization = diarize(
                audio, vad, request.get("speaker_count"), batch_size=diarization_batch_size,
            )
    elif settings.mock_mode:
        midpoint = duration * 0.52
        diarization = [{"start": 0, "end": midpoint, "speaker": "Speaker_0"}, {"start": midpoint, "end": duration, "speaker": "Speaker_1"}]
    else:
        diarization = [{"start": 0, "end": duration, "speaker": "Speaker_0"}]
    try:
        context.progress(0.32, f"qwen3_asr_{compute_device}")
        if settings.mock_mode:
            mock_language, examples = _mock_transcription_examples(request.get("language"))
            transcribed = {
                "chunks": [
                    {**item, "text": examples[i % len(examples)], "language": mock_language}
                    for i, item in enumerate(chunks)
                ]
            }
        else:
            transcribed = run_model_stage(context, "transcribe", {
                "model_path": str(settings.models_dir / asr_model["name"]),
                "model_id": asr_model["public_id"],
                "chunks": chunks,
                "language": request.get("language"),
                "context": request.get("effective_context", request.get("context", "")),
                "batch_size": target_batch_size,
            }, context.work_dir, compute_device, 0.32)
        transcription_acceleration = transcribed.get("acceleration", {
            "stage": "transcription", "target_batch_size": target_batch_size,
            "effective_batch_size": 1, "fallbacks": [],
        })
        items = transcribed["chunks"]
        language = next((item.get("language") for item in items if item.get("language")), request.get("language", "Unknown"))
        requested_alignment = bool(request.get("align"))
        can_align = language in ALIGNER_LANGUAGES
        internal_speaker_alignment = False
        if can_align and request.get("diarize") and not requested_alignment:
            requested_speakers = request.get("speaker_count")
            if requested_speakers is not None:
                internal_speaker_alignment = requested_speakers > 1
            else:
                if diarization_future is not None:
                    future_result = diarization_future.result()
                    if voiceprint_people:
                        diarization, voiceprint_matches, match_status = future_result
                        voiceprint_status.update(match_status)
                    else:
                        diarization = future_result
                    diarization_future = None
                internal_speaker_alignment = len({item["speaker"] for item in diarization}) > 1
        should_align = can_align and (requested_alignment or internal_speaker_alignment)
        alignment_acceleration = None
        context.progress(0.68, f"qwen3_forced_alignment_{compute_device}" if should_align else "building_segment_timestamps")
        if should_align and settings.mock_mode:
            for item in items:
                text = item["text"]
                units = text.split() if item.get("language") == "English" else list(text)
                step = (item["end"] - item["start"]) / max(len(units), 1)
                item["words"] = [{"text": unit, "start": round(item["start"] + i * step, 3), "end": round(item["start"] + (i + 1) * step, 3)} for i, unit in enumerate(units)]
        elif should_align:
            aligned = run_model_stage(context, "align", {
                "model_path": str(settings.models_dir / "Qwen3-ForcedAligner-0.6B"),
                "chunks": items,
                "batch_size": alignment_batch_size,
            }, context.work_dir, compute_device, 0.68)
            items = aligned["chunks"]
            alignment_acceleration = aligned.get("acceleration", {
                "stage": "alignment", "target_batch_size": alignment_batch_size,
                "effective_batch_size": 1, "fallbacks": [],
            })
        else:
            alignment_acceleration = None
        if diarization_future is not None:
            future_result = diarization_future.result()
            if voiceprint_people:
                diarization, voiceprint_matches, match_status = future_result
                voiceprint_status.update(match_status)
            else:
                diarization = future_result
    finally:
        if diarization_executor is not None:
            diarization_executor.shutdown(wait=True)
    context.progress(0.88, "merging_speakers_and_timestamps")
    result = assemble(
        items,
        diarization,
        duration,
        should_align,
        expose_words=requested_alignment and should_align,
        voiceprint_matches=voiceprint_matches,
    )
    result.update({
        "model": asr_model["public_id"],
        "model_name": asr_model["name"],
        "model_revision": asr_model["revision"],
        "compute_device": compute_device,
        "compute_device_name": compute_device_name(compute_device, request.get("compute_device_name")),
        "precision": "FP32" if compute_device == "cpu" else "BF16",
        "quantized": False,
        "voiceprint_library": voiceprint_status,
        "hotword_context": {
            "enabled": bool(request.get("hotword_list_ids")),
            "list_ids": list(request.get("hotword_list_ids") or []),
            "list_names": [item.get("name") for item in request.get("hotword_lists") or []],
            "term_count": int(request.get("hotword_term_count") or 0),
        },
        "acceleration": {
            **acceleration,
            "active": bool(
                acceleration["requested"]
                and (
                    transcription_acceleration["effective_batch_size"] > 1
                    or alignment_acceleration is not None
                    and alignment_acceleration["effective_batch_size"] > 1
                    or diarization_batch_size > 1 and bool(request.get("diarize"))
                )
            ),
            "stage_batch_sizes": {
                "transcription": transcription_acceleration["effective_batch_size"],
                "alignment": alignment_acceleration["effective_batch_size"] if alignment_acceleration else 1,
                "diarization": diarization_batch_size if request.get("diarize") else 1,
            },
            "stage_target_batch_sizes": {
                "transcription": target_batch_size,
                "alignment": alignment_batch_size,
                "diarization": diarization_batch_size if request.get("diarize") else 1,
            },
            "oom_fallbacks": [
                {"stage": stage["stage"], **fallback}
                for stage in (transcription_acceleration, alignment_acceleration)
                if stage is not None
                for fallback in stage.get("fallbacks", [])
            ],
        },
    })
    try:
        result["waveform"] = waveform_peaks(audio, 240)
    except Exception:
        result["waveform"] = []
    context.progress(0.96, "writing_exports")
    result = write_asr_exports(context.job_id, result, request.get("export_formats", ["json", "srt", "vtt", "txt"]))
    if request.get("purpose") == "voiceprint_import":
        _finalize_voiceprint_import(request, normalized, result)
    elif request.get("purpose") == "tts_clone_reference":
        target = context.output_dir / "reference.wav"
        shutil.copy2(normalized, target)
        result.setdefault("artifacts", []).append({
            "name": target.name, "path": str(target), "mime_type": "audio/wav",
            "size_bytes": target.stat().st_size,
        })
    return result


def _finalize_voiceprint_import(
    request: dict[str, Any], normalized: Path, result: dict[str, Any],
) -> None:
    sample = get_voiceprint_sample(request.get("voiceprint_sample_id", ""))
    if sample is None:
        raise ValueError("Voiceprint sample was deleted before import completed")
    target = settings.voiceprints_dir / sample["person_id"] / f"{sample['id']}.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".partial.wav")
    try:
        shutil.copy2(normalized, temporary)
        os.replace(temporary, target)
        vector = None
        if not settings.mock_mode:
            from funasr import AutoModel

            model = AutoModel(
                model=str(settings.models_dir / "CAM++"), device="cpu",
                disable_update=True, disable_pbar=True,
            )
            vector = _extract_embedding(model, str(target))
        words = [
            {"text": word.get("text", ""), "start": word["start"], "end": word["end"]}
            for segment in result.get("segments", []) for word in segment.get("words", [])
        ]
        values = dict(
            state="ready", language=result.get("language") or request.get("language") or "Auto",
            audio_path=str(target), transcript=result.get("text", ""), words_json=words,
            duration=result.get("duration"), embedding_error=None, error_message=None,
        )
        if vector is not None:
            values.update(
                embedding=vector.astype("float32").tobytes(), embedding_model=VOICEPRINT_EMBEDDING_MODEL,
            )
        update_voiceprint_sample(sample["id"], **values)
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
