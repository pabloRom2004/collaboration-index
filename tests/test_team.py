"""Exercise concurrent team lifecycle and scoring through real Inspect mock evaluations."""

import json
import math
from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.tool import ToolError
from inspect_ai.util import Store

from collaboration_index.counting import counting
from collaboration_index.game import TeamGame, distance, read_file
from collaboration_index.hle import hle_collaboration
from collaboration_index.hle.dataset import get_questions
from collaboration_index.replay import replay_data
from collaboration_index.smoke import fixture_model
from collaboration_index.spelling import spelling
from collaboration_index.state import TeamHistory

LOGS = Path(__file__).parents[1] / "logs"


@pytest.mark.parametrize("agents", [1, 2, 4, 8, 16, 32])
def test_counting_team(agents: int, tmp_path: Path) -> None:
    """Run fixed-work counting with each team size and verify trusted costs and board delivery."""
    task = counting(
        agents=agents,
        target=64,
        token_limit_per_agent=10000,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    log = read_eval_log(log.location)
    sample = log.samples[0]
    assert not sample.error
    assert sample.scores["team_score"].value["quality"] == 1
    metrics = sample.scores["team_score"].value
    assert metrics["tokens"] > 0
    assert all(
        p["tokens"] > 0 and p["turns"] > 0 for p in sample.store["TeamHistory:peers"]
    )
    assert metrics["tokens"] == metrics["input_tokens"] + metrics["output_tokens"]
    assert metrics["message_count"] == agents * (2 if agents > 1 else 1)
    assert metrics["global_messages"] == agents
    assert metrics["direct_messages"] == (agents if agents > 1 else 0)
    assert len(sample.store["TeamHistory:peers"]) == agents
    assert len(sample.store["TeamHistory:submissions"]) == 64
    assert len({p["started"] for p in sample.store["TeamHistory:peers"]}) <= agents
    artifact = Path(sample.store["TeamHistory:artifact_dir"])
    assert json.loads((artifact / "board-stopped.json").read_text())[
        "owned_process_stopped"
    ]
    replay = replay_data(Path(log.location), artifact / "board.jsonl")
    assert replay["team"]["quality"] == 1 and len(replay["agents"]) == agents
    assert replay["target"] == {
        "kind": "counting",
        "target": 64,
        "quota": sample.metadata["data"]["quota"],
    }
    assert replay["events"]
    assert replay_data(Path(log.location), None)["events"] == replay["events"]
    assert all(not p["grades"] for p in replay["agents"])


@pytest.mark.parametrize("condition", ["collaborative", "oracle_allocation"])
def test_spelling_team(condition: str, tmp_path: Path) -> None:
    """Preserve private hands and correct ordered spelling in both matched control conditions."""
    task = spelling(
        agents=4,
        condition=condition,
        token_limit_per_agent=10000,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert sample.scores["team_score"].value["quality"] == 1
    assert sample.scores["team_score"].value["completed"] == 1
    data = sample.metadata["data"]
    assert set().union(*(set(h) for h in data["hands"].values())) == set(
        data["dealt_sentence"]
    ) | {"\n"}
    assert sample.store["TeamHistory:condition"] == condition
    target = replay_data(Path(log.location), None)["target"]
    # the deal comes from one shown sentence, so the team can always spell that one
    assert data["dealt_sentence"] in target["feasible"]
    assert set(target["team_characters"]) >= set(data["dealt_sentence"]) | {"\n"}
    assert set(target["feasible"]) <= set(target["sentences"])
    if condition == "oracle_allocation":
        assert sample.scores["team_score"].value["message_count"] == 0


@pytest.fixture
def questions_file(tmp_path: Path) -> str:
    """Provide a tiny authored exam with deliberately distinct public and private fields."""
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps(
            [
                {"id": "one", "question": "Capital of France?", "answer": "Paris"},
                {"id": "two", "question": "2+2?", "answer": "4"},
            ]
        )
    )
    return str(path)


@pytest.mark.parametrize("condition", ["collaborative", "oracle_allocation"])
def test_exam_team(questions_file: str, condition: str, tmp_path: Path) -> None:
    """Write and read the public file, submit answers and retain one authoritative collective grade."""
    task = hle_collaboration(
        agents=2,
        records_file=questions_file,
        answer_judge="exact",
        condition=condition,
        token_limit_per_agent=10000,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    sample = read_eval_log(log.location).samples[0]
    assert sample.scores["team_score"].value["quality"] == 1
    public = json.loads(
        (Path(sample.store["TeamHistory:artifact_dir"]) / "questions.json").read_text()
    )
    assert all(
        set(row) == {"question_number", "id", "question", "answer_type"}
        for row in public
    )
    assert len(sample.store["TeamHistory:judgments"]) == 2


async def test_atomic_duplicate_and_hidden_output() -> None:
    """Commit only one racing answer without leaking the earlier answer or correctness."""
    import asyncio

    history = TeamHistory(
        store=Store(), initialized=True, benchmark="hle", condition="collaborative"
    )
    game = TeamGame(
        history, {"questions": [1, 2], "answer_characters": 100}, ["a", "b"]
    )
    outcomes = await asyncio.gather(
        game.submit("a", "first", 1),
        game.submit("b", "second", 1),
        return_exceptions=True,
    )
    assert sum(isinstance(x, dict) for x in outcomes) == 1
    assert any(isinstance(x, ToolError) for x in outcomes)
    assert len(history.submissions) == 1 and history.duplicate_submissions == 1


async def test_irreversible_wrong_count_and_quota() -> None:
    """Keep a wrong integer in arrival order and reject quota excess without appending."""
    history = TeamHistory(store=Store(), initialized=True, benchmark="counting")
    game = TeamGame(history, {"target": 4, "quota": 2}, ["a", "b"])
    await game.submit("a", 99)
    await game.submit("a", 2)
    with pytest.raises(ToolError):
        await game.submit("a", 3)
    assert [s.value for s in history.submissions] == [99, 2]
    assert distance([99, 2], [1, 2, 3, 4]) == 3


async def test_read_file_boundary() -> None:
    """Reject arbitrary paths and invalid ranges while returning only public question records."""
    reader = read_file('[{"question_number":1,"question":"Q"}]', False)
    with pytest.raises(ToolError):
        await reader(path="../../answer-key.json")
    with pytest.raises(ToolError):
        await reader(path="questions.json", start=0)
    assert json.loads(await reader(path="questions.json"))[0]["question_number"] == 1


def test_question_filter_and_duplicate_validation(tmp_path: Path) -> None:
    """Filter images and reject conflicting IDs before building an exam."""
    path = tmp_path / "records.json"
    path.write_text(
        json.dumps(
            [
                {"id": "a", "question": "Q", "answer": "A"},
                {"id": "b", "question": "Image", "answer": "B", "image": "fixture"},
            ]
        )
    )
    assert len(get_questions(str(path), "pin", "pin", False, None)) == 1
    path.write_text(json.dumps([{"id": "a", "question": "Q", "answer": "A"}] * 2))
    with pytest.raises(ValueError, match="unique"):
        get_questions(str(path), "pin", "pin", False, None)


def test_judge_failure_is_unscored(questions_file: str, tmp_path: Path) -> None:
    """Keep malformed judge output unscored and exclude judge tokens from subject measurements."""
    task = hle_collaboration(
        records_file=questions_file,
        token_limit_per_agent=10000,
        sandbox_enabled=False,
        max_grader_attempts=1,
        artifact_dir=str(tmp_path),
    )
    grader = get_model(
        "mockllm/model",
        custom_outputs=[ModelOutput.from_content("mockllm/model", "malformed")] * 2,
    )
    [log] = inspect_eval(
        task,
        model=fixture_model(task),
        model_roles={"grader": grader},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error
    sample = read_eval_log(log.location).samples[0]
    result = sample.scores["team_score"]
    assert result.value["unscored_questions"] == 2
    assert math.isnan(result.value["quality"])
    assert result.metadata["unscored_reason"] == "grader_failed"
    artifact = Path(sample.store["TeamHistory:artifact_dir"])
    replay = replay_data(Path(log.location), artifact / "board.jsonl")
    assert replay["team"]["metrics"]["quality"] is None
    json.dumps(replay, allow_nan=False)


def test_two_epochs_are_isolated(tmp_path: Path) -> None:
    """Keep distinct board runs and irreversible state for repeat team attempts."""
    task = counting(
        target=4,
        token_limit_per_agent=10000,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task,
        model=fixture_model(task),
        epochs=2,
        max_samples=1,
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error
    assert len({s.store["TeamHistory:run_id"] for s in log.samples}) == 2
    assert all(s.scores["team_score"].value["quality"] == 1 for s in log.samples)


def test_fixed_spelling_work_across_team_sizes(tmp_path: Path) -> None:
    """Hold the candidate set and target fixed while changing the number of private hands."""
    tasks = [
        spelling(
            agents=n,
            candidate_count=8,
            token_limit_per_agent=10000,
            sandbox_enabled=False,
            artifact_dir=str(tmp_path),
        )
        for n in (2, 8)
    ]
    first, second = [task.dataset[0].metadata["data"] for task in tasks]
    assert first["sentences"] == second["sentences"]
    assert first["dealt_sentence"] == second["dealt_sentence"]
    for task in tasks:
        [log] = inspect_eval(
            task, model=fixture_model(task), log_dir=str(LOGS), display="none"
        )
        assert log.status == "success", log.error
        assert log.samples[0].scores["team_score"].value["quality"] == 1
