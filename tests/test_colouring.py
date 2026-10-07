"""Exercise graph colouring rules, neighbour-only DMs and the network replay."""

import json
import time
from pathlib import Path

import httpx
import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.tool import ToolError
from inspect_ai.util import Store

from collaboration_index.board.client import read_messages, send_message
from collaboration_index.colouring import colouring
from collaboration_index.colouring.dataset import draw_network
from collaboration_index.game import TeamGame, clashing_edges
from collaboration_index.replay import replay_data
from collaboration_index.smoke import fixture_model
from collaboration_index.state import TeamHistory

LOGS = Path(__file__).parents[1] / "logs"
OPTIONS = {
    "url": "http://testserver",
    "run_id": "test",
    "agent_id": "agent_0",
    "token_env": "COLOURING_TOOL_TEST",
}


@pytest.mark.parametrize(
    "agents,condition",
    [
        (1, "collaborative"),
        (2, "collaborative"),
        (8, "collaborative"),
        (32, "collaborative"),
        (8, "oracle_allocation"),
    ],
)
def test_colouring_team(agents: int, condition: str, tmp_path: Path) -> None:
    """Colour the planted network through neighbour DMs and replay it as a graph."""
    task = colouring(
        agents=agents,
        condition=condition,
        token_limit_per_agent=10000,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    assert log.status == "success", log.error
    log = read_eval_log(log.location)
    sample = log.samples[0]
    assert not sample.error
    metrics = sample.scores["team_score"].value
    edges = sample.metadata["data"]["edges"]
    assert metrics["quality"] == 1 and metrics["completed"] == 1
    assert sample.store["TeamHistory:end_reason"] == "properly_coloured"
    assert metrics["global_messages"] == 0
    # the fixture DMs every neighbour once, so each edge carries two messages
    expected = 2 * len(edges) if condition == "collaborative" else 0
    assert metrics["direct_messages"] == metrics["message_count"] == expected
    artifact = Path(sample.store["TeamHistory:artifact_dir"])
    replay = replay_data(Path(log.location), artifact / "board.jsonl")
    # a remote runner's artifact directory is gone, so the stored journal must suffice
    assert replay_data(Path(log.location), None) == replay
    assert replay["topology"] == "graph"
    assert replay["graph"]["edges"] == edges
    assert len(replay["flags"]) == len(edges)
    if edges:
        assert all(replay["collective"]["grades"][-1]["capabilities"].values())
    json.dumps(replay, allow_nan=False)


@pytest.mark.parametrize("topology", ["random", "ring", "grid"])
@pytest.mark.parametrize("agents", [2, 5, 16, 32])
def test_planted_network_is_proper_and_seeded(topology: str, agents: int) -> None:
    """Keep the planted colouring proper, neighbours symmetric and draws reproducible."""
    data = draw_network(agents, 7, 3, topology, 3.0)
    assert data == draw_network(agents, 7, 3, topology, 3.0)
    assert not clashing_edges(data["planted"], data["edges"])
    for a, b in data["edges"]:
        assert b in data["neighbours"][a] and a in data["neighbours"][b]
    if topology == "random":
        assert all(data["neighbours"].values())


def test_network_settings_are_validated() -> None:
    """Reject palettes, topologies and two-colour odd rings that cannot be solved."""
    with pytest.raises(ValueError, match="colours"):
        draw_network(4, 0, 9, "random", 3.0)
    with pytest.raises(ValueError, match="topology"):
        draw_network(4, 0, 3, "star", 3.0)
    with pytest.raises(ValueError, match="odd ring"):
        draw_network(5, 0, 2, "ring", 3.0)


async def test_colour_rules_and_proper_end() -> None:
    """Reject unknown colours, let a node recolour, and end only when every edge differs."""
    history = TeamHistory(store=Store(), initialized=True, benchmark="colouring")
    data = {"colours": ["red", "green"], "edges": [["a", "b"], ["b", "c"]]}
    game = TeamGame(history, data, ["a", "b", "c"])
    with pytest.raises(ToolError, match="red, green"):
        await game.submit("a", "purple")
    assert history.rejected_submissions == 1 and not history.submissions
    await game.submit("a", " Red ")
    await game.submit("b", "red")
    await game.submit("c", "green")
    assert history.end_reason is None
    result = await game.submit("b", "green")
    assert history.end_reason is None and not result["team_ended"]
    result = await game.submit("c", "red")
    assert history.end_reason == "properly_coloured" and result["team_ended"]
    with pytest.raises(ToolError, match="ended"):
        await game.submit("a", "green")


def test_uncoloured_end_is_a_clash() -> None:
    """Count an edge with a missing colour as unsolved, as the score defines."""
    edges = [["a", "b"], ["b", "c"]]
    assert clashing_edges({"a": "red", "b": "green"}, edges) == [["b", "c"]]


async def test_dm_to_non_neighbour_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse a DM outside the network before any board request is made."""
    monkeypatch.setenv(OPTIONS["token_env"], "token")

    def respond(request: httpx.Request) -> httpx.Response:
        """Fail the test if the refused message reaches the board."""
        raise AssertionError("A refused DM reached the board")

    tool = send_message(OPTIONS, ["agent_1"], httpx.MockTransport(respond))
    with pytest.raises(ToolError, match="only message your neighbours"):
        await tool(neighbour="agent_2", text="hello")


async def test_waiting_reader_returns_when_the_team_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop a long wait promptly once the network is solved instead of sleeping it out."""
    monkeypatch.setenv(OPTIONS["token_env"], "token")
    polls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        """Report no unread DMs and count each poll."""
        nonlocal polls
        assert request.url.path == "/unread"
        polls += 1
        return httpx.Response(
            200,
            json={
                "run_id": "test",
                "agent_id": "agent_0",
                "result": {"global": 0, "direct": 0},
            },
        )

    tool = read_messages(
        OPTIONS, ["agent_1"], lambda: polls >= 3, httpx.MockTransport(respond)
    )
    started = time.monotonic()
    assert json.loads(await tool(wait_seconds=20)) == {
        "messages": [],
        "team_ended": True,
    }
    assert time.monotonic() - started < 5
