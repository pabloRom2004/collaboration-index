"""Retain generated Dockerfile/context hashes without copying benchmark contents."""

import hashlib
import json
import os
import sys
from pathlib import Path


def main():
    """Hash only regular generated build-context files and record exact source/run identity."""
    context = Path(sys.argv[1])
    target = sys.argv[2]
    output = Path(sys.argv[3])
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for path in sorted(context.rglob("*")):
        if path.is_file() and not path.is_symlink():
            records.append(
                {
                    "path": str(path.relative_to(context)),
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    proof = {
        "target": target,
        "context_sha256": hashlib.sha256(encoded).hexdigest(),
        "files": records,
        "github_run_id": os.environ["GITHUB_RUN_ID"],
        "github_sha": os.environ["GITHUB_SHA"],
        "upstream_commit": "5c9d7b00b0c6d7609003e33896cd41d28f092fcb",
        "platform": "linux/amd64",
    }
    (output / f"{target}-context.json").write_text(json.dumps(proof, indent=2) + "\n")


if __name__ == "__main__":
    main()
