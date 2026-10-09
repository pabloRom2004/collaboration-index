"""Serialize shared workspace commands and retain development evaluator feedback."""

import asyncio
import json
from functools import wraps
from typing import Any
from uuid import uuid4

from inspect_ai.tool import Tool, ToolDef, ToolError, ToolResult, tool
from inspect_ai.tool import bash as native_bash
from inspect_ai.tool import python as native_python
from inspect_ai.util import store_as

from collaboration_index.inferencebench.resource import workspace
from collaboration_index.inferencebench.state import (
    DevelopmentCheck,
    InferenceHistory,
    WorkspaceCall,
)
from collaboration_index.state import now


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


def remote_tool(item: Tool, timeout: int) -> Tool:
    """Preserve native shell schemas while executing every command on the external GPU."""
    definition = ToolDef(item)

    @wraps(definition.tool)
    async def execute(**kwargs: Any) -> str:
        """Run the native bash or Python command through the owned pod's SSH transport."""
        command = kwargs["command"] if definition.name == "bash" else "python3 -"
        result = await workspace().exec(
            ["bash", "--login", "-c", command],
            input=kwargs.get("code"),
            timeout=timeout,
        )
        return (f"{result.stderr}\n" if result.stderr else "") + result.stdout

    return ToolDef(
        execute,
        name=definition.name,
        description=definition.description,
        parameters=definition.parameters,
    ).as_tool()


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
        history = store_as(InferenceHistory)
        call = WorkspaceCall(
            id=uuid4().hex, actor=actor, tool=definition.name, requested=now()
        )
        update_call(history, call)
        try:
            async with lock:
                call.started, call.status = now(), "running"
                update_call(history, call)
                return await definition.tool(**kwargs)
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

    return ToolDef(
        execute,
        name=definition.name,
        description=definition.description,
        parameters=definition.parameters,
    ).as_tool()


@tool
def evaluate(timeout: int) -> Tool:
    """Run InferenceBench's development evaluator and retain its JSON measurements."""

    async def execute(quick: bool = False) -> str:
        """Measure the current shared server on development inputs.

        Args:
            quick: Use the original evaluator's quick smoke mode.
        """
        history = store_as(InferenceHistory)
        check = DevelopmentCheck(id=uuid4().hex, started=now())
        checks = history.checks
        checks.append(check)
        history.checks = checks
        path = f"/home/agent/task/metrics-{check.id}.json"
        command = ["/home/agent/task/evaluate.py", "--json-output-file", path]
        if quick:
            command.append("--quick")
        try:
            result = await workspace().exec(
                command, timeout=timeout, timeout_retry=False
            )
            if not result.success:
                raise ToolError(result.stdout + result.stderr)
            try:
                content = await workspace().read_file(path)
            except FileNotFoundError as error:
                raise ToolError(
                    "Development evaluator did not write its metrics file"
                ) from error
            try:
                metrics = json.loads(content)
            except json.JSONDecodeError as error:
                raise ToolError(
                    "Development evaluator returned malformed JSON"
                ) from error
            if not isinstance(metrics, dict):
                raise ToolError("Development evaluator returned a non-object result")
            check.metrics, check.status = metrics, "completed"
            return json.dumps(metrics)
        except BaseException as error:
            check.status = (
                "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
            )
            check.error_type = type(error).__name__
            raise
        finally:
            check.completed = now()
            history.checks = checks

    return execute
