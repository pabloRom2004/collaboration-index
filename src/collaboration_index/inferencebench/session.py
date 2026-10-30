"""Run the leased-GPU InferenceBench mode: a shared workspace pod and a separate grading pod.

Peers develop on a workspace pod whose GPUs are shared through exclusive
leases. Submitted snapshots of ``/home/agent/task`` are graded one at a time on
a second pod that holds the held-out requests, quality samples and speed
baseline. Peers receive only an outcome category and a rounded speedup.
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio
import yaml
from inspect_ai.solver import Generate, Solver, TaskState
from inspect_ai.tool import ToolError
from inspect_ai.util import (
    SandboxEnvironment,
    SandboxUnavailableError,
    store,
    store_as,
)

from collaboration_index.board.client import BoardHistory
from collaboration_index.inferencebench import pod, resource
from collaboration_index.inferencebench.backend.metrics import performance
from collaboration_index.inferencebench.backend.prompts import ASSETS
from collaboration_index.inferencebench.backend.run_config import load_config
from collaboration_index.inferencebench.orchestration import (
    GpuScheduler,
    GpuUsage,
    Lease,
    Submission,
    SubmissionQueue,
)
from collaboration_index.inferencebench.state import InferenceHistory
from collaboration_index.state import TeamHistory

SESSIONS: dict[str, "Session"] = {}
SESSION_KEY = "inferencebench_session"
REMOTE = "/tmp/inferencebench"
POD = f"{REMOTE}/pod.py"
PYTHON = "/opt/evaluator/bin/python"
TASK_DIR = "/home/agent/task"
BASE_PORT = 8000
# the final evaluation serves a submission on the grading pod's first GPU
GRADING_GPU = 0
PROBE_TIMEOUT = 60
# end the team once the workspace pod refuses connections this long after release
UNREACHABLE_SECONDS = 300
SHORT_TIMEOUT = 120
ARCHIVE_TIMEOUT = 3600
EVALUATION_TIMEOUT = 12 * 3600
# after cleanup, poll a GPU until less than this fraction of its memory is used
FREE_MEMORY_FRACTION = 0.05
RECLAIM_POLLS = 6
RECLAIM_POLL_SECONDS = 5
# fields the workspace pod may see; held-out seeds and baselines stay on the grader
WORKSPACE_OPTIONS = [
    "base_model",
    "max_model_len",
    "baseline_dtype",
    "quality_reference_backend",
    "quality_tau",
    "quality_concurrency",
    "quality_baseline_max_attempts",
    "request_timeout_seconds",
    "server_wait_seconds",
    "directory",
    "scenario",
    "dev_seed",
]


def workspace_options(metadata: dict[str, Any], dev: dict[str, Any]) -> dict[str, Any]:
    """Build the development options without the evaluation seed or held-out quality seed."""
    options = {key: metadata[key] for key in WORKSPACE_OPTIONS}
    options.update(
        dev_eval=dev,
        quality_seed=dev["quality_seed"],
        quality_samples=dev["quality_samples"],
        request_limit=dev["requests"],
    )
    return options


def workspace_gpu_config(source: str | None, gpus: int, folder: Path) -> str:
    """Write a provider config that differs from the grading pod only in its GPU count."""
    config = load_config(source or "runpod.yaml")
    config["pod"]["gpuCount"] = gpus
    # concurrent samples share the folder, so each writes its own file
    path = folder / f"workspace-{gpus}gpu-{uuid4().hex[:12]}.yaml"
    path.write_text(yaml.safe_dump(config))
    return str(path)


async def checked(
    env: SandboxEnvironment,
    command: list[str],
    timeout: int,
    env_vars: dict[str, str] | None = None,
) -> str:
    """Run trusted infrastructure on a pod and raise with its output on failure."""
    result = await env.exec(
        command, env=env_vars or {}, timeout=timeout, timeout_retry=False
    )
    if not result.success:
        raise RuntimeError(
            f"Pod command failed ({result.returncode}): {result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )
    return result.stdout


def board_names() -> dict[str, str]:
    """Map each peer to the board name it registered."""
    chosen = {}
    for call in store_as(BoardHistory).calls:
        name = (call.result or {}).get("name") if call.completed else None
        if call.action == "register" and call.agent_id and name:
            chosen[call.agent_id] = name
    return chosen


def last_json(output: str) -> Any:
    """Parse the JSON result line that pod.py prints last."""
    return json.loads(output.strip().splitlines()[-1])


async def install_scripts(env: SandboxEnvironment) -> None:
    """Upload the pinned runtime adapter and pod.py to a pod."""
    await env.write_file(
        f"{REMOTE}/runtime.py", (ASSETS / "scripts" / "runtime.py").read_text()
    )
    await env.write_file(POD, Path(pod.__file__).read_text())


def speedup(
    metrics: dict[str, Any], baseline: dict[str, Any], scenario: str
) -> float | None:
    """Divide measured by baseline performance, or return None when either is undefined."""
    try:
        return performance(metrics, scenario) / performance(baseline, scenario)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def classify_final(
    metrics: dict[str, Any], baseline: dict[str, Any], scenario: str
) -> dict[str, Any]:
    """Apply the upstream final-score rules, without the integrity judge, as an outcome peers may see."""
    if metrics.get("invalid_submission"):
        return {"outcome": "invalid_submission", "speedup": 1.0}
    if "quality_check" not in metrics:
        raise RuntimeError("final evaluation returned no quality result")
    quality = metrics["quality_check"] or {}
    if not quality.get("pass"):
        if quality.get("error"):
            raise RuntimeError(f"quality evaluation failed: {quality['error']}")
        return {"outcome": "quality_failed", "speedup": 1.0, "quality_pass": False}
    profiles = metrics.get("profiles") or {}
    if not profiles or any(p.get("success_count", 0) == 0 for p in profiles.values()):
        return {
            "outcome": "no_successful_requests",
            "speedup": 1.0,
            "quality_pass": True,
        }
    value = speedup(metrics, baseline, scenario)
    if value is None:
        return {
            "outcome": "undefined_performance",
            "speedup": 1.0,
            "quality_pass": True,
        }
    return {"outcome": "valid", "speedup": round(value, 2), "quality_pass": True}


def validation_record(
    metrics: dict[str, Any], public: dict[str, Any]
) -> dict[str, Any]:
    """Keep the trusted reasons behind a submission's outcome; peers never see these."""
    quality = metrics.get("quality_check") or {}
    profiles = metrics.get("profiles") or {}
    return {
        "outcome": public["outcome"],
        "invalid_submission": metrics.get("invalid_submission"),
        "evaluator_error": metrics.get("evaluator_error"),
        "quality_pass": quality.get("pass"),
        "quality_error": quality.get("error"),
        "profiles_without_success": sorted(
            name for name, p in profiles.items() if p.get("success_count", 0) == 0
        ),
    }


class Session:
    """Hold the attempt's pods, lease scheduler, submission queue, monitor and notices."""

    def __init__(
        self,
        options: dict[str, Any],
        budget: dict[str, Any] | None,
        tool_timeout: int,
        workspace: Any,
        grader: Any,
        artifacts: Path,
        metadata: dict[str, Any],
        dev_baseline: dict[str, Any],
        held_out_baseline: dict[str, Any],
    ) -> None:
        """Create empty coordination state for one team sample."""
        self.options = options
        self.budget = budget
        self.tool_timeout = tool_timeout
        self.workspace, self.grader = workspace, grader
        self.artifacts = artifacts
        self.metadata = metadata
        self.scenario = metadata["scenario"]
        self.dev_baseline = dev_baseline
        self.held_out_baseline = held_out_baseline
        self.managed = options["gpu_access"] == "scheduler"
        self.flags: dict[str, bool] = options["evaluation"]
        self.scheduler = GpuScheduler(
            options["workspace_gpus"], options["lease_seconds"]
        )
        self.queue = SubmissionQueue(
            options["max_pending_submissions"], options["scoring"]
        )
        # the harness's per-peer token meters, bound when peers start
        self.meters: dict[str, Any] = {}
        self.usage = GpuUsage(options["monitor_seconds"])
        self.commands = asyncio.Semaphore(options["max_parallel_commands"])
        self.pack_lock = asyncio.Lock()
        self.slot_locks: dict[int, asyncio.Lock] = {}
        self.running = 0
        self.events: list[dict[str, Any]] = []
        self.notices: dict[str, list[str]] = {}
        self.told_revoked: set[str] = set()
        self.told_reclaimed: dict[str, int] = {}
        self.flagged: list[int] = []
        self.reachable = time.monotonic()
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def start(self) -> None:
        """Start the grading worker and the GPU monitor."""
        self.tasks = {
            "grader": asyncio.create_task(self.queue.work(self.grade)),
            "monitor": asyncio.create_task(self.monitor()),
        }

    def lease_env(self, lease: Lease | None) -> dict[str, str]:
        """Expose a peer's leased GPUs, or every GPU in unmanaged mode, with their server ports."""
        if lease is not None and lease.slots:
            slots = sorted(lease.slots)
        elif not self.managed:
            slots = list(range(self.options["workspace_gpus"]))
        else:
            return {"CUDA_VISIBLE_DEVICES": "", "CUDA_DEVICE_ORDER": "PCI_BUS_ID"}
        env = {
            "CUDA_VISIBLE_DEVICES": ",".join(map(str, slots)),
            "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
            "PORT": str(BASE_PORT + slots[0]),
            "INFERENCEBENCH_GPUS": ",".join(map(str, slots)),
            "INFERENCEBENCH_PORTS": ",".join(str(BASE_PORT + s) for s in slots),
        }
        if lease is not None:
            env[pod.LEASE_VAR] = lease.token
        return env

    def measures(self) -> dict[str, Any]:
        """Record time, team tokens and GPU-seconds at a submission, as the evaluation flags select."""
        now = time.time()
        released = store_as(TeamHistory).released
        elapsed = (
            now - datetime.fromisoformat(released).timestamp() if released else None
        )
        measured: dict[str, Any] = {}
        if self.flags["record_wall_clock_timestamp"]:
            measured["submitted_at"] = datetime.fromtimestamp(now, UTC).isoformat()
            measured["seconds_since_release"] = elapsed
        if self.flags["record_cumulative_agent_tokens"]:
            measured["cumulative_agent_tokens"] = sum(
                int(meter.usage) for meter in self.meters.values()
            )
        if self.flags["record_cumulative_gpu_seconds"]:
            # busy: monitor probes with utilization >= 1%; allocated: workspace GPUs x elapsed
            measured["cumulative_gpu_busy_seconds"] = self.usage.busy_seconds()
            measured["cumulative_gpu_allocated_seconds"] = (
                None if elapsed is None else elapsed * self.options["workspace_gpus"]
            )
        return measured

    def names(self) -> dict[str, str]:
        """Map each peer to the board name it registered, for GPU status shown to teammates."""
        return board_names()

    def event(self, kind: str, actor: str | None = None, **details: Any) -> None:
        """Record one trusted coordination event with a wall-clock time."""
        self.events.append(
            {"kind": kind, "actor": actor, "time": time.time(), **details}
        )

    def notify(self, actor: str, text: str) -> None:
        """Queue a short notice for the peer's next decision update."""
        self.notices.setdefault(actor, []).append(text)

    @asynccontextmanager
    async def command(self) -> AsyncIterator[None]:
        """Hold one of the shared command slots and count the command as running."""
        async with self.commands:
            self.running += 1
            try:
                yield
            finally:
                self.running -= 1

    def record_oom(
        self, actor: str, lease: Lease | None, source: str, signature: str
    ) -> str:
        """Record a GPU out-of-memory failure and return guidance for the tool result."""
        self.event(
            "oom",
            actor,
            gpus=list(lease.slots) if lease else [],
            source=source,
            signature=signature,
        )
        clear = (
            "Stop stale processes, or release and re-acquire the lease to clear them"
            if self.managed
            else "Check which processes use the GPU (nvidia-smi) and stop stale ones you own"
        )
        return (
            f"\n[GPU out of memory: {signature!r}. {clear}, and lower memory use (for "
            "example gpu_memory_utilization, max_num_seqs or max_model_len) before retrying.]"
        )

    async def kill(
        self,
        tokens: Iterable[str] = (),
        pids: Iterable[int] = (),
        scoped: Iterable[tuple[str, int]] = (),
    ) -> list[int]:
        """Stop workspace processes of ended leases, of reclaimed GPUs or found misusing a GPU."""
        tokens, pids, scoped = set(tokens), set(pids), set(scoped)
        if not tokens and not pids and not scoped:
            return []
        command = [PYTHON, POD, "kill"]
        command += [f"--token={token}" for token in sorted(tokens)]
        command += [f"--pid={pid}" for pid in sorted(pids)]
        command += [f"--scoped={token}:{gpu}" for token, gpu in sorted(scoped)]
        return last_json(await checked(self.workspace, command, SHORT_TIMEOUT))[
            "killed"
        ]

    async def reclaim(self, slot: int) -> None:
        """Stop the processes earlier holders left on a GPU, then wait for its memory to free."""
        async with self.slot_locks.setdefault(slot, asyncio.Lock()):
            stale, shrunk = self.scheduler.take_cleanup(slot)
            if not stale and not shrunk:
                return
            used = None
            try:
                # a shrunk lease loses only the processes it started for this GPU
                killed = await self.kill(stale, scoped={(t, slot) for t in shrunk})
                if killed:
                    used = await self.wait_for_memory(slot)
            except BaseException as error:
                # requeue the cleanup, also on cancellation, so the next reclaim retries it
                self.scheduler.restore_cleanup(slot, stale, shrunk)
                self.event("reclaim_error", slot=slot, error=type(error).__name__)
                if not isinstance(error, Exception):
                    raise
                return
            self.event(
                "reclaimed",
                slot=slot,
                leases=len(stale),
                processes=len(killed),
                memory_used_mib=used,
            )

    async def wait_for_memory(self, slot: int) -> float | None:
        """Poll one GPU until little of its memory is used, and return the last reading."""
        used = None
        for _ in range(RECLAIM_POLLS):
            gpu: dict[str, Any] = next(
                (g for g in (await self.probe())["gpus"] if g["index"] == slot), {}
            )
            used, total = gpu.get("memory_used_mib"), gpu.get("memory_total_mib")
            if used is not None and total and used < FREE_MEMORY_FRACTION * total:
                break
            await asyncio.sleep(RECLAIM_POLL_SECONDS)
        return used

    async def probe(self) -> dict[str, Any]:
        """Read GPU memory, utilization, power and attributed processes on the workspace pod."""
        return last_json(
            await checked(self.workspace, [PYTHON, POD, "probe"], PROBE_TIMEOUT)
        )

    async def foreign_processes(self, slot: int) -> list[dict[str, Any]] | None:
        """List processes that use one GPU outside their own lease, or None if the probe fails."""
        try:
            sample = await self.probe()
        except Exception as error:
            self.event("probe_error", error=type(error).__name__)
            return None
        holders = {s: h.token for s, h in self.scheduler.holders.items()}
        return [v for v in pod.audit(sample, holders) if v["gpu"] == slot]

    def announce_revocations(self) -> None:
        """Tell holders whose leases or extra GPUs were taken back under contention."""
        for lease in self.scheduler.leases:
            if lease.end_reason == "expired" and lease.token not in self.told_revoked:
                self.told_revoked.add(lease.token)
                self.event("revoked", lease.actor)
                self.notify(
                    lease.actor,
                    "Your GPU lease was reclaimed because it exceeded "
                    f"{self.scheduler.lease_seconds:.0f} seconds while another agent waited. "
                    "Processes started under it are being stopped; call gpu to acquire again.",
                )
            told = self.told_reclaimed.get(lease.token, 0)
            if lease.reclaimed > told:
                self.told_reclaimed[lease.token] = lease.reclaimed
                self.event(
                    "extra_gpu_reclaimed", lease.actor, count=lease.reclaimed - told
                )
                self.notify(
                    lease.actor,
                    f"{lease.reclaimed - told} of your extra GPUs went to a teammate who had "
                    f"none; your GPU processes started for them are being stopped. You keep GPUs "
                    f"{sorted(lease.slots)}.",
                )

    async def monitor(self) -> None:
        """Sample GPU use, audit GPU processes and apply lease expiry until cancelled."""
        previous = None
        while True:
            try:
                previous = await self.observe(previous)
            except Exception as error:
                # keep monitoring after a failed round
                self.usage.errors += 1
                self.event("monitor_error", error=type(error).__name__)
            await asyncio.sleep(self.options["monitor_seconds"])

    async def observe(self, previous: float | None) -> float | None:
        """Run one monitor round and return the time of its counted probe, if any."""
        self.scheduler.tick()
        self.announce_revocations()
        # cleanup never stops a slot's current holder, so held slots are retried too
        for slot in range(self.scheduler.slots):
            if self.scheduler.has_cleanup(slot):
                await self.reclaim(slot)
        try:
            sample = await self.probe()
        except Exception as error:
            self.usage.errors += 1
            self.event("probe_error", error=type(error).__name__)
            history = store_as(TeamHistory)
            unreachable = time.monotonic() - self.reachable
            if (
                isinstance(error, SandboxUnavailableError)
                and history.released is not None
                and unreachable > UNREACHABLE_SECONDS
                and history.end_reason is None
            ):
                # every peer's commands fail too, so stop spending tokens on them
                history.end_reason = "workspace_unreachable"
                self.event("workspace_unreachable", seconds=round(unreachable))
            return previous
        now = time.monotonic()
        self.reachable = now
        holders = {slot: lease.token for slot, lease in self.scheduler.holders.items()}
        # without leases every GPU process is allowed, so there is nothing to audit
        violations = pod.audit(sample, holders) if self.managed else []
        # usage covers solving time only, from the release barrier onwards
        released = store_as(TeamHistory).released is not None
        if released:
            seconds = None if previous is None else now - previous
            for pressure in self.usage.add(sample, holders, len(violations), seconds):
                self.event("memory_pressure", **pressure)
        pids = sorted(v["pid"] for v in violations)
        if pids and pids != self.flagged:
            self.event("unleased_gpu_use", processes=violations)
        self.flagged = pids
        if pids and self.options["enforce"] == "kill":
            await self.kill(pids=pids)
        with (self.artifacts / "gpu-samples.jsonl").open("a") as handle:
            handle.write(json.dumps({"holders": holders, **sample}) + "\n")
        self.persist()
        # probes before release are not counted, so they start no counted interval
        return now if released else None

    async def snapshot(self, limit: int) -> tuple[dict[str, Any], Path]:
        """Archive the shared task directory on the workspace pod and copy it to the controller."""
        remote = f"/tmp/inferencebench-submissions/{uuid4().hex}.tar.gz"
        try:
            result = await self.workspace.exec(
                [PYTHON, POD, "pack", TASK_DIR, remote, "--max-bytes", str(limit)],
                timeout=ARCHIVE_TIMEOUT,
                timeout_retry=False,
            )
            if result.returncode == pod.TOO_LARGE:
                raise ToolError(
                    f"{TASK_DIR} is larger than the {limit}-byte submission limit"
                )
            if not result.success:
                raise ToolError(
                    "The snapshot failed, for example because files changed while it ran.\n"
                    + result.stderr[-1500:]
                )
            info = last_json(result.stdout)
            # peers are root on this pod, so check its report before it names a controller file
            if not (
                isinstance(info, dict)
                and re.fullmatch(r"[0-9a-f]{64}", str(info.get("digest")))
                and all(type(info.get(k)) is int for k in ("bytes", "files"))
            ):
                raise ToolError("The snapshot report is invalid.")
            local = self.artifacts / "submissions" / f"{info['digest']}.tar.gz"
            local.parent.mkdir(parents=True, exist_ok=True)
            if not local.exists():
                partial = local.with_name(f"{local.name}.{uuid4().hex}.partial")
                try:
                    await self.workspace.download(remote, str(partial))
                    partial.rename(local)
                finally:
                    partial.unlink(missing_ok=True)
            return info, local
        finally:
            try:
                await self.workspace.exec(["rm", "-f", remote])
            except Exception:
                self.event("snapshot_cleanup_error")

    async def restore_trusted(self) -> None:
        """Rewrite the grader's evaluator, scripts and held-out inputs from controller copies."""
        # submissions run as root, so every trusted file is rewritten before each grading
        await self.grader.upload(
            str(self.artifacts / "evaluator.tar.gz"), f"{REMOTE}/evaluator.tar.gz"
        )
        await checked(
            self.grader,
            ["tar", "xzf", f"{REMOTE}/evaluator.tar.gz", "-C", "/opt/inferencebench"],
            SHORT_TIMEOUT,
        )
        await checked(
            self.grader,
            [
                "cp",
                "/opt/inferencebench/src/eval/inference/bin/launch_supervised_server.sh",
                "/opt/inference_eval/bin/launch_supervised_server.sh",
            ],
            SHORT_TIMEOUT,
        )
        await install_scripts(self.grader)
        await self.grader.write_file(
            f"{REMOTE}/options.json", json.dumps(self.metadata)
        )
        for name in ["quality.json", "heldout-requests.jsonl", "quality-samples.jsonl"]:
            await self.grader.write_file(
                f"{REMOTE}/{name}", (self.artifacts / name).read_text()
            )

    async def install(self, item: Submission) -> None:
        """Replace the grader's task directory with one snapshot on a cleared GPU and port."""
        await self.restore_trusted()
        # stop anything an earlier submission left on the grading GPU or port
        await checked(
            self.grader,
            [PYTHON, POD, "kill", f"--gpu={GRADING_GPU}", f"--port={BASE_PORT}"],
            SHORT_TIMEOUT,
        )
        await self.grader.upload(item.archive, f"{REMOTE}/submission.tar.gz")
        await checked(
            self.grader,
            [PYTHON, POD, "unpack", f"{REMOTE}/submission.tar.gz", TASK_DIR],
            ARCHIVE_TIMEOUT,
        )
        await checked(
            self.grader,
            [
                "rm",
                "-f",
                f"{REMOTE}/submission.tar.gz",
                f"{REMOTE}/final.json",
                f"{REMOTE}/final-server.log",
            ],
            SHORT_TIMEOUT,
        )

    async def grade(self, item: Submission) -> tuple[dict[str, Any], dict[str, Any]]:
        """Measure one snapshot with the hidden final evaluation, without the integrity judge."""
        folder = self.artifacts / "submissions" / str(item.number)
        folder.mkdir(parents=True, exist_ok=True)
        await self.install(item)
        output = await checked(
            self.grader,
            [PYTHON, f"{REMOTE}/runtime.py", "final", f"{REMOTE}/options.json"],
            EVALUATION_TIMEOUT,
        )
        (folder / "final-evaluator.log").write_text(output)
        metrics = json.loads(await self.grader.read_file(f"{REMOTE}/final.json"))
        log = await self.grader.read_file(f"{REMOTE}/final-server.log")
        (folder / "final.json").write_text(json.dumps(metrics, indent=2))
        (folder / "final-server.log").write_text(log)
        # peers see only the outcome category and rounded speedup; the server log
        # can contain held-out data
        public = classify_final(metrics, self.held_out_baseline, self.scenario)
        detail: dict[str, Any] = {"metrics_file": str(folder / "final.json")}
        if self.flags["record_validation_errors"]:
            detail["validation"] = validation_record(metrics, public)
        return public, detail

    def proxy_speedup(self, metrics: dict[str, Any]) -> float | None:
        """Compare development measurements with the development Transformers baseline."""
        value = speedup(metrics, self.dev_baseline, self.scenario)
        return None if value is None else round(value, 3)

    def summary(
        self, released: float | None, completed: float | None
    ) -> dict[str, Any]:
        """Collect lease, GPU, submission and failure measurements over the solving interval."""
        submissions = self.queue.summary(
            released if released is not None else self.scheduler.started
        )
        if not self.flags["log_all_submissions"]:
            submissions["items"] = [
                item
                for item in submissions["items"]
                if item["number"] == submissions["final_submission"]
            ]
        return {
            "options": self.options,
            "leases": self.scheduler.summary(released, completed),
            "gpu": self.usage.summary(),
            "submissions": submissions,
            "events": self.events,
            "oom_events": sum(e["kind"] == "oom" for e in self.events),
        }

    def ledger(self) -> list[dict[str, Any]]:
        """Describe every submission with its shared grade, measures and archive, one row each."""
        selected = self.queue.select()
        rows = []
        for item in self.queue.items:
            source = self.queue.origin(item)
            rows.append(
                {
                    "number": item.number,
                    "actor": item.actor,
                    "digest": item.digest,
                    "bytes": item.size,
                    "status": source.status if item.same_as else item.status,
                    "same_content_as": item.same_as,
                    "submitted_at_epoch": item.submitted_at,
                    "grading_started_at_epoch": source.started_at,
                    "grading_finished_at_epoch": source.finished_at,
                    "result": source.result,
                    "detail": source.detail,
                    "measures": item.measures,
                    "archive": item.archive if Path(item.archive).exists() else None,
                    "selected": item is selected,
                }
            )
        return rows

    def persist(self) -> None:
        """Copy the current measurements into the sample store and ledger so failed runs keep them."""
        history = store_as(TeamHistory)
        released, completed = (
            datetime.fromisoformat(value).timestamp() if value else None
            for value in (history.released, history.completed)
        )
        store_as(InferenceHistory).orchestration = self.summary(released, completed)
        if self.flags["log_all_submissions"] and self.queue.items:
            path = self.artifacts / "submissions.jsonl"
            partial = path.with_name(path.name + ".partial")
            partial.write_text("".join(json.dumps(r) + "\n" for r in self.ledger()))
            partial.replace(path)

    def finalize(self, selected: Submission | None, score: Any) -> None:
        """Record which archive set the score and, unless all are kept, delete the others."""
        record = {
            "scoring": self.queue.scoring,
            "submission": selected.number if selected else None,
            "digest": selected.digest if selected else None,
            "archive": selected.archive if selected else None,
            "in_run_result": self.queue.origin(selected).result if selected else None,
            "final_score": score,
        }
        (self.artifacts / "final-submission.json").write_text(
            json.dumps(record, indent=2)
        )
        if not self.flags["save_submitted_artifacts"]:
            # the scored archive is always kept so the final server can be reproduced
            keep = selected.archive if selected else None
            for item in self.queue.items:
                if item.archive != keep:
                    Path(item.archive).unlink(missing_ok=True)
        self.persist()

    async def stop(self, *names: str) -> None:
        """Cancel the named background tasks, or all of them, and wait for them to exit."""
        stopping = [
            self.tasks.pop(name)
            for name in (names or list(self.tasks))
            if name in self.tasks
        ]
        for task in stopping:
            task.cancel()
        await asyncio.gather(*stopping, return_exceptions=True)


def current() -> Session | None:
    """Return the leased-GPU session bound to the current Inspect sample, if any."""
    identity = store().get(SESSION_KEY)
    return SESSIONS.get(identity) if identity else None


async def prepare_workspace_pod(
    workspace: SandboxEnvironment, metadata: dict[str, Any], options: dict[str, Any]
) -> dict[str, Any]:
    """Install development inputs and the development baseline on the workspace pod."""
    await checked(workspace, ["mkdir", "-p", REMOTE], SHORT_TIMEOUT)
    await install_scripts(workspace)
    await workspace.write_file(
        f"{REMOTE}/workspace-options.json",
        json.dumps(workspace_options(metadata, options["dev_eval"])),
    )
    inventory = await checked(
        workspace,
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
        SHORT_TIMEOUT,
    )
    if len(inventory.strip().splitlines()) != options["workspace_gpus"]:
        raise RuntimeError(
            f"Workspace pod exposes {inventory.strip().splitlines()} instead of {options['workspace_gpus']} GPUs"
        )
    result = last_json(
        await checked(
            workspace,
            [PYTHON, POD, "workspace", f"{REMOTE}/workspace-options.json"],
            EVALUATION_TIMEOUT,
            {"CUDA_VISIBLE_DEVICES": "0"},
        )
    )
    result["dev_baseline"] = json.loads(
        await workspace.read_file(f"{pod.DEV}/baseline.json")
    )
    result["gpu_inventory"] = inventory
    return result


async def open_session(
    state: TaskState,
    options: dict[str, Any],
    budget: dict[str, Any] | None,
    tool_timeout: int,
    upstream: Solver,
    generate: Generate,
) -> TaskState:
    """Allocate both pods, prepare grader and workspace in parallel and start background work."""
    receipts = Path(state.metadata["artifacts_root"]) / "runpod"
    receipts.mkdir(parents=True, exist_ok=True)
    config = workspace_gpu_config(
        state.metadata["gpu_config"], options["workspace_gpus"], receipts
    )
    # each pod bootstraps its own CUDA stack, so allocate both at once
    async with asyncio.TaskGroup() as group:
        group.create_task(resource.allocate(state))
        group.create_task(resource.allocate(state, resource.WORKSPACE_KEY, config))
    grader = resource.controller_gpu()
    workspace = resource.controller_gpu(resource.WORKSPACE_KEY)
    if grader is None or workspace is None:
        raise RuntimeError("Both InferenceBench pods must be allocated")
    # a failed preparation cancels the other before cleanup deletes both pods
    async with asyncio.TaskGroup() as group:
        graded = group.create_task(upstream(state, generate))
        developed = group.create_task(
            prepare_workspace_pod(workspace, state.metadata, options)
        )
    upstream_state, prepared = graded.result(), developed.result()
    artifacts = Path(store().get("artifacts"))
    session = Session(
        options,
        budget,
        tool_timeout,
        workspace,
        grader,
        artifacts,
        dict(state.metadata),
        dev_baseline=prepared.pop("dev_baseline"),
        held_out_baseline=json.loads((artifacts / "baseline.json").read_text()),
    )
    (artifacts / "workspace-preparation.json").write_text(
        json.dumps(prepared, indent=2)
    )
    identity = uuid4().hex
    SESSIONS[identity] = session
    store().set(SESSION_KEY, identity)
    session.start()
    session.persist()
    return upstream_state


async def close_session(state: TaskState) -> None:
    """Stop background work and keep the final measurements before pods are deleted."""
    identity = state.store.get(SESSION_KEY)
    session = SESSIONS.pop(identity, None) if identity else None
    if session is None:
        return
    with anyio.CancelScope(shield=True):
        await session.stop()
        session.persist()


async def cleanup(state: TaskState) -> None:
    """Close the leased-GPU session, then retain evidence and delete the sample's pods."""
    try:
        await close_session(state)
    finally:
        await resource.cleanup(state)
