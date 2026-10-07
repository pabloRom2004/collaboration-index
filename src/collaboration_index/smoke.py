"""Verify real team orchestration, board, submissions, sandbox and replay with authored mocks."""

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.model import ChatMessageTool, ModelOutput, ModelUsage, get_model

from collaboration_index.counting import counting
from collaboration_index.hle import hle_collaboration
from collaboration_index.replay import render
from collaboration_index.spelling import spelling


def fixture_model(task: Task) -> Any:
    """Script each peer's harmless collaboration actions and enforce ordered fixture submissions."""
    data = (task.dataset[0].metadata or {})["data"]
    count = (task.metadata or {})["agents"]
    kind = task.name
    collaborative = (task.metadata or {})["condition"] == "collaborative"
    runs: dict[str, tuple[asyncio.Barrier, list[asyncio.Event]]] = {}
    if kind == "hle":
        owned = {
            i: [
                (row["question_number"], row["answer"])
                for row in data["questions"]
                if (row["question_number"] - 1) % count == i
            ]
            for i in range(count)
        }
    elif kind == "counting":
        quota = data["quota"]
        owned = {
            i: [
                (n, n)
                for n in range(i * quota + 1, min((i + 1) * quota, data["target"]) + 1)
            ]
            for i in range(count)
        }
    else:
        text = data["dealt_sentence"] + "\n"
        owners = [
            next(i for i in range(count) if char in data["hands"][f"agent_{i}"])
            for char in text
        ]
        owned = {
            i: [
                (position + 1, char)
                for position, char in enumerate(text)
                if owners[position] == i
            ]
            for i in range(count)
        }

    async def reply(
        messages: list[Any], tools: list[Any], choice: Any, config: Any
    ) -> ModelOutput:
        """Emit a deterministic fixture action and assert previous tools succeeded."""
        run_id = next(
            m.metadata["team_run"]
            for m in messages
            if (m.metadata or {}).get("team_run")
        )
        if run_id not in runs:
            signals = [asyncio.Event() for _ in range(256)]
            signals[0].set()
            runs[run_id] = (asyncio.Barrier(count), signals)
        barrier, confirmed = runs[run_id]
        source = "\n".join(m.text for m in messages if m.role == "user")
        match = re.search(r"(?:fixed board ID is|fixed ID is) (agent_(\d+))", source)
        if match is None:
            raise AssertionError("No evaluator identity in fixture input")
        index = int(match[2])
        completed = [m for m in messages if isinstance(m, ChatMessageTool)]
        errors = [m.error for m in completed if m.error]
        if errors:
            raise AssertionError(str(errors))
        step = len(completed)
        function, arguments = "", {}
        if collaborative and step < 4:
            function = "message_board"
            if step == 0:
                arguments = {"action": "register", "name": f"Peer {index}"}
            elif step == 1:
                arguments = {"action": "send", "message": "Fixture peer ready."}
            elif step == 2:
                arguments = (
                    {
                        "action": "send",
                        "recipient": f"agent_{(index + 1) % count}",
                        "message": "Fixture coordination.",
                    }
                    if count > 1
                    else {"action": "agents"}
                )
            else:
                arguments = {"action": "read"}
        else:
            action_step = step - (4 if collaborative else 0)
            if action_step == 0:
                async with asyncio.timeout(20):
                    await barrier.wait()
                if kind == "hle":
                    function, arguments = "read_file", {"path": "questions.json"}
                elif not collaborative:
                    function, arguments = "oracle_progress", {}
                else:
                    function, arguments = "message_board", {"action": "agents"}
            else:
                offset = action_step - 1
                if offset > 0 and offset <= len(owned[index]) and kind != "hle":
                    confirmed[owned[index][offset - 1][0]].set()
                if offset < len(owned[index]):
                    position, value = owned[index][offset]
                    if kind != "hle":
                        async with asyncio.timeout(20):
                            await confirmed[position - 1].wait()
                    function = {
                        "hle": "submit_answer",
                        "counting": "submit_number",
                        "spelling": "submit_letter",
                    }[kind]
                    arguments = (
                        {"question_number": position, "answer": value}
                        if kind == "hle"
                        else {"number": value}
                        if kind == "counting"
                        else {"character": value}
                    )
        output = (
            ModelOutput.for_tool_call("mockllm/model", function, arguments)
            if function
            else ModelOutput.from_content("mockllm/model", "Fixture peer finished.")
        )
        output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
        return output

    return get_model("mockllm/model", custom_outputs=reply)


def main() -> None:
    """Run three authored mock fixtures and retain portable replays while deleting owned mock logs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", type=int, default=2)
    parser.add_argument("--no-sandbox", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    artifacts = root / "run-artifacts/mock-suite"
    artifacts.mkdir(parents=True, exist_ok=True)
    records = artifacts / "fixture-questions.json"
    records.write_text(
        json.dumps(
            [
                {
                    "id": "fixture-france",
                    "question": "What is the capital of France?",
                    "answer": "Paris",
                },
                {"id": "fixture-sum", "question": "What is 2+2?", "answer": "4"},
            ]
        )
    )
    common = dict(
        agents=args.agents,
        token_limit_per_agent=10000,
        team_time_limit=30,
        artifact_dir=str(artifacts),
        sandbox_enabled=not args.no_sandbox,
    )
    tasks = [
        hle_collaboration(records_file=str(records), answer_judge="exact", **common),
        counting(**common),
        spelling(**common),
    ]
    reports = []
    for task in tasks:
        [log] = inspect_eval(
            task, model=fixture_model(task), log_dir=str(root / "logs"), display="none"
        )
        if log.status != "success" or not log.samples or log.samples[0].error:
            raise RuntimeError(
                f"{task.name} mock pipeline failed; inspect its owned log"
            )
        sample = log.samples[0]
        directory = Path(sample.store["TeamHistory:artifact_dir"])
        log_path = Path(log.location)
        render(log_path, directory / "board.jsonl", artifacts / (task.name + ".html"))
        reports.append(
            {
                "task": task.name,
                "peers": len(sample.store["TeamHistory:peers"]),
                "metrics": (sample.scores or {})["team_score"].value,
                "sandbox_hostname": sample.store.get("shared_sandbox_hostname"),
                "mock_log_removed_after_render": True,
            }
        )
        log_path.unlink()
    (artifacts / "verification.json").write_text(json.dumps(reports, indent=2))
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
