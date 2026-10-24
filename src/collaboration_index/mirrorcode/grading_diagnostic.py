"""Replay frozen visible Ruff grading with the original sandbox and no model calls."""

import asyncio
import hashlib
import io
import json
import os
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox

IMAGE = "ghcr.io/pablorom2004/mirrorcode@sha256:9756f7bb80f33d2ca39ac746b7d4266d1c12b291f583fe10c054a91fd7691801"
BATCH_SHA256 = "aa38e6229b974b90d675c57e2dc26c3790f1cf580c3da0384626395bdd9e2c75"
BINDINGS = {
    "n32": {
        "source": "335204123250b57d537cacd3606b1eb0016f6083401ccfc6adae390116fcd8f9",
        "cases": "26cba9aefc2909fd45db39dd60020387ddb955b99c802d4b3f7795d2696a5973",
    },
    "n04": {
        "source": "bdba97e696ddc014f9a764548e7467eb216878c3b09935308e47cf69d938a635",
        "cases": "6cc54a5f1af0d24692262907ca939ff01a27f7f66025c95d443fc6745f4ff7a6",
    },
}


def validate_source(data: bytes, digest: str) -> None:
    """Reject changed bytes and unsafe archive members before any extraction."""
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Frozen source hash mismatch")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError("Frozen source archive has an unsafe member")


def visible_rows(case_bytes: bytes) -> list[dict[str, Any]]:
    """Decode the original saved sidecar's labels and require the exact case roster."""
    rows = [json.loads(line) for line in case_bytes.decode().splitlines()]
    visible = [row for row in rows if row["test_case"]["labels"] == []]
    hidden = [row for row in rows if row["test_case"]["labels"] == ["hidden"]]
    if len(rows) != 822 or len(visible) != 761 or len(hidden) != 61:
        raise ValueError("Original 822/761/61 case split changed")
    return visible


async def download_input(cohort: str, kind: str) -> bytes:
    """Fetch hash-bound private artifacts from runner secrets without logging signed URLs."""
    url = os.environ[f"DIAG_{cohort.upper()}_{kind.upper()}_URL"]
    if not url.startswith("https://"):
        raise ValueError("HTTPS artifact URL required")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url)
    except httpx.HTTPError:
        raise RuntimeError("Private artifact download failed") from None
    if not response.is_success:
        raise RuntimeError(f"Private artifact download status {response.status_code}")
    data = response.content
    if (
        len(data) > 64 * 1024 * 1024
        or hashlib.sha256(data).hexdigest() != BINDINGS[cohort][kind]
    ):
        raise ValueError("Frozen artifact size or hash mismatch")
    return data


@solver
def frozen_visible_replay() -> Solver:
    """Execute the pinned upstream batch once per frozen source without calling generate."""

    serial = asyncio.Lock()

    async def replay(state: TaskState, generate: Generate) -> TaskState:
        """Verify trusted guards, replay original cases and preserve diagnostic evidence."""
        import mc.scorer as upstream
        from inspect_ai.model import get_model

        async def inference_disabled(*args: Any, **kwargs: Any) -> Any:
            """Deny every generation before an event or provider request can be created."""
            raise RuntimeError("Inference disabled for frozen grading-only evaluation")

        evaluated_model = get_model()
        object.__setattr__(evaluated_model, "generate", inference_disabled)
        object.__setattr__(evaluated_model.api, "generate", inference_disabled)
        from mc import AgentImplementationLanguage, TargetProgram, TestCase

        from collaboration_index.mirrorcode.cleanup import install_cleanup_repair

        cohort = str(state.sample_id)
        evidence: dict[str, Any] = {
            "cohort": cohort,
            "started_at": datetime.now(UTC).isoformat(),
            "model_calls": 0,
            "generation_disabled_at_model_and_provider": True,
            "evaluated_model_provenance": "openrouter/moonshotai/kimi-k3.1-pro",
            "source_changed": False,
            "original_result_replaced": False,
            "image": IMAGE,
            "source_sha256": BINDINGS[cohort]["source"],
            "case_sidecar_sha256": BINDINGS[cohort]["cases"],
            "memory_bytes": 2147483648,
            "batch_timeout_seconds": 300,
            "per_case_timeout_seconds": 2,
            "diagnostic_only": True,
        }
        folder = upstream._resolve_sidecar_dir(state)
        if folder is None:
            raise RuntimeError("Durable diagnostic artifacts unavailable")
        folder.mkdir(parents=True, exist_ok=True)
        observations: list[dict[str, Any]] = []

        def save_evidence() -> None:
            """Save all observations before any later batch or operator failure."""
            with (folder / "diagnostic-proof.json").open("w") as output:
                json.dump(
                    {**evidence, "cgroup_observations": observations}, output, indent=2
                )

        async def metrics() -> None:
            """Sample available kernel memory and PID counters from the trusted controller."""
            cmd = [
                "sh",
                "-c",
                "for f in memory.max memory.current memory.peak memory.events pids.current pids.events cpu.max; do echo FIELD:$f; cat /sys/fs/cgroup/$f 2>/dev/null || true; done",
            ]
            result = await sandbox().exec(cmd, timeout=10)
            observations.append(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "stdout": result.stdout,
                    "returncode": result.returncode,
                }
            )
            save_evidence()

        save_evidence()
        arch = await sandbox().exec(["uname", "-m"], timeout=5)
        if not arch.success or arch.stdout.strip() != "x86_64":
            raise RuntimeError("Native x86_64 scoring sandbox required")
        guard = await sandbox().exec(
            ["/usr/local/bin/restricted-shell", "/bin/echo", "guard-ready"],
            user="coder",
            timeout=5,
        )
        evidence["guard_preflight"] = {
            "exit": guard.returncode,
            "stdout": guard.stdout,
            "stderr": guard.stderr,
        }
        save_evidence()
        if not guard.success or guard.stdout.strip() != "guard-ready":
            raise RuntimeError("Original guard unavailable; no subject execution")
        resolver = await sandbox().read_file("/etc/resolv.conf")
        if resolver.strip() != "nameserver 100::1":
            updated = await sandbox().exec(
                ["sh", "-c", "echo 'nameserver 100::1' > /etc/resolv.conf"], timeout=5
            )
            if not updated.success:
                raise RuntimeError("Original offline resolver unavailable")
        evidence["offline_resolver"] = "nameserver 100::1"
        await metrics()
        archive, case_bytes = await asyncio.gather(
            download_input(cohort, "source"), download_input(cohort, "cases")
        )
        validate_source(archive, BINDINGS[cohort]["source"])
        visible = visible_rows(case_bytes)
        cases = [TestCase.from_jsonable_dict(row["test_case"]) for row in visible]
        script = (
            upstream.PROJECT_ROOT / "mc" / "_data" / "batch_score_test_cases.py"
        ).read_text()
        if hashlib.sha256(script.encode()).hexdigest() != BATCH_SHA256:
            raise ValueError("Pinned original batch script changed")
        evidence["original_upstream_batch_sha256"] = BATCH_SHA256
        evidence["effective_original_cleanup_script_sha256"] = install_cleanup_repair()
        evidence["case_count"] = len(cases)
        evidence["batch_started_at"] = datetime.now(UTC).isoformat()
        save_evidence()
        operation = asyncio.create_task(
            upstream._execute_on_agent_scoring_sandbox(
                "default",
                script,
                cases,
                TargetProgram.RUFF,
                AgentImplementationLanguage.PYTHON,
                300,
                archive,
            )
        )
        try:
            while not operation.done():
                await asyncio.wait({operation}, timeout=2)
                await metrics()
            actual = await operation
            scored = [
                upstream._to_scored_test_case(
                    case,
                    upstream.TestResult(
                        upstream.ExecResult.from_jsonable_dict(row["expected"]),
                        False,
                        None,
                    ),
                    result,
                )
                for case, row, result in zip(cases, visible, actual, strict=True)
            ]
            upstream._write_scored_cases_sidecar(scored, state, 0)
            with (folder / "raw-batch-results.json").open("w") as output:
                from dataclasses import asdict

                json.dump([asdict(row) for row in actual], output)
            evidence.update(
                visible_passed=sum(row.passed() for row in scored),
                actual_results=sum(row.actual is not None for row in scored),
                ordinary_per_case_timeouts=sum(row.timed_out for row in actual),
                exceptions=[
                    row.raised_exception for row in actual if row.raised_exception
                ],
                batch_execution_failure=any(
                    "Batch scoring" in (row.raised_exception or "") for row in actual
                ),
            )
        finally:
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
            await metrics()
            evidence["completed_at"] = datetime.now(UTC).isoformat()
            save_evidence()
        state.metadata["diagnostic"] = evidence
        return state

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        """Replay the two independent samples serially without runner keyword overrides."""
        async with serial:
            return await replay(state, generate)

    return solve


@scorer(metrics=[mean()])
def frozen_visible_result() -> Scorer:
    """Expose completed replay measurements while failing visibly on a batch-wide error."""

    async def score(state: TaskState, target: Target) -> Score:
        """Keep infrastructure failure separate from the frozen implementation's pass count."""
        evidence = state.metadata["diagnostic"]
        if evidence["batch_execution_failure"]:
            raise RuntimeError(
                "Original batch execution failure reproduced; diagnostic evidence retained"
            )
        return Score(value=evidence["visible_passed"] / 761, metadata=evidence)

    return score


@task
def mirrorcode_frozen_visible_diagnostic() -> Task:
    """Grade both frozen submissions in separate original sandboxes with no solver model."""
    from collaboration_index.mirrorcode.task import prepare_hawk_dns

    prepare_hawk_dns()
    folder = Path(tempfile.mkdtemp(prefix="mirrorcode-frozen-grading-"))
    compose = folder / "compose.yaml"
    compose.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "default": {
                        "image": IMAGE,
                        "init": True,
                        "entrypoint": "tail -f /dev/null",
                        "mem_limit": "2g",
                        "network_mode": "none",
                    }
                }
            }
        )
    )
    return Task(
        dataset=[
            Sample(
                id=cohort,
                input="Trusted frozen visible-grading diagnostic; no model calls.",
            )
            for cohort in BINDINGS
        ],
        solver=frozen_visible_replay(),
        scorer=frozen_visible_result(),
        sandbox=("docker", str(compose)),
        fail_on_error=False,
        metadata={
            "diagnostic_only": True,
            "model_calls_authorized": 0,
            "subject_source_publication": False,
            "image": IMAGE,
        },
    )
