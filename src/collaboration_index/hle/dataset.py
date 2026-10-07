"""Construct a numbered public exam while retaining reference answers only in the controller."""

import hashlib
import json
from pathlib import Path
from typing import Any


def get_questions(
    records_file: str | None,
    dataset_revision: str,
    verified_revision: str,
    gold_only: bool,
    question_limit: int | None,
) -> list[dict[str, Any]]:
    """Load pinned text-only CAIS records and optionally intersect audited gold IDs."""
    if question_limit is not None and question_limit < 1:
        raise ValueError("question_limit must be positive or null")
    if records_file is not None:
        rows = json.loads(Path(records_file).read_text())
    else:
        try:
            from datasets import load_dataset
        except ImportError:
            raise RuntimeError(
                "Install the hle extra before loading CAIS data"
            ) from None
        rows = load_dataset(
            "cais/hle", split="test", revision=dataset_revision
        ).select_columns(["id", "question", "answer", "answer_type", "image"])
        if gold_only:
            verified = load_dataset(
                "skylenage-ai/HLE-Verified", split="train", revision=verified_revision
            ).select_columns(["id", "Verified_Classes"])
            ids = {
                row["id"]
                for row in verified
                if row["Verified_Classes"] == "Gold subset"
            }
            rows = [row for row in rows if row["id"] in ids]
    questions: list[dict[str, Any]] = []
    seen = set()
    for row in rows:
        if row.get("image"):
            continue
        if not all(
            isinstance(row.get(key), str) and row[key].strip()
            for key in ("id", "question", "answer")
        ):
            raise ValueError(
                "Each question requires nonempty string id, question and answer"
            )
        if row["id"] in seen:
            raise ValueError("Question IDs must be unique")
        seen.add(row["id"])
        questions.append(
            {
                "question_number": len(questions) + 1,
                "id": row["id"],
                "question": row["question"],
                "answer": row["answer"],
                "answer_type": row.get("answer_type", "exactMatch"),
            }
        )
    if question_limit is not None:
        questions = questions[:question_limit]
    if not questions:
        raise ValueError("The selected exam contains no text questions")
    return questions


def exam_id(questions: list[dict[str, Any]]) -> str:
    """Derive a stable team-sample ID from the complete selected exam."""
    return (
        "hle-batch-"
        + hashlib.sha256(json.dumps(questions, sort_keys=True).encode()).hexdigest()[
            :16
        ]
    )
