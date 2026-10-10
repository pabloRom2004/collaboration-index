"""Freeze native host checkpoint exports before a pinned restic backup."""

import bz2
import hashlib
import importlib.metadata
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import anyio

VERSION = "0.19.1"
ARCHIVE_HASHES = {
    "darwin_amd64": "c38d579622cf602f665234c5a8c315030b6cf70656028fe6dc29a786b60e5f35",
    "darwin_arm64": "7be0a144ccc377880f294204aa271d76e4b79554b42a751151d425ce6ebac143",
    "linux_amd64": "f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c",
    "linux_arm64": "a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465",
}
REQUIRED = (
    "events.json",
    "events_data.json",
    "attachments.json",
    "store.json",
    "sample_runtime.json",
)
OPTIONAL = ("agent_state.json", "assistant_internal.json")


def resolve_binary() -> Path:
    """Acquire an official archive with its committed SHA and isolate its binary."""
    from inspect_ai._util.appdirs import inspect_cache_dir
    from inspect_ai._util.download import download
    from inspect_ai.util._restic.resolver import _current_platform

    platform = _current_platform()
    if platform not in ARCHIVE_HASHES:
        raise RuntimeError("Checkpoint backup platform is unsupported")
    directory = inspect_cache_dir("collaboration-restic")
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"restic_{VERSION}_{platform}.bz2"
    expected = ARCHIVE_HASHES[platform]
    if (
        not archive.exists()
        or hashlib.sha256(archive.read_bytes()).hexdigest() != expected
    ):
        download(
            f"https://github.com/restic/restic/releases/download/v{VERSION}/{archive.name}",
            expected,
            archive,
            timeout=30,
        )
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Checkpoint restic archive integrity failure")
    binary = archive.with_suffix("")
    contents = bz2.decompress(archive.read_bytes())
    if not binary.exists() or binary.read_bytes() != contents:
        fd, name = tempfile.mkstemp(dir=directory, prefix="binary-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(contents)
            os.chmod(name, 0o700)
            os.replace(name, binary)
        finally:
            Path(name).unlink(missing_ok=True)
    return binary


def freeze_context(source: Path, destination: Path) -> dict[str, str]:
    """Copy only validated regular restore exports and return their exact hashes."""
    hashes = {}
    for name in REQUIRED + OPTIONAL:
        path = source / name
        if not path.exists() and not path.is_symlink() and name in OPTIONAL:
            continue
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Required checkpoint export is missing or nonregular")
        contents = path.read_bytes()
        json.loads(contents)
        (destination / name).write_bytes(contents)
        hashes[name] = hashlib.sha256(contents).hexdigest()
    return hashes


def install_host_backup(cp: Any) -> None:
    """Adapt only this native owner while preserving native commit and restore code."""
    from inspect_ai.util._checkpoint._layout.schemas import SnapshotDetails
    from inspect_ai.util._checkpoint.checkpointer_impl import _EnteredCheckpointer

    if importlib.metadata.version("inspect_ai") != "0.3.277" or not isinstance(
        cp, _EnteredCheckpointer
    ):
        raise RuntimeError(
            "Checkpoint backup adapter requires verified Inspect 0.3.277"
        )
    if getattr(cp, "_collaboration_backup_installed", False):
        return

    async def backup(checkpoint_id: int) -> SnapshotDetails:
        """Back up a frozen complete export, accepting only a successful native summary."""
        from inspect_ai.util._checkpoint.checkpointer_impl import checkpoint_tag
        from inspect_ai.util._restic.ops import restic_env
        from inspect_ai.util._restic.summary import ResticBackupSummary

        binary = await anyio.to_thread.run_sync(resolve_binary)
        with tempfile.TemporaryDirectory(prefix="checkpoint-export-") as directory:
            frozen = Path(directory) / "context"
            frozen.mkdir()
            hashes = freeze_context(Path(cp._context_dir), frozen)
            process = await anyio.run_process(
                [
                    str(binary),
                    "--no-cache",
                    "-r",
                    cp._host_repo,
                    "backup",
                    str(frozen),
                    "--compression",
                    "max",
                    "--no-scan",
                    "--tag",
                    checkpoint_tag(checkpoint_id),
                    "--json",
                    "--quiet",
                ],
                env=restic_env(cp._restic_password),
                check=True,
            )
            actual = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in frozen.iterdir()
            }
            if actual != hashes:
                raise RuntimeError("Frozen checkpoint exports changed during backup")
            summary = ResticBackupSummary.from_stdout(process.stdout.decode())
            if (
                summary.dry_run
                or summary.total_files_processed != len(hashes)
                or summary.total_bytes_processed
                != sum(path.stat().st_size for path in frozen.iterdir())
            ):
                raise RuntimeError("Checkpoint backup omitted required export data")
            return SnapshotDetails(
                snapshot_id=summary.snapshot_id,
                size_bytes=summary.data_added_packed,
                duration_ms=int(summary.total_duration * 1000),
            )

    setattr(cp, "_backup_host", backup)
    setattr(cp, "_collaboration_backup_installed", True)
