"""Validate host staging and real pinned backup completeness without participant data."""

import json

import pytest

from collaboration_index.checkpoint_backup import (
    OPTIONAL,
    REQUIRED,
    freeze_context,
    install_host_backup,
)


def test_frozen_exports_exclude_live_spool(tmp_path):
    """Freeze all restore files while omitting the independently mutable spool."""
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for name in REQUIRED + OPTIONAL:
        (source / name).write_text(json.dumps({"fixture": 7}))
    (source / "checkpoint_transcript.sqlite-journal").write_bytes(b"fixture")
    hashes = freeze_context(source, target)
    assert set(hashes) == set(REQUIRED + OPTIONAL)
    assert all(
        (source / name).read_bytes() == (target / name).read_bytes() for name in hashes
    )


@pytest.mark.parametrize("failure", ["missing", "symlink", "invalid_json", "directory"])
def test_required_export_failure_is_fatal(tmp_path, failure):
    """Refuse missing, unreadable-shaped or invalid required restore files."""
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for name in REQUIRED:
        (source / name).write_text("{}")
    path = source / REQUIRED[0]
    path.unlink()
    if failure == "symlink":
        path.symlink_to(source / REQUIRED[1])
    elif failure == "invalid_json":
        path.write_text("broken")
    elif failure == "directory":
        path.mkdir()
    with pytest.raises((RuntimeError, ValueError)):
        freeze_context(source, target)


def test_adapter_rejects_unknown_native_owner():
    """Fail closed rather than patch an unverified dependency implementation."""
    with pytest.raises(RuntimeError):
        install_host_backup(object())


@pytest.mark.asyncio
async def test_native_adapter_complete_restore(tmp_path):
    """Restore exact full exported state through native repository verification."""
    from inspect_ai.util._checkpoint._layout.host_context import read
    from inspect_ai.util._checkpoint.checkpointer_impl import _EnteredCheckpointer
    from inspect_ai.util._restic.ops import init_repo, restore_repo
    from inspect_ai.util._restic.resolver import _current_platform, cache_path

    source = tmp_path / "context"
    source.mkdir()
    values = {
        "events.json": [],
        "events_data.json": {"messages": [], "calls": []},
        "attachments.json": {"fixture_hash": "authored fixture"},
        "store.json": {"trusted_fixture": 37},
        "sample_runtime.json": {"fixture_usage": 123},
        "agent_state.json": {"fixture_peer": {"tokens": 123}},
        "assistant_internal.json": {"fixture_wire": "authored fixture"},
    }
    for name, value in values.items():
        (source / name).write_text(json.dumps(value))
    (source / "checkpoint_transcript.sqlite-journal").write_bytes(b"unused fixture")
    native = cache_path(_current_platform())
    if not native.exists():
        pytest.skip("Native restic must be resolved for this bounded integration check")
    cp = object.__new__(_EnteredCheckpointer)
    cp._context_dir = str(source)
    cp._host_repo = str(tmp_path / "repo")
    cp._restic_password = "harmless-fixture-only"
    await init_repo(native, cp._host_repo, cp._restic_password)
    install_host_backup(cp)
    details = await cp._backup_host(1)
    assert details.snapshot_id
    restored = tmp_path / "restored"
    await restore_repo(
        native,
        cp._host_repo,
        cp._restic_password,
        str(restored),
        max_files=20,
        max_bytes=10000,
    )
    assert set(p.name for p in restored.iterdir()) == set(values)
    assert all(
        (source / name).read_bytes() == (restored / name).read_bytes()
        for name in values
    )
    state = read(str(restored))
    assert state.store == values["store.json"]
    assert state.agent_state == values["agent_state.json"]
    assert state.assistant_internal == values["assistant_internal.json"]
    assert state.sample_runtime == values["sample_runtime.json"]


@pytest.mark.asyncio
async def test_incomplete_backup_cannot_return_snapshot(tmp_path, monkeypatch):
    """Propagate restic exit three instead of exposing an incomplete snapshot identity."""
    from subprocess import CalledProcessError

    from inspect_ai.util._checkpoint.checkpointer_impl import _EnteredCheckpointer

    import collaboration_index.checkpoint_backup as backup

    source = tmp_path / "context"
    source.mkdir()
    for name in REQUIRED:
        (source / name).write_text("{}")
    cp = object.__new__(_EnteredCheckpointer)
    cp._context_dir = str(source)
    cp._host_repo = str(tmp_path / "repo")
    cp._restic_password = "harmless-fixture-only"
    install_host_backup(cp)
    monkeypatch.setattr(backup, "resolve_binary", lambda: tmp_path / "fixture-restic")

    async def incomplete(*args, **kwargs):
        """Return the actual exception contract for an incomplete restic backup."""
        assert kwargs["check"] is True
        raise CalledProcessError(3, ["harmless-fixture"])

    monkeypatch.setattr(backup.anyio, "run_process", incomplete)
    with pytest.raises(CalledProcessError) as error:
        await cp._backup_host(1)
    assert error.value.returncode == 3


def test_optional_dangling_symlink_is_fatal(tmp_path):
    """Treat a dangling optional export as corruption rather than legitimate absence."""
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for name in REQUIRED:
        (source / name).write_text("{}")
    (source / OPTIONAL[0]).symlink_to(source / "absent")
    with pytest.raises(RuntimeError):
        freeze_context(source, target)
