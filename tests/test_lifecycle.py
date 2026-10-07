"""Check partial work, cancellation, configuration and real shared-sandbox boundaries."""

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.model import ModelOutput, ModelUsage, get_model
from inspect_ai.util import Store

from collaboration_index.counting import counting
from collaboration_index.game import TeamGame
from collaboration_index.hle import hle_collaboration
from collaboration_index.scaffold import create_project
from collaboration_index.smoke import fixture_model
from collaboration_index.state import TeamHistory
from collaboration_index.task import defaults

LOGS = Path(__file__).parents[1] / "logs"


def test_partial_work_preserved_on_budget(tmp_path: Path) -> None:
    """Stop peers at small native limits without inventing completion or discarding state."""
    task = counting(
        target=8,
        token_limit_per_agent=30,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert sample.scores["team_score"].value["quality"] == 0
    assert sample.scores["team_score"].value["completed"] == 0
    assert all(peer["tokens"] == 30 for peer in sample.store["TeamHistory:peers"])
    assert all(
        peer["status"] == "limited" for peer in sample.store["TeamHistory:peers"]
    )


def test_deadline_joins_peers_and_closes_board(tmp_path: Path) -> None:
    """Cancel slow inference after release and finish a scored partial attempt with no orphan board."""

    async def slow(*args: Any) -> ModelOutput:
        """Simulate a harmless in-flight model request beyond the team deadline."""
        await asyncio.sleep(5)
        return ModelOutput.from_content("mockllm/model", "done")

    task = counting(
        target=8,
        token_limit_per_agent=10000,
        team_time_limit=0.05,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task,
        model=get_model("mockllm/model", custom_outputs=slow),
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert sample.store["TeamHistory:end_reason"] == "deadline"
    assert all(
        peer["status"] == "cancelled" for peer in sample.store["TeamHistory:peers"]
    )
    assert sample.scores["team_score"].value["completed"] == 0
    artifact = Path(sample.store["TeamHistory:artifact_dir"])
    assert json.loads((artifact / "board-stopped.json").read_text())[
        "owned_process_stopped"
    ]


def test_missing_answers_are_loss_not_judge_failure(tmp_path: Path) -> None:
    """Grade one wrong answer and one omission without requiring an unused grader model."""
    path = tmp_path / "records.json"
    path.write_text(
        json.dumps(
            [
                {"id": "a", "question": "Q1", "answer": "A1"},
                {"id": "b", "question": "Q2", "answer": "A2"},
            ]
        )
    )
    task = hle_collaboration(
        agents=1,
        token_limit_per_agent=1000,
        records_file=str(path),
        answer_judge="exact",
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    outputs = [
        ModelOutput.for_tool_call(
            "mockllm/model", "submit_answer", {"question_number": 1, "answer": "wrong"}
        ),
        ModelOutput.from_content("mockllm/model", "finished"),
    ]
    for output in outputs:
        output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
    [log] = inspect_eval(
        task,
        model=get_model("mockllm/model", custom_outputs=outputs),
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error
    metrics = log.samples[0].scores["team_score"].value
    assert (
        metrics["quality"] == 0
        and metrics["coverage"] == 0.5
        and metrics["unscored_questions"] == 0
    )


async def test_private_hand_and_allocation_enforced() -> None:
    """Prevent hand spoofing and cross-assignment submission at the trusted tool boundary."""
    from inspect_ai.tool import ToolError

    history = TeamHistory(store=Store(), initialized=True, benchmark="spelling")
    game = TeamGame(
        history,
        {"hands": {"a": ["x"], "b": ["y", "\n"]}, "max_characters": None},
        ["a", "b"],
    )
    with pytest.raises(ToolError, match="private hand"):
        await game.submit("a", "y")
    assert len(history.submissions) == 0
    history = TeamHistory(
        store=Store(), initialized=True, benchmark="hle", condition="oracle_allocation"
    )
    game = TeamGame(history, {"questions": [1, 2], "answer_characters": 10}, ["a", "b"])
    with pytest.raises(ToolError, match="another participant"):
        await game.submit("a", "A", 2)


def test_starter_uses_common_core_and_public_defaults(tmp_path: Path) -> None:
    """Generate a fresh project and execute its task using the installed core without copied plumbing."""
    project = create_project(tmp_path / "new-benchmark")
    assert not (project / "board").exists()
    module_spec = importlib.util.spec_from_file_location(
        "starter_task", project / "task.py"
    )
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    task = module.toy_exam(
        token_limit_per_agent=10000,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path / "artifacts"),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    assert log.samples[0].scores["team_score"].value["quality"] == 1
    with pytest.raises(ValueError, match="new destination"):
        create_project(project)
    for name in ("hle", "counting", "spelling"):
        config = defaults(name)
        assert config["task"]["args"]["sandbox_enabled"]
        assert config["task"]["args"]["token_limit_per_agent"] is None
        assert config["task"]["args"]["compaction_threshold"] == 0.75


@pytest.mark.docker
def test_one_real_sandbox_and_one_log_for_eight_peers(tmp_path: Path) -> None:
    """Prove every native Inspect peer sees the same container and trusted public exam file."""
    path = tmp_path / "records.json"
    path.write_text(
        json.dumps(
            [
                {"id": "a", "question": "Capital of France?", "answer": "Paris"},
                {"id": "b", "question": "2+2?", "answer": "4"},
            ]
        )
    )
    task = hle_collaboration(
        agents=8,
        token_limit_per_agent=10000,
        records_file=str(path),
        answer_judge="exact",
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    assert len(log.samples) == 1
    sample = log.samples[0]
    peers = sample.store["TeamHistory:peers"]
    assert len(peers) == 8
    assert {peer["sandbox_hostname"] for peer in peers} == {
        sample.store["shared_sandbox_hostname"]
    }
    assert sample.scores["team_score"].value["quality"] == 1
    assert sample.scores["team_score"].value["message_count"] == 16
    artifact = Path(sample.store["TeamHistory:artifact_dir"])
    assert not any(
        "answer" in row for row in json.loads((artifact / "questions.json").read_text())
    )


def alternate_agent(**kwargs: Any) -> Any:
    """Provide a compatible interchangeable agent factory for the harness contract."""
    from inspect_ai.agent import react

    return react(**kwargs)


def test_replaceable_agent_keeps_task_state(tmp_path: Path) -> None:
    """Replace the peer factory while keeping the task's invariant board, tools and grader."""
    task = counting(
        agents=2,
        target=4,
        token_limit_per_agent=10000,
        agent="test_lifecycle.alternate_agent",
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert sample.scores["team_score"].value["quality"] == 1
    assert len(sample.store["TeamHistory:submissions"]) == 4
