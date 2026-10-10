"""Dedicated real-provider Ruff QA with authored live transcript-spool pressure."""

import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import yaml
from inspect_ai import Task, task
from inspect_ai.event import InfoEvent

from collaboration_index.checkpoint_qa import checkpoint_ruff_qa
from collaboration_index.checkpoints import TeamCheckpoints

ORIGINAL_INIT = getattr(
    TeamCheckpoints, "_backup_qa_original_init", TeamCheckpoints.__init__
)


def pressure_owner(self: Any, *args: Any, **kwargs: Any) -> None:
    """Wrap this dedicated QA owner's host backup with bounded authored spool writes."""
    ORIGINAL_INIT(self, *args, **kwargs)
    native = self.cp._backup_host

    async def pressure(checkpoint_id: int) -> Any:
        """Keep SQLite journals changing while the frozen native export is backed up."""
        stop = threading.Event()
        counts = {"writes": 0, "errors": 0}

        def writer() -> None:
            """Add count-only trusted fixture events without inspecting participant content."""
            try:
                for index in range(10000):
                    if stop.is_set():
                        break
                    self.cp._transcript_store.merge_event(
                        InfoEvent(
                            uuid=uuid.uuid4().hex,
                            source="checkpoint_backup_qa",
                            data={"fixture_index": index},
                        ),
                        lambda _: None,
                    )
                    counts["writes"] += 1
                    time.sleep(0.001)
            except Exception:
                counts["errors"] += 1

        thread = threading.Thread(target=writer, name="checkpoint-fixture-spool")
        thread.start()
        try:
            result = await native(checkpoint_id)
        finally:
            stop.set()
            thread.join(timeout=10)
        assert not thread.is_alive() and counts["writes"] > 0 and counts["errors"] == 0
        prior = self.task.store.get("checkpoint_backup_qa_writes", 0)
        self.task.store.set("checkpoint_backup_qa_writes", prior + counts["writes"])
        self.task.store.set("checkpoint_backup_qa_errors", counts["errors"])
        return result

    self.cp._backup_host = pressure


@task
def checkpoint_backup_qa(
    artifact_dir: str = "/tmp/checkpoint-backup-qa",
    token_limit_per_agent: int = 20000,
    image_pins: dict[str, str] | None = None,
) -> Task:
    """Run actual-provider native Ruff interruption and restore with live-spool pressure."""
    setattr(TeamCheckpoints, "_backup_qa_original_init", ORIGINAL_INIT)
    setattr(TeamCheckpoints, "__init__", pressure_owner)
    result = checkpoint_ruff_qa(
        mode="provider",
        artifact_dir=artifact_dir,
        token_limit_per_agent=token_limit_per_agent,
        agents=2,
    )
    if image_pins is not None:
        sample = result.dataset[0]
        assert sample.sandbox is not None
        compose = yaml.safe_load(Path(sample.sandbox.config).read_text())
        effective = {}
        for name, service in compose["services"].items():
            image = service["image"].replace(
                "${MC_IMAGE_NAME:-mclocal}", os.environ.get("MC_IMAGE_NAME", "mclocal")
            )
            reference = image_pins.get(image, "")
            if not re.fullmatch(
                re.escape(image.rsplit(":", 1)[0]) + r"@sha256:[0-9a-f]{64}", reference
            ):
                raise ValueError("QA image pin is missing or mismatches its repository")
            service["image"] = reference
            service.pop("build", None)
            effective[name] = reference
        if set(image_pins.values()) != set(effective.values()):
            raise ValueError("QA image pins contain an unexpected role")
        digest = hashlib.sha256(
            json.dumps(image_pins, sort_keys=True).encode()
        ).hexdigest()
        path = Path(sample.sandbox.config).with_name(f"backup-qa-{digest}-compose.yaml")
        path.write_text(yaml.safe_dump(compose))
        sample.sandbox = sample.sandbox.model_copy(update={"config": str(path)})
        for metadata in [result.metadata, sample.metadata]:
            assert metadata is not None
            metadata.update(image_pins=image_pins, effective_images=effective)
    for metadata in [result.metadata, result.dataset[0].metadata]:
        assert metadata is not None
        metadata["checkpoint_backup_qa"] = True
        metadata["checkpoint_restic_version"] = "0.19.1"
    return result
