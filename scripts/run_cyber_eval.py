"""Submit one prepared Cyber Eval through the protected Hawk work session."""

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import keyring
import yaml
from hawk.cli.util.auth import ensure_logged_in
from hawk.client import HawkClient
from hawk.core.types import EvalSetConfig

DEFAULT_CONFIG = Path("run-artifacts/cyber-eval/cyber-eval.eval-set.yaml")
VIEWER_ROOT = "https://viewer.hawk.hawk.generalitylabs.ai/eval-set/"


def parse_args() -> argparse.Namespace:
    """Parse the optional immutable plan path and non-submitting validation mode."""
    parser = argparse.ArgumentParser(description="Launch the prepared Cyber Eval")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("CYBER_EVAL_CONFIG", DEFAULT_CONFIG)),
        help="prepared eval-set YAML path",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the plan and protected session without submitting",
    )
    parser.add_argument(
        "--resume-pending",
        action="store_true",
        help="resume one reconciled pending receipt after confirming no job exists",
    )
    return parser.parse_args()


async def owned_job_exists(token: str, label: str) -> bool:
    """Return whether the protected account already owns this exact launch label."""
    async with HawkClient(
        api_url="https://api.hawk.hawk.generalitylabs.ai", token=token, timeout=45
    ) as api:
        jobs = await api.get_jobs(mine=True, limit=500)
    return any(str(job.get("job_id", "")).startswith(label) for job in jobs)


def work_provider_key() -> str:
    """Load the designated work key without writing it to files or output."""
    work_key_path = Path.home() / ".config" / "openrouter" / "work-api-key"
    work_key = work_key_path.read_text().strip()
    if not work_key:
        raise RuntimeError("The configured OpenRouter work key is empty")
    return work_key


async def submit_with_hawk_api(
    token: str, config: dict[str, object], work_key: str
) -> str:
    """Submit one validated config through Hawk's authenticated client API."""
    async with HawkClient(
        api_url="https://api.hawk.hawk.generalitylabs.ai", token=token, timeout=45
    ) as api:
        eval_set_id = await api.create_eval_set(
            eval_set_config=config,
            secrets={"OPENROUTER_API_KEY": work_key},
        )
    if not isinstance(eval_set_id, str) or not eval_set_id:
        raise RuntimeError("Hawk did not return a Cyber Eval identifier")
    return eval_set_id


async def main() -> None:
    """Validate one plan and submit it once without exposing task contents in output."""
    args = parse_args()
    if type(keyring.get_keyring()).__name__ != "SessionKeyring":
        raise RuntimeError("Cyber Eval requires the protected memory-only Hawk session")
    config_path = args.config.resolve()
    config = yaml.safe_load(config_path.read_text())
    EvalSetConfig.model_validate(config)
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    receipt_path = config_path.with_suffix(".receipt.json")
    token = await ensure_logged_in()

    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("state") != "submission_pending":
            print(
                json.dumps(
                    {
                        "label": "Cyber Eval",
                        "state": receipt.get("state", "unknown"),
                        "viewer_url": receipt.get("viewer_url"),
                        "new_submission": False,
                    }
                )
            )
            return
        if not args.resume_pending:
            raise RuntimeError(
                "Reconcile the pending Cyber Eval receipt before retrying"
            )
        if await owned_job_exists(token, str(config["name"])):
            raise RuntimeError(
                "The pending Cyber Eval receipt already has an owned job"
            )
        receipt_path.write_text(
            json.dumps(
                {
                    **receipt,
                    "state": "submission_retrying",
                    "reconciled_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
            )
            + "\n"
        )

    if await owned_job_exists(token, str(config["name"])):
        raise RuntimeError("Reconcile the existing Cyber Eval job before submitting")
    if args.dry_run:
        print(
            json.dumps(
                {
                    "label": "Cyber Eval",
                    "state": "validated",
                    "config_sha256": config_hash,
                    "new_submission": False,
                }
            )
        )
        return

    initial = {
        "state": "submission_pending",
        "config_sha256": config_hash,
        "intent_at": datetime.now(UTC).isoformat(),
    }
    receipt_path.write_text(json.dumps(initial, indent=2) + "\n")
    eval_set_id = await submit_with_hawk_api(token, config, work_provider_key())
    receipt = {
        **initial,
        "state": "submitted",
        "eval_set_id": eval_set_id,
        "submitted_at": datetime.now(UTC).isoformat(),
        "viewer_url": VIEWER_ROOT + eval_set_id,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(
        json.dumps(
            {
                "label": "Cyber Eval",
                "state": "submitted",
                "viewer_url": receipt["viewer_url"],
                "new_submission": True,
            }
        )
    )


asyncio.run(main())
