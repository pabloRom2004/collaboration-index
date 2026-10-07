"""Trusted submission records and agent measurements for one team attempt."""

from datetime import UTC, datetime
from typing import Any

from inspect_ai.util import StoreModel
from pydantic import BaseModel, Field


def now() -> str:
    """Return a UTC timestamp suitable for logs and board replay."""
    return datetime.now(UTC).isoformat()


class Submission(BaseModel):
    """Record an accepted irreversible action with its trusted actor and arrival order."""

    number: int
    actor: str
    value: str | int
    time: str
    question_number: int | None = None


class Peer(BaseModel):
    """Retain a peer's private execution status and subject-model token usage."""

    id: str
    sandbox_hostname: str | None = None
    status: str = "ready"
    started: str | None = None
    completed: str | None = None
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    tool_calls: int = 0


class Judgment(BaseModel):
    """Preserve correctness separately from missing or failed grading."""

    question_number: int
    correct: bool | None
    reason: str | None = None


class TeamHistory(StoreModel):
    """Provide the authoritative sample store for scoring and structural replay."""

    initialized: bool = False
    benchmark: str = ""
    run_id: str = ""
    condition: str = ""
    artifact_dir: str = ""
    released: str | None = None
    completed: str | None = None
    objective_completed: str | None = None
    end_reason: str | None = None
    submissions: list[Submission] = Field(default_factory=list)
    peers: list[Peer] = Field(default_factory=list)
    judgments: list[Judgment] = Field(default_factory=list)
    duplicate_submissions: int = 0
    rejected_submissions: int = 0
    public_file_hash: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
