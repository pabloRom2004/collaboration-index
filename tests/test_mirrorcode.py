"""Check the MirrorCode team task's shared workspace, serialized scoring and team submission."""

import asyncio
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import pytest
import yaml
from inspect_ai import eval as inspect_eval
from inspect_ai.model import (
    ChatMessageTool,
    ChatMessageUser,
    GenerateConfig,
    ModelOutput,
    ModelUsage,
    get_model,
)

pytest.importorskip("mc")

from collaboration_index.mirrorcode import mirrorcode  # noqa: E402

SOLUTION = (Path(__file__).parent / "fixtures/mirrorcode_rev_main.txt").read_text()


async def wait_for_fixture(action: Awaitable[Any], timeout: int) -> Any:
    """Fail a stalled scripted interaction instead of letting Inspect score a timeout."""
    try:
        return await asyncio.wait_for(action, timeout=timeout)
    except TimeoutError as exc:
        raise AssertionError("Scripted peer coordination timed out") from exc


def test_task_hides_resources_and_team_size() -> None:
    """Keep upstream instructions except the sample-limit tool a peer cannot use."""
    task = mirrorcode(agents=3, token_limit_per_agent=1000)
    sample = task.dataset[0]
    assert "resources" not in str(sample.input)
    assert "/workdir/src/" in str(sample.input)
    assert sample.target == ["rev", "Python"]
    assert task.metadata["planned_team_token_budget"] == 3000
    with pytest.raises(ValueError):
        mirrorcode(language="cobol", token_limit_per_agent=1000)


def test_workspace_grows_with_the_team() -> None:
    """Give every peer its own share of memory and CPU in the one shared workspace."""
    task = mirrorcode(agents=64, token_limit_per_agent=1000, target="mailauth")
    compose = Path(str(task.dataset[0].sandbox.config))
    assert compose.name.endswith("compose.yaml")
    services = yaml.safe_load(compose.read_text())["services"]
    assert services["default"]["mem_limit"] == "18432m"
    assert services["default"]["cpus"] == 17
    assert services["agent-scoring-visible"]["mem_limit"] == "2g"
    # eight agents share each scoring pipeline by default
    assert task.metadata["scoring_pipelines"] == 8
    assert (
        "agent-scoring-hidden-7" in services and "reference-scoring-8" not in services
    )
    with pytest.raises(ValueError):
        mirrorcode(agents=65, token_limit_per_agent=1000)


def team_fixture(updates: list[str] | None = None) -> Any:
    """Script the first two peers to write, score at once and submit; any others register and wait."""
    both_ready = asyncio.Barrier(2)
    submitted = asyncio.Event()

    async def reply(
        messages: list[Any], tools: list[Any], choice: Any, config: Any
    ) -> ModelOutput:
        """Emit each peer's next action and fail on any earlier tool error."""
        opening = next(m for m in messages if (m.metadata or {}).get("team_actor"))
        actor = opening.metadata["team_actor"]
        if "Any agent's submit ends the task for" not in opening.text:
            raise AssertionError("The opening message omits the shared submit rule")
        done = [m for m in messages if isinstance(m, ChatMessageTool)]
        errors = [m.error for m in done if m.error]
        if errors:
            raise AssertionError(str(errors))
        update = [m for m in messages if isinstance(m, ChatMessageUser)][-1].text
        if updates is not None:
            updates.append(update)
        if (
            "Token update" not in update
            or "(limit 10,000)" not in update
            or "unread messages from the Global Board" not in update
        ):
            raise AssertionError(f"Unexpected decision update: {update}")
        names = {tool.name for tool in tools}
        if "resources" in names or not {"bash", "text_editor", "submit"} <= names:
            raise AssertionError(f"Unexpected peer tools: {sorted(names)}")
        step, function, arguments = len(done), "", {}
        if step == 0:
            function = "message_board"
            arguments = {"action": "register", "name": f"Peer {actor.split('_')[1]}"}
        elif actor not in ("agent_0", "agent_1"):
            await wait_for_fixture(submitted.wait(), 300)
        elif actor == "agent_0":
            if step == 1:
                function = "bash"
                arguments = {"cmd": "mkdir -p src/rev && touch src/rev/__init__.py"}
            elif step == 2:
                function = "text_editor"
                arguments = {
                    "command": "create",
                    "path": "/workdir/src/rev/__main__.py",
                    "file_text": SOLUTION,
                }
            elif step == 3:
                await wait_for_fixture(both_ready.wait(), 60)
                function = "evaluate_testcases"
            else:
                await wait_for_fixture(submitted.wait(), 120)
                await asyncio.sleep(1)
        elif step == 1:
            await wait_for_fixture(both_ready.wait(), 60)
            function = "evaluate_testcases"
        elif step == 2:
            function = "bash"
            arguments = {"cmd": "head -c 80 /workdir/src/rev/__main__.py"}
        elif step == 3:
            if "Reference rev solution" not in done[-1].text:
                raise AssertionError("The peer cannot see the shared source file")
            function = "submit"
            submitted.set()
        output = (
            ModelOutput.for_tool_call("mockllm/model", function, arguments)
            if function
            else ModelOutput.from_content("mockllm/model", "Fixture peer finished.")
        )
        output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
        return output

    # Waiting fixture calls hold model connections, so all 64 peers need a slot
    # or the idle peers can prevent the writers from reaching their next turn.
    return get_model(
        "mockllm/model",
        custom_outputs=reply,
        config=GenerateConfig(max_connections=64),
    )


def mailauth_fixture() -> Any:
    """Have two peers score an intentionally incomplete implementation before submitting it."""
    written = asyncio.Event()
    scored = asyncio.Barrier(2)
    scored_peers: set[str] = set()
    submitted = asyncio.Event()

    async def reply(
        messages: list[Any], tools: list[Any], choice: Any, config: Any
    ) -> ModelOutput:
        """Exercise each scoring pipeline and reject any reference execution failure."""
        opening = next(m for m in messages if (m.metadata or {}).get("team_actor"))
        actor = opening.metadata["team_actor"]
        done = [m for m in messages if isinstance(m, ChatMessageTool)]
        errors = [m.error for m in done if m.error]
        if errors:
            raise AssertionError(str(errors))
        if actor == "agent_0" and not done:
            function = "bash"
            arguments = {
                "cmd": "mkdir -p src/mailauth && touch src/mailauth/__init__.py "
                "&& printf 'raise SystemExit(1)\\n' > src/mailauth/__main__.py"
            }
        elif not any(m.function == "evaluate_testcases" for m in done):
            if actor == "agent_0":
                written.set()
            await wait_for_fixture(written.wait(), 60)
            function, arguments = "evaluate_testcases", {}
        else:
            if actor not in scored_peers:
                await wait_for_fixture(scored.wait(), 120)
                scored_peers.add(actor)
            if actor == "agent_0":
                submitted.set()
                function, arguments = "submit", {}
            else:
                await wait_for_fixture(submitted.wait(), 60)
                function, arguments = "", {}
        output = (
            ModelOutput.for_tool_call("mockllm/model", function, arguments)
            if function
            else ModelOutput.from_content("mockllm/model", "Fixture finished.")
        )
        output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
        return output

    return get_model("mockllm/model", custom_outputs=reply)


@pytest.mark.docker
@pytest.mark.parametrize("per_pipeline", [8, 1])
def test_mailauth_scores_with_unresponsive_dns(
    tmp_path: Path, per_pipeline: int
) -> None:
    """Produce tool feedback and a final grade despite an initially unresponsive resolver."""
    task = mirrorcode(
        agents=2,
        target="mailauth",
        token_limit_per_agent=10000,
        artifact_dir=str(tmp_path),
        agents_per_scoring_pipeline=per_pipeline,
    )
    sample = task.dataset[0]
    compose = Path(str(sample.sandbox.config))
    spec = yaml.safe_load(compose.read_text())
    # A Docker bridge with an unreachable DNS server reproduces the reference
    # timeout; setup must restore upstream's immediate offline failure behavior.
    for service in spec["services"].values():
        service["network_mode"] = "bridge"
        service["dns"] = ["192.0.2.1"]
        service.pop("build", None)
    simulated = tmp_path / "unresponsive-dns-compose.yaml"
    simulated.write_text(yaml.safe_dump(spec))
    sample.sandbox = sample.sandbox.model_copy(update={"config": str(simulated)})
    [log] = inspect_eval(
        task, model=mailauth_fixture(), log_dir=str(tmp_path), display="none"
    )
    assert log.status == "success", log.error
    result = log.samples[0]
    assert result.error is None
    assert result.store["TeamHistory:end_reason"] == "codebase_submitted"
    assert 0 <= result.scores["mirrorcode_scorer"].value["all"] <= 1
    calls = [
        event
        for event in result.events
        if event.event == "tool" and event.function == "evaluate_testcases"
    ]
    assert len(calls) == 2
    assert all(call.error is None for call in calls)


@pytest.mark.docker
@pytest.mark.parametrize("per_pipeline", [8, 1])
def test_two_peers_share_one_workspace_and_submit(
    tmp_path: Path, per_pipeline: int
) -> None:
    """Score one shared codebase after simultaneous evaluations on one or two pipelines."""
    task = mirrorcode(
        agents=2,
        token_limit_per_agent=10000,
        artifact_dir=str(tmp_path),
        agents_per_scoring_pipeline=per_pipeline,
    )
    updates: list[str] = []
    # the scorer writes sidecar files beside the log, so keep it out of logs/
    [log] = inspect_eval(
        task, model=team_fixture(updates), log_dir=str(tmp_path), display="none"
    )
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert sample.error is None
    peers = sample.store["TeamHistory:peers"]
    assert {peer["sandbox_hostname"] for peer in peers} == {
        sample.store["shared_sandbox_hostname"]
    }
    assert sample.store["TeamHistory:end_reason"] == "codebase_submitted"
    assert [s["actor"] for s in sample.store["TeamHistory:submissions"]] == ["agent_1"]
    assert sample.scores["mirrorcode_scorer"].value["all"] == 1.0
    assert updates and not any("Time update" in update for update in updates)


@pytest.mark.docker
def test_sixty_four_peers_share_one_workspace(tmp_path: Path) -> None:
    """Run the largest supported team through the board, scoring and submit in one box."""
    task = mirrorcode(
        agents=64, token_limit_per_agent=10000, artifact_dir=str(tmp_path)
    )
    [log] = inspect_eval(
        task, model=team_fixture(), log_dir=str(tmp_path), display="none"
    )
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert sample.error is None
    peers = sample.store["TeamHistory:peers"]
    assert len(peers) == 64
    assert len({peer["sandbox_hostname"] for peer in peers}) == 1
    assert (
        sum(e["kind"] == "register" for e in sample.store["BoardHistory:journal"]) == 64
    )
    assert sample.store["TeamHistory:end_reason"] == "codebase_submitted"
    assert sample.scores["mirrorcode_scorer"].value["all"] == 1.0


def busy_team_fixture() -> Any:
    """Make all 64 peers edit and communicate, then queue nine real grading requests."""
    written = asyncio.Event()
    ready = asyncio.Barrier(64)
    ready_peers: set[str] = set()
    graded: set[str] = set()
    grading_done = asyncio.Event()

    async def reply(
        messages: list[Any], tools: list[Any], choice: Any, config: Any
    ) -> ModelOutput:
        """Run each peer's contribution and keep the known solution unchanged."""
        opening = next(m for m in messages if (m.metadata or {}).get("team_actor"))
        actor = opening.metadata["team_actor"]
        index = int(actor.removeprefix("agent_"))
        done = [m for m in messages if isinstance(m, ChatMessageTool)]
        if any(m.error for m in done):
            raise AssertionError("An active fixture peer received a tool error")
        offset = 2 if index == 0 else 0
        step = len(done) - offset
        function, arguments = "", {}
        if index == 0 and len(done) == 0:
            function, arguments = (
                "bash",
                {
                    "cmd": "mkdir -p src/rev src/fixture-markers && touch src/rev/__init__.py"
                },
            )
        elif index == 0 and len(done) == 1:
            function, arguments = (
                "text_editor",
                {
                    "command": "create",
                    "path": "/workdir/src/rev/__main__.py",
                    "file_text": SOLUTION,
                },
            )
        elif step == 0:
            if index == 0:
                written.set()
            await wait_for_fixture(written.wait(), 120)
            function, arguments = (
                "message_board",
                {"action": "register", "name": f"Fixture {index}"},
            )
        elif step == 1:
            function, arguments = (
                "bash",
                {
                    "cmd": f"printf 'fixture contribution\\n' > src/fixture-markers/{index}.txt"
                },
            )
        elif step == 2:
            function, arguments = (
                "message_board",
                {"action": "send", "message": "Fixture contribution complete."},
            )
        elif step == 3:
            if actor not in ready_peers:
                await wait_for_fixture(ready.wait(), 120)
                ready_peers.add(actor)
            if index < 9:
                function = "evaluate_testcases"
            else:
                await wait_for_fixture(grading_done.wait(), 240)
        else:
            graded.add(actor)
            if len(graded) == 9:
                grading_done.set()
            await wait_for_fixture(grading_done.wait(), 240)
            if index == 0:
                function = "submit"
        output = (
            ModelOutput.for_tool_call("mockllm/model", function, arguments)
            if function
            else ModelOutput.from_content(
                "mockllm/model", "Fixture contribution complete."
            )
        )
        output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
        return output

    return get_model(
        "mockllm/model",
        custom_outputs=reply,
        config=GenerateConfig(max_connections=64),
    )


@pytest.mark.docker
def test_busy_sixty_four_peers_and_all_scoring_pipelines(tmp_path: Path) -> None:
    """Keep all peers active while eight grading pipelines serve nine overlapping calls."""
    task = mirrorcode(
        agents=64, token_limit_per_agent=10000, artifact_dir=str(tmp_path)
    )
    [log] = inspect_eval(
        task, model=busy_team_fixture(), log_dir=str(tmp_path), display="none"
    )
    assert log.status == "success", log.error
    sample = log.samples[0]
    assert sample.error is None
    assert sample.store["TeamHistory:end_reason"] == "codebase_submitted"
    assert sample.scores["mirrorcode_scorer"].value["all"] == 1.0
    assert all(peer["tool_calls"] >= 3 for peer in sample.store["TeamHistory:peers"])
    calls = [
        event
        for event in sample.events
        if event.event == "tool" and event.function == "evaluate_testcases"
    ]
    assert len(calls) == 9
    assert all(call.error is None for call in calls)
    journal = sample.store["BoardHistory:journal"]
    assert sum(event["kind"] == "register" for event in journal) == 64
    assert sum(event["kind"] == "message" for event in journal) == 64
