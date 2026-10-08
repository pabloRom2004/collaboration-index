"""Own a fresh separate board process for each local team attempt."""

import asyncio
import json
import os
import secrets
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from inspect_ai.util import store_as

from collaboration_index.board.client import LIMITS, BoardHistory
from collaboration_index.board.database import token_hash
from collaboration_index.board.service import service_environment


@asynccontextmanager
async def local_board(
    directory: Path, run_id: str, actors: list[str]
) -> AsyncIterator[list[dict[str, str]]]:
    """Provision hashed credentials, supervise an unprivileged service, and export its journal."""
    directory.mkdir(parents=True, exist_ok=True)
    tokens = {actor: secrets.token_urlsafe(32) for actor in actors}
    observer = secrets.token_urlsafe(32)
    credential_file = directory / "board-credential-hashes.json"
    credential_file.write_text(
        json.dumps(
            {
                "runs": {run_id: {a: token_hash(t) for a, t in tokens.items()}},
                "admin_hash": token_hash(observer),
            }
        )
    )
    credential_file.chmod(0o600)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    settings = {
        "BOARD_CREDENTIALS_FILE": str(credential_file),
        "BOARD_DATABASE_URL": f"sqlite:///{directory / 'board.sqlite'}",
    }
    env_names = {
        a: f"COLLAB_BOARD_{run_id.replace('-', '_')}_{i}" for i, a in enumerate(actors)
    }
    if any(key in os.environ for key in env_names.values()):
        raise RuntimeError("A scoped board environment variable already exists")
    process = None
    console = (directory / "board-console.log").open("wb")
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "collaboration_index.board.server:application",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
            env=service_environment(settings),
            stdout=console,
            stderr=console,
        )
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            async with asyncio.timeout(35):
                while True:
                    if process.returncode is not None:
                        raise RuntimeError("Owned board service exited during startup")
                    try:
                        response = await client.get(url + "/health")
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.05)
        for actor, token in tokens.items():
            os.environ[env_names[actor]] = token
        (directory / "board-receipt.json").write_text(
            json.dumps(
                {
                    "url": url,
                    "run_id": run_id,
                    "pid": process.pid,
                    "participants": len(actors),
                    "credential_storage": "hashes_only",
                },
                indent=2,
            )
        )
        yield [
            {"url": url, "run_id": run_id, "agent_id": a, "token_env": env_names[a]}
            for a in actors
        ]
    finally:
        for key in env_names.values():
            os.environ.pop(key, None)
        if process is not None:
            try:
                if process.returncode is None:
                    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                        events: list[dict[str, Any]] = []
                        after = 0
                        deadline = (
                            asyncio.get_running_loop().time()
                            + LIMITS["request_timeout"]
                        )
                        while True:
                            response = await client.get(
                                url + "/admin/export/" + run_id,
                                params={"after": after},
                                headers={"Authorization": "Bearer " + observer},
                            )
                            # requests from cancelled peers can hold every database
                            # connection briefly, so a busy board is retried until
                            # their bounded waits have drained
                            if (
                                response.status_code == 503
                                and asyncio.get_running_loop().time() < deadline
                            ):
                                await asyncio.sleep(LIMITS["retry_seconds"])
                                continue
                            response.raise_for_status()
                            page = response.json()
                            events.extend(page["events"])
                            if not page["has_more"]:
                                break
                            if page["next_after"] <= after:
                                raise RuntimeError(
                                    "Board export cursor did not advance"
                                )
                            after = page["next_after"]
                    (directory / "board.jsonl").write_text(
                        "".join(
                            json.dumps(e, ensure_ascii=False) + "\n" for e in events
                        )
                    )
                    # Remote runners may discard artifact_dir; the log keeps the replay source.
                    store_as(BoardHistory).journal = events
            finally:
                if process.returncode is None:
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                console.close()
                (directory / "board-stopped.json").write_text(
                    json.dumps(
                        {
                            "pid": process.pid,
                            "returncode": process.returncode,
                            "owned_process_stopped": True,
                        }
                    )
                )
        else:
            console.close()
