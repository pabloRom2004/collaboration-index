"""Prepare target-bound two-peer concurrent grader/race proof on an isolated Linux runner."""

import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    """Adapt the maintained real-Docker fixture inside a disposable remote artifact only."""
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    target = os.environ["MIRRORCODE_QA_TARGET"]
    assert target in {"cprepro", "sed"}
    repo = Path.cwd()
    root = repo / "run-artifacts" / f"remote-{target}-concurrent-qa"
    root.mkdir(parents=True, exist_ok=True)
    pins = json.loads(Path(os.environ["MIRRORCODE_QA_IMAGE_PINS"]).read_text())
    source = (repo / "tests/fixtures/mirrorcode_cleanup_qa.py").read_text()
    assert source.count('target="ruff"') == 1
    assert source.count("cases == [822]") == 1
    assert source.count('authored_cleanup_injected_services"] = 3') == 1
    assert source.count("await asyncio.wait_for(written.wait(), 60)") == 1
    assert source.count("written = asyncio.Event()") == 1
    assert source.count("agents_per_scoring_pipeline=8") == 1
    assert source.count("src/ruff") == 3
    source = source.replace(
        'target="ruff"', f'target="{target}", image_pins={pins!r}'
    ).replace("src/ruff", f"src/{target}")
    source = source.replace(
        "agents_per_scoring_pipeline=8", "agents_per_scoring_pipeline=1"
    )
    source = source.replace(
        "written = asyncio.Event()",
        "written = asyncio.Event()\nscoring_barrier = asyncio.Barrier(2)",
    )
    source = source.replace(
        "await asyncio.wait_for(written.wait(), 60)",
        "await asyncio.wait_for(written.wait(), 60)\n        await asyncio.wait_for(scoring_barrier.wait(), 60)",
    )
    source = source.replace(
        '"agent-scoring-hidden",',
        '"agent-scoring-hidden",\n            "reference-scoring-1",\n            "agent-scoring-visible-1",\n            "agent-scoring-hidden-1",',
    )
    source = source.replace(
        'authored_cleanup_injected_services"] = 3',
        'authored_cleanup_injected_services"] = 6',
    )
    counter = '''
import importlib
from inspect_ai.tool import ToolDef
_task_module = importlib.import_module("collaboration_index.mirrorcode.task")
_original_pooled = _task_module.pooled
_active_calls = 0
_peak_calls = 0
def counted_pooled(definition, pipelines, tar_lock):
    """Record overlapping real native testing calls on the assigned grading pool."""
    wrapped = _original_pooled(definition, pipelines, tar_lock)
    info = ToolDef(wrapped)
    async def execute():
        """Keep numeric admission counts while delegating the unchanged production tool."""
        global _active_calls, _peak_calls
        _active_calls += 1
        _peak_calls = max(_peak_calls, _active_calls)
        try:
            return await wrapped()
        finally:
            _active_calls -= 1
    return ToolDef(execute, name=info.name, description=info.description, parameters=info.parameters).as_tool()
_task_module.pooled = counted_pooled
'''
    assert source.count("task = mirrorcode(") == 1
    source = source.replace("task = mirrorcode(", counter + "task = mirrorcode(")
    assert source.count("proof = {") == 1
    extra = (
        """
assert _peak_calls == 2
assert log.eval.metadata["scoring_pipelines"] == 2
rates = s.scores["mirrorcode_scorer"].metadata["pass_rates"]["default"]
expected = """
        + repr(
            {
                "cprepro": {"all": 1310, "visible": 680, "hidden": 630},
                "sed": {"all": 794, "visible": 470, "hidden": 324},
            }[target]
        )
        + """
assert {k: rates[k]["total"] for k in expected} == expected
final_cases = next(v["cases"] for k,v in s.metadata.items() if k.startswith("scored_cases_"))
assert all(c["expected"] is not None and c["actual"] is not None for c in final_cases)
"""
    )
    source = source.replace(
        "proof = {",
        extra
        + 'proof = {"peak_native_testing_calls": _peak_calls, "case_totals": expected, ',
    )
    total = {"cprepro": 1310, "sed": 794}[target]
    source = source.replace("cases == [822]", f"cases == [{total}]")
    fixture = root / "concurrent_qa.py"
    fixture.write_text(source)
    env = os.environ.copy()
    env["COLLAB_CLEANUP_QA_DIR"] = str(root)
    subprocess.run([sys.executable, str(fixture)], env=env, check=True, timeout=1200)
    proof = json.loads((root / "proof.json").read_text())
    assert proof["passed"] and proof["testing_calls"] == 2 and proof["tool_errors"] == 0
    assert proof["authored_injected_grading_services"] == 6


if __name__ == "__main__":
    main()
