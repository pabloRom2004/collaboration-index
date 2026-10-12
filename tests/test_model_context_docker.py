"""Verify reserved input fitting and durable compaction in real Ruff Docker."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.docker
def test_reserved_context_ruff_checkpoint_restore(tmp_path):
    """Restore oversized-tool compaction, files, board and cumulative caps through native Inspect."""
    pytest.importorskip("mc")
    images = subprocess.run(
        ["docker", "image", "inspect", "mclocal:mc_ruff_python_llm_workspace_0.1.193"],
        capture_output=True,
        timeout=15,
    )
    if images.returncode:
        pytest.skip("Pinned upstream Ruff Docker images must be built first")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "DOCKER_HOST", "DOCKER_CONTEXT"}
    }
    environment["COLLAB_CONTEXT_QA_DIR"] = str(tmp_path)
    fixture = Path(__file__).parent / "fixtures/mirrorcode_context_qa.py"
    completed = subprocess.run(
        [sys.executable, str(fixture)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    proof = json.loads((tmp_path / "context-docker-proof.json").read_text())
    assert proof["passed"] and proof["all_request_envelopes_valid"]
    assert proof["graded_cases"] == [822] and proof["restored_file_peers"] == 2
    assert proof["summary_calls"] >= 2 and proof["board_embedded"]
