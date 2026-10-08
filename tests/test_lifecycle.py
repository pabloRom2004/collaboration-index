"""Check partial work, cancellation, configuration and real shared-sandbox boundaries."""

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from inspect_ai import eval as inspect_eval
from inspect_ai.model import ModelOutput, ModelUsage, get_model
from inspect_ai.util import Store

from collaboration_index.counting import counting
from collaboration_index.game import TeamGame
from collaboration_index.harness import team_agents
from collaboration_index.hle import hle_collaboration
from collaboration_index.scaffold import create_project
from collaboration_index.smoke import fixture_model
from collaboration_index.spelling import spelling
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


def test_team_without_token_budget_runs_to_completion(tmp_path: Path) -> None:
    """Run peers with no per-agent budget so only the task or deadline ends them."""
    task = counting(
        target=8,
        token_limit_per_agent=None,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    assert task.metadata["planned_team_token_budget"] is None
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert sample.scores["team_score"].value["quality"] == 1
    peers = sample.store["TeamHistory:peers"]
    assert all(p["status"] == "completed" and p["tokens"] > 0 for p in peers)
    with pytest.raises(ValueError, match="time limit or a per-agent token limit"):
        team_agents(None, None, "react", {}, 0.75)
    with pytest.raises(ValueError, match="positive, or null"):
        counting(token_limit_per_agent=0, artifact_dir=str(tmp_path))


def test_busy_board_export_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the journal and score when the board briefly answers its final export as busy."""
    original = httpx.AsyncClient.get
    busy = []

    async def get(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        """Answer the first two export pages as busy, then use the real board."""
        if "/admin/export/" in str(url) and len(busy) < 2:
            busy.append(url)
            return httpx.Response(503, request=httpx.Request("GET", url))
        return await original(self, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    task = counting(
        target=4,
        token_limit_per_agent=10000,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert len(busy) == 2
    assert sample.scores["team_score"].value["quality"] == 1
    assert sample.store["BoardHistory:journal"]


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


def test_prompt_omits_sandbox_when_disabled(tmp_path: Path) -> None:
    """Tell peers about a shared computer only when the task provides one."""
    seen: list[str] = []

    async def capture(*args: Any) -> ModelOutput:
        """Record each peer's model input and answer without tool calls."""
        seen.append("\n".join(message.text for message in args[0]))
        return ModelOutput.from_content("mockllm/model", "done")

    task = counting(
        target=8,
        token_limit_per_agent=10000,
        team_time_limit=3,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task,
        model=get_model("mockllm/model", custom_outputs=capture),
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error
    assert seen and all("you don't know how many others" in text for text in seen)
    assert not any("sandbox" in text or "board ID" in text for text in seen)


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
        team_time_limit=1,
        records_file=str(path),
        answer_judge="exact",
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    calls = 0

    async def reply(*args: Any) -> ModelOutput:
        """Answer one question wrongly, then keep saying it is finished until time runs out."""
        nonlocal calls
        calls += 1
        output = (
            ModelOutput.for_tool_call(
                "mockllm/model",
                "submit_answer",
                {"question_number": 1, "answer": "wrong"},
            )
            if calls == 1
            else ModelOutput.from_content("mockllm/model", "finished")
        )
        output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
        return output

    [log] = inspect_eval(
        task,
        model=get_model("mockllm/model", custom_outputs=reply),
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
    for name in ("hle", "counting", "spelling", "colouring"):
        config = defaults(name)
        # colouring peers exchange DMs only and have no file or shell tool
        assert config["task"]["args"]["sandbox_enabled"] is (name != "colouring")
        assert config["task"]["args"]["token_limit_per_agent"] is None
        assert config["task"]["args"]["compaction_threshold"] == 0.75


def test_sandbox_type_selects_packaged_definition() -> None:
    """Keep Docker as the default and resolve k8s to the packaged Helm values file."""
    for name in ("hle", "counting", "spelling", "colouring"):
        assert defaults(name)["task"]["args"]["sandbox_type"] == "docker"
    docker = counting(token_limit_per_agent=1).sandbox
    k8s = spelling(token_limit_per_agent=1, sandbox_type="k8s").sandbox
    assert docker.type == "docker" and Path(docker.config).name == "compose.yaml"
    assert k8s.type == "k8s" and Path(k8s.config).name == "values.yaml"
    compose = yaml.safe_load(Path(docker.config).read_text())
    values = yaml.safe_load(Path(k8s.config).read_text())
    assert (
        values["services"]["default"]["image"]
        == compose["services"]["default"]["image"]
    )
    with pytest.raises(ValueError, match="sandbox_type"):
        counting(token_limit_per_agent=1, sandbox_type="podman")


def test_default_work_is_two_per_agent() -> None:
    """Scale default counting and spelling work with team size; explicit values stay fixed."""
    for agents in (1, 4, 8, 32):
        task = counting(agents=agents, token_limit_per_agent=1)
        assert task.dataset[0].metadata["data"] == {"target": 2 * agents, "quota": 2}
        shown = spelling(agents=agents, token_limit_per_agent=1).dataset[0]
        assert len(shown.metadata["data"]["sentences"]) == 2 * agents
    fixed = counting(agents=8, target=64, token_limit_per_agent=1)
    assert fixed.dataset[0].metadata["data"]["target"] == 64


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
