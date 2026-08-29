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
    voice_design = "voice_design"


class VoiceprintSampleState(str, Enum):
    pending = "pending"
    ready = "ready"
    failed = "failed"


class HotwordListKind(str, Enum):
    custom = "custom"
    system = "system"


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


class ProgressBasis(str, Enum):
    observed = "observed"
    estimated = "estimated"


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
    id: ComputeDevice = Field(description="计算设备 ID / Compute device ID")
    precision: str = Field(description="该设备使用的模型精度 / Model precision used on this device")
    available: bool = Field(description="当前模型能否在该设备运行 / Whether the current model can run on this device")
    default: bool = Field(description="该模型当前建议的默认设备 / Recommended default device for this model")
    quantized: bool = Field(description="是否使用量化权重 / Whether quantized weights are used")
    minimum_memory_mib: int | None = Field(
        None,
        description="GPU 准入所需的最小总显存；不是当前空闲显存 / Minimum reported total GPU memory for admission, not current free memory",
    )
    total_memory_mib: int | None = Field(
        None,
        description="当前 GPU 报告的总显存 / Total memory reported by the current GPU",
    )
    unavailable_reason_code: str | None = Field(
        None,
        description="设备不可用时的稳定原因代码 / Stable reason code when the device is unavailable",
    )
    unavailable_reason: str | None = Field(
        None,
        description="设备不可用时的可读原因 / Human-readable reason when the device is unavailable",
    )


class AccelerationCapability(PublicModel):
    supported: bool
    default: bool


class TtsControlCapability(PublicModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "instruction_voice_modes": [],
                "instruction_required_voice_modes": [],
                "max_instruction_chars": 1000,
                "speaking_rate_parameter": False,
                "pitch_parameter": False,
                "sampling_parameters": False,
            }
        },
    )

    instruction_voice_modes: list[VoiceMode] = Field(
        description="支持自然语言风格、情绪和韵律指令的音色模式 / Voice modes supporting natural-language style, emotion, and prosody instructions",
    )
    instruction_required_voice_modes: list[VoiceMode] = Field(
        description="必须提供自然语言指令的音色模式 / Voice modes that require a natural-language instruction",
    )
    max_instruction_chars: int = Field(
        description="自然语言指令最大字符数 / Maximum natural-language instruction length",
    )
    speaking_rate_parameter: bool = Field(
        description="公共 API 是否提供独立语速参数 / Whether the public API exposes a dedicated speaking-rate parameter",
    )
    pitch_parameter: bool = Field(
        description="公共 API 是否提供独立音高参数 / Whether the public API exposes a dedicated pitch parameter",
    )
    sampling_parameters: bool = Field(
        description="公共 API 是否开放底层采样参数 / Whether the public API exposes low-level sampling parameters",
    )


class SpeakerCountCapability(PublicModel):
    min: int
    max: int
    default: str


class AsrModelCapability(PublicModel):
    id: str = Field(description="提交接口使用的规范 ASR 模型 ID / Canonical ASR model ID used by submission endpoints")
    name: str = Field(description="ASR 模型显示名称 / ASR model display name")
    revision: str = Field(description="模型清单固定的 revision / Revision pinned by the model manifest")
    installed: bool = Field(description="固定 revision 是否完整安装 / Whether the pinned revision is completely installed")
    installation_state: str = Field(description="模型安装检查状态 / Model installation check state")
    default: bool = Field(description="是否为省略 model 时的默认模型 / Whether this is the default when model is omitted")
    compute_devices: list[ComputeCapability] = Field(
        description="该模型逐设备的实时可用性 / Live per-device availability for this model",
    )


class TtsCheckpointCapability(PublicModel):
    variant: str = Field(description="模型组内的 checkpoint 类型 / Checkpoint variant within the model group")
    name: str = Field(description="模型清单中的实际 checkpoint 名称 / Physical checkpoint name from the manifest")
    revision: str = Field(description="模型清单固定的 revision / Revision pinned by the model manifest")
    installed: bool = Field(description="固定 revision 是否完整安装 / Whether the pinned revision is completely installed")
    installation_state: str = Field(description="模型安装检查状态 / Model installation check state")


class TtsModelCapability(PublicModel):
    id: str = Field(description="提交接口使用的规范 TTS 模型 ID / Canonical TTS model ID used by submission endpoints")
    name: str = Field(description="TTS 模型组显示名称 / TTS model-group display name")
    default: bool = Field(description="是否为省略 model 时的默认模型 / Whether this is the default when model is omitted")
    installed: bool = Field(description="该模型组所需 checkpoint 是否完整安装 / Whether all checkpoints required by this model group are installed")
    installation_state: str = Field(description="模型组聚合安装状态 / Aggregate model-group installation state")
    voice_modes: list[VoiceMode] = Field(description="该模型组支持的音色模式 / Voice modes supported by this model group")
    compute_devices: list[ComputeCapability] = Field(description="该模型逐设备的实时可用性 / Live per-device availability for this model")
    controls: TtsControlCapability = Field(description="该模型的高级控制能力 / Advanced controls supported by this model")
    checkpoints: list[TtsCheckpointCapability] = Field(description="模型组使用的固定 checkpoint / Pinned checkpoints used by the model group")


class HotwordLibraryCapability(PublicModel):
    supported: bool = Field(description="是否支持本地 ASR 热词库 / Whether the local ASR hotword library is supported")
    max_lists: int = Field(description="最多可保存的自定义词表数；系统词表不计入 / Maximum custom lists; system lists are excluded")
    max_terms_per_list: int = Field(description="单个自定义词表最多词条数 / Maximum terms per custom list")
    max_selected_lists: int = Field(description="单次 ASR 最多选择的词表数 / Maximum lists selected for one ASR request")
    max_selected_terms: int = Field(description="单次 ASR 合并后最多唯一词条数 / Maximum unique merged terms for one ASR request")
    max_prompt_chars: int = Field(
        description="自动生成 Vocabulary 段的最大字符数 / Maximum characters in the generated Vocabulary section",
    )
    max_name_chars: int = Field(description="词表名称最大字符数 / Maximum list-name length")
    max_term_chars: int = Field(description="单个热词最大字符数 / Maximum hotword-term length")


class AsrCapability(PublicModel):
    model: str = Field(description="默认模型显示名称的兼容字段 / Compatibility field containing the default model display name")
    default_model: str = Field(description="省略 model 时使用的规范模型 ID / Canonical model ID used when model is omitted")
    models: list[AsrModelCapability] = Field(description="所有可提交 ASR 模型及逐设备能力 / All submit-capable ASR models and per-device capabilities")
    diarization: str
    speaker_count: SpeakerCountCapability
    voiceprint_library: bool
    languages: list[str]
    default_language: str
    timestamp_precisions: list[str]
    aligner_languages: list[str]
    exports: list[str]
    compute_devices: list[ComputeCapability] = Field(
        description="默认 ASR 模型的设备能力兼容视图；新客户端使用 models[].compute_devices / Compatibility view for the default ASR model; new clients should use models[].compute_devices",
    )
    single_task_acceleration: AccelerationCapability
    hotword_library: HotwordLibraryCapability = Field(
        description="本地场景热词库的支持状态与提交限制 / Support status and submission limits for the local scenario hotword library",
    )


class TtsCapability(PublicModel):
    models: list[str] = Field(
        description="旧客户端兼容的实际 checkpoint 名称列表；新客户端使用 model_capabilities[] / Physical checkpoint names retained for compatibility; new clients should use model_capabilities[]",
    )
    default_model: str = Field(
        description="省略 model 时使用的规范公共模型 ID / Canonical public model ID used when model is omitted",
    )
    model_capabilities: list[TtsModelCapability] = Field(
        description="按规范模型 ID 提供的权威设备、音色模式、控制和 checkpoint 能力 / Authoritative device, voice-mode, control, and checkpoint capabilities by canonical model ID",
    )
    voice_modes: list[VoiceMode] = Field(
        description="所有模型组音色模式的兼容并集；按所选模型判断时使用 model_capabilities[].voice_modes / Compatibility union across model groups; use model_capabilities[].voice_modes for the selected model",
    )
    preset_speakers: list[str]
    preset_speaker_native_languages: dict[str, str]
    languages: list[str]
    default_language: str
    formats: list[str]
    compute_devices: list[ComputeCapability] = Field(
        description="默认 0.6B 模型的设备能力兼容视图；新客户端使用 model_capabilities[].compute_devices / Compatibility view for the default 0.6B model; new clients should use model_capabilities[].compute_devices",
    )
    single_task_acceleration: AccelerationCapability
    controls: TtsControlCapability = Field(
        description="默认 0.6B 模型的控制能力兼容视图；新客户端使用 model_capabilities[].controls / Compatibility control view for the default 0.6B model; new clients should use model_capabilities[].controls",
    )


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
    note: str | None = None
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
    requested: bool = Field(description="本次任务是否请求单任务加速 / Whether single-task acceleration was requested")
    active: bool = Field(description="至少一个阶段是否实际使用大于 1 的批次 / Whether any stage actually used a batch larger than 1")
    device: ComputeDevice = Field(description="计算批次档位时使用的设备 / Device used to resolve the batch tier")
    target_batch_size: int = Field(description="应用模型保守降档后的任务目标批次 / Job target batch after model-specific conservative penalties")
    stage_target_batch_sizes: dict[str, int] | None = Field(
        None,
        description="各阶段在 OOM 回退前的目标批次；仅在流水线阶段目标不同时返回 / Per-stage targets before OOM fallback; returned only when pipeline stages have different targets",
    )
    stage_batch_sizes: dict[str, int] = Field(description="各阶段最终使用的有效批次 / Effective batch size used by each stage")
    batch_penalty_steps: int = Field(
        0,
        description="模型在硬件档位基础上保守降低的批次档位数 / Number of conservative batch-tier reductions applied for the model",
    )
    gpu_memory_total_mib: int | None = Field(
        None,
        description="GPU 分档时报告的总显存 MiB；不是当前空闲显存 / Reported total GPU memory in MiB used for tiering, not current free memory",
    )
    physical_cores: int | None = Field(
        None,
        description="CPU 分档时检测到的物理核心数 / Physical CPU cores detected for tiering",
    )
    available_memory_bytes: int | None = Field(
        None,
        description="CPU 分档时检测到的可用内存字节数 / Available memory bytes detected for CPU tiering",
    )
    oom_fallbacks: list[OomFallback] = Field(description="按发生顺序记录的 OOM 降批重试 / OOM batch-reduction retries in occurrence order")


class HotwordContextResponse(PublicModel):
    enabled: bool = Field(
        description="本次任务是否选择了已保存词表；不表示是否传入一次性 context/prompt / Whether stored lists were selected; does not indicate a one-off context or prompt",
    )
    list_ids: list[str] = Field(description="提交时保存的词表 ID 快照 / Hotword-list ID snapshot stored at submission")
    list_names: list[str] = Field(description="提交时保存的词表名称快照 / Hotword-list name snapshot stored at submission")
    term_count: int = Field(description="合并去重后的热词数 / Number of unique merged hotword terms")


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
    model: str | None = Field(None, description="执行任务的规范模型 ID / Canonical model ID used for execution")
    model_name: str | None = Field(None, description="执行任务的模型显示名称 / Display name of the model used for execution")
    model_revision: str | None = Field(None, description="执行任务的固定模型 revision / Pinned model revision used for execution")
    instruct: str | None = Field(None, description="TTS 使用的自然语言控制指令快照 / Natural-language control instruction snapshot used by TTS")
    hotword_context: HotwordContextResponse | None = Field(
        None,
        description="ASR 任务使用的已保存热词表快照摘要 / Summary of stored hotword-list snapshots used by an ASR job",
    )
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


class JobProgressActivity(PublicModel):
    sequence: int = Field(ge=1, description="当前阶段内推理调用序号；OOM 重试会递增 / Inference-call sequence within the stage; increments on OOM retry")
    current: int = Field(ge=0, description="已观测的当前模型活动量 / Observed model activity count")
    total: int | None = Field(default=None, ge=1, description="预计或已知的当前调用总量 / Estimated or known total for the current call")
    unit: str = Field(description="model_load、codec_frame、output_token 或 model_layer / Model-load, codec-frame, output-token, or model-layer activity")
    basis: ProgressBasis = Field(description="total 及其比例是实测还是估算 / Whether total and its ratio are observed or estimated")
    updated_at: str = Field(description="活动计数更新时间 / Activity counter update time")


class JobProgressDetail(PublicModel):
    stage_code: str = Field(description="稳定但可扩展的阶段代码 / Stable but extensible stage code")
    stage_progress: float | None = Field(default=None, ge=0, le=1)
    basis: ProgressBasis = Field(ProgressBasis.observed, description="阶段百分比是确认值还是最佳估算 / Whether stage percentage is confirmed or best-effort")
    current: int | None = Field(default=None, description="已确认完成的阶段单元数 / Confirmed completed stage units")
    total: int | None = Field(default=None, description="阶段单元总数 / Total stage units")
    unit: str | None = Field(default=None, description="text_chunk、audio_chunk 或其他可扩展单位 / Extensible unit such as text_chunk or audio_chunk")
    activity: JobProgressActivity | None = Field(default=None, description="当前推理调用的细粒度模型活动 / Fine-grained activity for the current inference call")


class JobEstimate(PublicModel):
    state: EstimateState = Field(description="warming_up 表示历史样本不足；ready 表示区间可用 / warming_up means insufficient history; ready means ranges are available")
    confidence: EstimateConfidence | None = Field(default=None, description="本机历史估计置信度；不是 SLA / Local-history confidence; never an SLA")
    sample_count: int = Field(description="参与估计或热身统计的本机历史样本数 / Local historical sample count")
    start_after_seconds: QueueRange | None = Field(default=None, description="预计开始处理前的秒数区间 / Estimated seconds before processing starts")
    remaining_seconds: QueueRange | None = Field(default=None, description="预计完成前的总剩余秒数区间 / Estimated total seconds until completion")
    completes_at: CompletionRange | None = Field(default=None, description="预计完成时间区间 / Estimated completion-time range")
    updated_at: str = Field(description="估计基准时间 / Estimate reference time")


class JobSummaryResponse(PublicModel):
    id: str
    kind: JobKind
    state: JobState
    progress: float = Field(ge=0, le=1, description="单调的最佳任务进度 0–1；细粒度阶段可能为估算，查看 progress_detail.basis / Monotonic best-effort progress from 0 to 1; inspect progress_detail.basis for estimated stages")
    stage: str = Field(description="当前流水线阶段 / Current pipeline stage")
    display_name: str
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


class JobResponse(JobSummaryResponse):
    request: dict[str, Any]
    result: JobResultResponse | None = None


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
    items: list[JobSummaryResponse] = Field(description="当前分页中的任务摘要，按创建时间稳定倒序；完整 request/result 仅由单任务接口返回 / Job summaries in the current page, stably ordered newest-first; full request/result are returned only by the per-job endpoint")
    count: int = Field(description="本页返回数量，不是全库总数 / Number returned on this page, not a total")
    total: int = Field(ge=0, description="当前筛选条件下的任务总数 / Total jobs matching the current filters")
    limit: int = Field(ge=1, le=500, description="当前每页上限 / Current page-size limit")
    offset: int = Field(ge=0, description="当前分页偏移 / Current page offset")
    has_more: bool = Field(description="当前页后是否还有匹配任务 / Whether more matching jobs follow this page")


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
    note: str | None = Field(None, description="人员备注，最多 20 字 / Optional person note, maximum 20 characters")
    include_in_hotword_library: bool = Field(description="名字是否同步到系统人名热词表 / Whether the name is synchronized to the system name hotword list")
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


class HotwordListResponse(PublicModel):
    id: str
    name: str
    kind: HotwordListKind = Field(description="词表来源；system 词表只读 / List source; system lists are read-only")
    terms: list[str] = Field(description="自定义词表按首次出现顺序保存；系统人名词表按规范化名称排序 / Custom terms preserve first-occurrence order; system person names use normalized-name order")
    term_count: int = Field(description="当前词表中的热词数量 / Number of terms in the current list")
    created_at: str
    updated_at: str


class HotwordListsResponse(PublicModel):
    items: list[HotwordListResponse]
    count: int


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
    jobs: list[JobSummaryResponse]
    workers: list[WorkerResponse]


class EventUpdate(PublicModel):
    jobs: list[JobSummaryResponse]
    removed_job_ids: list[str]
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
        json_schema_extra={"enum": ["qwen3-tts-0.6b", "qwen3-tts-1.7b"]},
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
    instructions: str = Field(
        "",
        description="1.7B 预置音色的可选自然语言风格、韵律和情绪指令；其它组合必须为空 / Optional natural-language style, prosody, and emotion instruction for a 1.7B preset voice; must be empty for other combinations",
        json_schema_extra={"maxLength": 1000},
    )
    compute_device: str = Field(
        "gpu", description="计算设备；GPU 不可用时返回 503 / Compute device; unavailable GPU returns 503",
        json_schema_extra={"enum": ["gpu", "cpu"]},
    )
    accelerate_single_task: bool = Field(
        True, description="启用质量中性的单任务自动批处理 / Enable quality-neutral single-job auto-batching",
    )
