"""Validate and serialize submissions without exposing the hidden output."""

import asyncio
import json
import math
from typing import Any

from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import sandbox

from collaboration_index.state import Submission, TeamHistory, now


def distance(sequence: list[Any] | str, expected: list[Any] | str) -> int:
    """Compute Levenshtein distance between two ordered outputs."""
    previous = list(range(len(expected) + 1))
    for i, value in enumerate(sequence, 1):
        current = [i]
        for j, wanted in enumerate(expected, 1):
            current.append(
                min(
                    previous[j] + 1,
                    current[-1] + 1,
                    previous[j - 1] + (value != wanted),
                )
            )
        previous = current
    return previous[-1]


def similarity(sequence: list[Any] | str, expected: list[Any] | str) -> float:
    """Normalize edit distance by nonempty target length and floor at zero."""
    if not expected:
        raise ValueError("A scoring target cannot be empty")
    return max(0.0, 1 - distance(sequence, expected) / len(expected))


def final_colours(submissions: list[Submission]) -> dict[str, str]:
    """Return each node's latest colour, since a later set_colour replaces an earlier one."""
    return {s.actor: str(s.value) for s in submissions}


def clashing_edges(colours: dict[str, str], edges: list[list[str]]) -> list[list[str]]:
    """List network edges with an uncoloured end or two ends of the same colour."""
    return [
        [a, b]
        for a, b in edges
        if a not in colours or b not in colours or colours[a] == colours[b]
    ]


class TeamGame:
    """Own one sample's lock and apply the benchmark's submission contract."""

    def __init__(
        self, history: TeamHistory, data: dict[str, Any], actors: list[str]
    ) -> None:
        """Bind trusted state, fixed actor IDs and private assignment data."""
        self.history = history
        self.data = data
        self.actors = actors
        self.lock = asyncio.Lock()

    async def submit(
        self, actor: str, value: str | int, question_number: int | None = None
    ) -> dict[str, Any]:
        """Commit one valid action atomically without revealing other submissions or grades."""
        async with self.lock:
            history = self.history
            if actor not in self.actors:
                raise RuntimeError("Unknown trusted participant")
            if history.end_reason:
                raise ToolError("The team attempt has ended")
            kind = history.benchmark
            if kind == "hle":
                if type(question_number) is not int or not 1 <= question_number <= len(
                    self.data["questions"]
                ):
                    history.rejected_submissions += 1
                    raise ToolError("Unknown question number")
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value) > self.data["answer_characters"]
                ):
                    history.rejected_submissions += 1
                    raise ToolError(
                        "Supply a nonempty answer within the character limit"
                    )
                if history.condition == "oracle_allocation" and (
                    question_number - 1
                ) % len(self.actors) != self.actors.index(actor):
                    history.rejected_submissions += 1
                    raise ToolError("This question is assigned to another participant")
                if any(
                    s.question_number == question_number for s in history.submissions
                ):
                    history.duplicate_submissions += 1
                    raise ToolError("An answer is already recorded for this question")
            elif kind == "counting":
                if type(value) is not int or not -(2**63) <= value < 2**63:
                    history.rejected_submissions += 1
                    raise ToolError("Supply a signed 64-bit integer")
                quota = self.data["quota"]
                if sum(s.actor == actor for s in history.submissions) >= quota:
                    history.rejected_submissions += 1
                    raise ToolError("Your submission budget is exhausted")
            elif kind == "spelling":
                if not isinstance(value, str):
                    raise ToolError("Supply one character")
                value = {"space": " ", "comma": ",", "period": ".", "return": "\n"}.get(
                    value, value
                )
                if len(value) != 1 or value not in self.data["hands"][actor]:
                    history.rejected_submissions += 1
                    raise ToolError("The character is not in your private hand")
                if (
                    self.data["max_characters"] is not None
                    and len(history.submissions) >= self.data["max_characters"]
                ):
                    history.end_reason = "output_limit"
                    raise ToolError("The shared output reached its character limit")
            elif kind == "colouring":
                if (
                    not isinstance(value, str)
                    or value.strip().lower() not in self.data["colours"]
                ):
                    history.rejected_submissions += 1
                    raise ToolError(
                        "The colour must be one of: "
                        + ", ".join(self.data["colours"])
                        + "; nothing changed"
                    )
                value = value.strip().lower()
            elif kind != "mirrorcode":
                raise RuntimeError("Unknown initialized benchmark")
            entries = history.submissions
            entries.append(
                Submission(
                    number=len(entries) + 1,
                    actor=actor,
                    value=value,
                    time=now(),
                    question_number=question_number,
                )
            )
            history.submissions = entries
            if kind == "hle" and len(history.submissions) == len(
                self.data["questions"]
            ):
                history.end_reason = "all_answers_submitted"
            elif kind == "counting" and len(history.submissions) == self.data["target"]:
                history.end_reason = "sequence_full"
            elif kind == "spelling" and value == "\n":
                history.end_reason = "line_returned"
            elif kind == "mirrorcode":
                history.end_reason = "codebase_submitted"
            elif kind == "colouring":
                colours = final_colours(history.submissions)
                if len(colours) == len(self.actors) and not clashing_edges(
                    colours, self.data["edges"]
                ):
                    history.end_reason = "properly_coloured"
            if history.end_reason is not None:
                history.objective_completed = now()
            return {
                "accepted": True,
                "recorded": value,
                "question_number": question_number,
                "team_ended": history.end_reason is not None,
            }

    def assignments(self, actor: str) -> dict[str, Any]:
        """Provide the explicit oracle allocation without exposing HLE answer keys."""
        index, count = self.actors.index(actor), len(self.actors)
        if self.history.benchmark == "hle":
            return {
                "question_numbers": list(
                    range(index + 1, len(self.data["questions"]) + 1, count)
                )
            }
        if self.history.benchmark == "counting":
            quota = math.ceil(self.data["target"] / count)
            return {
                "numbers": list(
                    range(
                        index * quota + 1,
                        min((index + 1) * quota, self.data["target"]) + 1,
                    )
                )
            }
        if self.history.benchmark == "colouring":
            return {"colour": self.data["planted"][actor]}
        target = self.data["dealt_sentence"]
        owners = [
            next(a for a in self.actors if char in self.data["hands"][a])
            for char in target + "\n"
        ]
        return {
            "sentence": target,
            "positions": [i + 1 for i, owner in enumerate(owners) if owner == actor],
        }


@tool
def submit_answer(game: TeamGame, actor: str) -> Tool:
    """Bind an answer submission tool to an evaluator-fixed participant."""

    async def execute(question_number: int, answer: str) -> str:
        """Submit your final answer without receiving correctness feedback.

        Args:
            question_number: The question number from questions.json.
            answer: Your final answer; the first accepted answer is irreversible.
        """
        return json.dumps(await game.submit(actor, answer, question_number))

    return execute


@tool
def submit_number(game: TeamGame, actor: str) -> Tool:
    """Bind irreversible counting submissions to one trusted participant."""

    async def execute(number: int) -> str:
        """Append one integer to the hidden shared list in arrival order.

        Args:
            number: The integer to append; incorrect integers still occupy a slot.
        """
        return json.dumps(await game.submit(actor, number))

    return execute


@tool
def submit_letter(game: TeamGame, actor: str) -> Tool:
    """Bind the spelling tool to one participant's private reusable hand."""

    async def execute(character: str) -> str:
        """Append a held character; a held return token ends the whole attempt.

        Args:
            character: One held character, or space, comma, period, return.
        """
        return json.dumps(await game.submit(actor, character))

    return execute


@tool
def set_colour(game: TeamGame, actor: str) -> Tool:
    """Bind the colour tool to the network node owned by one trusted participant."""

    async def execute(colour: str) -> str:
        """Set or change your colour. You can call this at any time.

        Args:
            colour: One of the allowed colour names.
        """
        return json.dumps(await game.submit(actor, colour))

    return execute


@tool(name="submit")
def submit_codebase(game: TeamGame, actor: str) -> Tool:
    """Let any peer end the team attempt and send the shared codebase to scoring."""

    async def execute() -> str:
        """Submit the shared codebase as the team's final answer. This ends the task for every agent."""
        await game.submit(actor, "codebase")
        return "Submission successful. No feedback is provided."

    return execute


@tool
def oracle_progress(game: TeamGame) -> Tool:
    """Expose prefix length only in the explicitly privileged oracle condition."""

    async def execute() -> str:
        """Read the trusted number of accepted entries to sequence oracle-assigned work."""
        return json.dumps(
            {
                "entries": len(game.history.submissions),
                "ended": game.history.end_reason is not None,
            }
        )

    return execute


@tool
def read_file(content: str, sandbox_enabled: bool) -> Tool:
    """Expose only the immutable public question file, never arbitrary host paths."""

    async def execute(path: str, start: int = 1, count: int | None = None) -> str:
        """Read numbered records from the shared questions.json file.

        Args:
            path: Exactly questions.json.
            start: First question number to read, beginning at 1.
            count: Number of questions; omit to read the entire public file.
        """
        if path != "questions.json":
            raise ToolError("Only questions.json is readable")
        visible = content
        if sandbox_enabled:
            # Docker cp cannot read tmpfs; use an evaluator-fixed path inside the container.
            result = await sandbox().exec(["cat", "/workspace/questions.json"])
            if not result.success:
                raise RuntimeError("Cannot read the shared public question file")
            visible = result.stdout
        if visible != content:
            raise RuntimeError("The immutable public question file changed")
        questions = json.loads(visible)
        if start < 1 or start > len(questions) or (count is not None and count < 1):
            raise ToolError("Invalid question range")
        stop = len(questions) if count is None else start - 1 + count
        return json.dumps(questions[start - 1 : stop], ensure_ascii=False)

    return execute
