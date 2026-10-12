"""Own sample-scoped external GPU resources independently of Inspect sandbox patching."""

from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio
from inspect_ai.solver import TaskState
from inspect_ai.util import SandboxEnvironment, sandbox, store

RESOURCES: dict[str, Any] = {}
RESOURCE_KEY = "inferencebench_external_resource"
# the leased-GPU mode adds a workspace pod; RESOURCE_KEY then names the grading pod
WORKSPACE_KEY = "inferencebench_workspace_resource"


def controller_gpu(key: str = RESOURCE_KEY) -> Any | None:
    """Resolve only the external GPU belonging to the current Inspect sample."""
    identity = store().get(key)
    if identity is None:
        return None
    if identity not in RESOURCES:
        raise RuntimeError("The sample's external GPU is unavailable")
    return RESOURCES[identity]


def workspace() -> SandboxEnvironment:
    """Return the peers' development pod, else the single external GPU or native sandbox."""
    for key in (WORKSPACE_KEY, RESOURCE_KEY):
        external = controller_gpu(key)
        if external is not None:
            return external
    return sandbox()


async def allocate(
    state: TaskState, key: str = RESOURCE_KEY, config: str | None = None
) -> None:
    """Allocate one owned RunPod pod after the caller has verified its model roles."""
    from collaboration_index.inferencebench.backend.runpod_sandbox import RunPodSandbox

    if state.store.get(key) is not None:
        raise RuntimeError("External GPU checkpoint continuation is unsupported")
    identity = uuid4().hex
    receipts = Path(state.metadata["artifacts_root"]) / "runpod"
    receipts.mkdir(parents=True, exist_ok=True)
    # The artifact hook can retain provider receipts even if bootstrap fails.
    if state.store.get("artifacts") is None:
        state.store.set("artifacts", str(receipts))
    # The provider cleans up an accepted allocation if initialization fails.
    environments = await RunPodSandbox.sample_init(
        "collaboration_index/inferencebench",
        config or state.metadata["gpu_config"],
        state.metadata,
    )
    RESOURCES[identity] = environments["default"]
    state.store.set(key, identity)
    state.store.set(
        "external_gpu_lifecycle" if key == RESOURCE_KEY else "workspace_gpu_lifecycle",
        {"resource_id": environments["default"].resource_id, "status": "allocated"},
    )


async def terminate(state: TaskState, key: str, label: str) -> None:
    """Delete one owned pod idempotently and record its teardown."""
    identity = state.store.get(key)
    if identity in RESOURCES:
        await RESOURCES[identity].terminate()
        state.store.set(
            label,
            {"resource_id": RESOURCES[identity].resource_id, "status": "terminated"},
        )
        del RESOURCES[identity]
        state.store.set(key, None)


async def cleanup(state: TaskState) -> None:
    """Retain submission evidence before shielded, idempotent teardown of owned external pods."""
    from collaboration_index.inferencebench.backend.environment import (
        retain_failed_submission,
    )

    identity = state.store.get(RESOURCE_KEY)
    workspace_identity = state.store.get(WORKSPACE_KEY)
    if (
        state.metadata["gpu_management"] == "controller"
        and identity is None
        and workspace_identity is None
    ):
        return
    with anyio.CancelScope(shield=True):
        try:
            await retain_failed_submission(state)
        finally:
            try:
                await terminate(state, WORKSPACE_KEY, "workspace_gpu_lifecycle")
            finally:
                await terminate(state, RESOURCE_KEY, "external_gpu_lifecycle")
