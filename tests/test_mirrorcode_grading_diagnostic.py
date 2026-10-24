"""Check frozen-input integrity, zero-model replay and visible batch-failure reporting."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.util import ExecResult

from collaboration_index.mirrorcode import grading_diagnostic as diagnostic


def archive_bytes(name: str = "ruff/__init__.py") -> bytes:
    """Build a harmless source fixture without executing its contents."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.size = 4
        archive.addfile(member, io.BytesIO(b"pass"))
    return output.getvalue()


def test_frozen_source_rejects_changes_and_unsafe_members() -> None:
    """Fail on mismatched bytes and traversal before the source can reach extraction."""
    data = archive_bytes()
    diagnostic.validate_source(data, hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError, match="hash mismatch"):
        diagnostic.validate_source(data, "0" * 64)
    data = archive_bytes("../outside.py")
    with pytest.raises(ValueError, match="unsafe member"):
        diagnostic.validate_source(data, hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize("batch_failed", [False, True])
def test_mock_end_to_end_replay_without_model_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, batch_failed: bool
) -> None:
    """Run the solver and scorer through Inspect with scripted sandbox results and no model."""
    import mc.scorer as upstream
    from mc import TestCase, TestCaseLabel

    data = archive_bytes()
    rows = [
        {
            "test_case": TestCase(
                args=[], stdin="", labels=[] if i < 761 else [TestCaseLabel.HIDDEN]
            ).as_jsonable_dict(agent=False),
            "expected": {"returncode": 0, "stdout": "ok", "stderr": ""},
        }
        for i in range(822)
    ]
    raw_cases = "\n".join(json.dumps(row) for row in rows).encode()
    monkeypatch.setattr(
        diagnostic,
        "BINDINGS",
        {
            "n32": {
                "source": hashlib.sha256(data).hexdigest(),
                "cases": hashlib.sha256(raw_cases).hexdigest(),
            }
        },
    )
    calls = []

    class ScriptedSandbox:
        """Expose only the trusted preflight and monitoring used by this fixture."""

        async def exec(self, cmd, **kwargs):
            """Return scripted trusted checks while rejecting unexpected commands."""
            calls.append((cmd, kwargs))
            if cmd == ["uname", "-m"]:
                return ExecResult(True, 0, "x86_64\n", "")
            if cmd == ["/usr/local/bin/restricted-shell", "/bin/echo", "guard-ready"]:
                assert kwargs["user"] == "coder"
                return ExecResult(True, 0, "guard-ready\n", "")
            assert cmd[:2] == ["sh", "-c"] and "memory.max" in cmd[2]
            return ExecResult(True, 0, "FIELD:memory.max\n2147483648\n", "")

        async def read_file(self, path):
            """Retain the same offline resolver as the original evaluations."""
            assert path == "/etc/resolv.conf"
            return "nameserver 100::1\n"

    async def download(cohort, kind):
        """Supply frozen local fixture bytes without a network call."""
        return data if kind == "source" else raw_cases

    async def batch(service, script, cases, target, language, timeout, tar_bytes):
        """Check the unchanged upstream call contract and supply bounded execution results."""
        from inspect_ai.model import get_model

        with pytest.raises(RuntimeError, match="Inference disabled"):
            await get_model().generate("Blocked local fixture probe")
        assert service == "default" and len(cases) == 761 and timeout == 300
        assert tar_bytes == data
        assert hashlib.sha256(script.encode()).hexdigest() == diagnostic.BATCH_SHA256
        return [
            upstream.TestResult(
                None if batch_failed else upstream.ExecResult(True, 0, "ok", ""),
                False,
                "Batch scoring was killed (exit code 137, possible OOM)."
                if batch_failed
                else None,
            )
            for _ in cases
        ]

    monkeypatch.setattr(diagnostic, "sandbox", lambda: ScriptedSandbox())
    monkeypatch.setattr(diagnostic, "download_input", download)
    monkeypatch.setattr(upstream, "_execute_on_agent_scoring_sandbox", batch)
    configured = diagnostic.mirrorcode_frozen_visible_diagnostic()
    configured.sandbox = None
    log = inspect_eval(
        configured, model="mockllm/model", log_dir=str(tmp_path), display="none"
    )[0]
    full = read_eval_log(log.location)
    assert full.samples and len(full.samples) == 1
    sample = full.samples[0]
    assert not any(event.event == "model" for event in sample.events)
    assert sample.error is not None if batch_failed else sample.error is None
    if not batch_failed:
        assert next(iter(sample.scores.values())).value == 1.0
    proof_paths = list(tmp_path.rglob("diagnostic-proof.json"))
    assert len(proof_paths) == 1
    proof = json.loads(proof_paths[0].read_text())
    assert (
        proof["model_calls"] == 0 and proof["batch_execution_failure"] == batch_failed
    )
    assert len(calls) >= 3
