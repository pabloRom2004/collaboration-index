"""Verify complete checkpoint continuation through the upstream Ruff Docker task."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.docker
def test_ruff_restores_files_board_caps_and_finished_peer(tmp_path):
    """Resume a forced failure without repeating a finished peer or losing its compaction."""
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
    environment["COLLAB_CHECKPOINT_QA_DIR"] = str(tmp_path)
    fixture = Path(__file__).parent / "fixtures/mirrorcode_checkpoint_qa.py"
    completed = subprocess.run(
        [sys.executable, str(fixture)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    proof = json.loads((tmp_path / "finished-peer-proof.json").read_text())
    assert proof["passed"] and proof["finished_compaction_retained"]
    assert proof["graded_cases"] == [822] and proof["peer_tokens"] == [240, 240]
    assert (
        proof["finished_peer_calls_before"] == proof["finished_peer_calls_after"] == 4
    )
    assert proof["restored_file_peers"] == 1 and proof["frozen_writer_checks"] >= 2
