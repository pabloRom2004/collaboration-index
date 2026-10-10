"""Verify the shared InferenceBench adapter through scripted native peer attempts."""

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import (
    ChatMessageTool,
    GenerateConfig,
    ModelOutput,
    ModelUsage,
    get_model,
)
from inspect_ai.util import (
    ExecResult,
    SandboxEnvironment,
    SandboxEnvironmentSpec,
    sandbox,
    sandboxenv,
)

pytest.importorskip("asyncssh")
pytest.importorskip("modal")

from collaboration_index.inferencebench import inferencebench  # noqa: E402
from collaboration_index.inferencebench.task import ARGS  # noqa: E402
from collaboration_index.replay import replay_data  # noqa: E402

LOGS = Path(__file__).parents[1] / "logs"
METRICS = {
    "profiles": {"fixture": {"success_count": 10, "ttft": {"p50": 1.0}}},
    "quality_check": {"pass": True},
}


@sandboxenv(name="inferencebench_fixture")
class FixtureSandbox(SandboxEnvironment):
    """Simulate GPU measurements while exercising real task, tools, board and scorer code."""

    def __init__(self) -> None:
        """Initialize an isolated authored filesystem and command-concurrency counter."""
        super().__init__()
        self.files: dict[str, str | bytes] = {}
        self.active = self.peak = self.restarts = 0
        self.terminated = False

    @property
    def resource_id(self) -> str:
        """Expose a harmless resource identity for the original preparation receipts."""
        return "authored-fixture-gpu"

    @classmethod
    async def sample_init(
        cls, task_name: str, config: Any, metadata: Any
    ) -> dict[str, SandboxEnvironment]:
        """Create one fixture filesystem per independent team sample."""
        return {"default": cls()}

    @classmethod
    async def sample_cleanup(
        cls, task_name: str, config: Any, environments: Any, interrupted: bool
    ) -> None:
        """Leave no external resources after the authored attempt."""
        return None

    async def write_file(self, file: str, contents: str | bytes) -> None:
        """Store a fixture file without executing participant content."""
        self.files[file] = contents

    async def read_file(self, file: str, text: bool = True) -> Any:
        """Read fixture content and preserve missing-file failures."""
        if file not in self.files:
            raise FileNotFoundError(file)
        return self.files[file]

    async def upload(self, local: str, remote: str) -> None:
        """Copy trusted host fixture inputs into the simulated scoring filesystem."""
        self.files[remote] = Path(local).read_bytes()

    async def download(self, remote: str, local: str) -> None:
        """Retain fixture measurements through the original artifact capture code."""
        content = self.files.get(remote, b"authored archive")
        Path(local).write_bytes(
            content.encode() if isinstance(content, str) else content
        )

    async def restart(self, config: Any) -> "FixtureSandbox":
        """Record scoring restart after every peer has completed."""
        self.restarts += 1
        assert self.active == 0
        return self

    async def terminate(self) -> None:
        """Record the upstream scorer's resource cleanup."""
        self.terminated = True

    async def exec(self, cmd: list[str], **kwargs: Any) -> ExecResult[str]:
        """Respond only to authored fixture commands and simulate serialized development work."""
        stdout = ""
        if cmd == ["cat", "/etc/hostname"]:
            stdout = "fixture-gpu\n"
        elif cmd == ["cat", "/workspace/team.json"]:
            stdout = str(self.files[cmd[1]])
        elif cmd[0] == "nvidia-smi":
            stdout = "name, memory.total, driver_version\nAuthored H100, 80000 MiB, fixture\n"
        elif "prepare" in cmd:
            self.files.update(
                {
                    "/tmp/inferencebench/baseline.json": json.dumps(
                        {
                            "profiles": {
                                "fixture": {"success_count": 10, "ttft": {"p50": 2.0}}
                            }
                        }
                    ),
                    **{
                        f"/tmp/inferencebench/{name}": "{}"
                        for name in [
                            "quality.json",
                            "heldout-requests.jsonl",
                            "quality-samples.jsonl",
                            "provenance.json",
                        ]
                    },
                }
            )
        elif "final" in cmd:
            self.files["/tmp/inferencebench/final.json"] = json.dumps(METRICS)
            self.files["/tmp/inferencebench/final-server.log"] = "authored final server"
        elif cmd[0] in {"bash", "python3", "/home/agent/task/evaluate.py"}:
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.03)
                if cmd[0] == "bash":
                    if "fixture-write" in cmd[-1]:
                        self.files["shared"] = "shared fixture"
                    elif "fixture-read" in cmd[-1]:
                        assert self.files["shared"] == "shared fixture"
                        stdout = "shared fixture"
                if "--json-output-file" in cmd:
                    self.files[cmd[cmd.index("--json-output-file") + 1]] = json.dumps(
                        METRICS
                    )
            finally:
                self.active -= 1
        return ExecResult(True, 0, stdout, "")


def fixture_model(expect_evaluate_error: bool = False) -> Any:
    """Have four peers communicate, use the same files and queue development evaluations."""
    written = asyncio.Event()

    async def reply(
        messages: list[Any], tools: Any, choice: Any, config: Any
    ) -> ModelOutput:
        """Script each peer independently and verify evaluator-injected updates and tool affordances."""
        opening = next(m for m in messages if (m.metadata or {}).get("team_actor"))
        actor = opening.metadata["team_actor"]
        done = [m for m in messages if isinstance(m, ChatMessageTool)]
        errors = [m for m in done if m.error]
        assert all(
            expect_evaluate_error and m.function == "evaluate" for m in errors
        ), [m.error for m in errors]
        names = {t.name for t in tools}
        assert {"bash", "python", "evaluate", "web_search", "message_board"} <= names
        assert "submit" not in names
        assert "Time update:" not in messages[-1].text
        assert "Token update:" in messages[-1].text
        assert "unread" in messages[-1].text.lower()
        step = len(done)
        if step == 0:
            function, arguments = (
                "message_board",
                {"action": "register", "name": "Fixture " + actor[-1]},
            )
        elif step == 1:
            function, arguments = (
                "message_board",
                {"action": "send", "message": "Coordinate fixture work"},
            )
        elif step == 2:
            if actor == "agent_0":
                function, arguments = "bash", {"command": "fixture-write"}
            else:
                await asyncio.wait_for(written.wait(), 20)
                function, arguments = "bash", {"command": "fixture-read"}
        elif step == 3:
            if actor == "agent_0":
                written.set()
            function, arguments = "evaluate", {"quick": True}
        elif step == 4:
            function, arguments = "python", {"code": "print('authored fixture')"}
        else:
            output = ModelOutput.from_content(
                "mockllm/model", "Fixture continues until its native cap."
            )
            output.usage = ModelUsage(
                input_tokens=20, output_tokens=10, total_tokens=30
            )
            return output
        output = ModelOutput.for_tool_call("mockllm/model", function, arguments)
        output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
        return output

    return get_model(
        "mockllm/model", custom_outputs=reply, config=GenerateConfig(max_connections=4)
    )


def test_inferencebench_team_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-trip budgets, shared files, serialized checks, upstream final scoring and replay."""
    from collaboration_index.inferencebench.backend import environment

    monkeypatch.setattr(
        environment, "gpu_environment", lambda: sandbox().as_type(FixtureSandbox)
    )
    monkeypatch.setattr(environment, "BASELINE_CACHE", tmp_path / "baselines")
    task = inferencebench(
        agents=4, token_limit_per_agent=240, artifact_dir=str(tmp_path)
    )
    task.sandbox = SandboxEnvironmentSpec("inferencebench_fixture")
    judge = get_model(
        "mockllm/judge",
        custom_outputs=[
            ModelOutput.from_content(
                "mockllm/judge", "No contamination detected\nOnly allowed use detected"
            )
        ],
    )
    [log] = inspect_eval(
        task,
        model=fixture_model(),
        model_roles={"integrity": judge},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error.traceback if log.error else None
    log = read_eval_log(log.location)
    sample = log.samples[0]
    assert not sample.error, sample.error
    assert sample.scores["inference_team_speedup"].value["speedup"] == 2
    peers = sample.store["TeamHistory:peers"]
    assert len(peers) == 4
    assert {p["sandbox_hostname"] for p in peers} == {"fixture-gpu"}
    assert all(
        p["status"] == "limited" and p["tokens"] >= 240 and p["nudges"] for p in peers
    )
    calls = sample.store["InferenceHistory:calls"]
    assert len(calls) == 12 and all(c["status"] == "completed" for c in calls)
    assert {c["actor"] for c in calls} == {f"agent_{i}" for i in range(4)}
    intervals = sorted((c["started"], c["completed"]) for c in calls)
    assert all(left[1] <= right[0] for left, right in zip(intervals, intervals[1:]))
    checks = sample.store["InferenceHistory:checks"]
    assert len(checks) == 4 and all(c["metrics"] == METRICS for c in checks)
    assert sample.store["InferenceHistory:final"]["value"]["speedup"] == 2
    replay = replay_data(Path(log.location), None)
    assert replay["team"]["status"] == "scored" and replay["team"]["speedup"] == 2
    assert replay["team"]["quality"] is None
    assert replay["events"] and len(replay["agents"]) == 4


def test_task_configuration_and_validation() -> None:
    """Preserve workload settings, one GPU and independent peer budgets without an aggregate cap."""
    task = inferencebench(agents=4, token_limit_per_agent=100000)
    assert task.token_limit is None and len(task.dataset) == 1
    assert task.metadata["planned_team_token_budget"] == 400000
    assert task.dataset[0].metadata["agent_seconds"] is None
    assert task.sandbox.type == "inferencebench_runpod"
    assert task.dataset[0].metadata["quality_tau"] == 0.95
    assert (
        task.dataset[0].metadata["base_model"] == "mistralai/Mistral-7B-Instruct-v0.3"
    )
    with pytest.raises(ValueError, match="team sizes"):
        inferencebench(agents=33)
    with pytest.raises(ValueError, match="time limit or a per-agent token limit"):
        inferencebench()
    workload = copy.deepcopy(ARGS["workload"])
    workload["quality_samples"] = 0
    with pytest.raises(ValueError, match="positive"):
        inferencebench(workload=workload)
    assert (
        inferencebench(
            token_limit_per_agent=100000, gpu_management="controller"
        ).sandbox
        is None
    )
    with pytest.raises(ValueError, match="gpu_management"):
        inferencebench(gpu_management="local")


@pytest.mark.parametrize("failure", [None, "prepare", "final", "missing_role"])
def test_controller_gpu_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    """Exercise remote peer tools and cleanup across success, setup failure and grading failure."""
    from collaboration_index.inferencebench import resource
    from collaboration_index.inferencebench.backend import environment
    from collaboration_index.inferencebench.backend import scorers as backend_scorers
    from collaboration_index.inferencebench.backend.runpod_sandbox import RunPodSandbox

    allocated: list[FixtureSandbox] = []
    original_exec = FixtureSandbox.exec
    original_react = backend_scorers.react
    judge_thresholds = []

    def judge_agent(**kwargs: Any) -> Any:
        """Verify the bound judge harness consumes the configured compaction threshold."""
        judge_thresholds.append(kwargs["compaction"].threshold)
        return original_react(**kwargs)

    async def allocate(cls: Any, *args: Any) -> dict[str, FixtureSandbox]:
        """Simulate exactly one external allocation without an Inspect sandbox context."""
        env = FixtureSandbox()
        allocated.append(env)
        return {"default": env}

    async def execute(
        self: FixtureSandbox, cmd: list[str], **kwargs: Any
    ) -> ExecResult[str]:
        """Fail only the selected trusted infrastructure stage after pod allocation."""
        if failure in {"prepare", "final"} and failure in cmd:
            return ExecResult(False, 1, "", "Authored infrastructure failure")
        return await original_exec(self, cmd, **kwargs)

    monkeypatch.setattr(RunPodSandbox, "sample_init", classmethod(allocate))
    monkeypatch.setattr(FixtureSandbox, "exec", execute)
    monkeypatch.setattr(backend_scorers, "react", judge_agent)
    monkeypatch.setattr(environment, "BASELINE_CACHE", tmp_path / "baselines")
    monkeypatch.setenv("HAWK_JOB_ID", "authored-fixture-hawk")
    task = inferencebench(
        agents=4,
        token_limit_per_agent=240,
        artifact_dir=str(tmp_path),
        gpu_management="controller",
        grader_context_window=1050000,
    )
    judge = get_model(
        "mockllm/judge",
        custom_outputs=[
            ModelOutput.from_content(
                "mockllm/judge", "No contamination detected\nOnly allowed use detected"
            )
        ],
    )
    [log] = inspect_eval(
        task,
        model=fixture_model(),
        model_roles={} if failure == "missing_role" else {"integrity": judge},
        log_dir=str(LOGS),
        display="none",
    )
    assert not resource.RESOURCES
    if failure == "missing_role":
        assert not allocated and log.status == "error"
        return
    assert len(allocated) == 1 and allocated[0].terminated
    assert (Path(log.samples[0].store["artifacts"]) / "submission.tar.gz").is_file()
    if failure is not None:
        assert log.status == "error"
        assert log.samples[0].error is not None
        return
    assert log.status == "success", log.error
    sample = read_eval_log(log.location).samples[0]
    assert sample.scores["inference_team_speedup"].value["speedup"] == 2
    assert allocated[0].peak == 1 and allocated[0].restarts == 1
    assert {p["sandbox_hostname"] for p in sample.store["TeamHistory:peers"]} == {
        "fixture-gpu"
    }
    assert len(sample.store["InferenceHistory:calls"]) == 12
    assert len(sample.store["InferenceHistory:checks"]) == 4
    assert sample.store[resource.RESOURCE_KEY] is None
    assert sample.store["external_gpu_lifecycle"]["status"] == "terminated"
    assert judge_thresholds == [787500]


def test_concurrent_external_samples_are_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep simultaneous teams bound to distinct external resources and independent cleanup."""
    from collaboration_index.inferencebench import resource
    from collaboration_index.inferencebench.backend import environment
    from collaboration_index.inferencebench.backend.runpod_sandbox import RunPodSandbox

    allocated: list[FixtureSandbox] = []
    original_exec = FixtureSandbox.exec

    async def allocate(cls: Any, *args: Any) -> dict[str, FixtureSandbox]:
        """Create independent fixture resources and permit both samples to initialize concurrently."""
        env = FixtureSandbox()
        allocated.append(env)
        await asyncio.sleep(0.02)
        return {"default": env}

    async def execute(
        self: FixtureSandbox, cmd: list[str], **kwargs: Any
    ) -> ExecResult[str]:
        """Expose distinct hosts so readiness detects accidental cross-sample binding."""
        if cmd == ["cat", "/etc/hostname"]:
            return ExecResult(True, 0, f"fixture-gpu-{allocated.index(self)}", "")
        return await original_exec(self, cmd, **kwargs)

    async def reply(*args: Any, **kwargs: Any) -> ModelOutput:
        """Use one complete model turn to reach each peer's fixture token allowance."""
        output = ModelOutput.from_content("mockllm/model", "Authored fixture work.")
        output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
        return output

    monkeypatch.setattr(RunPodSandbox, "sample_init", classmethod(allocate))
    monkeypatch.setattr(FixtureSandbox, "exec", execute)
    monkeypatch.setattr(environment, "BASELINE_CACHE", tmp_path / "baselines")
    task = inferencebench(
        agents=2,
        token_limit_per_agent=30,
        artifact_dir=str(tmp_path),
        gpu_management="controller",
        seed_pairs=[[21, 1337], [22, 1338]],
    )
    judge = get_model(
        "mockllm/judge",
        custom_outputs=[
            ModelOutput.from_content(
                "mockllm/judge", "No contamination detected\nOnly allowed use detected"
            )
        ]
        * 2,
    )
    [log] = inspect_eval(
        task,
        model=get_model("mockllm/model", custom_outputs=reply),
        model_roles={"integrity": judge},
        max_samples=2,
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error
    assert len(allocated) == 2 and all(env.terminated for env in allocated)
    assert not resource.RESOURCES
    hosts = [
        {p["sandbox_hostname"] for p in sample.store["TeamHistory:peers"]}
        for sample in log.samples
    ]
    assert all(len(host) == 1 for host in hosts) and hosts[0].isdisjoint(hosts[1])
    assert all(
        sample.scores["inference_team_speedup"].value["speedup"] == 2
        for sample in log.samples
    )


def test_hawk_readonly_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run real pod initialization and final grading with all artifacts outside a read-only working directory."""
    from collaboration_index.inferencebench.backend.runpod_sandbox import RunPodSandbox

    cwd = tmp_path / "readonly"
    cwd.mkdir()
    cwd.chmod(0o555)
    root = tmp_path / "run-artifacts" / "team"
    fixture = FixtureSandbox()
    calls = []

    async def request(self: Any, method: str, path: str, **kwargs: Any) -> Any:
        """Simulate only owned pod creation and deletion with the real provider lifecycle."""
        calls.append((method, path))
        return {"id": "authored-fixture-pod"} if method == "POST" else None

    async def ready(self: Any, previous_boot: Any) -> None:
        """Replace SSH bootstrap with an already ready authored filesystem."""
        return None

    async def execute(self: Any, cmd: list[str], **kwargs: Any) -> ExecResult[str]:
        """Forward trusted and participant fixture commands through the simulated remote transport."""
        return await fixture.exec(cmd, **kwargs)

    async def read(self: Any, *args: Any, **kwargs: Any) -> Any:
        """Read only the authored remote filesystem."""
        return await fixture.read_file(*args, **kwargs)

    async def write(self: Any, *args: Any, **kwargs: Any) -> None:
        """Write only the authored remote filesystem."""
        await fixture.write_file(*args, **kwargs)

    async def upload(self: Any, *args: Any, **kwargs: Any) -> None:
        """Upload trusted fixture inputs while keeping controller files in their configured directory."""
        await fixture.upload(*args, **kwargs)

    async def download(self: Any, *args: Any, **kwargs: Any) -> None:
        """Retain authored measurements and submission archives."""
        await fixture.download(*args, **kwargs)

    async def restart(self: Any, config: Any) -> Any:
        """Simulate the single scoring restart after peer join."""
        await fixture.restart(config)
        return self

    monkeypatch.chdir(cwd)
    monkeypatch.setenv("RUNPOD_API_KEY", "authored-fixture-key")
    monkeypatch.setenv("HAWK_JOB_ID", "authored-fixture-hawk")
    for name, method in {
        "_request": request,
        "_wait_ready": ready,
        "exec": execute,
        "read_file": read,
        "write_file": write,
        "upload": upload,
        "download": download,
        "restart": restart,
    }.items():
        monkeypatch.setattr(RunPodSandbox, name, method)
    task = inferencebench(
        agents=4,
        token_limit_per_agent=240,
        artifact_dir=str(root),
        gpu_management="controller",
    )
    judge = get_model(
        "mockllm/judge",
        custom_outputs=[
            ModelOutput.from_content(
                "mockllm/judge", "No contamination detected\nOnly allowed use detected"
            )
        ],
    )
    [log] = inspect_eval(
        task,
        model=fixture_model(),
        model_roles={"integrity": judge},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error
    assert not (cwd / "run-artifacts").exists()
    receipts = list((root / "runpod").glob("*/pod.json"))
    assert (
        len(receipts) == 1
        and json.loads(receipts[0].read_text())["status"] == "terminated"
    )
    assert sum(method == "POST" for method, path in calls) == 1
    assert all(
        path == "/pods/authored-fixture-pod"
        for method, path in calls
        if method == "DELETE"
    )
    sample = log.samples[0]
    assert Path(sample.store["artifacts"]).is_relative_to(root)
    assert (Path(sample.store["artifacts"]) / "submission.tar.gz").is_file()
    assert sample.scores["inference_team_speedup"].value["speedup"] == 2


@pytest.mark.parametrize("failure", ["command", "missing", "malformed", "non_object"])
def test_development_failure_remains_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Keep failed development checks visible while permitting a joined team to receive a final grade."""
    from collaboration_index.inferencebench.backend import environment

    original_exec = FixtureSandbox.exec

    async def failing_check(
        self: FixtureSandbox, cmd: list[str], **kwargs: Any
    ) -> ExecResult[str]:
        """Introduce one authored evaluator failure without changing final trusted measurements."""
        result = await original_exec(self, cmd, **kwargs)
        if cmd[0] == "/home/agent/task/evaluate.py":
            path = cmd[cmd.index("--json-output-file") + 1]
            if failure == "command":
                return ExecResult(False, 1, "", "Authored evaluator failure")
            if failure == "missing":
                del self.files[path]
            else:
                self.files[path] = "broken JSON" if failure == "malformed" else "[]"
        return result

    monkeypatch.setattr(FixtureSandbox, "exec", failing_check)
    monkeypatch.setattr(
        environment, "gpu_environment", lambda: sandbox().as_type(FixtureSandbox)
    )
    monkeypatch.setattr(environment, "BASELINE_CACHE", tmp_path / "baselines")
    task = inferencebench(
        agents=4, token_limit_per_agent=240, artifact_dir=str(tmp_path)
    )
    task.sandbox = SandboxEnvironmentSpec("inferencebench_fixture")
    judge = get_model(
        "mockllm/judge",
        custom_outputs=[
            ModelOutput.from_content(
                "mockllm/judge", "No contamination detected\nOnly allowed use detected"
            )
        ],
    )
    [log] = inspect_eval(
        task,
        model=fixture_model(expect_evaluate_error=True),
        model_roles={"integrity": judge},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error
    sample = read_eval_log(log.location).samples[0]
    checks = sample.store["InferenceHistory:checks"]
    assert len(checks) == 4 and all(c["status"] == "error" for c in checks)
    assert all(c["error_type"] and not c.get("metrics") for c in checks)
    calls = sample.store["InferenceHistory:calls"]
    assert sum(c["status"] == "error" for c in calls) == 4
    assert sample.scores["inference_team_speedup"].value["speedup"] == 2


def test_missing_epoch_judgment_stays_unavailable() -> None:
    """Preserve an unavailable integrity judgment when averaging otherwise successful epochs."""
    import math

    from inspect_ai.scorer import Score

    from collaboration_index.inferencebench.backend.metrics import complete_mean

    result = complete_mean()(
        [Score(value={"speedup": 2}), Score(value={"speedup": math.nan})]
    )
    assert math.isnan(result.value["speedup"])


def test_unavailable_integrity_judgment_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain an unparsed integrity verdict as unavailable in the durable log and portable replay."""
    import math

    from collaboration_index.inferencebench.backend import environment
    from collaboration_index.replay import render

    monkeypatch.setattr(
        environment, "gpu_environment", lambda: sandbox().as_type(FixtureSandbox)
    )
    monkeypatch.setattr(environment, "BASELINE_CACHE", tmp_path / "baselines")
    spec = copy.deepcopy(ARGS["scorer"])
    spec["args"]["max_grader_attempts"] = 1
    task = inferencebench(
        agents=1, token_limit_per_agent=240, artifact_dir=str(tmp_path), scorer=spec
    )
    task.sandbox = SandboxEnvironmentSpec("inferencebench_fixture")
    judge = get_model(
        "mockllm/judge",
        custom_outputs=[
            ModelOutput.from_content("mockllm/judge", "Authored unparsable verdict")
        ],
    )
    [log] = inspect_eval(
        task,
        model=fixture_model(),
        model_roles={"integrity": judge},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error
    value = (
        read_eval_log(log.location).samples[0].scores["inference_team_speedup"].value
    )
    assert math.isnan(value["speedup"])
    replay = replay_data(Path(log.location), None)
    assert replay["team"]["status"] == "unscored"
    assert replay["team"]["speedup"] is None
    assert replay["team"]["metrics"]["speedup"] is None
    render(Path(log.location), None, tmp_path / "unscored-replay.html")


@pytest.mark.docker
def test_inferencebench_docker_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute four peers' shared edits and queued evaluator calls in a real Docker container."""
    import yaml
    from inspect_ai.solver import solver
    from inspect_ai.util import store

    from collaboration_index.inferencebench.backend import environment, scorers

    original_model = fixture_model()
    original_generate = original_model.api.generate

    async def docker_reply(*args: Any, **kwargs: Any) -> Any:
        """Translate authored fixture shell actions into actual shared-file writes and reads."""
        output, model_call = await original_generate(*args, **kwargs)
        for call in output.message.tool_calls or []:
            if call.function == "bash":
                action = call.arguments["command"]
                call.arguments["command"] = (
                    "printf 'shared fixture' > /home/agent/task/shared"
                    if action == "fixture-write"
                    else "test \"$(cat /home/agent/task/shared)\" = 'shared fixture'"
                )
        return output, model_call

    monkeypatch.setattr(original_model.api, "generate", docker_reply)

    @solver
    def prepare_fixture() -> Any:
        """Replace only GPU baseline preparation with authored measurements for the Docker check."""

        async def solve(state: Any, generate: Any) -> Any:
            """Install a real executable development evaluator and trusted synthetic baseline."""
            folder = tmp_path / "measurements"
            folder.mkdir()
            store().set("artifacts", str(folder))
            (folder / "baseline.json").write_text(
                json.dumps(
                    {
                        "profiles": {
                            "fixture": {"success_count": 10, "ttft": {"p50": 2.0}}
                        }
                    }
                )
            )
            script = "#!/usr/local/bin/python\nimport json,sys,time\ntime.sleep(0.05)\n"
            script += (
                "open(sys.argv[sys.argv.index('--json-output-file')+1],'w').write("
                + repr(json.dumps(METRICS))
                + ")\n"
            )
            await sandbox().write_file("/home/agent/task/evaluate.py", script)
            result = await sandbox().exec(
                ["chmod", "+x", "/home/agent/task/evaluate.py"]
            )
            assert result.success
            return state

        return solve

    async def restart_fixture(state: Any, include_transcript: bool) -> Any:
        """Inspect the joined workspace through the original scorer without simulating a GPU restart."""
        assert all(
            p.status == "limited"
            for p in state.store_as(
                __import__(
                    "collaboration_index.state", fromlist=["TeamHistory"]
                ).TeamHistory
            ).peers
        )

        class ScoringWorkspace:
            """Delegate real container reads and leave lifecycle cleanup to Inspect."""

            async def exec(self, *args: Any, **kwargs: Any) -> Any:
                """Execute the explicitly bound fixture judge's inspection commands."""
                return await sandbox().exec(*args, **kwargs)

            async def read_file(self, path: str) -> str:
                """Supply synthetic final measurements and delegate other submission evidence reads."""
                if path == "/tmp/inferencebench/final.json":
                    return json.dumps(METRICS)
                if path == "/tmp/inferencebench/final-server.log":
                    return "authored Docker final"
                return await sandbox().read_file(path)

            async def terminate(self) -> None:
                """Allow Inspect to remove its owned Docker container after final scoring."""
                return None

        return ScoringWorkspace()

    async def fixture_final(env: Any, command: Any, timeout: int) -> str:
        """Acknowledge synthetic GPU measurement while retaining real workspace tool execution."""
        assert command[-2] == "final"
        return "Authored fixture: GPU measurements synthetic"

    monkeypatch.setattr(environment, "prepare_environment", prepare_fixture)
    monkeypatch.setattr(scorers, "restart_for_scoring", restart_fixture)
    monkeypatch.setattr(scorers, "checked_exec", fixture_final)
    task = inferencebench(
        agents=4, token_limit_per_agent=240, artifact_dir=str(tmp_path)
    )
    compose = yaml.safe_load(
        (
            Path(__file__).parents[1]
            / "src/collaboration_index/assets/sandbox/compose.yaml"
        ).read_text()
    )
    compose["services"]["default"]["working_dir"] = "/home/agent/task"
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o777)
    workspace.chmod(0o777)
    # Docker cp cannot read tmpfs files; use an isolated authored fixture volume.
    compose["services"]["default"]["volumes"] = [f"{workspace}:/home/agent/task"]
    path = tmp_path / "fixture-compose.yaml"
    path.write_text(yaml.safe_dump(compose))
    task.sandbox = SandboxEnvironmentSpec("docker", str(path))
    judge = get_model(
        "mockllm/judge",
        custom_outputs=[
            ModelOutput.from_content(
                "mockllm/judge", "No contamination detected\nOnly allowed use detected"
            )
        ],
    )
    [log] = inspect_eval(
        task,
        model=original_model,
        model_roles={"integrity": judge},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error.traceback if log.error else None
    result = read_eval_log(log.location).samples[0]
    assert len({p["sandbox_hostname"] for p in result.store["TeamHistory:peers"]}) == 1
    assert len(result.store["InferenceHistory:checks"]) == 4
    assert all(
        c["status"] == "completed" for c in result.store["InferenceHistory:calls"]
    )
    assert result.scores["inference_team_speedup"].value["speedup"] == 2
