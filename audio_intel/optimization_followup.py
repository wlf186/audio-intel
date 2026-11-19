"""Durable requests to optimize a newly submitted transcript."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import db


class ExpectedService(BaseModel):
    """Match the displayed service when a resumable upload takes time."""
    model_config = ConfigDict(extra="forbid", strict=True)
    base_url: str = Field(max_length=4096)
    model: str = Field(max_length=1024)


class OptimizationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rewrite_mode: Literal["polish", "rewrite", "editorial"] = "rewrite"
    analyze_topics: bool = False
    expected_service: ExpectedService | None = None


SERVICE_CHANGED = "优化服务已停用或变更，请确认配置后启动优化 / Service disabled or changed; confirm settings before starting optimization"


def migrate(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS optimization_followups (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        options TEXT NOT NULL, base_url TEXT, model TEXT, error TEXT, retry_at REAL NOT NULL DEFAULT 0
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_optimization_followups_ready ON optimization_followups(retry_at) WHERE error IS NULL")
    connection.execute("UPDATE schema_meta SET version=28 WHERE version<28")


def schedule(connection, job_id, request):
    options = request.get("optimization_after_transcription")
    if options is None:
        return
    row = connection.execute("SELECT value_json FROM optimization_settings WHERE singleton=1").fetchone()
    settings = json.loads(row[0]) if row else {}
    expected = options.get("expected_service")
    matched = settings.get("enabled") and (expected is None or all(settings.get(k) == v for k, v in expected.items()))
    workflow = {k: options[k] for k in ("rewrite_mode", "analyze_topics")}
    connection.execute("INSERT INTO optimization_followups(job_id,options,base_url,model,error) VALUES(?,?,?,?,?)",
                       (job_id, json.dumps(workflow), settings.get("base_url"), settings.get("model"),
                        None if matched else SERVICE_CHANGED))


def status(job_id):
    with db.connect() as connection:
        row = connection.execute("SELECT error FROM optimization_followups WHERE job_id=?", (job_id,)).fetchone()
    return {"error": row[0]} if row else None
