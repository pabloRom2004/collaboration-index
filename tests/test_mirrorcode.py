"""Check the MirrorCode team task's shared workspace, serialized scoring and team submission."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.model import (
    ChatMessageTool,
    ChatMessageUser,
    ModelOutput,
    ModelUsage,
    get_model,
)

pytest.importorskip("mc")

from collaboration_index.mirrorcode import mirrorcode  # noqa: E402

SOLUTION = (Path(__file__).parent / "fixtures/mirrorcode_rev_main.txt").read_text()


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


def team_fixture(updates: list[str] | None = None) -> Any:
    """Script two peers: one writes the solution, both score at once, the other submits."""
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
            arguments = {"action": "register", "name": f"Peer {actor[-1]}"}
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
                async with asyncio.timeout(60):
                    await both_ready.wait()
                function = "evaluate_testcases"
            else:
                async with asyncio.timeout(120):
                    await submitted.wait()
                await asyncio.sleep(1)
        elif step == 1:
            async with asyncio.timeout(60):
                await both_ready.wait()
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

    return get_model("mockllm/model", custom_outputs=reply)


@pytest.mark.docker
def test_two_peers_share_one_workspace_and_submit(tmp_path: Path) -> None:
    """Score one shared codebase after simultaneous evaluations and a peer's submit, with no deadline."""
    task = mirrorcode(
        agents=2,
        token_limit_per_agent=10000,
        artifact_dir=str(tmp_path),
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
