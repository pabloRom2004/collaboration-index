"""Verify immutable compose image overrides and grading-pipeline provenance."""

from pathlib import Path

import pytest
import yaml

pytest.importorskip("mc")

from collaboration_index.mirrorcode import mirrorcode  # noqa: E402
from collaboration_index.mirrorcode.task import scaled_workspace  # noqa: E402


def compose_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Build harmless upstream-shaped services with distinct role image identities."""
    images = {
        role: f"ghcr.io/fixture/task:{role}_v1"
        for role in ["workspace", "agent", "reference"]
    }
    services = {
        name: {"image": images[role], "build": {"context": "."}}
        for name, role in [
            ("default", "workspace"),
            ("agent-scoring-visible", "agent"),
            ("agent-scoring-hidden", "agent"),
            ("reference-scoring", "reference"),
        ]
    }
    path = tmp_path / "compose.yaml"
    path.write_text(yaml.safe_dump({"services": services}))
    return path, {
        image: "ghcr.io/fixture/task@sha256:" + str(i) * 64
        for i, image in enumerate(images.values(), 1)
    }


def test_pins_cover_all_grading_copies(tmp_path: Path) -> None:
    """Require immutable images and disabled builds across all eight scoring pipelines."""
    path, pins = compose_fixture(tmp_path)
    spec = yaml.safe_load(scaled_workspace(path, 64, 256, 0.25, 8, pins).read_text())
    assert len(spec["services"]) == 25
    assert all(
        service["image"] in pins.values() and "build" not in service
        for service in spec["services"].values()
    )
    assert (
        spec["services"]["agent-scoring-visible-7"]["image"]
        == pins["ghcr.io/fixture/task:agent_v1"]
    )
    assert (
        spec["services"]["reference-scoring-7"]["image"]
        == pins["ghcr.io/fixture/task:reference_v1"]
    )
    assert spec["services"]["default"]["mem_limit"] == "18432m"


@pytest.mark.parametrize(
    "failure",
    ["missing", "extra", "wrong_repository", "tag", "invalid_digest", "wrong_role"],
)
def test_invalid_pin_rosters_fail_closed(tmp_path: Path, failure: str) -> None:
    """Reject incomplete or unexpected roles and mutable or malformed image identities."""
    path, pins = compose_fixture(tmp_path)
    first = next(iter(pins))
    if failure == "missing":
        pins.pop(first)
    elif failure == "extra":
        pins["unexpected"] = pins[first]
    elif failure == "wrong_role":
        pins["ghcr.io/fixture/task:unknown_v1"] = pins.pop(first)
    else:
        pins[first] = {
            "wrong_repository": "ghcr.io/other/task@sha256:" + "1" * 64,
            "tag": first,
            "invalid_digest": "ghcr.io/fixture/task@sha256:bad",
        }[failure]
    with pytest.raises(ValueError):
        scaled_workspace(path, 2, 256, 0.25, 2, pins)


def test_unpinned_default_preserves_builds(tmp_path: Path) -> None:
    """Keep existing tag and build behavior when no immutable override is supplied."""
    path, _ = compose_fixture(tmp_path)
    spec = yaml.safe_load(scaled_workspace(path, 2, 256, 0.25, 1).read_text())
    assert all(
        "build" in service and "@sha256:" not in service["image"]
        for service in spec["services"].values()
    )


def test_task_records_exact_effective_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expose original and effective image provenance without loading participant content."""
    monkeypatch.setenv("MC_IMAGE_NAME", "ghcr.io/fixture/task")
    task = mirrorcode(
        agents=2, target="ruff", token_limit_per_agent=1, artifact_dir=str(tmp_path)
    )
    services = yaml.safe_load(Path(str(task.dataset[0].sandbox.config)).read_text())[
        "services"
    ]
    pins = {
        image: image.rsplit(":", 1)[0] + "@sha256:" + str(i) * 64
        for i, image in enumerate(
            sorted(
                {
                    service["image"].replace(
                        "${MC_IMAGE_NAME:-mclocal}", "ghcr.io/fixture/task"
                    )
                    for service in services.values()
                }
            ),
            1,
        )
    }
    pinned = mirrorcode(
        agents=2,
        target="ruff",
        token_limit_per_agent=1,
        artifact_dir=str(tmp_path),
        image_pins=pins,
    )
    assert pinned.dataset[0].sandbox.config != task.dataset[0].sandbox.config
    assert pinned.metadata["image_pins"] == pins
    assert (
        pinned.dataset[0].metadata["effective_images"]
        == pinned.metadata["effective_images"]
    )
    assert set(pinned.metadata["effective_images"].values()) == set(pins.values())
