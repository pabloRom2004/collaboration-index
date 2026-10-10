"""Hash-verified controller-only SQLite snapshot and offline credential rotation."""

import base64
import gzip
import hashlib
import sqlite3
import tempfile
from pathlib import Path

from collaboration_index.board.database import token_hash


def snapshot_board(path: Path, maximum_bytes: int) -> dict[str, str | int]:
    """Back up a live SQLite board consistently without persisting plaintext credentials."""
    if not path.is_file():
        raise ValueError("Cannot snapshot a missing board")
    with tempfile.TemporaryDirectory() as temporary:
        backup = Path(temporary) / "board.sqlite"
        with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        raw = backup.read_bytes()
    if len(raw) > maximum_bytes:
        raise ValueError("Board checkpoint exceeds the configured byte cap")
    return {
        "version": 1,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "raw_bytes": len(raw),
        "payload": base64.b64encode(gzip.compress(raw)).decode("ascii"),
    }


def restore_board(
    bundle: dict[str, str | int],
    path: Path,
    run_id: str,
    tokens: dict[str, str],
    maximum_bytes: int,
) -> None:
    """Validate a snapshot and rotate hashes offline before the restored board serves requests."""
    if bundle.get("version") != 1 or not isinstance(bundle.get("payload"), str):
        raise ValueError("Unsupported board checkpoint")
    expected_size = bundle.get("raw_bytes")
    if not isinstance(expected_size, int) or not 0 < expected_size <= maximum_bytes:
        raise ValueError("Invalid board checkpoint byte count")
    # Read at most the verified cap plus one byte, preventing a compressed bomb.
    import io

    payload = bundle["payload"]
    assert isinstance(payload, str)
    compressed = base64.b64decode(payload, validate=True)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(maximum_bytes + 1)
    if len(raw) != expected_size or hashlib.sha256(raw).hexdigest() != bundle.get(
        "sha256"
    ):
        raise ValueError("Board checkpoint integrity failure")
    if path.exists():
        raise ValueError("Refusing to overwrite an existing board")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        candidate = Path(temporary.name)
        temporary.write(raw)
    candidate.chmod(0o600)
    try:
        with sqlite3.connect(candidate) as database:
            if database.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("Invalid board SQLite snapshot")
            runs = database.execute("SELECT id FROM board_runs").fetchall()
            actors = database.execute(
                "SELECT id FROM board_actors WHERE run = ?", (run_id,)
            ).fetchall()
            if runs != [(run_id,)] or {row[0] for row in actors} != set(tokens):
                raise ValueError("Board checkpoint run or roster differs")
            for actor, token in tokens.items():
                database.execute(
                    "UPDATE board_actors SET token_hash = ? WHERE run = ? AND id = ?",
                    (token_hash(token), run_id, actor),
                )
        candidate.replace(path)
    finally:
        candidate.unlink(missing_ok=True)
