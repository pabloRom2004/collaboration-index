"""Generate a benchmark starter wired to the local reusable collaboration core."""

import argparse
import json
import re
import shutil
from pathlib import Path


def create_project(destination: Path, core_path: Path | None = None) -> Path:
    """Copy the packaged starter into a new directory with an explicit editable core dependency."""
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Choose a new destination directory")
    core = (core_path or Path(__file__).resolve().parents[2]).resolve()
    if not (core / "pyproject.toml").is_file():
        raise ValueError(
            "Supply --core-path pointing to the collaboration-index source checkout"
        )
    project = re.sub(r"[^a-z0-9]+", "-", destination.name.lower()).strip("-")
    if not project:
        raise ValueError("Use a nonempty project name")
    shutil.copytree(Path(__file__).parent / "assets/starter", destination)
    pyproject = destination / "pyproject.toml"
    # JSON-quoted strings are valid TOML basic strings; replace the entire quoted placeholder.
    pyproject.write_text(
        pyproject.read_text()
        .replace('"__PROJECT_NAME__"', json.dumps(project))
        .replace('"__CORE_PATH__"', json.dumps(str(core)))
    )
    return destination


def main() -> None:
    """Create an unlaunched local starter that installs the same board and visualiser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--core-path", type=Path)
    args = parser.parse_args()
    directory = create_project(args.destination, args.core_path)
    print(f"Created {directory}; run uv sync and uv run python smoke.py there.")


if __name__ == "__main__":
    main()
