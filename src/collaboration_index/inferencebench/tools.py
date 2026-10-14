"""Bind shared workspace tools to peers and retain development evaluator feedback.

By default, foreground commands share one lock. In the leased-GPU mode
(``orchestration.enabled``), peers get the ``submit`` and ``submissions`` tools.
With ``gpu_access: scheduler`` they share GPUs through leases and the ``gpu``
tool; with ``gpu_access: unmanaged`` every command sees every GPU.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any
from uuid import uuid4

from inspect_ai.tool import Tool, ToolDef, ToolError, ToolResult, tool
from inspect_ai.tool import bash as native_bash
from inspect_ai.tool import python as native_python
from inspect_ai.util import SandboxEnvironment, store_as

from collaboration_index.inferencebench import pod
from collaboration_index.inferencebench.orchestration import (
    SCORING_RULE_TEXT,
    Lease,
    QueueFull,
)
from collaboration_index.inferencebench.resource import workspace
from collaboration_index.inferencebench.session import (
    BASE_PORT,
    TASK_DIR,
    Session,
    current,
)
from collaboration_index.inferencebench.state import (
    DevelopmentCheck,
    InferenceHistory,
    WorkspaceCall,
)
from collaboration_index.state import now

DEFAULT_WAIT_SECONDS = 600
MAX_WAIT_SECONDS = 3600
NOTE_LENGTH = 200


def update_call(history: InferenceHistory, call: WorkspaceCall) -> None:
    """Update one call without overwriting records appended by concurrent peers."""
    calls = history.calls
    for index, existing in enumerate(calls):
        if existing.id == call.id:
            calls[index] = call
            break
    else:
        calls.append(call)
    history.calls = calls


@asynccontextmanager
async def recorded_call(actor: str, name: str) -> AsyncIterator[WorkspaceCall]:
    """Record one workspace call from queueing to completion."""
    history = store_as(InferenceHistory)
    call = WorkspaceCall(id=uuid4().hex, actor=actor, tool=name, requested=now())
    update_call(history, call)
    try:
        yield call
    except BaseException as error:
        call.status = (
            "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
        )
        raise
    finally:
        if call.status == "running":
            call.status = "completed"
        call.completed = now()
        update_call(history, call)


def start_call(call: WorkspaceCall) -> None:
    """Mark a recorded call as running."""
    call.started, call.status = now(), "running"
    update_call(store_as(InferenceHistory), call)


@asynccontextmanager
async def recorded_check(**fields: Any) -> AsyncIterator[DevelopmentCheck]:
    """Record one development evaluation from start to completion."""
    history = store_as(InferenceHistory)
    check = DevelopmentCheck(id=uuid4().hex, started=now(), **fields)
    checks = history.checks
    checks.append(check)
    history.checks = checks
    try:
        yield check
    except BaseException as error:
        check.status = (
            "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
        )
        check.error_type = type(error).__name__
        raise
    finally:
        check.completed = now()
        history.checks = checks


async def read_metrics(env: SandboxEnvironment, path: str) -> dict[str, Any]:
    """Read the development evaluator's JSON metrics file or raise a tool error."""
    try:
        content = await env.read_file(path)
    except FileNotFoundError as error:
        raise ToolError(
            "Development evaluator did not write its metrics file"
        ) from error
    try:
        metrics = json.loads(content)
    except json.JSONDecodeError as error:
        raise ToolError("Development evaluator returned malformed JSON") from error
    if not isinstance(metrics, dict):
        raise ToolError("Development evaluator returned a non-object result")
    return metrics


async def run_command(
    env: SandboxEnvironment,
    name: str,
    arguments: dict[str, Any],
    timeout: int,
    env_vars: dict[str, str] | None = None,
) -> str:
    """Run one native bash or Python call on a pod and return its stderr and stdout."""
    command = arguments["command"] if name == "bash" else "python3 -"
    result = await env.exec(
        ["bash", "--login", "-c", command],
        input=arguments.get("code"),
        env=env_vars or {},
        timeout=timeout,
    )
    return (f"{result.stderr}\n" if result.stderr else "") + result.stdout


def rewrap(definition: ToolDef, execute: Callable[..., Any]) -> Tool:
    """Expose a wrapper under the original tool's name, description and parameters."""
    return ToolDef(
        execute,
        name=definition.name,
        description=definition.description,
        parameters=definition.parameters,
    ).as_tool()


def remote_tool(item: Tool, timeout: int) -> Tool:
    """Preserve native shell schemas while executing every command on the external GPU."""
    definition = ToolDef(item)

    @wraps(definition.tool)
    async def execute(**kwargs: Any) -> str:
        """Run the native bash or Python command through the owned pod's SSH transport."""
        return await run_command(workspace(), definition.name, kwargs, timeout)

    return rewrap(definition, execute)


def workspace_tools(timeout: int, external: bool) -> list[Tool]:
    """Select native or external execution without changing participant tool affordances."""
    items = [native_bash(timeout=timeout), native_python(timeout=timeout)]
    return [remote_tool(item, timeout) for item in items] if external else items


def serialized_tool(item: Tool, lock: asyncio.Lock, actor: str) -> Tool:
    """Serialize one foreground command with the same sample-local lock for all peers."""
    definition = ToolDef(item)

    @wraps(definition.tool)
    async def execute(**kwargs: Any) -> ToolResult:
        """Record queueing and completion without accepting participant-supplied identities."""
        async with recorded_call(actor, definition.name) as call:
            async with lock:
                start_call(call)
                return await definition.tool(**kwargs)

    return rewrap(definition, execute)


@tool
def evaluate(timeout: int) -> Tool:
    """Run InferenceBench's development evaluator and retain its JSON measurements."""

    async def execute(quick: bool = False) -> str:
        """Measure the current shared server on development inputs.

        Args:
            quick: Use the original evaluator's quick smoke mode.
        """
        async with recorded_check() as check:
            path = f"{TASK_DIR}/metrics-{check.id}.json"
            command = [f"{TASK_DIR}/evaluate.py", "--json-output-file", path]
            if quick:
                command.append("--quick")
            result = await workspace().exec(
                command, timeout=timeout, timeout_retry=False
            )
            if not result.success:
                raise ToolError(result.stdout + result.stderr)
            metrics = await read_metrics(workspace(), path)
            check.metrics, check.status = metrics, "completed"
            return json.dumps(metrics)

    return execute


def leased_tool(item: Tool, session: Session, actor: str) -> Tool:
    """Run a peer's shell or Python command on the workspace pod with its lease environment."""
    definition = ToolDef(item)

    @wraps(definition.tool)
    async def execute(**kwargs: Any) -> str:
        """Expose only the peer's leased GPUs and ports, record the call and flag GPU memory failures."""
        lease: Lease | None = None
        async with recorded_call(actor, definition.name) as call:
            async with session.command():
                # read the lease when the command starts, not when it was queued
                held = session.scheduler.held(actor)
                lease = held if held and session.scheduler.protect(held) else None
                try:
                    call.gpus = sorted(lease.slots) if lease else []
                    start_call(call)
                    output = await run_command(
                        session.workspace,
                        definition.name,
                        kwargs,
                        session.tool_timeout,
                        session.lease_env(lease),
                    )
                finally:
                    if lease:
                        session.scheduler.unprotect(lease)
        signature = pod.detect_oom(output)
        if signature:
            call.oom = signature
            update_call(store_as(InferenceHistory), call)
            output += session.record_oom(actor, lease, definition.name, signature)
        return output

    return rewrap(definition, execute)


def gpu_tool(session: Session, actor: str) -> Tool:
    """Let one peer acquire, give back or inspect its GPU lease."""

    async def gpu_lease(
        action: str = "status",
        wait_seconds: int = DEFAULT_WAIT_SECONDS,
        count: int = 1,
        gpu: int | None = None,
        note: str = "",
    ) -> str:
        """Acquire, give back or inspect your GPUs on the shared development machine.

        Your bash and python commands see only the GPUs you hold. The task
        instructions describe the environment variables and when GPUs are taken
        back.

        Args:
            action: "acquire" to get GPUs (waiting for a first one if needed, or adding free extra ones if you already hold some), "release" to give back one GPU or all of yours, or "status" to see every GPU, its holder and note.
            wait_seconds: For "acquire" without any GPU, the longest time to wait in the queue, from 0 to 3600 seconds.
            count: For "acquire", how many GPUs you want in total, at least 1.
            gpu: For "release", the one GPU to give back; omit it to give back all of yours.
            note: For "acquire", a short description of your GPU work that teammates see in status.
        """
        if action == "status":
            return json.dumps(session.scheduler.status(actor, session.names()))
        if action == "release":
            lease = session.scheduler.held(actor)
            if gpu is not None and (lease is None or gpu not in lease.slots):
                raise ToolError(f"You do not hold GPU {gpu}")
            freed = session.scheduler.release(actor, gpu)
            for slot in freed:
                await session.reclaim(slot)
            session.persist()
            return json.dumps(
                {
                    "released_gpus": freed,
                    **session.scheduler.status(actor, session.names()),
                }
            )
        if action != "acquire":
            raise ToolError('action must be "acquire", "release" or "status"')
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= MAX_WAIT_SECONDS:
            raise ToolError(
                f"wait_seconds must be an integer from 0 to {MAX_WAIT_SECONDS}"
            )
        slots = session.scheduler.slots
        if type(count) is not int or not 1 <= count <= slots:
            raise ToolError(f"count must be an integer from 1 to {slots}")
        lease = await session.scheduler.acquire(
            actor, wait_seconds, count, note.strip()[:NOTE_LENGTH]
        )
        if lease is None:
            return json.dumps(
                {"granted": False, **session.scheduler.status(actor, session.names())}
            )
        # stop what earlier holders left on these GPUs before handing them over
        for slot in list(lease.slots):
            await session.reclaim(slot)
        session.persist()
        env = session.lease_env(lease)
        return json.dumps(
            {
                "granted": True,
                "your_gpus": sorted(lease.slots),
                "requested_gpus": count,
                "CUDA_VISIBLE_DEVICES": env["CUDA_VISIBLE_DEVICES"],
                "PORT": env["PORT"],
                "INFERENCEBENCH_PORTS": env["INFERENCEBENCH_PORTS"],
                # a grown lease keeps the expiry of its first grant
                "reclaimable_after_seconds": max(
                    0, round(lease.expires_at - session.scheduler.clock())
                ),
            }
        )

    return ToolDef(gpu_lease, name="gpu").as_tool()


def leased_evaluate(session: Session, actor: str) -> Tool:
    """Measure a peer's leased server with the development evaluator and record contention."""

    async def evaluate(quick: bool = False, gpu: int | None = None) -> str:
        """Measure a server on one of your leased GPUs with the fast development evaluation.

        It uses a few visible development requests and a small quality sample;
        submit runs the hidden final evaluation.

        Args:
            quick: Use fewer requests and quality questions for a smoke test.
            gpu: Which of your GPUs to measure; the server must listen on port 8000 plus this number. Omit it to use your first GPU.
        """
        lease = session.scheduler.held(actor)
        if lease is None or not session.scheduler.protect(lease):
            raise ToolError(
                "evaluate measures a server on your leased GPU. Call gpu with "
                'action="acquire", start ./start_server.sh (it reads $PORT), then evaluate.'
            )
        try:
            slot = min(lease.slots) if gpu is None else gpu
            if slot not in lease.slots:
                raise ToolError(
                    f"You do not hold GPU {gpu}; yours are {sorted(lease.slots)}"
                )
            return await measure(session, actor, lease, BASE_PORT + slot, quick, slot)
        finally:
            session.scheduler.unprotect(lease)

    return ToolDef(evaluate, name="evaluate").as_tool()


def open_evaluate(session: Session, actor: str) -> Tool:
    """Measure any server on the shared machine when GPU access is unmanaged."""

    async def evaluate(quick: bool = False, port: int = BASE_PORT) -> str:
        """Measure a server on the shared development machine with the fast development evaluation.

        It uses a few visible development requests and a small quality sample;
        submit runs the hidden final evaluation.

        Args:
            quick: Use fewer requests and quality questions for a smoke test.
            port: The port your server listens on; by convention GPU k serves on 8000 plus k.
        """
        if type(port) is not int or not 1 <= port <= 65535:
            raise ToolError("port must be an integer from 1 to 65535")
        return await measure(session, actor, None, port, quick)

    return ToolDef(evaluate, name="evaluate").as_tool()


async def measure(
    session: Session,
    actor: str,
    lease: Lease | None,
    port: int,
    quick: bool,
    slot: int | None = None,
) -> str:
    """Run the development evaluator against one local port and record the check."""
    async with recorded_check(actor=actor, slot=slot, port=port, quick=quick) as check:
        path = f"{TASK_DIR}/metrics-{check.id}.json"
        command = [
            f"{TASK_DIR}/evaluate.py",
            "--json-output-file",
            path,
            "--server-url",
            f"http://127.0.0.1:{port}",
        ] + (["--quick"] if quick else [])
        async with session.command():
            check.concurrent_commands = session.running
            # other agents' processes on a leased GPU; unmanaged GPUs have no owner
            before = None if slot is None else await session.foreign_processes(slot)
            result = await session.workspace.exec(
                command,
                env={"CUDA_VISIBLE_DEVICES": ""},
                timeout=session.tool_timeout,
                timeout_retry=False,
            )
            after = None if slot is None else await session.foreign_processes(slot)
        check.contended = (
            None if before is None or after is None else bool(before or after)
        )
        if not result.success:
            text = result.stdout + result.stderr
            signature = pod.detect_oom(text)
            if signature:
                text += session.record_oom(actor, lease, "evaluate", signature)
            raise ToolError(text)
        metrics = await read_metrics(session.workspace, path)
        check.metrics, check.status = metrics, "completed"
        check.proxy_speedup = session.proxy_speedup(metrics)
        reply: dict[str, Any] = {
            "proxy_speedup": check.proxy_speedup,
            "quality_pass": (metrics.get("quality_check") or {}).get("pass"),
        }
        if slot is not None:
            reply["other_processes_on_your_gpu"] = check.contended
        return json.dumps({**reply, "metrics": metrics})


def submit_tool(session: Session, actor: str) -> Tool:
    """Snapshot the shared task directory and queue it for the hidden final evaluation."""
    limit = session.options["max_submission_bytes"]

    async def submit() -> str:
        """Submit the current /home/agent/task for the hidden final evaluation.

        The snapshot is graded later, one submission at a time, on a separate
        GPU: ./start_server.sh is launched fresh with PORT=8000 and measured
        on held-out requests and the quality gate. Everything the server needs
        must be inside /home/agent/task. You may submit any number of times,
        but submit refuses new content while the maximum number of snapshots
        are waiting for or in grading. An identical snapshot reuses the earlier
        grade. Every submission is graded and reported; the task instructions
        say which one sets the score.
        """
        async with session.pack_lock:
            # measure when the snapshot starts, not after a long pack and copy
            measures = session.measures()
            try:
                info, local = await session.snapshot(limit)
            except ToolError:
                raise
            except Exception as error:
                # a transport failure fails this call, not the whole attempt
                session.event("snapshot_error", actor, error=type(error).__name__)
                raise ToolError(
                    f"The snapshot could not be copied ({type(error).__name__})."
                ) from error
        try:
            item = await session.queue.submit(
                actor, info["digest"], info["bytes"], str(local), measures
            )
        except QueueFull as error:
            raise ToolError(
                f"{error}; wait for a result before submitting again."
            ) from error
        session.event(
            "submitted",
            actor,
            number=item.number,
            digest=info["digest"],
            bytes=info["bytes"],
            files=info["files"],
        )
        session.persist()
        reuse = (
            f" It matches submission #{item.same_as}, so that grade is reused."
            if item.same_as
            else ""
        )
        return (
            f"Submission #{item.number} recorded ({info['files']} files, "
            f"{info['bytes']} bytes compressed); {session.queue.pending()} waiting for "
            f"or in grading.{reuse} Once graded, {SCORING_RULE_TEXT[session.queue.scoring]}. "
            "Results appear in your updates and through submissions."
        )

    return ToolDef(submit, name="submit").as_tool()


def submissions_tool(session: Session, actor: str) -> Tool:
    """Report every team submission and its hidden-evaluation result to one peer."""

    async def submissions() -> str:
        """List the team's submissions with their status and hidden-evaluation results.

        A result gives an outcome and a speedup over the Transformers baseline,
        or the fallback score of 1.0 when the submission is invalid.
        """
        # this listing reports every result, so later updates do not repeat them
        session.queue.unseen(actor)
        selected = session.queue.select()
        return json.dumps(
            {
                "scoring_rule": SCORING_RULE_TEXT[session.queue.scoring],
                "currently_scored_submission": selected.number if selected else None,
                "submissions": [
                    session.queue.view(actor, item) for item in session.queue.items
                ],
            }
        )

    return ToolDef(submissions, name="submissions").as_tool()


def peer_tools(tools: list[Tool], lock: asyncio.Lock, actor: str) -> list[Tool]:
    """Bind the shared workspace tools to one peer: one command lock, or GPU leases."""
    session = current()
    if session is None:
        return [
            serialized_tool(item, lock, actor)
            if ToolDef(item).name in {"bash", "python", "evaluate"}
            else item
            for item in tools
        ]
    bound = []
    for item in tools:
        name = ToolDef(item).name
        if name in {"bash", "python"}:
            bound.append(leased_tool(item, session, actor))
        elif name == "evaluate":
            bound.append(
                leased_evaluate(session, actor)
                if session.managed
                else open_evaluate(session, actor)
            )
        else:
            bound.append(item)
    return bound + [
        *([gpu_tool(session, actor)] if session.managed else []),
        submit_tool(session, actor),
        submissions_tool(session, actor),
    ]


def bind_meters(meters: dict[str, Any]) -> None:
    """Let the session read the harness's per-peer token meters for submission records."""
    session = current()
    if session is not None:
        session.meters = meters


def decision_notes(actor: str) -> str:
    """Summarize new submission results and lease notices for a peer's next decision."""
    session = current()
    if session is None:
        return ""
    session.announce_revocations()
    lines = session.notices.pop(actor, [])
    for view in session.queue.unseen(actor):
        result = view["result"] or {}
        lines.append(
            f"Submission #{view['number']} (by {view['by']}): "
            f"{result.get('outcome', view['status'])}"
            + (f", speedup {result['speedup']:.2f}x" if "speedup" in result else "")
            + "."
        )
    if not lines:
        return ""
    selected = session.queue.select()
    lines.append(
        f"Submission #{selected.number} currently sets the score "
        f"({SCORING_RULE_TEXT[session.queue.scoring]})."
        if selected
        else "No valid submission yet; the score is the 1.0 baseline."
    )
    return "\n".join(lines)


def finish_peer(actor: str) -> None:
    """Release a finished peer's GPU lease and queued request."""
    session = current()
    if session is not None:
        session.scheduler.finish(actor)
        session.persist()
