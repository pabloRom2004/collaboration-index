"""Own sample-scoped external GPU resources independently of Inspect sandbox patching."""

from typing import Any
from uuid import uuid4

import anyio
from inspect_ai.solver import TaskState
from inspect_ai.util import SandboxEnvironment, sandbox, store

RESOURCES: dict[str, Any] = {}
RESOURCE_KEY = "inferencebench_external_resource"


def controller_gpu() -> Any | None:
    """Resolve only the external GPU belonging to the current Inspect sample."""
    identity = store().get(RESOURCE_KEY)
    if identity is None:
        return None
    if identity not in RESOURCES:
        raise RuntimeError("The sample's external GPU is unavailable")
    return RESOURCES[identity]


def workspace() -> SandboxEnvironment:
    """Return the current sample's external GPU or its native Inspect sandbox."""
    external = controller_gpu()
    return external if external is not None else sandbox()


async def allocate(state: TaskState) -> None:
    """Allocate one owned RunPod pod after the caller has verified its model roles."""
    from collaboration_index.inferencebench.backend.runpod_sandbox import RunPodSandbox

    if state.store.get(RESOURCE_KEY) is not None:
        raise RuntimeError("External GPU checkpoint continuation is unsupported")
    identity = uuid4().hex
    # The provider cleans up an accepted allocation if initialization fails.
    environments = await RunPodSandbox.sample_init(
        "collaboration_index/inferencebench",
        state.metadata["gpu_config"],
        state.metadata,
    )
    RESOURCES[identity] = environments["default"]
    state.store.set(RESOURCE_KEY, identity)
    state.store.set(
        "external_gpu_lifecycle",
        {
            "resource_id": environments["default"].resource_id,
            "status": "allocated",
        },
    )


async def cleanup(state: TaskState) -> None:
    """Retain submission evidence before shielded, idempotent teardown of an owned external pod."""
    from collaboration_index.inferencebench.backend.environment import (
        retain_failed_submission,
    )

    identity = state.store.get(RESOURCE_KEY)
    if state.metadata["gpu_management"] == "controller" and identity is None:
        return
    with anyio.CancelScope(shield=True):
        try:
            await retain_failed_submission(state)
        finally:
            if identity in RESOURCES:
                await RESOURCES[identity].terminate()
                state.store.set(
                    "external_gpu_lifecycle",
                    {
                        "resource_id": RESOURCES[identity].resource_id,
                        "status": "terminated",
                    },
                )
                del RESOURCES[identity]
                state.store.set(RESOURCE_KEY, None)
