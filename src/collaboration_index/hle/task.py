"""The hle collaboration task with YAML-owned experiment defaults."""

from typing import Any

from inspect_ai import Task, task
from inspect_ai.dataset import Sample

from collaboration_index.hle.dataset import exam_id, get_questions
from collaboration_index.task import defaults, make_task

CONFIG = defaults("hle")
ARGS = CONFIG["task"]["args"]


@task
def hle_collaboration(
    title: str = ARGS["title"],
    agents: int = ARGS["agents"],
    condition: str = ARGS["condition"],
    seed: int = ARGS["seed"],
    token_limit_per_agent: int | None = ARGS["token_limit_per_agent"],
    team_time_limit: float | None = ARGS["team_time_limit"],
    agent: str = ARGS["agent"],
    agent_args: dict[str, Any] = ARGS["agent_args"],
    artifact_dir: str = ARGS["artifact_dir"],
    sandbox_enabled: bool = ARGS["sandbox_enabled"],
    sandbox_type: str = ARGS["sandbox_type"],
    compaction_threshold: float = ARGS["compaction_threshold"],
    context_window: int | None = ARGS["context_window"],
    records_file: str | None = ARGS["records_file"],
    dataset_revision: str = ARGS["dataset_revision"],
    verified_revision: str = ARGS["verified_revision"],
    gold_only: bool = ARGS["gold_only"],
    question_limit: int | None = ARGS["question_limit"],
    answer_characters: int = ARGS["answer_characters"],
    answer_judge: str = ARGS["answer_judge"],
    max_grader_attempts: int = ARGS["max_grader_attempts"],
) -> Task:
    """Build one team attempt with private peers, a common board and shared sandbox."""
    if answer_characters < 1:
        raise ValueError("answer_characters must be positive")
    if answer_judge == "exact" and records_file is None:
        raise ValueError("Exact matching is a fixture judge, not HLE grading")
    questions = get_questions(
        records_file, dataset_revision, verified_revision, gold_only, question_limit
    )
    sample = Sample(
        id=exam_id(questions),
        input=f"Complete the exam of {len(questions)} questions in the shared read-only questions.json file. Every question has a question_number. Use read_file to read that file and submit_answer(question_number, answer) to record final answers. The first accepted answer is irreversible. Correctness is not revealed until scoring.",
        metadata={
            "data": {"questions": questions, "answer_characters": answer_characters},
            "seed": seed,
            "dataset_revision": dataset_revision,
            "verified_revision": verified_revision,
            "gold_only": gold_only,
            "records_source": "local_fixture" if records_file else "cais/hle",
        },
    )
    return make_task(
        "hle",
        sample,
        CONFIG,
        agents,
        condition,
        token_limit_per_agent,
        team_time_limit,
        agent,
        agent_args,
        artifact_dir,
        sandbox_enabled,
        sandbox_type,
        answer_judge,
        max_grader_attempts,
        compaction_threshold,
        context_window=context_window,
        title=title,
    )
