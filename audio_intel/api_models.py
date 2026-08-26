from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PublicModel(BaseModel):
    """Public wire models keep forward-compatible fields without hiding existing data."""

    model_config = ConfigDict(extra="allow")


class JobKind(str, Enum):
    asr = "asr"
    tts = "tts"


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class ComputeDevice(str, Enum):
    cpu = "cpu"
    gpu = "gpu"


class VoiceMode(str, Enum):
    preset = "preset"
    profile = "profile"
    inline_clone = "inline_clone"
    voiceprint = "voiceprint"


class VoiceprintSampleState(str, Enum):
    pending = "pending"
    ready = "ready"
    failed = "failed"


class ProblemDetail(PublicModel):
    type: str = Field("about:blank", description="问题类型 URI / Problem type URI")
    title: str = Field(description="简短错误标题 / Short error title")
    status: int = Field(description="HTTP 状态码 / HTTP status code")
    code: str = Field(description="稳定错误代码 / Stable error code")
    detail: str = Field(description="可供开发者阅读的详情 / Developer-readable detail")


class AuthSessionResponse(PublicModel):
    required: bool = Field(description="服务是否要求鉴权 / Whether authentication is required")
    authenticated: bool = Field(description="当前请求是否已认证 / Whether this client is authenticated")


class HealthResponse(PublicModel):
    status: str
    version: str
    offline: bool = Field(description="模型运行时是否强制离线 / Whether model runtime is offline-only")


class ComputeCapability(PublicModel):
    id: ComputeDevice
    precision: str
    available: bool
    default: bool
    quantized: bool


class AccelerationCapability(PublicModel):
    supported: bool
    default: bool


class SpeakerCountCapability(PublicModel):
    min: int
    max: int
    default: str


class AsrCapability(PublicModel):
    model: str
    diarization: str
    speaker_count: SpeakerCountCapability
    voiceprint_library: bool
    timestamp_precisions: list[str]
    aligner_languages: list[str]
    exports: list[str]
    compute_devices: list[ComputeCapability]
    single_task_acceleration: AccelerationCapability


class TtsCapability(PublicModel):
    models: list[str]
    voice_modes: list[VoiceMode]
    preset_speakers: list[str]
    formats: list[str]
    compute_devices: list[ComputeCapability]
    single_task_acceleration: AccelerationCapability


class ApiLimits(PublicModel):
    max_upload_bytes: int
    max_tts_chars: int
    max_clone_reference_seconds: int


class CapabilitiesResponse(PublicModel):
    services: list[JobKind]
    offline: bool
    asr: AsrCapability
    tts: TtsCapability
    limits: ApiLimits


class WorkerResponse(PublicModel):
    id: str
    kind: JobKind
    pid: int
    state: str
    current_job_id: str | None = None
    details: dict[str, Any] | None = None
    heartbeat_at: str


class GpuSnapshot(PublicModel):
    name: str
    memory_used_mib: int | float
    memory_total_mib: int | float
    utilization: int | float


class HardwareSnapshot(PublicModel):
    cpu_percent: float | None = None
    memory_used: int | None = None
    memory_total: int | None = None
    disk_used: int | None = None
    disk_total: int | None = None
    gpu: GpuSnapshot | None = None


class ModelInstallation(PublicModel):
    name: str
    device: str
    installed: bool
    state: str
    revision: str
    actual_revision: str | None = None
    missing_files: list[str]
    path: str


class SystemResponse(PublicModel):
    status: str
    version: str
    offline: bool
    bind: str
    services: list[JobKind]
    workers: list[WorkerResponse]
    hardware: HardwareSnapshot
    models: list[ModelInstallation]
    storage: dict[str, str]


class ArtifactResponse(PublicModel):
    name: str
    path: str
    mime_type: str
    size_bytes: int


class WordResponse(PublicModel):
    text: str
    start: float
    end: float
    speaker: str | None = None


class VoiceprintMatch(PublicModel):
    person_id: str
    name: str
    score: float


class SpeakerResponse(PublicModel):
    id: str
    label: str
    label_source: str | None = None
    voiceprint_match: VoiceprintMatch | None = None


class SegmentResponse(PublicModel):
    id: int
    start: float
    end: float
    speaker: str
    speaker_label: str
    text: str
    words: list[WordResponse] = Field(default_factory=list)


class OomFallback(PublicModel):
    stage: str
    from_: int = Field(alias="from", serialization_alias="from")
    to: int


class AccelerationResponse(PublicModel):
    requested: bool
    active: bool
    device: ComputeDevice
    target_batch_size: int
    stage_batch_sizes: dict[str, int]
    oom_fallbacks: list[OomFallback]


class JobResultResponse(PublicModel):
    text: str | None = None
    language: str | None = None
    duration: float | None = None
    timestamp_precision: str | None = None
    segments: list[SegmentResponse] | None = None
    speakers: list[SpeakerResponse] | None = None
    waveform: list[float] | None = None
    artifacts: list[ArtifactResponse] | None = None
    speaker: str | None = None
    format: str | None = None
    sample_rate: int | None = None
    voice_mode: VoiceMode | None = None
    compute_device: ComputeDevice | None = None
    compute_device_name: str | None = None
    precision: str | None = None
    quantized: bool | None = None
    voiceprint_person_id: str | None = None
    voiceprint_sample_id: str | None = None
    reference_duration_original: float | None = None
    reference_duration_used: float | None = None
    reference_truncated: bool | None = None
    acceleration: AccelerationResponse | None = None


class JobResponse(PublicModel):
    id: str
    kind: JobKind
    state: JobState
    progress: float = Field(ge=0, le=1, description="阶段性进度 0–1；不是剩余时间承诺 / Stage progress from 0 to 1; not an ETA")
    stage: str = Field(description="当前流水线阶段 / Current pipeline stage")
    display_name: str
    request: dict[str, Any]
    result: JobResultResponse | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancel_requested: bool = False
    attempts: int = 0
    worker_id: str | None = None
    heartbeat_at: str | None = None
    processing_seconds: float = 0
    processing_as_of: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str | None = None
    compute_device: ComputeDevice
    compute_device_name: str
    status_url: str | None = None
    source_url: str | None = None
    result_url: str | None = None


class JobListResponse(PublicModel):
    items: list[JobResponse]
    count: int = Field(description="本页返回数量，不是全库总数 / Number returned on this page, not a total")
    limit: int
    offset: int


class EventJobResponse(JobResponse):
    """SSE emits database snapshots rather than the computed public polling view."""

    compute_device: ComputeDevice | None = None
    compute_device_name: str | None = None


class VoiceProfileResponse(PublicModel):
    id: str
    name: str
    language: str
    ref_audio_path: str
    ref_text: str
    sample_id: str
    words: list[WordResponse] = Field(default_factory=list)
    duration: float | None = None
    created_at: str
    updated_at: str


class VoiceListResponse(PublicModel):
    items: list[VoiceProfileResponse]
    preset_speakers: list[str]


class VoiceprintSampleResponse(PublicModel):
    id: str
    person_id: str
    state: VoiceprintSampleState
    language: str
    transcript: str | None = None
    words: list[WordResponse] = Field(default_factory=list)
    duration: float | None = None
    source_job_id: str | None = None
    source_segment_id: int | None = None
    source_speaker_id: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    tts_eligible: bool
    embedding_status: str
    audio_url: str | None = None


class VoiceprintPersonResponse(PublicModel):
    id: str
    name: str
    created_at: str
    updated_at: str
    sample_count: int
    samples: list[VoiceprintSampleResponse]


class VoiceprintPeopleResponse(PublicModel):
    items: list[VoiceprintPersonResponse]


class VoiceprintSamplesResponse(PublicModel):
    items: list[VoiceprintSampleResponse]


class VoiceprintUploadResponse(PublicModel):
    sample: VoiceprintSampleResponse
    job: JobResponse


class PurgeDeletedItem(PublicModel):
    id: str
    reclaimed_bytes: int


class PurgeFailedItem(PublicModel):
    id: str
    code: str
    message: str


class BatchDeleteResponse(PublicModel):
    requested_count: int
    deleted_count: int
    failed_count: int
    reclaimed_bytes: int
    database_reclaimed_bytes: int
    database_compacted: bool
    maintenance_error: str | None = None
    deleted: list[PurgeDeletedItem]
    failed: list[PurgeFailedItem]


class EventSnapshot(PublicModel):
    jobs: list[EventJobResponse]
    workers: list[WorkerResponse]


class OpenAIModel(PublicModel):
    id: str
    object: str
    owned_by: str


class OpenAIModelList(PublicModel):
    object: str
    data: list[OpenAIModel]


class OpenAITranscription(PublicModel):
    text: str


class OpenAIVerboseTranscription(PublicModel):
    task: str
    language: str | None = None
    duration: float | None = None
    text: str
    segments: list[SegmentResponse]
