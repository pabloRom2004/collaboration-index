"""Shared-machine team mode: private agent workspaces, code-only submissions, one team grade.

Every agent sees all GPUs of one pod, and nothing schedules them. Each agent gets its own
directory and Python environment (.venv) on the pod's persistent volume. ``submit`` stores
the caller's code plus its .venv package list, and the newest submission from any agent is
the team's answer. After the deadline the pod restarts, earlier submissions are measured for
the progress record (not the score), and the team's submission is restored with a rebuilt
.venv and graded on GPU 0.
"""

import json
import shlex
import time
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from inspect_ai.tool import Tool, ToolDef, ToolError
from inspect_ai.util import SandboxEnvironment, StoreModel, store, store_as
from pydantic import Field

from collaboration_index.inferencebench import pod
from collaboration_index.inferencebench.resource import workspace
from collaboration_index.inferencebench.session import (
    ARCHIVE_TIMEOUT,
    BASE_PORT,
    POD,
    PYTHON,
    REMOTE,
    SHORT_TIMEOUT,
    TASK_DIR,
    board_names,
    checked,
    classify_final,
    install_scripts,
    last_json,
    speedup,
)
from collaboration_index.state import now

AGENTS = "/workspace/agents"
SHARED = "/workspace/shared"
SUBMISSIONS = "/workspace/submissions"
CACHE = {
    "UV_CACHE_DIR": "/workspace/.uv-cache",
    "PIP_CACHE_DIR": "/workspace/.pip-cache",
    # copies, so an agent that edits an installed file cannot change other environments
    "UV_LINK_MODE": "copy",
}
# The command travels on stdin into a file, so `pkill -f <pattern>` cannot match the
# shell running it and kill the caller's own command.
RUNNER = (
    'f=$(mktemp /tmp/agent-command-XXXXXX.sh); trap \'rm -f "$f"\' EXIT; cat > "$f"; '
    'bash --login "$f" </dev/null'
)
FIELDS = {"enabled", "gpus", "volume_gb", "max_snapshot_bytes", "track_minutes"}


class TeamWorkspace(StoreModel):
    """Keep each agent's workspace, every submission and the post-deadline measurements."""

    options: dict[str, Any] = Field(default_factory=dict)
    # evaluator actor -> opaque workspace handle; peers never see evaluator IDs
    handles: dict[str, str] = Field(default_factory=dict)
    submissions: list[dict[str, Any]] = Field(default_factory=list)
    # actor -> number of submissions already announced to that actor
    seen: dict[str, int] = Field(default_factory=dict)
    tracked: list[dict[str, Any]] = Field(default_factory=list)


def validate(options: dict[str, Any]) -> None:
    """Reject malformed team-workspace settings before allocating a paid GPU."""
    if set(options) != FIELDS:
        raise ValueError("team_workspace must contain exactly the documented fields")
    if not 1 <= options["gpus"] <= 8:
        raise ValueError("team_workspace gpus must be from 1 to 8")
    if options["volume_gb"] < 1 or options["max_snapshot_bytes"] < 1 or options["track_minutes"] < 0:
        raise ValueError("team_workspace sizes must be positive and track_minutes nonnegative")


def pod_config(options: dict[str, Any], folder: str) -> str:
    """Write the bundled RunPod config with this mode's GPU count and volume size."""
    from collaboration_index.inferencebench.backend.run_config import load_config

    config = load_config("runpod.yaml")
    config["pod"]["gpuCount"] = options["gpus"]
    config["pod"]["volumeInGb"] = options["volume_gb"]
    path = Path(folder).expanduser().resolve() / f"runpod-team-{options['gpus']}gpu-{uuid4().hex[:8]}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return str(path)


def directory(handle: str) -> str:
    """Return an agent's workspace path from its opaque handle."""
    return f"{AGENTS}/{handle}"


def agent_rc(path: str) -> str:
    """Activate an agent's .venv and start its commands in its workspace."""
    exports = {"AGENT_WORKDIR": path, "VIRTUAL_ENV": f"{path}/.venv", **CACHE}
    lines = [f"export {key}={value}" for key, value in exports.items()]
    return "\n".join([*lines, f'export PATH="{path}/.venv/bin:$PATH"', f"cd {path}"]) + "\n"


async def setup(actors: list[str], options: dict[str, Any]) -> None:
    """Give every agent a copy of the starter files and its own .venv."""
    env = workspace()
    await install_scripts(env)
    handles = {actor: uuid4().hex[:8] for actor in actors}
    script = ["set -euo pipefail", f"mkdir -p {AGENTS} {SHARED} {SUBMISSIONS}"]
    for handle in handles.values():
        path = directory(handle)
        script += [
            f"mkdir -p {path}",
            f"cp -a {TASK_DIR}/. {path}/",
            f"uv venv --seed --system-site-packages --python /usr/bin/python3 {path}/.venv",
            f"printf %s {shlex.quote(agent_rc(path))} > {path}/.agentrc",
        ]
    await checked(env, ["bash", "-c", "\n".join(script)], ARCHIVE_TIMEOUT, CACHE)
    state = store_as(TeamWorkspace)
    state.options = options
    state.handles = handles


def active() -> TeamWorkspace | None:
    """Return the team-workspace state when this sample uses the mode."""
    state = store_as(TeamWorkspace)
    return state if state.handles else None


def command_tool(item: Tool, actor: str, timeout: int) -> Tool:
    """Run an agent's bash or python call in its own workspace and .venv, unserialized."""
    from collaboration_index.inferencebench.tools import rewrap

    definition = ToolDef(item)

    @wraps(definition.tool)
    async def execute(**kwargs: Any) -> str:
        path = directory(store_as(TeamWorkspace).handles[actor])
        if definition.name == "bash":
            body = kwargs["command"]
        else:
            body = f"python3 - <<'INFERENCEBENCH_PYTHON'\n{kwargs['code']}\nINFERENCEBENCH_PYTHON"
        result = await workspace().exec(
            ["bash", "-c", RUNNER], input=f". {path}/.agentrc\n{body}\n", timeout=timeout
        )
        return (f"{result.stderr}\n" if result.stderr else "") + result.stdout

    return rewrap(definition, execute)


def evaluate_tool(actor: str, timeout: int) -> Tool:
    """Measure a server on a chosen port with the development evaluator in the agent's workspace."""
    from collaboration_index.inferencebench.tools import read_metrics, recorded_check

    async def evaluate(quick: bool = False, port: int = BASE_PORT) -> str:
        """Measure a server on this machine with the development evaluation.

        The full mode uses the development requests and as many quality
        questions as the final evaluation; quick mode uses fewer and is only a
        smoke test, so its quality result is noisy.

        Args:
            quick: Use fewer requests and quality questions for a smoke test.
            port: The port your server listens on; by convention GPU k serves on 8000 plus k.
        """
        path = directory(store_as(TeamWorkspace).handles[actor])
        async with recorded_check(actor=actor, port=port, quick=quick) as check:
            output = f"{path}/metrics-{check.id}.json"
            command = [f"{path}/evaluate.py", "--json-output-file", output,
                       "--server-url", f"http://127.0.0.1:{port}"]
            result = await workspace().exec(
                command + (["--quick"] if quick else []),
                env={"CUDA_VISIBLE_DEVICES": ""},
                timeout=timeout,
                timeout_retry=False,
            )
            if not result.success:
                raise ToolError(result.stdout + result.stderr)
            metrics = await read_metrics(workspace(), output)
            check.metrics, check.status = metrics, "completed"
            return json.dumps(metrics)

    return ToolDef(evaluate, name="evaluate").as_tool()


def submit_tool(actor: str) -> Tool:
    """Store the caller's workspace code as the team's submission."""

    async def submit() -> str:
        """Make your workspace the team's submission.

        Saves your workspace's code and the package list of its .venv, not the
        environment itself. The newest submission from any agent replaces
        earlier ones; all agents are told. After the deadline it is restored at
        the same path, its .venv is rebuilt, and ./start_server.sh runs fresh
        with PORT=8000 on GPU 0.
        """
        state = store_as(TeamWorkspace)
        handle = state.handles[actor]
        limit = state.options["max_snapshot_bytes"]
        remote = f"{SUBMISSIONS}/{handle}-{uuid4().hex[:12]}.tar.gz"
        for _ in range(2):  # files that change while the archive is written fail one attempt
            result = await workspace().exec(
                [PYTHON, POD, "snapshot", directory(handle), remote, "--max-bytes", str(limit)],
                env=CACHE,
                timeout=ARCHIVE_TIMEOUT,
                timeout_retry=False,
            )
            if result.success or result.returncode == pod.TOO_LARGE:
                break
        if result.returncode == pod.TOO_LARGE:
            raise ToolError(
                f"Your workspace is larger than {limit} bytes without its .venv. Move "
                "model weights and other large files outside it, or have start_server.sh "
                "create them."
            )
        if not result.success:
            raise ToolError("The snapshot failed.\n" + result.stderr[-1500:])
        info = last_json(result.stdout)
        # a small offline copy, so the analysis does not depend on the pod
        folder = Path(store().get("artifacts")) / "team-submissions"
        folder.mkdir(parents=True, exist_ok=True)
        local = folder / Path(remote).name
        await workspace().download(remote, str(local))
        # no await between numbering and appending, so concurrent submits stay ordered
        submissions = state.submissions
        number = len(submissions) + 1
        submissions.append({
            "number": number,
            "actor": actor,
            "by": board_names().get(actor, "an agent"),
            "time": now(),
            "archive": str(local),
            **info,
        })
        state.submissions = submissions
        return json.dumps({
            "submission": number,
            "status": "This is now the team's submission; it is graded after the deadline.",
            "saved_files": info["files"],
        })

    return ToolDef(submit, name="submit").as_tool()


def bind(tools: list[Tool], actor: str, timeout: int) -> list[Tool]:
    """Give one agent its workspace-bound commands, its evaluate and submit."""
    bound = []
    for item in tools:
        name = ToolDef(item).name
        if name in {"bash", "python"}:
            bound.append(command_tool(item, actor, timeout))
        elif name == "evaluate":
            bound.append(evaluate_tool(actor, timeout))
        else:
            bound.append(item)
    return bound + [submit_tool(actor)]


def status() -> str:
    """Describe the team's current submission."""
    state = store_as(TeamWorkspace)
    if not state.submissions:
        return "The team has no submission yet; without one the score is the 1.0 baseline."
    last = state.submissions[-1]
    return f"The team's submission is #{last['number']}, by {last['by']}."


def notes(actor: str) -> str:
    """Announce submissions this agent has not been told about."""
    state = store_as(TeamWorkspace)
    fresh = state.submissions[state.seen.get(actor, 0):]
    if not fresh:
        return ""
    state.seen = {**state.seen, actor: len(state.submissions)}
    return "\n".join([*(f"Submission #{s['number']} by {s['by']}." for s in fresh), status()])


def launcher(path: str, error: str | None = None) -> str:
    """Run a restored workspace's start_server.sh with its .venv, on GPU 0 and port 8000."""
    if error is not None:
        return f"#!/bin/bash\necho {shlex.quote('The submission could not be restored: ' + error)} >&2\nexit 1\n"
    return (
        "#!/bin/bash\n"
        f"# The submitted workspace is {path}; this file only launches it.\n"
        f". {path}/.agentrc\n"
        f"export CUDA_VISIBLE_DEVICES=0 PORT={BASE_PORT}\n"
        'exec bash ./start_server.sh "$@"\n'
    )


async def clear_gpus(env: SandboxEnvironment) -> None:
    """Stop every GPU process and the port-8000 server, then wait until GPU 0's memory is free."""
    gpus = [f"--gpu={index}" for index in range(store_as(TeamWorkspace).options["gpus"])]
    await checked(env, [PYTHON, POD, "kill", *gpus, f"--port={BASE_PORT}"], SHORT_TIMEOUT)
    wait = (
        "for i in $(seq 60); do "
        "[ \"$(nvidia-smi -i 0 --query-gpu=memory.used --format=csv,noheader,nounits)\" -lt 2000 ] && exit 0; "
        "sleep 2; done; exit 1"
    )
    await checked(env, ["bash", "-c", wait], SHORT_TIMEOUT + 60)


async def install(env: SandboxEnvironment, record: dict[str, Any], timeout: int) -> str | None:
    """Restore one submission at its workspace path and point the task launcher at it; return any error."""
    path = directory(store_as(TeamWorkspace).handles[record["actor"]])
    error = None
    try:
        await env.upload(record["archive"], f"{REMOTE}/restore.tar.gz")
        await checked(env, [PYTHON, POD, "restore", f"{REMOTE}/restore.tar.gz", path], timeout, CACHE)
    except Exception as failure:  # an unrestorable submission is invalid, not an evaluator crash
        error = f"{type(failure).__name__}: {str(failure)[-1500:]}"
    await env.write_file(f"{TASK_DIR}/start_server.sh", launcher(path, error))
    await checked(env, ["chmod", "755", f"{TASK_DIR}/start_server.sh"], SHORT_TIMEOUT)
    return error


async def measure(env: SandboxEnvironment, timeout: int) -> dict[str, Any]:
    """Run the upstream final measurement on the installed launcher and return its metrics."""
    await checked(env, ["rm", "-rf", f"{REMOTE}/final.json", f"{REMOTE}/final"], SHORT_TIMEOUT)
    try:
        await checked(env, [PYTHON, f"{REMOTE}/runtime.py", "final", f"{REMOTE}/options.json"], timeout)
        return json.loads(await env.read_file(f"{REMOTE}/final.json"))
    finally:
        await clear_gpus(env)


async def prepare_final(env: SandboxEnvironment, state: Any) -> None:
    """After the restart, measure earlier submissions for the record, then install the team's one."""
    team = active()
    if team is None or not team.submissions:
        return
    baseline = json.loads((Path(store().get("artifacts")) / "baseline.json").read_text())
    scenario = state.metadata["scenario"]
    final = team.submissions[-1]
    deadline = time.monotonic() + 60 * team.options["track_minutes"]
    tracked, graded = [], {final["digest"]}
    await clear_gpus(env)
    for record in team.submissions[:-1]:
        row: dict[str, Any] = {"number": record["number"], "by": record["by"], "time": record["time"]}
        if record["digest"] in graded:
            row["status"] = "same code as another graded submission"
        elif time.monotonic() > deadline:
            row["status"] = "not graded: track_minutes ran out"
        else:
            try:
                remaining = max(60, int(deadline - time.monotonic()))
                restore_error = await install(env, record, remaining)
                if restore_error:
                    raise RuntimeError(restore_error)
                metrics = await measure(env, max(60, int(deadline - time.monotonic())))
                quality = (metrics.get("quality_check") or {}).get("datasets", {}).get("mmlu_pro", {})
                row.update(
                    status="graded",
                    **classify_final(metrics, baseline, scenario),
                    measured_speedup=speedup(metrics, baseline, scenario),
                    quality_ratio=quality.get("ratio"),
                )
                graded.add(record["digest"])
            except Exception as error:  # a broken earlier submission must not block the score
                row["status"] = f"error: {type(error).__name__}: {str(error)[:300]}"
        tracked.append(row)
        team.tracked = tracked
    error = await install(env, final, ARCHIVE_TIMEOUT)
    if error:
        team.tracked = [*tracked, {"number": final["number"], "status": f"final restore failed: {error[:300]}"}]
    # the scorer's own run must not read a tracked measurement
    await checked(env, ["rm", "-rf", f"{REMOTE}/final.json", f"{REMOTE}/final"], SHORT_TIMEOUT)
