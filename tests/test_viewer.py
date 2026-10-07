"""Check replay source links and the opt-in .eval downloads served beside replays."""

from pathlib import Path

from fastapi.testclient import TestClient
from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log, write_eval_log

from collaboration_index.counting import counting
from collaboration_index.replay import replay_data
from collaboration_index.smoke import fixture_model
from collaboration_index.viewer import create_viewer

LOGS = Path(__file__).parents[1] / "logs"


def test_source_links_point_at_the_log_and_hawk_sample(tmp_path: Path) -> None:
    """Link local runs to their file only, and Hawk runs to the exact viewer sample too."""
    task = counting(
        target=4,
        token_limit_per_agent=10000,
        sandbox_enabled=False,
        artifact_dir=str(tmp_path),
    )
    [log] = inspect_eval(
        task, model=fixture_model(task), log_dir=str(LOGS), display="none"
    )
    path = Path(log.location)
    local = replay_data(path, None, hawk_viewer="https://viewer.example")
    assert local["source"] == {"eval_file": path.name, "hawk_url": None}
    stored = read_eval_log(str(path))
    stored.eval.eval_set_id = "demo-set"
    hawk_path = tmp_path / path.name
    write_eval_log(stored, str(hawk_path))
    hawk = replay_data(hawk_path, None, hawk_viewer="https://viewer.example/")
    sample = stored.samples[0]
    assert hawk["source"]["hawk_url"] == (
        f"https://viewer.example/eval-set/demo-set#/samples/{path.name}"
        f"/sample/{sample.id}/{sample.epoch}/"
    )


def test_viewer_serves_only_top_level_eval_files_when_enabled(tmp_path: Path) -> None:
    """Serve .eval downloads from the chosen folder and refuse other files or no folder."""
    replays, logs = tmp_path / "replays", tmp_path / "logs"
    replays.mkdir()
    logs.mkdir()
    (logs / "run.eval").write_bytes(b"PK")
    (logs / "notes.txt").write_text("not a log")
    (tmp_path / "outside.eval").write_bytes(b"PK")
    client = TestClient(create_viewer(replays, logs), base_url="http://127.0.0.1")
    response = client.get("/logs/run.eval")
    assert response.status_code == 200 and response.content == b"PK"
    assert "attachment" in response.headers["content-disposition"]
    assert client.get("/logs/notes.txt").status_code == 404
    assert client.get("/logs/..%2Foutside.eval").status_code == 404
    closed = TestClient(create_viewer(replays), base_url="http://127.0.0.1")
    assert closed.get("/logs/run.eval").status_code == 404
