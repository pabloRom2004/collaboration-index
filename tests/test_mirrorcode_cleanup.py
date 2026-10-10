"""Check that grading cleanup only tolerates disappearing filesystem paths."""

import importlib.util
from types import SimpleNamespace

import pytest

from collaboration_index.mirrorcode.cleanup import (
    CLEANUP_LINE,
    install_cleanup_repair,
    repair_cleanup_script,
)


def upstream_script() -> str:
    """Read only trusted installed upstream grading implementation."""
    import mc.scorer as scorer

    return (scorer.PROJECT_ROOT / "mc/_data/batch_score_test_cases.py").read_text()


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(2, "authored vanished path"),
        PermissionError(13, "authored permission"),
        OSError(5, "authored I/O"),
    ],
)
def test_cleanup_preserves_nonmissing_errors(tmp_path, error):
    """Remove harmless files despite missing paths and propagate other cleanup errors."""
    script = tmp_path / "batch.py"
    script.write_text(repair_cleanup_script(upstream_script()))
    spec = importlib.util.spec_from_file_location("cleanup_fixture", script)
    module = importlib.util.module_from_spec(spec)
    # Dataclass annotations need the module registered during import.
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    directory = tmp_path / "run"
    (directory / "child").mkdir(parents=True)

    def interrupted_delete(item, *, onexc):
        """Inject only an authored filesystem error into upstream cleanup callback."""
        onexc(None, str(item / "leaf"), error)

    module.shutil = SimpleNamespace(rmtree=interrupted_delete)
    if isinstance(error, FileNotFoundError):
        module.cleanup_files(directory)
    else:
        with pytest.raises(type(error)):
            module.cleanup_files(directory)


def test_unknown_cleanup_source_refused():
    """Fail closed when dependency source no longer matches the scoped repair."""
    with pytest.raises(RuntimeError):
        repair_cleanup_script(
            upstream_script().replace(
                CLEANUP_LINE, "            different_cleanup(item)"
            )
        )


def test_context_repairs_reference_and_agent_script(monkeypatch):
    """Apply the same repair through the real upstream scoring context constructor."""
    import mc.scorer as scorer

    original = scorer.SandboxScoringContext
    monkeypatch.setattr(scorer, "SandboxScoringContext", original)
    digest = install_cleanup_repair()
    context = scorer.SandboxScoringContext(None, upstream_script(), "[]", "{}")
    assert "onexc=missing_descendant" in context.script_content
    assert len(digest) == 64
    assert install_cleanup_repair() == digest


@pytest.mark.docker
def test_cleanup_race_through_real_ruff_grading(tmp_path):
    """Exercise vanished descendants in reference/testing/final grading without hiding errors."""
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    environment = os.environ.copy()
    environment["COLLAB_CLEANUP_QA_DIR"] = str(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parent / "fixtures/mirrorcode_cleanup_qa.py"),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    proof = json.loads((tmp_path / "proof.json").read_text())
    assert proof["passed"] and proof["graded_cases"] == [822]
    assert proof["testing_calls"] == 2 and proof["tool_errors"] == 0
    assert proof["authored_injected_grading_services"] == 3
    assert proof["peer_statuses"] == ["limited", "limited"]
    assert proof["peer_tokens"] == [120, 120]
