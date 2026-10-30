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


def passing_judge() -> Any:
    """Return an integrity judge that finds no contamination and only allowed use."""
    return get_model(
        "mockllm/judge",
        custom_outputs=[
            ModelOutput.from_content(
                "mockllm/judge", "No contamination detected\nOnly allowed use detected"
            )
        ],
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
    judge = passing_judge()
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
        inferencebench(agents=65)
    assert inferencebench(agents=64, token_limit_per_agent=10).metadata["agents"] == 64
    with pytest.raises(ValueError, match="scoring"):
        inferencebench(token_limit_per_agent=10, scoring="first_submit")
    flags = {**ARGS["evaluation"], "record_wall_clock_timestamp": "yes"}
    with pytest.raises(ValueError, match="evaluation"):
        inferencebench(token_limit_per_agent=10, evaluation=flags)
    with pytest.raises(ValueError, match="evaluation"):
        inferencebench(token_limit_per_agent=10, evaluation={})
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
    leased = copy.deepcopy(ARGS["orchestration"])
    leased["enabled"] = True
    with pytest.raises(ValueError, match="controller"):
        inferencebench(token_limit_per_agent=10, orchestration=leased)
    bad = copy.deepcopy(leased)
    bad["dev_eval"]["quality_seed"] = ARGS["workload"]["quality_seed"]
    with pytest.raises(ValueError, match="quality seed"):
        inferencebench(
            token_limit_per_agent=10, gpu_management="controller", orchestration=bad
        )
    bad = copy.deepcopy(leased)
    bad["workspace_gpus"] = 9
    with pytest.raises(ValueError, match="workspace_gpus"):
        inferencebench(
            token_limit_per_agent=10, gpu_management="controller", orchestration=bad
        )
    for change, message in [
        ({"gpu_access": "shared"}, "gpu_access"),
        ({"gpu_access": "unmanaged", "enforce": "kill"}, "needs gpu_access"),
    ]:
        with pytest.raises(ValueError, match=message):
            inferencebench(
                token_limit_per_agent=10,
                gpu_management="controller",
                orchestration={**leased, **change},
            )
    with pytest.raises(ValueError, match="needs orchestration enabled"):
        inferencebench(agents=4, token_budget={"mode": "split", "tokens": 1000})
    with pytest.raises(ValueError, match="without token_limit_per_agent"):
        inferencebench(
            token_limit_per_agent=10,
            token_budget={"mode": "split", "tokens": 100},
            gpu_management="controller",
            orchestration=leased,
        )
    split = inferencebench(
        agents=4,
        token_budget={"mode": "split", "tokens": 1000},
        gpu_management="controller",
        orchestration=leased,
    )
    assert split.metadata["planned_team_token_budget"] == 1000
    assert split.metadata["token_limit_per_agent"] == 250
    shared = inferencebench(
        agents=4,
        token_budget={"mode": "shared", "tokens": 1000},
        gpu_management="controller",
        orchestration=leased,
    )
    assert shared.metadata["token_limit_per_agent"] is None
    assert shared.metadata["token_budget"]["team_pool"] == 1000


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
    judge = passing_judge()
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
    judge = passing_judge()
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
    judge = passing_judge()
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
    judge = passing_judge()
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


class LeasedPod(FixtureSandbox):
    """Answer the pod.py, inventory and snapshot commands of the leased-GPU mode."""

    # final measurements per installed archive; tests patch it, and empty keeps METRICS
    graded: dict[bytes, dict[str, Any]] = {}
    # GPU processes every probe reports; tests patch it
    processes: list[dict[str, Any]] = []

    def __init__(self, role: str, gpus: int = 1) -> None:
        """Name the pod's role and GPU count so tests can check which inputs reached it."""
        super().__init__()
        self.role = role
        self.gpus = gpus
        self.envs: list[dict[str, str]] = []
        self.packs = 0
        self.kills: list[str] = []
        self.installed = b""

    async def exec(self, cmd: list[str], **kwargs: Any) -> ExecResult[str]:
        """Simulate pod operations and record the environment of participant commands."""
        text = " ".join(cmd)
        if cmd[:2] == ["bash", "--login"]:
            self.envs.append(dict(kwargs.get("env") or {}))
            if "fixture-oom" in cmd[-1]:
                return ExecResult(
                    True, 0, "", "torch.OutOfMemoryError: CUDA out of memory."
                )
        if cmd[:2] == ["nvidia-smi", "--query-gpu=name"]:
            return ExecResult(True, 0, "Authored H100\n" * self.gpus, "")
        if "pod.py probe" in text:
            sample = {
                "time": 0.0,
                "gpus": [
                    {
                        "index": index,
                        "bus_id": f"00000000:{index:02X}:00.0",
                        "memory_used_mib": 1000.0,
                        "memory_total_mib": 80000.0,
                        "utilization_percent": 50.0,
                        "power_watts": 300.0,
                    }
                    for index in range(self.gpus)
                ],
                "processes": self.processes,
            }
            return ExecResult(True, 0, json.dumps(sample) + "\n", "")
        if "pod.py kill" in text:
            self.kills.append(text)
            return ExecResult(True, 0, '{"killed": []}\n', "")
        if "pod.py unpack" in text:
            archive = self.files.get("/tmp/inferencebench/submission.tar.gz", b"")
            self.installed = archive if isinstance(archive, bytes) else archive.encode()
            return ExecResult(True, 0, '{"files": 1}\n', "")
        if "final" in cmd and self.installed in self.graded:
            self.files["/tmp/inferencebench/final.json"] = json.dumps(
                self.graded[self.installed]
            )
            self.files["/tmp/inferencebench/final-server.log"] = "authored final"
            return ExecResult(True, 0, "", "")
        if "pod.py workspace" in text:
            self.files["/opt/inferencebench-dev/baseline.json"] = json.dumps(
                {"profiles": {"fixture": {"success_count": 5, "ttft": {"p50": 2.0}}}}
            )
            return ExecResult(True, 0, '{"requests": 5}\n', "")
        if "pod.py pack" in text:
            self.packs += 1
            remote = text.split(" pack /home/agent/task ")[1].split()[0]
            self.files[remote] = b"authored submission %d" % self.packs
            info = {"digest": f"{self.packs:064x}", "bytes": 10, "files": 1}
            return ExecResult(True, 0, json.dumps(info) + "\n", "")
        return await super().exec(cmd, **kwargs)


def leased_model(
    submit: bool = True,
    agents: int = 2,
    counts: dict[str, int] | None = None,
    first: bool = False,
) -> Any:
    """Script peers that acquire ``counts`` GPUs, measure, submit, release and idle, with agent_0 first if ``first``."""
    counts = counts or {}
    # idle turns spend the shared pool, so no peer idles until every script is done
    finished: set[str] = set()
    everyone = asyncio.Event()
    leader_holds = asyncio.Event()

    async def reply(
        messages: list[Any], tools: Any, choice: Any, config: Any
    ) -> ModelOutput:
        """Follow one fixed tool sequence per peer and verify the leased-GPU tools."""
        opening = next(m for m in messages if (m.metadata or {}).get("team_actor"))
        actor = opening.metadata["team_actor"]
        names = {t.name for t in tools}
        assert {"bash", "python", "gpu", "evaluate", "submit", "submissions"} <= names
        assert "-token pool" in messages[-1].text
        step = len([m for m in messages if isinstance(m, ChatMessageTool)])
        if actor == "agent_0" and step >= 2:
            # agent_0's acquire call has returned before its third decision
            leader_holds.set()
        if first and actor != "agent_0" and step == 1:
            await asyncio.wait_for(leader_holds.wait(), 30)
        plan = [
            ("message_board", {"action": "register", "name": "Fixture " + actor[-1]}),
            (
                "gpu",
                {
                    "action": "acquire",
                    "wait_seconds": 60,
                    "count": counts.get(actor, 1),
                },
            ),
            (
                "bash",
                {
                    "command": "fixture-oom"
                    if actor == "agent_0"
                    else "fixture-gpu-work"
                },
            ),
            ("evaluate", {"quick": True}),
            ("submit", {}) if submit else ("submissions", {}),
            ("gpu", {"action": "release"}),
            ("submissions", {}),
        ]
        if step < len(plan):
            output = ModelOutput.for_tool_call("mockllm/model", *plan[step])
        else:
            finished.add(actor)
            if len(finished) == agents:
                everyone.set()
            await asyncio.wait_for(everyone.wait(), 30)
            output = ModelOutput.from_content("mockllm/model", "Fixture idles.")
        output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
        return output

    return get_model(
        "mockllm/model",
        custom_outputs=reply,
        config=GenerateConfig(max_connections=agents),
    )


def leased_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agents: int = 2,
    gpus: int = 1,
    tokens: int = 600,
    **options: Any,
) -> tuple[Any, list[LeasedPod]]:
    """Build a leased-GPU task whose grading and workspace pods are authored fixtures."""
    from collaboration_index.inferencebench.backend import environment
    from collaboration_index.inferencebench.backend.runpod_sandbox import RunPodSandbox

    pods: list[LeasedPod] = []

    async def allocate(
        cls: Any, task_name: str, config: Any, metadata: Any
    ) -> dict[str, LeasedPod]:
        """Create the grading pod or the workspace pod from the requested provider config."""
        workspace = "workspace-" in str(config)
        env = LeasedPod(
            "workspace" if workspace else "grader", gpus if workspace else 1
        )
        pods.append(env)
        return {"default": env}

    monkeypatch.setattr(RunPodSandbox, "sample_init", classmethod(allocate))
    monkeypatch.setattr(environment, "BASELINE_CACHE", tmp_path / "baselines")
    orchestration = copy.deepcopy(ARGS["orchestration"])
    orchestration.update(enabled=True, monitor_seconds=0.05, workspace_gpus=gpus)
    orchestration.update(options.pop("orchestration", {}))
    task = inferencebench(
        agents=agents,
        token_budget={"mode": "shared", "tokens": tokens},
        artifact_dir=str(tmp_path),
        gpu_management="controller",
        orchestration=orchestration,
        **options,
    )
    return task, pods


def test_leased_gpus_with_more_agents_than_gpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Let one agent hold both GPUs, then share two GPUs among three agents until all finish."""
    task, pods = leased_task(tmp_path, monkeypatch, agents=3, gpus=2, tokens=900)
    judge = passing_judge()
    [log] = inspect_eval(
        task,
        model=leased_model(agents=3, counts={"agent_0": 2}, first=True),
        model_roles={"integrity": judge},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error.traceback if log.error else None
    workspace = next(p for p in pods if p.role == "workspace")
    assert workspace.gpus == 2 and all(p.terminated for p in pods)
    leased = [env for env in workspace.envs if env.get("CUDA_VISIBLE_DEVICES")]
    assert leased
    for env in leased:
        gpus = env["CUDA_VISIBLE_DEVICES"].split(",")
        assert set(gpus) <= {"0", "1"} and env["PORT"] == str(8000 + int(gpus[0]))
        assert env["INFERENCEBENCH_PORTS"] == ",".join(str(8000 + int(g)) for g in gpus)
    sample = read_eval_log(log.location).samples[0]
    leases = sample.store["InferenceHistory:orchestration"]["leases"]
    # agent_0 held both GPUs before anyone else asked; every agent got a GPU in turn
    assert leases["max_gpus_per_lease"] == 2 and leases["grants"] == 3
    assert "active" not in leases["end_reasons"]
    assert {lease["actor"] for lease in leases["leases"]} == {
        "agent_0",
        "agent_1",
        "agent_2",
    }
    # a GPU taken from a lease that keeps others stops only that lease's processes
    # for that GPU, never every process on the device
    assert not any("--gpu=" in command for command in workspace.kills)
    for lease in leases["leases"]:
        if lease["reclaimed"]:
            assert any(f"--scoped={lease['token']}:" in c for c in workspace.kills)


def test_leased_gpus_submissions_and_final_grade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Queue two peers on one leased GPU, grade both submissions and score the latest one."""
    from collaboration_index.inferencebench import resource

    task, pods = leased_task(tmp_path, monkeypatch)
    judge = passing_judge()
    [log] = inspect_eval(
        task,
        model=leased_model(),
        model_roles={"integrity": judge},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error.traceback if log.error else None
    assert not resource.RESOURCES
    assert sorted(p.role for p in pods) == ["grader", "workspace"]
    assert all(p.terminated for p in pods)
    grader = next(p for p in pods if p.role == "grader")
    workspace = next(p for p in pods if p.role == "workspace")
    # held-out inputs and the evaluation seed never reach the agents' pod
    assert not any(
        "heldout" in name or "quality.json" == name.rsplit("/", 1)[-1]
        for name in workspace.files
    )
    options = json.loads(
        str(workspace.files["/tmp/inferencebench/workspace-options.json"])
    )
    assert "eval_seed" not in options and options["quality_seed"] == 1729
    assert grader.restarts == 1 and workspace.restarts == 0
    leased = [env for env in workspace.envs if env.get("CUDA_VISIBLE_DEVICES")]
    assert leased and all(
        env["PORT"] == "8000" and env["INFERENCEBENCH_GPU_LEASE"] for env in leased
    )
    sample = read_eval_log(log.location).samples[0]
    assert sample.scores["inference_team_speedup"].value["speedup"] == 2
    measured = sample.store["InferenceHistory:orchestration"]
    assert (
        measured["submissions"]["count"] == 2 and measured["submissions"]["valid"] == 2
    )
    assert (
        measured["leases"]["grants"] == 2
        and measured["leases"]["end_reasons"]["released"] == 2
    )
    assert measured["gpu"]["samples"] > 0 and measured["oom_events"] == 1
    checks = sample.store["InferenceHistory:checks"]
    assert len(checks) == 2 and all(c["proxy_speedup"] == 2.0 for c in checks)
    peers = sample.store["TeamHistory:peers"]
    assert all(p["status"] == "limited" for p in peers)
    assert 600 <= sum(p["tokens"] for p in peers) < 600 + 2 * 30
    result = sample.store["TeamHistory:result"]
    assert result["submitted"] and result["submissions"] == 2
    assert sample.store["workspace_gpu_lifecycle"]["status"] == "terminated"


def test_leased_gpus_without_submission_scores_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give a team that never submits the 1.0 fallback without restarting the grader or calling the judge."""
    task, pods = leased_task(tmp_path, monkeypatch)
    judge = get_model("mockllm/judge", custom_outputs=[])
    [log] = inspect_eval(
        task,
        model=leased_model(submit=False),
        model_roles={"integrity": judge},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error.traceback if log.error else None
    sample = read_eval_log(log.location).samples[0]
    score = sample.scores["inference_team_speedup"]
    assert score.value["speedup"] == 1.0 and score.metadata["submitted"] is False
    assert all(p.restarts == 0 and p.terminated for p in pods)


def test_leased_gpus_end_when_workspace_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End the team when the workspace pod refuses probes, and error the sample instead of scoring 1.0."""
    from inspect_ai.util import SandboxUnavailableError, store_as

    from collaboration_index.inferencebench import session
    from collaboration_index.state import TeamHistory

    async def exec(self: LeasedPod, cmd: list[str], **kwargs: Any) -> ExecResult[str]:
        """Refuse every workspace probe once the team is released."""
        if (
            self.role == "workspace"
            and "pod.py probe" in " ".join(cmd)
            and store_as(TeamHistory).released is not None
        ):
            raise SandboxUnavailableError("fixture refuses SSH")
        return await original(self, cmd, **kwargs)

    async def reply(*args: Any) -> ModelOutput:
        """Keep working slowly, so only the unreachable guard can end the team."""
        await asyncio.sleep(0.2)
        output = ModelOutput.for_tool_call("mockllm/model", "submissions", {})
        output.usage = ModelUsage(input_tokens=2, output_tokens=1, total_tokens=3)
        return output

    original = LeasedPod.exec
    monkeypatch.setattr(LeasedPod, "exec", exec)
    monkeypatch.setattr(session, "UNREACHABLE_SECONDS", 0)
    task, pods = leased_task(tmp_path, monkeypatch, tokens=10_000)
    [log] = inspect_eval(
        task,
        model=get_model("mockllm/model", custom_outputs=reply),
        model_roles={"integrity": get_model("mockllm/judge", custom_outputs=[])},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "error"
    sample = read_eval_log(log.location).samples[0]
    assert sample.store["TeamHistory:end_reason"] == "workspace_unreachable"
    assert "unreachable" in sample.error.message
    assert all(p.terminated for p in pods)


def test_idle_peers_wait_longer_between_nudges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Space out nudges to a peer that keeps answering without a tool call, so it spends fewer turns."""
    from collaboration_index import harness

    async def reply(*args: Any) -> ModelOutput:
        """Decline to work, as Haiku 5.5 did after its first hour."""
        await asyncio.sleep(0.05)
        output = ModelOutput.from_content("mockllm/model", "I am done.")
        output.usage = ModelUsage(input_tokens=2, output_tokens=1, total_tokens=3)
        return output

    monkeypatch.setattr(harness, "IDLE_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(harness, "IDLE_WAIT_CAP_SECONDS", 1)
    task, pods = leased_task(tmp_path, monkeypatch, tokens=100_000, team_time_limit=3)
    [log] = inspect_eval(
        task,
        model=get_model("mockllm/model", custom_outputs=reply),
        model_roles={"integrity": passing_judge()},
        log_dir=str(LOGS),
        display="none",
    )
    sample = read_eval_log(log.location).samples[0]
    peers = sample.store["TeamHistory:peers"]
    # without waits each peer takes about 60 nudges in 3 s; with them, about 5
    assert all(1 < p["nudges"] <= 8 for p in peers), [p["nudges"] for p in peers]
    assert all(p.terminated for p in pods)


def test_runpod_startup_command_hides_installer_from_pid1() -> None:
    """Keep process names that agents pkill out of the argv that PID 1 holds for the pod's life."""
    import re
    import subprocess

    from collaboration_index.inferencebench.backend.runpod_sandbox import RunPodSandbox

    env = RunPodSandbox.__new__(RunPodSandbox)
    env.config = {"bootstrap": True}
    command = env._startup_command()
    # only an encoded payload reaches PID 1's argv
    assert re.fullmatch(r'exec bash -c "\$\(echo [A-Za-z0-9+/=]+ \| base64 -d \| gunzip\)"', command)
    assert command == env._startup_command()
    script = subprocess.run(
        ["bash", "-c", command.replace("exec bash -c", "printf %s", 1)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "uv pip install" in script and "MaxStartups=64" in script
    assert script.endswith("exec bash /opt/runpod_start.sh")


def unmanaged_model() -> Any:
    """Script one agent that uses every GPU without a lease, measures a chosen port and submits twice."""

    async def reply(
        messages: list[Any], tools: Any, choice: Any, config: Any
    ) -> ModelOutput:
        """Follow one fixed tool sequence and check that no tool call failed."""
        names = {t.name for t in tools}
        assert {"bash", "python", "evaluate", "submit", "submissions"} <= names
        assert "gpu" not in names
        done = [m for m in messages if isinstance(m, ChatMessageTool)]
        assert not [m.error for m in done if m.error]
        plan = [
            ("bash", {"command": "fixture-gpu-work"}),
            ("evaluate", {"quick": True, "port": 8001}),
            ("submit", {}),
            ("bash", {"command": "fixture-edit"}),
            ("submit", {}),
            ("submissions", {}),
        ]
        if len(done) < len(plan):
            output = ModelOutput.for_tool_call("mockllm/model", *plan[len(done)])
        else:
            output = ModelOutput.from_content("mockllm/model", "Fixture idles.")
        output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
        return output

    return get_model("mockllm/model", custom_outputs=reply)


@pytest.mark.parametrize(
    ("scoring", "expected", "speedup"),
    [("best_submit", 1, 4.0), ("last_submit", 2, 2.0)],
)
def test_unmanaged_gpus_scoring_rules_and_submission_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scoring: str,
    expected: int,
    speedup: float,
) -> None:
    """Show every GPU without leases, record each submission and re-grade the one the scoring rule selects."""
    fast = {
        "profiles": {"fixture": {"success_count": 10, "ttft": {"p50": 0.5}}},
        "quality_check": {"pass": True},
    }
    # the first snapshot is twice as fast as the second
    monkeypatch.setattr(
        LeasedPod,
        "graded",
        {b"authored submission 1": fast, b"authored submission 2": METRICS},
    )
    # a process without a lease would be flagged in scheduler mode
    monkeypatch.setattr(
        LeasedPod,
        "processes",
        [{"pid": 4242, "token": None, "command": "python serve.py", "gpus": [0]}],
    )
    keep_all = scoring == "best_submit"
    task, pods = leased_task(
        tmp_path,
        monkeypatch,
        agents=1,
        gpus=2,
        orchestration={"gpu_access": "unmanaged"},
        scoring=scoring,
        evaluation={**ARGS["evaluation"], "save_submitted_artifacts": keep_all},
    )
    [log] = inspect_eval(
        task,
        model=unmanaged_model(),
        model_roles={"integrity": passing_judge()},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error.traceback if log.error else None
    workspace = next(p for p in pods if p.role == "workspace")
    assert workspace.envs and all(
        env["CUDA_VISIBLE_DEVICES"] == "0,1"
        and env["INFERENCEBENCH_PORTS"] == "8000,8001"
        and "INFERENCEBENCH_GPU_LEASE" not in env
        for env in workspace.envs
    )
    sample = read_eval_log(log.location).samples[0]
    score = sample.scores["inference_team_speedup"]
    assert score.value["speedup"] == speedup
    assert score.metadata["scoring"] == scoring
    assert score.metadata["final_submission"] == expected
    [check] = sample.store["InferenceHistory:checks"]
    # the log store omits None fields
    assert check["port"] == 8001 and check.get("slot") is None
    assert check["proxy_speedup"] == 2.0
    measured = sample.store["InferenceHistory:orchestration"]
    assert measured["leases"]["grants"] == 0
    assert measured["gpu"]["samples"] > 0
    assert measured["gpu"]["unleased_gpu_process_observations"] == 0
    assert not any(e["kind"] == "unleased_gpu_use" for e in measured["events"])
    assert not any("--pid=" in command for command in workspace.kills)
    artifacts = Path(sample.store["artifacts"])
    ledger = [
        json.loads(line)
        for line in (artifacts / "submissions.jsonl").read_text().splitlines()
    ]
    assert [row["result"]["speedup"] for row in ledger] == [4.0, 2.0]
    assert [row["selected"] for row in ledger] == [expected == 1, expected == 2]
    for row in ledger:
        measures = row["measures"]
        assert "submitted_at" in measures and measures["seconds_since_release"] >= 0
        assert measures["cumulative_agent_tokens"] > 0
        assert measures["cumulative_gpu_busy_seconds"] >= 0
        assert measures["cumulative_gpu_allocated_seconds"] >= 0
        assert row["detail"]["validation"]["outcome"] == "valid"
    tokens = [row["measures"]["cumulative_agent_tokens"] for row in ledger]
    assert tokens[1] > tokens[0]
    final = json.loads((artifacts / "final-submission.json").read_text())
    assert final["submission"] == expected and Path(final["archive"]).is_file()
    archives = list((artifacts / "submissions").glob("*.tar.gz"))
    assert len(archives) == (2 if keep_all else 1)
    assert [row["archive"] is not None for row in ledger] == (
        [True, True] if keep_all else [False, True]
    )


def test_snapshot_keeps_code_and_package_list_and_restore_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leave .venv out of a snapshot, keep its package list, then rebuild it from that list."""
    import subprocess
    import tarfile

    from collaboration_index.inferencebench import pod

    root = tmp_path / "agent"
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_bytes(b"x" * 5000)
    (root / "start_server.sh").write_text("#!/bin/bash\n")
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Freeze to a fixed list and record rebuild commands."""
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "vllm==0.31.0\n", "")

    monkeypatch.setattr(pod.subprocess, "run", run)
    archive = tmp_path / "snap.tar.gz"
    pod.snapshot(str(root), str(archive), max_bytes=1000)
    with tarfile.open(archive) as handle:
        assert set(handle.getnames()) == {"start_server.sh", pod.LOCK}

    target = tmp_path / "restored"
    calls.clear()
    pod.restore(str(archive), str(target))
    assert calls[0][:2] == ["uv", "venv"] and calls[0][-1] == str(target / ".venv")
    assert calls[1][:3] == ["uv", "pip", "install"] and calls[1][-1] == str(target / pod.LOCK)
    assert (target / pod.LOCK).read_text() == "vllm==0.31.0\n"


class TeamPod(FixtureSandbox):
    """Answer the team-workspace setup, command, snapshot and restore operations."""

    async def upload(self, local: str, remote: str) -> None:
        """Remember which host archive a restore will read."""
        self.uploaded = local
        await super().upload(local, remote)

    def __init__(self) -> None:
        """Record what each agent ran and which snapshots were restored."""
        super().__init__()
        self.commands: list[str] = []
        self.restored: list[str] = []
        self.snapshots = 0
        self.uploaded = ""
        self.setup = ""

    async def exec(self, cmd: list[str], **kwargs: Any) -> ExecResult[str]:
        """Simulate the pod side of team mode and defer everything else to the base fixture."""
        from collaboration_index.inferencebench import team_workspace

        if cmd[:2] == ["bash", "-c"] and "uv venv" in cmd[2]:
            self.setup = cmd[2]
            return ExecResult(True, 0, "", "")
        if cmd == ["bash", "-c", team_workspace.RUNNER]:
            self.commands.append(kwargs["input"])
            return ExecResult(True, 0, "ran\n", "")
        if cmd[0].endswith("/evaluate.py"):
            self.files[cmd[cmd.index("--json-output-file") + 1]] = json.dumps(METRICS)
            return ExecResult(True, 0, "", "")
        if "snapshot" in cmd:
            self.snapshots += 1
            self.files[cmd[cmd.index("snapshot") + 2]] = b"code snapshot"
            info = {"digest": f"{self.snapshots:064x}", "bytes": 10, "files": 3}
            return ExecResult(True, 0, json.dumps(info) + "\n", "")
        if "restore" in cmd:
            self.restored.append(self.uploaded)
            return ExecResult(True, 0, "{}\n", "")
        if cmd[0] in {"chmod", "rm"}:
            return ExecResult(True, 0, "", "")
        return await super().exec(cmd, **kwargs)


def team_model() -> Any:
    """Have two agents work in their own workspaces, measure on a chosen port and submit in turn."""
    first = asyncio.Event()

    async def reply(messages: list[Any], tools: Any, choice: Any, config: Any) -> ModelOutput:
        """Script each agent and check the team-workspace prompt and tools."""
        opening = next(m for m in messages if (m.metadata or {}).get("team_actor"))
        actor = opening.metadata["team_actor"]
        assert any("Your workspace" in m.text for m in messages[:3])
        names = {t.name for t in tools}
        assert {"bash", "python", "evaluate", "submit"} <= names and "gpu" not in names
        step = len([m for m in messages if isinstance(m, ChatMessageTool)])
        plan = [
            ("message_board", {"action": "register", "name": "Fixture " + actor[-1]}),
            ("bash", {"command": f"echo work-{actor}"}),
            ("evaluate", {"port": 8001}),
            ("submit", {}),
        ]
        if step < len(plan):
            if step == 3 and actor == "agent_1":
                await asyncio.wait_for(first.wait(), 20)
            function, arguments = plan[step]
            output = ModelOutput.for_tool_call("mockllm/model", function, arguments)
        else:
            if actor == "agent_0":
                first.set()
            output = ModelOutput.from_content("mockllm/model", "Done for now.")
        output.usage = ModelUsage(input_tokens=40, output_tokens=10, total_tokens=50)
        return output

    return get_model("mockllm/model", custom_outputs=reply)


def test_team_workspace_grades_the_newest_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give agents private workspaces on shared GPUs, record each submission and grade the newest one."""
    import yaml

    from collaboration_index.inferencebench.backend import environment
    from collaboration_index.inferencebench.backend.runpod_sandbox import RunPodSandbox

    pods: list[TeamPod] = []

    async def allocate(cls: Any, *args: Any) -> dict[str, TeamPod]:
        """Create the one team pod."""
        pods.append(TeamPod())
        return {"default": pods[-1]}

    monkeypatch.setattr(RunPodSandbox, "sample_init", classmethod(allocate))
    monkeypatch.setattr(environment, "BASELINE_CACHE", tmp_path / "baselines")
    options = {**ARGS["team_workspace"], "enabled": True, "gpus": 4, "disk_gb": 400}
    task = inferencebench(
        agents=2,
        token_limit_per_agent=400,
        artifact_dir=str(tmp_path),
        gpu_management="controller",
        team_workspace=options,
    )
    config = yaml.safe_load(Path(task.dataset[0].metadata["gpu_config"]).read_text())
    assert config["pod"]["gpuCount"] == 4 and config["pod"]["containerDiskInGb"] == 400
    [log] = inspect_eval(
        task,
        model=team_model(),
        model_roles={"integrity": passing_judge()},
        log_dir=str(LOGS),
        display="none",
    )
    assert log.status == "success", log.error.traceback if log.error else None
    sample = read_eval_log(log.location).samples[0]
    [pod] = pods
    handles = sample.store["TeamWorkspace:handles"]
    assert set(handles) == {"agent_0", "agent_1"} and len(set(handles.values())) == 2
    for actor, handle in handles.items():
        assert f"/team/agents/{handle}/.venv" in pod.setup
        assert any(
            c.startswith(f". /team/agents/{handle}/.agentrc") and f"work-{actor}" in c
            for c in pod.commands
        )
    submissions = sample.store["TeamWorkspace:submissions"]
    assert [(s["number"], s["actor"]) for s in submissions] == [(1, "agent_0"), (2, "agent_1")]
    assert submissions[1]["by"] == "Fixture 1"
    # the earlier submission is measured for the record, then the newest one is graded
    assert pod.restored == [submissions[0]["archive"], submissions[1]["archive"]]
    [tracked] = sample.store["TeamWorkspace:tracked"]
    assert tracked["status"] == "graded" and tracked["speedup"] == 2
    launcher = pod.files["/home/agent/task/start_server.sh"]
    assert f". /team/agents/{handles['agent_1']}/.agentrc" in launcher
    assert "CUDA_VISIBLE_DEVICES=0 PORT=8000" in launcher
    assert sample.scores["inference_team_speedup"].value["speedup"] == 2
    team = sample.scores["inference_team_speedup"].metadata["team"]
    assert team["submissions"] == 2 and team["final_submission"] == 2
    assert sample.store["TeamWorkspace:seen"]["agent_1"] == 2
    checks = sample.store["InferenceHistory:checks"]
    assert {c["port"] for c in checks} == {8001}
