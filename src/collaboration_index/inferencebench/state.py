"""Trusted call accounting and development feedback for one shared GPU attempt."""

from typing import Any

from inspect_ai.util import StoreModel
from pydantic import BaseModel, Field


class WorkspaceCall(BaseModel):
    """Record one fixed-actor invocation and its serialized workspace interval."""

    id: str
    actor: str
    tool: str
    requested: str
    started: str | None = None
    completed: str | None = None
    status: str = "queued"
    gpus: list[int] = Field(default_factory=list)
    oom: str | None = None


class DevelopmentCheck(BaseModel):
    """Retain development measurements separately from the authoritative final score."""

    id: str
    started: str
    completed: str | None = None
    status: str = "running"
    metrics: dict[str, Any] | None = None
    error_type: str | None = None
    actor: str | None = None
    slot: int | None = None
    port: int | None = None
    quick: bool | None = None
    proxy_speedup: float | None = None
    contended: bool | None = None
    concurrent_commands: int | None = None


class InferenceHistory(StoreModel):
    """Keep sample-local workspace calls, development checks and final outcomes."""

    calls: list[WorkspaceCall] = Field(default_factory=list)
    checks: list[DevelopmentCheck] = Field(default_factory=list)
    final: dict[str, Any] = Field(default_factory=dict)
    # lease, GPU, submission and failure measurements of the leased-GPU mode
    orchestration: dict[str, Any] = Field(default_factory=dict)
