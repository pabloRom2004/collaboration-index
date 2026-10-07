"""Adapt trusted team state to the shared board replay without exporting private model histories."""

import argparse
import json
import math
from pathlib import Path
from typing import Any

from inspect_ai.log import read_eval_log

from collaboration_index.game import clashing_edges, similarity


def target_view(kind: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Describe what an ordered task should produce, so the replay can show progress against it."""
    if kind == "counting":
        return {"kind": kind, "target": data["target"], "quota": data["quota"]}
    if kind == "spelling":
        # hands are reusable, so the team can spell any sentence covered by their union
        letters = set().union(*(set(hand) for hand in data["hands"].values()))
        return {
            "kind": kind,
            "sentences": data["sentences"],
            "dealt_sentence": data["dealt_sentence"],
            "team_characters": sorted(letters),
            "feasible": [s for s in data["sentences"] if set(s) <= letters],
        }
    return None


def replay_data(
    log_path: Path,
    journal_path: Path | None,
    sample_id: str | None = None,
    epoch: int | None = None,
) -> dict[str, Any]:
    """Validate one team sample and combine its structural measurements with the board journal."""
    log = read_eval_log(str(log_path), resolve_attachments=False)
    samples = [
        s
        for s in log.samples or []
        if (sample_id is None or str(s.id) == sample_id)
        and (epoch is None or s.epoch == epoch)
    ]
    if len(samples) != 1:
        raise ValueError("Select exactly one team sample and epoch")
    sample = samples[0]
    store = sample.store
    if not store.get("TeamHistory:initialized"):
        raise ValueError("The log has no trusted team measurement state")
    run_id = store["TeamHistory:run_id"]
    peers = store["TeamHistory:peers"]
    actors = [p["id"] for p in peers]
    events = (
        [json.loads(line) for line in journal_path.read_text().splitlines() if line]
        if journal_path is not None
        else store.get("BoardHistory:journal")
    )
    if events is None:
        raise ValueError("The log has no stored board journal; pass its board.jsonl")
    if any(e["run"] != run_id or e["actor"] not in actors for e in events):
        raise ValueError("Journal identity differs from the selected team attempt")
    result = store.get("TeamHistory:result", {})
    kind = store["TeamHistory:benchmark"]
    entries = store.get("TeamHistory:submissions", [])
    data = sample.metadata["data"]
    expected_sentence = data.get("dealt_sentence", "")
    if kind == "spelling":
        line = "".join(str(entry["value"]) for entry in entries).removesuffix("\n")
        expected_sentence = max(
            data["sentences"], key=lambda sentence: similarity(line, sentence)
        )
    count = (
        len(data["questions"])
        if kind == "hle"
        else data["target"]
        if kind == "counting"
        else len(data["edges"])
        if kind == "colouring"
        else len(expected_sentence)
    )
    items = [
        f"question_{i}" if kind == "hle" else f"position_{i}"
        for i in range(1, count + 1)
    ]
    grades: list[dict[str, Any]] = []
    if kind == "colouring":
        # one progress item per network edge, re-checked after every colour change
        items = [f"{a} ↔ {b}" for a, b in data["edges"]]
        colours: dict[str, str] = {}
        for entry in entries:
            colours[entry["actor"]] = entry["value"]
            clashes = clashing_edges(colours, data["edges"])
            grades.append(
                {
                    "time": entry["time"],
                    "capabilities": {
                        item: edge not in clashes
                        for item, edge in zip(items, data["edges"], strict=True)
                    },
                    "error": False,
                }
            )
    elif kind != "hle":
        for index, entry in enumerate(entries):
            caps = {}
            for i, previous in enumerate(entries[: index + 1]):
                if i < count:
                    expected = i + 1 if kind == "counting" else expected_sentence[i]
                    caps[items[i]] = previous["value"] == expected
            grades.append({"time": entry["time"], "capabilities": caps, "error": False})
    if kind == "hle" and sample.completed_at:
        grades.append(
            {
                "time": sample.completed_at,
                "capabilities": {
                    f"question_{j['question_number']}": j.get("correct")
                    for j in store.get("TeamHistory:judgments", [])
                },
                "error": bool(result.get("unscored_questions")),
            }
        )
    # JSON has no NaN; retain failed grading as explicit null in portable replay data.
    result = {
        key: None if isinstance(value, float) and not math.isfinite(value) else value
        for key, value in result.items()
    }
    quality = result.get("quality")
    scored = (
        quality is not None
        and isinstance(quality, (int, float))
        and math.isfinite(quality)
        and not sample.error
    )
    return {
        "fixture_verification": log.eval.model.startswith("mockllm/"),
        "events": events,
        "topology": "graph" if kind == "colouring" else "shared_sandbox",
        "graph": {
            "nodes": actors,
            "edges": data["edges"],
            "colours": data["colours"],
        }
        if kind == "colouring"
        else None,
        "target": target_view(kind, data),
        "benchmark_title": sample.metadata.get("benchmark_title", kind),
        "condition": store["TeamHistory:condition"],
        "flags": items,
        "submissions": entries,
        "agents": [
            {
                "id": p["id"],
                "run_id": run_id,
                "status": p["status"],
                "started": p.get("started"),
                "completed": p.get("completed"),
                "tokens": p["tokens"],
                "model": log.eval.model,
                "grades": [],
                "benchmark": True,
                "result": {"status": p["status"]},
                "source": None,
                "filename": log_path.name,
            }
            for p in peers
        ],
        "collective": {
            "started": store.get("TeamHistory:released"),
            "completed": sample.completed_at,
            "grades": grades,
            "benchmark": True,
            "result": {"status": "scored" if scored else "unscored"},
        },
        "team": {
            "mode": "shared_sandbox",
            "expected_agents": actors,
            "expected_count": len(actors),
            "status": "scored" if scored else "unscored",
            "quality": quality if scored else None,
            "collective_flags": sum(
                g.get("correct") is True for g in store.get("TeamHistory:judgments", [])
            ),
            "metrics": result,
        },
    }


def render(
    log_path: Path,
    journal_path: Path | None,
    output: Path,
    sample_id: str | None = None,
    epoch: int | None = None,
) -> None:
    """Write a portable replay using the common frontend and safely embedded journal data."""
    template = (Path(__file__).parent / "assets/forum/replay.html").read_text()
    data = json.dumps(
        replay_data(log_path, journal_path, sample_id, epoch),
        ensure_ascii=False,
        allow_nan=False,
    ).replace("<", "\\u003c")
    output.write_text(template.replace("__BOARD_EVENTS__", data))


def main() -> None:
    """Render a selected collective log and board journal without reading model transcripts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval", required=True, type=Path)
    parser.add_argument("--journal", type=Path, help="Defaults to the log's journal")
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--sample")
    parser.add_argument("--epoch", type=int)
    args = parser.parse_args()
    render(args.eval, args.journal, args.html, args.sample, args.epoch)


if __name__ == "__main__":
    main()
