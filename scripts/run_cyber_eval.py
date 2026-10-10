"""Submit one prepared Cyber Eval through the protected Hawk work session."""

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import keyring
import yaml
from hawk.cli.cli import cli
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
    return parser.parse_args()


async def owned_job_exists(token: str, label: str) -> bool:
    """Return whether the protected account already owns this exact launch label."""
    async with HawkClient(
        api_url="https://api.hawk.hawk.generalitylabs.ai", token=token, timeout=45
    ) as api:
        jobs = await api.get_jobs(mine=True, limit=500)
    return any(str(job.get("job_id", "")).startswith(label) for job in jobs)


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
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        eval_set_id = cli.main(
            args=["eval-set", "run", str(config_path), "--skip-confirm"],
            standalone_mode=False,
        )
    if not isinstance(eval_set_id, str) or not eval_set_id:
        raise RuntimeError("Hawk did not return a Cyber Eval identifier")
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
