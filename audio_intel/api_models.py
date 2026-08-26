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


class QueueWaitReason(str, Enum):
    worker = "worker"
    gpu = "gpu"


class EstimateState(str, Enum):
    warming_up = "warming_up"
    ready = "ready"


class EstimateConfidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ProblemDetail(PublicModel):
    type: str = Field("about:blank", description="问题类型 URI / Problem type URI")
    title: str = Field(description="简短错误标题 / Short error title")
    status: int = Field(description="HTTP 状态码 / HTTP status code")
    code: str = Field(description="稳定错误代码 / Stable error code")
    detail: str = Field(description="可供开发者阅读的详情 / Developer-readable detail")


class AdmissionQueueDetail(PublicModel):
    kind: JobKind = Field(description="被拒绝的任务类型 / Rejected job kind")
    depth: int = Field(description="当前持久化排队数 / Current durable queued count")
    capacity: int = Field(description="该任务类型的排队上限 / Queue capacity for this job kind")


class AdmissionStorageDetail(PublicModel):
    free_bytes: int = Field(description="数据卷当前可用字节 / Current free bytes on the data volume")
    minimum_free_bytes: int = Field(description="提交后必须保留的最小可用字节 / Minimum free bytes reserved after submission")


class AdmissionProblemDetail(ProblemDetail):
    retry_after_seconds: int = Field(description="建议等待秒数，与 Retry-After 一致 / Suggested delay matching Retry-After")
    queue: AdmissionQueueDetail
    storage: AdmissionStorageDetail


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
    languages: list[str]
    default_language: str
    timestamp_precisions: list[str]
    aligner_languages: list[str]
    exports: list[str]
    compute_devices: list[ComputeCapability]
    single_task_acceleration: AccelerationCapability


class TtsCapability(PublicModel):
    models: list[str]
    voice_modes: list[VoiceMode]
    preset_speakers: list[str]
    preset_speaker_native_languages: dict[str, str]
    languages: list[str]
    default_language: str
    formats: list[str]
    compute_devices: list[ComputeCapability]
    single_task_acceleration: AccelerationCapability


class ApiLimits(PublicModel):
    max_upload_bytes: int
    max_tts_chars: int
    max_clone_reference_seconds: int
    max_queued_asr: int
    max_queued_tts: int
    max_concurrent_submissions: int
    min_free_disk_bytes: int


class CapabilitiesResponse(PublicModel):
    services: list[JobKind]
    offline: bool
    asr: AsrCapability
    tts: TtsCapability
    limits: ApiLimits
    events: dict[str, Any]


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
    reference_job_id: str | None = None
    reference_language: str | None = None
    reference_duration_original: float | None = None
    reference_duration_used: float | None = None
    reference_truncated: bool | None = None
    acceleration: AccelerationResponse | None = None


class QueueRange(PublicModel):
    lower: int | float
    upper: int | float


class CompletionRange(PublicModel):
    earliest: str
    latest: str


class JobQueueStatus(PublicModel):
    scope: JobKind = Field(description="独立排队范围：asr 或 tts / Independent queue scope: asr or tts")
    position: int | None = Field(default=None, description="排队任务从 1 开始的位置；运行中为 null / One-based queued position; null while running")
    depth: int = Field(description="同类持久化排队任务数 / Durable queued jobs of the same kind")
    capacity: int = Field(description="同类任务排队上限 / Queue capacity for this job kind")
    waiting_for: QueueWaitReason | None = Field(default=None, description="当前等待 worker 或全局 GPU 锁 / Currently waiting for a worker or the global GPU lock")


class JobProgressDetail(PublicModel):
    stage_code: str = Field(description="稳定但可扩展的阶段代码 / Stable but extensible stage code")
    stage_progress: float | None = Field(default=None, ge=0, le=1)
    current: int | None = Field(default=None, description="可计数阶段的当前批次 / Current item in a countable stage")
    total: int | None = Field(default=None, description="可计数阶段的总批次 / Total items in a countable stage")
    unit: str | None = Field(default=None, description="当前计数单位，现为 batch / Counting unit, currently batch")


class JobEstimate(PublicModel):
    state: EstimateState = Field(description="warming_up 表示历史样本不足；ready 表示区间可用 / warming_up means insufficient history; ready means ranges are available")
    confidence: EstimateConfidence | None = Field(default=None, description="本机历史估计置信度；不是 SLA / Local-history confidence; never an SLA")
    sample_count: int = Field(description="参与估计或热身统计的本机历史样本数 / Local historical sample count")
    start_after_seconds: QueueRange | None = Field(default=None, description="预计开始处理前的秒数区间 / Estimated seconds before processing starts")
    remaining_seconds: QueueRange | None = Field(default=None, description="预计完成前的总剩余秒数区间 / Estimated total seconds until completion")
    completes_at: CompletionRange | None = Field(default=None, description="预计完成时间区间 / Estimated completion-time range")
    updated_at: str = Field(description="估计基准时间 / Estimate reference time")


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
    queue: JobQueueStatus | None = None
    progress_detail: JobProgressDetail | None = None
    estimate: JobEstimate | None = None
    poll_after_seconds: int | None = None


class QueueKindResponse(PublicModel):
    kind: JobKind
    queued: int = Field(description="持久化排队任务数 / Durable queued jobs")
    running: int = Field(description="当前运行任务数 / Currently running jobs")
    reserved: int = Field(description="正在接收但尚未持久化的提交数 / Submissions admitted but not yet persisted")
    capacity: int = Field(description="持久化排队上限 / Durable queue capacity")
    accepting: bool = Field(description="当前预检是否允许提交；最终仍以 POST 原子准入为准 / Advisory acceptance; POST admission remains authoritative")
    retry_after_seconds: int | None = Field(default=None, description="不接受时的建议等待秒数 / Suggested delay while not accepting")


class QueueStorageResponse(PublicModel):
    free_bytes: int
    minimum_free_bytes: int


class QueueResponse(PublicModel):
    items: list[QueueKindResponse]
    active_submissions: int
    max_concurrent_submissions: int
    storage: QueueStorageResponse


class JobListResponse(PublicModel):
    items: list[JobResponse]
    count: int = Field(description="本页返回数量，不是全库总数 / Number returned on this page, not a total")
    limit: int
    offset: int


class EventJobResponse(JobResponse):
    """SSE uses the same public job shape while allowing legacy device fields to be absent."""

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


class OpenAISpeechRequest(PublicModel):
    model: str = Field(
        "qwen3-tts-0.6b",
        description="本地兼容模型别名 / Local compatible model alias",
    )
    input: str = Field(description="需要合成的文本 / Text to synthesize")
    voice: str = Field(
        "Vivian",
        description="官方预置音色或 voice_ 声音档案 ID / Official preset or voice_ profile ID",
    )
    response_format: str = Field(
        "wav", description="输出格式 / Output format",
        json_schema_extra={"enum": ["wav", "flac", "mp3"]},
    )
    language: str = Field(
        "Auto",
        description="输出文本语种；已知时应显式指定 / Target text language; specify it when known",
        json_schema_extra={
            "enum": [
                "Auto", "Chinese", "English", "Japanese", "Korean", "German",
                "French", "Russian", "Portuguese", "Spanish", "Italian",
            ]
        },
    )
    instructions: str = Field("", description="预置音色风格指令 / Preset voice style instruction")
    compute_device: str = Field(
        "gpu", description="计算设备；GPU 不可用时返回 503 / Compute device; unavailable GPU returns 503",
        json_schema_extra={"enum": ["gpu", "cpu"]},
    )
    accelerate_single_task: bool = Field(
        True, description="启用质量中性的单任务自动批处理 / Enable quality-neutral single-job auto-batching",
    )
