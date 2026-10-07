"""Keep evaluator IDs and the team size out of everything a board-task peer reads."""

import json
import re
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import Sample
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.solver import generate, use_tools

from collaboration_index.board.client import LIMITS, message_board
from collaboration_index.board.database import BoardDatabase, token_hash
from collaboration_index.board.server import create_app
from collaboration_index.counting import counting
from collaboration_index.smoke import fixture_model
from collaboration_index.spelling import spelling

LOGS = Path(__file__).parents[1] / "logs"
EVALUATOR_ID = re.compile(r"agent_\d+")


@pytest.mark.parametrize("benchmark", ["counting", "spelling"])
def test_peer_input_hides_ids_and_team_size(benchmark: str, tmp_path: Path) -> None:
    """Scan every prompt, tool description and board reply a peer receives for IDs or the team size."""
    agents = 5
    common = dict(
        agents=agents,
        token_limit_per_agent=10000,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    task = (
        counting(target=9, **common)
        if benchmark == "counting"
        else spelling(candidate_count=6, **common)
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    sample = read_eval_log(log.location, resolve_attachments=True).samples[0]
    assert sample.scores["team_score"].value["quality"] == 1
    calls = [event for event in sample.events if event.event == "model"]
    assert calls
    for event in calls:
        for message in event.input:
            if message.role in ("system", "user", "tool"):
                assert not EVALUATOR_ID.search(message.text), message.text
            # time updates carry clock readings, not the team size
            if message.role == "user" and not message.text.startswith("Time update:"):
                assert not re.search(rf"\b{agents}\b", message.text), message.text
        for info in event.tools:
            assert not EVALUATOR_ID.search(info.model_dump_json())
    replies = [
        str(event.result)
        for event in sample.events
        if event.event == "tool" and event.function == "message_board"
    ]
    assert replies
    for reply in replies:
        assert not EVALUATOR_ID.search(reply), reply
        assert "total_agents" not in reply and "run_id" not in reply


def test_board_tool_shows_only_chosen_names(tmp_path: Path, monkeypatch) -> None:
    """List only registered teammates, label unnamed senders, and address DMs by chosen name."""
    database = BoardDatabase("sqlite:///" + str(tmp_path / "names.db"), LIMITS)
    database.provision(
        {
            "test": {
                a: token_hash(a + "-token") for a in ("agent_0", "agent_1", "agent_2")
            }
        }
    )

    def call(actor: str, action: str, **args: str) -> None:
        """Apply one setup operation directly through the board's transaction boundary."""
        database.execute(
            "test", actor, {"request_id": uuid4().hex, "action": action, **args}
        )

    call("agent_1", "register", name="Bee")
    call("agent_1", "send", message="hello from Bee")
    call("agent_2", "send", message="hello from an unnamed slot")
    monkeypatch.setenv("NAMES_TEST_TOKEN", "agent_0-token")
    options = {
        "url": "http://testserver",
        "run_id": "test",
        "agent_id": "agent_0",
        "token_env": "NAMES_TEST_TOKEN",
    }
    steps = iter(
        [
            {"action": "register", "name": "Alpha"},
            {"action": "agents"},
            {"action": "read"},
            {"action": "send", "recipient": "Bee", "message": "hi Bee"},
            {"action": "conversations"},
            {"action": "send", "recipient": "agent_1", "message": "guessing an ID"},
        ]
    )

    def output(messages, tools, tool_choice, config):  # type: ignore[no-untyped-def]
        """Script one board action per turn, then finish."""
        args = next(steps, None)
        if args is None:
            return ModelOutput.from_content("mockllm/model", "Done")
        return ModelOutput.for_tool_call("mockllm/model", "message_board", args)

    transport = httpx.ASGITransport(app=create_app(database, token_hash("observer")))
    task = Task(
        dataset=[Sample(input="Use the board")],
        solver=[use_tools(message_board(options, transport)), generate()],
    )
    [log] = inspect_eval(
        task,
        model=get_model("mockllm/model", custom_outputs=output, memoize=False),
        display="none",
        score=False,
        log_dir=str(LOGS),
    )
    assert log.status == "success" and log.samples[0].error is None
    tool = [m for m in log.samples[0].messages if m.role == "tool"]
    register, roster, read, sent, rooms, guess = tool
    assert json.loads(register.text) == {"name": "Alpha"}
    # agent_2 never registered, so it is not listed and the team size stays hidden
    assert json.loads(roster.text) == {"you": "Alpha", "teammates": ["Bee"]}
    senders = [m["from"] for m in json.loads(read.text)["messages"]]
    assert senders == ["Bee", "unregistered teammate"]
    assert set(json.loads(sent.text)) == {"sequence", "conversation", "time"}
    titles = {room["title"] for room in json.loads(rooms.text)["conversations"]}
    assert titles == {"Global", "Alpha ↔ Bee"}
    assert guess.error and "No teammate has registered that name" in guess.error.message
    assert not any(EVALUATOR_ID.search(m.text) for m in tool[:-1])


def test_every_decision_follows_a_time_update(tmp_path: Path) -> None:
    """Send each peer the team clock before its first and every later decision."""
    task = counting(
        agents=3,
        target=6,
        token_limit_per_agent=10000,
        team_time_limit=600,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    sample = read_eval_log(log.location, resolve_attachments=True).samples[0]
    assert sample.scores["team_score"].value["quality"] == 1
    calls = [event for event in sample.events if event.event == "model"]
    assert calls
    for event in calls:
        last = event.input[-1]
        assert last.role == "user" and last.text.startswith("Time update:"), last.text
        assert "of the 10-minute team deadline" in last.text


def test_team_time_limit_is_required(tmp_path: Path) -> None:
    """Refuse to build a team task without a positive wall-clock deadline."""
    with pytest.raises(ValueError, match="team_time_limit"):
        counting(
            token_limit_per_agent=10000,
            team_time_limit=None,
            sandbox_enabled=False,
            artifact_dir=str(tmp_path),
        )


def test_context_window_sets_absolute_compaction_threshold(
    tmp_path: Path, monkeypatch
) -> None:
    """Resolve the compaction fraction against a supplied window, not Inspect's default."""
    import collaboration_index.harness as harness

    thresholds = []

    def recording(threshold):
        thresholds.append(threshold)
        return harness_compaction(threshold=threshold)

    harness_compaction = harness.CompactionAuto
    monkeypatch.setattr(harness, "CompactionAuto", recording)
    task = counting(
        agents=2,
        target=4,
        token_limit_per_agent=10000,
        team_time_limit=600,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
        context_window=1_000_000,
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    assert log.eval.metadata["context_window"] == 1_000_000
    assert thresholds == [750_000, 750_000]
