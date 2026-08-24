#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from rapidfuzz.distance import Levenshtein


ROOT = Path(__file__).resolve().parent.parent
TERMINAL = {"succeeded", "failed", "cancelled"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def chinese_number(value: int) -> str:
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value < 20:
        return "十" + (digits[value % 10] if value % 10 else "")
    return digits[value // 10] + "十" + (digits[value % 10] if value % 10 else "")


def make_story(limit: int = 49000, seed: int = 20810) -> str:
    rng = random.Random(seed)
    characters = ["林澈", "苏岚", "周栖", "沈遥", "顾临", "陆青"]
    places = ["北港旧站", "远灯城东区", "白塔市场", "河岸数据站", "南环维修巷", "中央档案馆"]
    weather = ["细雨敲打着金属雨棚", "夜风穿过高架桥的缝隙", "清晨的雾贴着街面缓慢移动", "远处的雷声压过城市低沉的电流声"]
    objects = ["一枚没有编号的存储片", "一本写满旧地址的纸质笔记", "一把磨损严重的黄铜钥匙", "一段被反复覆盖的录音", "一张褪色的车站通行证"]
    clues = ["失踪列车最后一次发出的信号", "十年前停电事故留下的时间记录", "城北废弃天线重复播放的坐标", "档案中被人为删去的一页名单", "无人认领的广播频率"]
    decisions = ["先保护仍然活着的人", "把事实交给所有市民", "沿着最慢却最可靠的路线继续追查", "相信彼此亲眼看见的证据", "拒绝用新的谎言掩盖旧的错误"]
    chapter_titles = ["雨中的信号", "旧站回声", "沉默档案", "河岸灯火", "白塔来客", "最后一班列车"]
    paragraphs: list[str] = ["《远灯城纪事》", "这是一部长篇本地合成测试小说。故事发生在被高架铁路和潮湿霓虹包围的远灯城。"]
    chapter = 1
    while len("\n\n".join(paragraphs)) < limit:
        hero = characters[(chapter - 1) % len(characters)]
        partner = rng.choice([item for item in characters if item != hero])
        place = rng.choice(places)
        item = rng.choice(objects)
        clue = rng.choice(clues)
        decision = rng.choice(decisions)
        title = chapter_titles[(chapter - 1) % len(chapter_titles)]
        paragraphs.append(f"第{chinese_number((chapter - 1) % 99 + 1)}章，{title}。")
        paragraphs.append(
            f"{rng.choice(weather)}。{hero}在{place}停下脚步，听见公共广播报出一个早已注销的站名。"
            f"他从外套内袋取出{item}，确认上面的划痕与昨夜收到的照片完全一致。"
        )
        paragraphs.append(
            f"{partner}从街角走来，低声提醒他，追踪者已经关闭附近三条道路。"
            f"两人没有立刻离开，而是借着维修灯微弱的光，重新核对与{clue}有关的每一条记录。"
            f"记录中的时间相差七分钟，这个微小差异证明有人修改过城市主时钟。"
        )
        paragraphs.append(
            f"他们穿过拥挤的夜市，避开自动巡逻车，在一间仍使用机械门锁的小店里见到老维修师。"
            f"老人说，真正可靠的记忆从来不在云端，而在人愿意为它承担什么。"
            f"他交出一张手绘线路图，并要求他们答应一件事：无论找到什么，都不能让普通居民成为代价。"
        )
        paragraphs.append(
            f"{hero}沉默片刻，最终决定{decision}。{partner}点亮便携终端，把分散的证据复制到三个离线节点。"
            f"当第一份校验结果返回时，他们发现所有线索都指向同一列不存在于时刻表的列车。"
            f"列车将在午夜经过城北隧道，车上保存着远灯城被遗忘的原始档案。"
        )
        paragraphs.append(
            f"午夜之前，他们还有四个小时。两人沿着河堤向北走，城市的灯在水面上拉成长线。"
            f"{partner}谈起小时候第一次乘车离开旧城区的经历，{hero}则想起父亲留下的那句告诫：速度可以争取时间，方向才能决定归途。"
            f"他们不再回头，因为身后的脚步声已经越来越近。"
        )
        paragraphs.append(
            f"这一夜的行动没有英雄式的掌声，只有门轴、雨水和呼吸的声音。"
            f"但在远灯城数百万盏窗口灯之间，一条由普通人守护的信息链正在悄悄形成。"
            f"新的章节由此开始，而他们仍在通往真相的路上。"
        )
        chapter += 1
    story = "\n\n".join(paragraphs)
    cut = max(story.rfind("。", 0, limit), story.rfind("！", 0, limit), story.rfind("？", 0, limit))
    return story[: cut + 1]


def sentence_prefix(text: str, target: int) -> str:
    target = max(200, min(target, len(text)))
    window_start = max(0, target - 500)
    window_end = min(len(text), target + 500)
    candidates = [window_start + match.start() for match in re.finditer(r"[。！？]", text[window_start:window_end])]
    cut = min(candidates, key=lambda item: abs(item - target)) if candidates else target - 1
    return text[: cut + 1].strip()


def normalized_text(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", value).lower()


def wall_seconds(job: dict[str, Any]) -> float | None:
    if not job.get("started_at") or not job.get("finished_at"):
        return None
    return (datetime.fromisoformat(job["finished_at"]) - datetime.fromisoformat(job["started_at"])).total_seconds()


class MatrixRunner:
    def __init__(self, base_url: str, output_dir: Path, target: float, tolerance: float, calibration_chars: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = output_dir / "manifest.json"
        self.metrics_path = output_dir / "metrics.jsonl"
        self.report_path = output_dir / "report.json"
        self.report_md_path = output_dir / "report.md"
        self.target = target
        self.tolerance = tolerance
        self.calibration_chars = calibration_chars
        self.client = httpx.Client(timeout=None)
        if self.manifest_path.is_file():
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        else:
            self.manifest = {
                "version": 1,
                "created_at": utcnow(),
                "target_duration": target,
                "tolerance": tolerance,
                "phases": {},
            }
        self.manifest["target_duration"] = target
        self.manifest["tolerance"] = tolerance

    def save(self) -> None:
        self.manifest["updated_at"] = utcnow()
        atomic_json(self.manifest_path, self.manifest)

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        while True:
            try:
                response = self.client.request(method, f"{self.base_url}{path}", **kwargs)
                response.raise_for_status()
                return response
            except (httpx.ConnectError, httpx.ReadError) as exc:
                print(f"[network] {exc}; retrying in 5 seconds", flush=True)
                time.sleep(5)

    def record_metric(self, phase: str, job: dict[str, Any]) -> None:
        try:
            health = self.request("GET", "/api/v1/health").json()
        except httpx.HTTPError:
            return
        row = {
            "timestamp": utcnow(),
            "phase": phase,
            "job": {"id": job["id"], "state": job["state"], "stage": job["stage"], "progress": job["progress"]},
            "hardware": health.get("hardware", {}),
        }
        with self.metrics_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")

    def wait(self, phase: str, job_id: str) -> dict[str, Any]:
        last_marker: tuple[Any, ...] | None = None
        next_metric = 0.0
        while True:
            job = self.request("GET", f"/api/v1/jobs/{job_id}").json()
            marker = (job["state"], job["stage"], round(job["progress"] * 100))
            if marker != last_marker:
                print(f"[{phase}] {job_id} {marker}", flush=True)
                last_marker = marker
                self.manifest["phases"][phase].update({"state": job["state"], "stage": job["stage"], "progress": job["progress"]})
                self.save()
            now = time.monotonic()
            if now >= next_metric:
                self.record_metric(phase, job)
                next_metric = now + 10
            if job["state"] in TERMINAL:
                if job["state"] != "succeeded":
                    raise RuntimeError(f"{phase} failed: {job.get('error_message') or job['state']}")
                result = job.get("result") or {}
                self.manifest["phases"][phase].update({
                    "state": "succeeded",
                    "started_at": job.get("started_at"),
                    "finished_at": job.get("finished_at"),
                    "wall_seconds": wall_seconds(job),
                    "duration": result.get("duration"),
                    "compute_device": result.get("compute_device"),
                    "precision": result.get("precision"),
                    "quantized": result.get("quantized"),
                    "artifacts": result.get("artifacts", []),
                })
                self.save()
                return job
            time.sleep(2)

    def phase(self, name: str, submit: Callable[[], str]) -> dict[str, Any]:
        phase = self.manifest["phases"].setdefault(name, {})
        if not phase.get("job_id"):
            phase.update({"job_id": submit(), "submitted_at": utcnow(), "state": "queued"})
            self.save()
        return self.wait(name, phase["job_id"])

    def submit_tts(self, text: str, device: str, display_name: str) -> str:
        response = self.request("POST", "/api/v1/tts/jobs", data={
            "text": text,
            "language": "Chinese",
            "voice_mode": "preset",
            "speaker": "Vivian",
            "response_format": "wav",
            "display_name": display_name,
            "compute_device": device,
        })
        return response.json()["id"]

    def submit_asr(self, audio: Path, device: str) -> str:
        with audio.open("rb") as source:
            response = self.request("POST", "/api/v1/asr/jobs", data={
                "language": "Chinese",
                "speaker_count": "1",
                "diarize": "true",
                "align": "true",
                "export_formats": "json,srt,vtt,txt",
                "compute_device": device,
            }, files={"file": ("two-hour-novel.wav", source, "audio/wav")})
        return response.json()["id"]

    def validate_wav(self, job: dict[str, Any]) -> dict[str, Any]:
        result = job["result"]
        artifact = next(item for item in result["artifacts"] if item["name"].endswith(".wav"))
        path = Path(artifact["path"])
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            width = audio.getsampwidth()
            rate = audio.getframerate()
            frames = audio.getnframes()
        duration = frames / rate
        if channels != 1 or width != 2 or rate != 24000:
            raise RuntimeError(f"Unexpected WAV format: channels={channels}, width={width}, rate={rate}")
        if path.stat().st_size <= 44:
            raise RuntimeError("TTS output is empty")
        return {"path": str(path), "duration": duration, "sample_rate": rate, "channels": channels, "sample_width": width, "size_bytes": path.stat().st_size, "sha256": sha256(path)}

    def run_tts_target(self, device: str, initial_text: str) -> tuple[dict[str, Any], str, dict[str, Any]]:
        text = initial_text
        for attempt in range(1, 3):
            phase_name = f"tts_{device}" if attempt == 1 else f"tts_{device}_retry"
            text_path = self.output_dir / f"novel-{device}{'-retry' if attempt > 1 else ''}.txt"
            if not text_path.is_file():
                text_path.write_text(text, encoding="utf-8")
            else:
                text = text_path.read_text(encoding="utf-8")
            job = self.phase(phase_name, lambda: self.submit_tts(text, device, f"two-hour-novel-{device}"))
            audio = self.validate_wav(job)
            self.manifest["phases"][phase_name].update({"text_path": str(text_path), "text_chars": len(text), "audio": audio})
            elapsed = wall_seconds(job)
            self.manifest["phases"][phase_name]["rtf"] = elapsed / audio["duration"] if elapsed else None
            self.save()
            if abs(audio["duration"] - self.target) <= self.tolerance:
                return job, text, audio
            if attempt == 2:
                raise RuntimeError(f"TTS {device} duration {audio['duration']:.1f}s is outside tolerance")
            source = (self.output_dir / "story-source.txt").read_text(encoding="utf-8")
            adjusted = round(len(text) * self.target / audio["duration"])
            text = sentence_prefix(source, adjusted)
        raise AssertionError("unreachable")

    def validate_asr(self, job: dict[str, Any], reference: str, source_duration: float) -> dict[str, Any]:
        result = job["result"]
        transcript = normalized_text(result.get("text", ""))
        expected = normalized_text(reference)
        cer = Levenshtein.normalized_distance(expected, transcript)
        segments = result.get("segments") or []
        speakers = {item.get("speaker") for item in segments}
        previous = 0.0
        word_count = 0
        for segment in segments:
            start, end = float(segment["start"]), float(segment["end"])
            if start < previous - 0.05 or end < start or end > source_duration + 1:
                raise RuntimeError(f"Invalid segment timestamp: {start}-{end}")
            previous = start
            word_previous = start
            for word in segment.get("words") or []:
                word_start, word_end = float(word["start"]), float(word["end"])
                if word_start < word_previous - 0.05 or word_end < word_start or word_end > source_duration + 1:
                    raise RuntimeError(f"Invalid word timestamp: {word_start}-{word_end}")
                word_previous = word_start
                word_count += 1
        suffixes = {Path(item["name"]).suffix for item in result.get("artifacts", [])}
        if speakers != {"Speaker_0"}:
            raise RuntimeError(f"Expected one speaker, got {sorted(speakers)}")
        if not {".json", ".srt", ".vtt", ".txt"}.issubset(suffixes):
            raise RuntimeError(f"Missing ASR exports: {suffixes}")
        if result.get("timestamp_precision") != "word_or_character" or word_count == 0:
            raise RuntimeError("Word/character alignment is missing")
        if abs(float(result.get("duration", 0)) - source_duration) > 2:
            raise RuntimeError("ASR duration does not match the source audio")
        if cer > 0.05:
            raise RuntimeError(f"CER {cer:.2%} exceeds 5%")
        return {"cer": cer, "characters": len(transcript), "segments": len(segments), "words": word_count, "speakers": sorted(speakers)}

    def metric_summaries(self) -> dict[str, Any]:
        summaries: dict[str, dict[str, float]] = {}
        if not self.metrics_path.is_file():
            return summaries
        for line in self.metrics_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                phase = summaries.setdefault(row["phase"], {})
                hardware = row.get("hardware", {})
                gpu = hardware.get("gpu") or {}
                values = {
                    "peak_cpu_percent": float(hardware.get("cpu_percent") or 0),
                    "peak_memory_used_bytes": float(hardware.get("memory_used") or 0),
                    "peak_gpu_memory_mib": float(gpu.get("memory_used_mib") or 0),
                    "peak_gpu_utilization": float(gpu.get("utilization") or 0),
                }
                for key, value in values.items():
                    phase[key] = max(phase.get(key, 0), value)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return summaries

    def write_report(self) -> dict[str, Any]:
        report = {"generated_at": utcnow(), "target_duration": self.target, "tolerance": self.tolerance, "manifest": str(self.manifest_path), "metrics": self.metric_summaries(), "phases": self.manifest["phases"]}
        atomic_json(self.report_path, report)
        lines = ["# Sandevistan-Audio 两小时设备矩阵", "", f"生成时间：{report['generated_at']}", "", "| 阶段 | 状态 | 设备 | 精度 | 音频时长 | 墙钟时间 | RTF |", "|---|---|---|---|---:|---:|---:|"]
        for name, phase in self.manifest["phases"].items():
            duration = phase.get("audio", {}).get("duration", phase.get("duration", ""))
            lines.append(f"| {name} | {phase.get('state', '')} | {phase.get('compute_device', '')} | {phase.get('precision', '')} | {duration} | {phase.get('wall_seconds', '')} | {phase.get('rtf', '')} |")
        self.report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report

    def run(self) -> None:
        self.manifest.pop("failure", None)
        health = self.request("GET", "/api/v1/health").json()
        if not health.get("hardware", {}).get("gpu"):
            raise RuntimeError("GPU is unavailable")
        story_path = self.output_dir / "story-source.txt"
        if not story_path.is_file():
            story_path.write_text(make_story(), encoding="utf-8")
        story = story_path.read_text(encoding="utf-8")
        self.manifest.update({"story_path": str(story_path), "story_chars": len(story), "story_sha256": sha256(story_path)})
        self.save()
        calibration_path = self.output_dir / "calibration.txt"
        calibration_cpu_phase = self.manifest["phases"].get("calibration_cpu", {})
        if calibration_cpu_phase.get("job_id"):
            calibration_job = self.request("GET", f"/api/v1/jobs/{calibration_cpu_phase['job_id']}").json()
            calibration = str(calibration_job.get("request", {}).get("text") or "")
        elif calibration_path.is_file():
            calibration = calibration_path.read_text(encoding="utf-8")
        else:
            calibration = sentence_prefix(story, self.calibration_chars)
        if not calibration:
            raise RuntimeError("Calibration text is unavailable")
        calibration_path.write_text(calibration, encoding="utf-8")
        calibrations: dict[str, dict[str, Any]] = {}
        for device in ("cpu", "gpu"):
            job = self.phase(f"calibration_{device}", lambda device=device: self.submit_tts(calibration, device, f"calibration-{device}"))
            audio = self.validate_wav(job)
            rate = audio["duration"] / len(calibration)
            calibrations[device] = {"duration": audio["duration"], "seconds_per_character": rate}
            self.manifest["phases"][f"calibration_{device}"].update({"text_chars": len(calibration), "audio": audio, **calibrations[device]})
            self.save()
        lower = max((self.target - self.tolerance) / value["seconds_per_character"] for value in calibrations.values())
        upper = min((self.target + self.tolerance) / value["seconds_per_character"] for value in calibrations.values())
        if lower <= upper:
            target_chars = round((lower + upper) / 2)
            common_text = sentence_prefix(story, target_chars)
            cpu_initial = gpu_initial = common_text
        else:
            cpu_initial = sentence_prefix(story, round(self.target / calibrations["cpu"]["seconds_per_character"]))
            gpu_initial = sentence_prefix(story, round(self.target / calibrations["gpu"]["seconds_per_character"]))
        cpu_job, cpu_text, cpu_audio = self.run_tts_target("cpu", cpu_initial)
        gpu_job, gpu_text, gpu_audio = self.run_tts_target("gpu", gpu_initial)
        if not (abs(cpu_audio["duration"] - self.target) <= self.tolerance and abs(gpu_audio["duration"] - self.target) <= self.tolerance):
            raise RuntimeError("TTS matrix did not pass; ASR phases will not start")
        canonical_audio = Path(cpu_audio["path"])
        asr_results: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for device in ("cpu", "gpu"):
            job = self.phase(f"asr_{device}", lambda device=device: self.submit_asr(canonical_audio, device))
            validation = self.validate_asr(job, cpu_text, cpu_audio["duration"])
            elapsed = wall_seconds(job)
            self.manifest["phases"][f"asr_{device}"].update({"validation": validation, "rtf": elapsed / cpu_audio["duration"] if elapsed else None})
            self.save()
            asr_results[device] = (job, validation)
        cpu_transcript = normalized_text(asr_results["cpu"][0]["result"]["text"])
        gpu_transcript = normalized_text(asr_results["gpu"][0]["result"]["text"])
        divergence = Levenshtein.normalized_distance(cpu_transcript, gpu_transcript)
        if divergence > 0.01:
            raise RuntimeError(f"CPU/GPU ASR divergence {divergence:.2%} exceeds 1%")
        self.manifest["asr_cpu_gpu_divergence"] = divergence
        self.manifest["completed_at"] = utcnow()
        self.save()
        self.write_report()
        print(json.dumps({"status": "passed", "report": str(self.report_md_path), "divergence": divergence}, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run resumable two-hour CPU/GPU TTS and ASR pressure tests.")
    parser.add_argument("--base-url", default="http://127.0.0.1:20810")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/stress/device-matrix-2h")
    parser.add_argument("--target", type=float, default=7200)
    parser.add_argument("--tolerance", type=float, default=120)
    parser.add_argument("--calibration-chars", type=int, default=1200)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    runner = MatrixRunner(args.base_url, args.output_dir.resolve(), args.target, args.tolerance, args.calibration_chars)
    if args.prepare_only:
        story = make_story()
        path = runner.output_dir / "story-source.txt"
        path.write_text(story, encoding="utf-8")
        print(json.dumps({"path": str(path), "characters": len(story), "sha256": sha256(path)}, ensure_ascii=False))
        return
    try:
        runner.run()
    except Exception as exc:
        runner.manifest["failure"] = {"at": utcnow(), "type": type(exc).__name__, "message": str(exc)}
        runner.save()
        runner.write_report()
        raise


if __name__ == "__main__":
    main()
