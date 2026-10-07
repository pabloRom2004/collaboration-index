"""Start a dedicated board process without inheriting evaluator or cloud credentials."""

import argparse
import os
import sys
from collections.abc import Mapping

MAX_PORT = 65535


def require_unprivileged() -> None:
    """Refuse root execution before opening credentials or provisioning a database."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise RuntimeError("Run the board service as a dedicated non-root OS user")


def service_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Forward only explicit board configuration and certificate paths to the API."""
    for key in ("BOARD_DATABASE_URL", "BOARD_CREDENTIALS_FILE"):
        if not source.get(key):
            raise RuntimeError(f"Required board setting is missing: {key}")
    allowed = (
        "BOARD_DATABASE_URL",
        "BOARD_CREDENTIALS_FILE",
        "BOARD_LIMITS_FILE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    )
    return {key: source[key] for key in allowed if key in source} | {
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def main() -> None:
    """Replace the launcher with Uvicorn using a narrow environment and loopback by default."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= MAX_PORT:
        parser.error("--port must be between 1 and 65535")
    require_unprivileged()
    os.execve(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "collaboration_index.board.server:application",
            "--factory",
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--no-access-log",
        ],
        service_environment(os.environ),
    )


if __name__ == "__main__":
    main()
