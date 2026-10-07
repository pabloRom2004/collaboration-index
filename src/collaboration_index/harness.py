"""Release symmetric Inspect agents together against trusted shared submission state."""

import asyncio
import importlib
import json
import math
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from inspect_ai.agent import AgentState, react, run
from inspect_ai.model import ChatMessageUser, CompactionAuto, ModelOutput, get_model
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox, token_limit

from collaboration_index.board.client import (
    BoardClient,
    message_board,
    read_messages,
    send_message,
)
from collaboration_index.board.runtime import local_board
from collaboration_index.game import (
    TeamGame,
    oracle_progress,
    read_file,
    set_colour,
    submit_answer,
    submit_letter,
    submit_number,
)
from collaboration_index.prompts import (
    COLLABORATE,
    COLLABORATE_NO_SANDBOX,
    COLOURING,
    ORACLE,
    TIME_UPDATE,
)
from collaboration_index.state import Peer, TeamHistory, now


@solver
def prepare_team(
    benchmark: str, agents: int, condition: str, artifact_dir: str
) -> Solver:
    """Initialize scoring state and materialize only participant-visible input files."""

    async def execute(state: TaskState, generate: Generate) -> TaskState:
        """Create an isolated attempt directory before the replaceable team solver runs."""
        history = state.store_as(TeamHistory)
        if history.initialized:
            raise ValueError(
                "Checkpoint continuation of peer histories is not supported yet"
            )
        history.initialized = True
        history.benchmark = benchmark
        history.condition = condition
        history.run_id = "team-" + uuid4().hex
        history.peers = [Peer(id=f"agent_{i}") for i in range(agents)]
        directory = Path(artifact_dir).resolve() / history.run_id
        directory.mkdir(parents=True, exist_ok=False)
        history.artifact_dir = str(directory)
        if benchmark == "hle" and state.metadata["answer_judge"] == "hle_json_judge":
            get_model(role="grader", required=True)
        if state.metadata["sandbox_enabled"]:
            hostname = await sandbox().exec(["cat", "/etc/hostname"])
            if not hostname.success:
                raise RuntimeError("Cannot identify the shared sandbox")
            state.store.set("shared_sandbox_hostname", hostname.stdout.strip())
            await sandbox().write_file(
                "/workspace/team.json",
                json.dumps({"run_id": history.run_id, "agents": agents}),
            )
        if benchmark == "hle":
            import hashlib

            public = json.dumps(
                [
                    {
                        k: row[k]
                        for k in ("question_number", "id", "question", "answer_type")
                    }
                    for row in state.metadata["data"]["questions"]
                ],
                ensure_ascii=False,
            )
            (directory / "questions.json").write_text(public)
            if state.metadata["sandbox_enabled"]:
                await sandbox().write_file("/workspace/questions.json", public)
            history.public_file_hash = hashlib.sha256(public.encode()).hexdigest()
        return state

    return execute


@solver
def team_agents(
    token_limit_per_agent: int,
    team_time_limit: float,
    agent: str,
    agent_args: dict[str, Any],
    compaction_threshold: float,
) -> Solver:
    """Build fixed-identity tools and run prepared peers with independent token limits."""
    if (
        token_limit_per_agent < 1
        or team_time_limit is None
        or not math.isfinite(team_time_limit)
        or team_time_limit <= 0
    ):
        raise ValueError("Team token and time limits must be finite and positive")
    forbidden = {"tools", "submit", "on_continue", "model", "compaction"} & set(
        agent_args
    )
    if forbidden:
        raise ValueError(
            "Team tools, submission, lifecycle and model are evaluator-controlled"
        )
    if not 0 < compaction_threshold <= 1:
        raise ValueError("Compaction threshold must be within (0,1]")
    factory = (
        react
        if agent == "react"
        else getattr(
            importlib.import_module(agent.rsplit(".", 1)[0]), agent.rsplit(".", 1)[1]
        )
    )

    async def execute(state: TaskState, generate: Generate) -> TaskState:
        """Authenticate a fresh board, synchronize inference, and join peers before final scoring."""
        history = state.store_as(TeamHistory)
        if not history.initialized:
            raise RuntimeError("Missing invariant team setup")
        actors = [peer.id for peer in history.peers]
        data = state.metadata["data"]
        game = TeamGame(history, data, actors)
        async with local_board(
            Path(history.artifact_dir), history.run_id, actors
        ) as participants:
            for options in participants:
                roster = await BoardClient(options).call(
                    {"request_id": uuid4().hex, "action": "agents"}
                )
                if roster["total_agents"] != len(actors):
                    raise RuntimeError(
                        "Authenticated board roster differs from the team"
                    )
            ready: asyncio.Queue[None] = asyncio.Queue()
            release = asyncio.Event()
            clock = {"released": 0.0}

            def time_update() -> str:
                """Tell a peer the team's elapsed and remaining solving time before its next decision."""
                elapsed = time.monotonic() - clock["released"]
                return TIME_UPDATE.prompt.format(
                    elapsed=elapsed,
                    remaining=max(0.0, team_time_limit - elapsed),
                    minutes=team_time_limit / 60,
                )

            async def peer(record: Peer, options: dict[str, str]) -> None:
                """Run one private model history with trusted tools and own usage accounting."""
                if state.metadata["sandbox_enabled"]:
                    identity = await sandbox().exec(["cat", "/etc/hostname"])
                    public = await sandbox().exec(["cat", "/workspace/team.json"])
                    if (
                        not identity.success
                        or identity.stdout.strip()
                        != state.store.get("shared_sandbox_hostname")
                        or not public.success
                        or json.loads(public.stdout)["run_id"] != history.run_id
                    ):
                        raise RuntimeError("A peer does not share the team's sandbox")
                    record.sandbox_hostname = identity.stdout.strip()
                limit = token_limit(token_limit_per_agent)

                async def on_continue(current: AgentState) -> bool | str:
                    """Count this peer's turn, then stop at the team's end or send the clock."""
                    record.turns += 1
                    output = current.output
                    record.tool_calls += len(output.message.tool_calls or [])
                    if output.usage:
                        record.input_tokens += output.usage.input_tokens
                        record.output_tokens += output.usage.output_tokens
                    # peers work until the task ends or time runs out, not until a
                    # turn happens to carry no tool call
                    return history.end_reason is None and time_update()

                tools = [
                    {
                        "hle": submit_answer,
                        "counting": submit_number,
                        "spelling": submit_letter,
                        "colouring": set_colour,
                    }[history.benchmark](game, record.id)
                ]
                if history.benchmark == "hle":
                    tools.append(
                        read_file(
                            (Path(history.artifact_dir) / "questions.json").read_text(),
                            state.metadata["sandbox_enabled"],
                        )
                    )
                if history.condition == "collaborative" and (
                    history.benchmark == "colouring"
                ):
                    contacts = data["neighbours"][record.id]
                    tools += [
                        send_message(options, contacts),
                        read_messages(
                            options, contacts, lambda: history.end_reason is not None
                        ),
                    ]
                    extra = COLOURING.prompt.format(
                        actor=record.id, neighbours=", ".join(contacts) or "none"
                    )
                elif history.condition == "collaborative":
                    tools.append(message_board(options))
                    # agents see neither their evaluator ID nor the team size
                    extra = (
                        COLLABORATE
                        if state.metadata["sandbox_enabled"]
                        else COLLABORATE_NO_SANDBOX
                    ).prompt
                else:
                    if history.benchmark in ("counting", "spelling"):
                        tools.append(oracle_progress(game))
                    extra = ORACLE.prompt.format(
                        agents=len(actors),
                        actor=record.id,
                        assignment=json.dumps(game.assignments(record.id)),
                    )
                if history.benchmark == "spelling":
                    extra += "\nYour private reusable character hand: " + json.dumps(
                        data["hands"][record.id]
                    )
                messages = [
                    message.model_copy(deep=True) for message in state.messages
                ] + [
                    ChatMessageUser(
                        content=extra,
                        # message metadata stays controller-side; it is never sent to the model
                        metadata={"team_run": history.run_id, "team_actor": record.id},
                    )
                ]
                runner = factory(
                    tools=tools,
                    submit=False,
                    on_continue=on_continue,
                    compaction=CompactionAuto(threshold=compaction_threshold),
                    **dict(agent_args),
                )
                ready.put_nowait(None)
                try:
                    await release.wait()
                    record.started, record.status = now(), "running"
                    messages.append(ChatMessageUser(content=time_update()))
                    _, exceeded = await run(
                        runner, messages, limits=[limit], name=record.id
                    )
                    record.status = "limited" if exceeded else "completed"
                except asyncio.CancelledError:
                    record.status = "cancelled"
                    raise
                except Exception:
                    record.status = "error"
                    raise
                finally:
                    record.tokens = int(limit.usage)
                    record.completed = now()

            async def run_team() -> None:
                """Prepare every peer, release together, then apply a deadline only to solving work."""
                async with asyncio.TaskGroup() as group:
                    tasks = [
                        group.create_task(peer(record, options))
                        for record, options in zip(
                            history.peers, participants, strict=True
                        )
                    ]
                    for _ in actors:
                        await ready.get()
                    history.released = now()
                    clock["released"] = time.monotonic()
                    release.set()
                    _, pending = await asyncio.wait(tasks, timeout=team_time_limit)
                    if pending:
                        history.end_reason = history.end_reason or "deadline"
                        for pending_task in pending:
                            pending_task.cancel()

            await run_team()
            history.end_reason = history.end_reason or "peers_finished"
            history.completed = now()
        state.output = ModelOutput.from_content(
            "", "Team attempt ended; scoring uses trusted submission state."
        )
        return state

    return execute
