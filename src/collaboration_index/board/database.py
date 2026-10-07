"""Persist run-scoped identities, conversations, and an ordered event journal."""

import hashlib
import json
import math
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Index,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    delete,
    event,
    func,
    insert,
    inspect,
    select,
    update,
)
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.pool import QueuePool

metadata = MetaData()
runs = Table(
    "board_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("sequence", BigInteger, nullable=False),
    Column("retained_bytes", BigInteger, nullable=False, default=0),
)
actors = Table(
    "board_actors",
    metadata,
    Column("run", String, primary_key=True),
    Column("id", String, primary_key=True),
    Column("token_hash", String, unique=True, nullable=False),
    Column("name", String),
    UniqueConstraint("run", "name"),
)
rooms = Table(
    "board_rooms",
    metadata,
    Column("run", String, primary_key=True),
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("title", String, nullable=False),
    Column("creator", String, nullable=False),
)
members = Table(
    "board_members",
    metadata,
    Column("run", String, primary_key=True),
    Column("room", String, primary_key=True),
    Column("actor", String, primary_key=True),
)
events = Table(
    "board_events",
    metadata,
    Column("run", String, primary_key=True),
    Column("sequence", BigInteger, primary_key=True),
    Column("actor", String, nullable=False),
    Column("room", String),
    Column("kind", String, nullable=False),
    Column("data", Text, nullable=False),
    Column("time", String, nullable=False),
)
requests = Table(
    "board_requests",
    metadata,
    Column("run", String, primary_key=True),
    Column("actor", String, primary_key=True),
    Column("id", String, primary_key=True),
    Column("fingerprint", String, nullable=False),
    Column("result", Text, nullable=False),
    Column("expires_at", Float),
    Column("storage_bytes", BigInteger, nullable=False, default=0),
)
receipts = Table(
    "board_receipts",
    metadata,
    Column("run", String, primary_key=True),
    Column("actor", String, primary_key=True),
    Column("sequence", BigInteger, primary_key=True),
)
Index(
    "board_message_page", events.c.run, events.c.room, events.c.kind, events.c.sequence
)
Index("board_actor_membership", members.c.run, members.c.actor, members.c.room)
request_expiry = Index("board_request_expiry", requests.c.run, requests.c.expires_at)


def token_hash(token: str) -> str:
    """Hash a high-entropy credential without retaining its plaintext."""
    return hashlib.sha256(token.encode()).hexdigest()


class BoardError(ValueError):
    """Reject a participant request without changing the board."""


class BoardFull(BoardError):
    """Reject writes that would exceed the collaboration run's retention budget."""


class BoardBusy(BoardError):
    """Ask a caller to retry when bounded board resources are occupied."""


def retained_size(value: dict[str, Any]) -> int:
    """Charge serialized row bytes plus conservative metadata and index overhead."""
    return len(json.dumps(value, ensure_ascii=True).encode()) + 512


class BoardDatabase:
    """Apply authenticated board operations in transactions with durable ordering."""

    def __init__(self, url: str, limits: dict[str, Any]) -> None:
        """Open a database with explicit message and pagination bounds."""
        self.limits = dict(limits)
        if not 0 < limits["read_cache_bytes"] < limits["storage_bytes"]:
            raise ValueError("Read cache must fit inside the run storage cap")
        if limits["page_bytes"] < 6 * limits["message_characters"] + 4096:
            raise ValueError("Page byte limit must fit one maximum-length message")
        if limits["read_cache_seconds"] <= (
            limits["request_timeout"] * limits["request_attempts"]
            + limits["retry_seconds"] * (limits["request_attempts"] - 1)
        ):
            raise ValueError("Read retry cache must outlive bounded transport retries")
        timeout = limits["database_seconds"]
        backend = make_url(url).get_backend_name()
        if backend == "sqlite":
            connect_args = {"timeout": timeout, "check_same_thread": False}
        elif backend == "postgresql":
            milliseconds = max(1, math.ceil(timeout * 1000))
            connect_args = {
                "connect_timeout": max(1, math.ceil(timeout)),
                "options": f"-c statement_timeout={milliseconds} -c lock_timeout={milliseconds}",
            }
        else:
            raise ValueError("The board supports SQLite or PostgreSQL")
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            poolclass=QueuePool,
            pool_size=limits["database_connections"],
            max_overflow=0,
            pool_timeout=timeout,
            connect_args=connect_args,
            hide_parameters=True,
        )
        if backend == "sqlite":

            @event.listens_for(self.engine, "before_cursor_execute")
            def bounded_query(
                connection: Connection,
                cursor: Any,
                statement: str,
                parameters: Any,
                context: Any,
                many: bool,
            ) -> None:
                """Interrupt SQLite statements that exceed the configured database wait."""
                deadline = time.monotonic() + timeout

                def expired() -> int:
                    """Stop database work once this statement exhausts its time allowance."""
                    return int(time.monotonic() >= deadline)

                cursor.connection.set_progress_handler(expired, 1000)

        has_receipts = inspect(self.engine).has_table(receipts.name)
        metadata.create_all(self.engine)
        self._upgrade_storage()
        if not has_receipts:
            with self.engine.begin() as db:
                for row in db.execute(
                    select(events.c.run, events.c.actor, events.c.data)
                    .where(events.c.kind == "read")
                    .execution_options(yield_per=100)
                ).mappings():
                    self._record_receipts(
                        db,
                        row["run"],
                        row["actor"],
                        json.loads(row["data"])["delivered"],
                    )

    def _upgrade_storage(self) -> None:
        """Add accounting columns once without removing existing history or retry records."""
        inspector = inspect(self.engine)
        missing_usage = "retained_bytes" not in {
            column["name"] for column in inspector.get_columns(runs.name)
        }
        with self.engine.begin() as db:
            if missing_usage:
                db.exec_driver_sql(
                    "ALTER TABLE board_runs ADD COLUMN retained_bytes BIGINT NOT NULL DEFAULT 0"
                )
            request_columns = {
                column["name"] for column in inspector.get_columns(requests.name)
            }
            if "expires_at" not in request_columns:
                db.exec_driver_sql(
                    "ALTER TABLE board_requests ADD COLUMN expires_at DOUBLE PRECISION"
                )
            if "storage_bytes" not in request_columns:
                db.exec_driver_sql(
                    "ALTER TABLE board_requests ADD COLUMN storage_bytes BIGINT NOT NULL DEFAULT 0"
                )
                for row in db.execute(select(requests)).mappings():
                    result = json.loads(row["result"])
                    if not set(result).intersection(
                        ("messages", "agents", "conversations")
                    ):
                        continue
                    if "messages" in result:
                        result["message_sequences"] = [
                            entry["sequence"] for entry in result.pop("messages")
                        ]
                    replacement = dict(row) | {
                        "result": json.dumps(result),
                        "expires_at": time.time() + self.limits["read_cache_seconds"],
                    }
                    db.execute(
                        update(requests)
                        .where(
                            and_(
                                requests.c.run == row["run"],
                                requests.c.actor == row["actor"],
                                requests.c.id == row["id"],
                            )
                        )
                        .values(
                            result=replacement["result"],
                            expires_at=replacement["expires_at"],
                            storage_bytes=retained_size(replacement),
                        )
                    )
            if missing_usage:
                for run in cast(
                    list[str], db.execute(select(runs.c.id)).scalars().all()
                ):
                    total = retained_size({"id": run, "sequence": 0})
                    for table in (actors, rooms, members, events, requests, receipts):
                        for row in db.execute(
                            select(table).where(table.c.run == run)
                        ).mappings():
                            if table is requests and row["expires_at"] is not None:
                                continue
                            total += retained_size(dict(row))
                            if table is events and row["kind"] == "message":
                                total += self._delivery_reserve(db, run, row["room"])
                    db.execute(
                        update(runs)
                        .where(runs.c.id == run)
                        .values(retained_bytes=total)
                    )
            request_expiry.create(db, checkfirst=True)

    def _retain(self, db: Connection, run: str, amount: int) -> None:
        """Atomically charge retained rows and preallocated read metadata under the run lock."""
        changed = db.execute(
            update(runs)
            .where(
                and_(
                    runs.c.id == run,
                    runs.c.retained_bytes + amount
                    <= self.limits["storage_bytes"] - self.limits["read_cache_bytes"],
                )
            )
            .values(retained_bytes=runs.c.retained_bytes + amount)
        )
        if changed.rowcount != 1:
            raise BoardFull("Message board full")

    def _delivery_reserve(self, db: Connection, run: str, room: str) -> int:
        """Reserve one receipt and worst-case single-message read event for each visible actor."""
        visible = select(actors.c.id).where(actors.c.run == run)
        if room != "global":
            visible = select(members.c.actor).where(
                and_(members.c.run == run, members.c.room == room)
            )
        total = 0
        for actor in cast(Sequence[str], db.execute(visible).scalars().all()):
            receipt = {"run": run, "actor": actor, "sequence": 2**63 - 1}
            entry = {
                "run": run,
                "sequence": 2**63 - 1,
                "actor": actor,
                "room": room,
                "kind": "read",
                "time": "9999-12-31T23:59:59.999999+00:00",
                "data": json.dumps(
                    {
                        "after": 2**63 - 1,
                        "next_after": 2**63 - 1,
                        "delivered": [2**63 - 1],
                    }
                ),
            }
            total += retained_size(receipt) + retained_size(entry)
        return total

    def _page(
        self, rows: Sequence[Any], key: str, after: int, **fields: Any
    ) -> dict[str, Any]:
        """Stop a page at either bound while advancing only past entries actually returned."""
        page: list[dict[str, Any]] = []
        result = {key: page, "next_after": after, "has_more": False, **fields}
        for row in rows[: self.limits["page_messages"]]:
            entry = dict(row) | {"data": json.loads(row["data"])}
            candidate = result | {
                key: page + [entry],
                "next_after": entry["sequence"],
                "has_more": True,
            }
            if (
                len(
                    json.dumps(
                        candidate, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                )
                > self.limits["page_bytes"]
            ):
                if not page:
                    raise BoardError("Page byte limit cannot fit this message")
                break
            page.append(entry)
        result["next_after"] = page[-1]["sequence"] if page else after
        result["has_more"] = len(rows) > len(page)
        return result

    def _cached_result(self, db: Connection, run: str, cached: str) -> dict[str, Any]:
        """Reconstruct a read retry from immutable sequences without retaining copied text."""
        result: dict[str, Any] = json.loads(cached)
        sequences = result.pop("message_sequences", None)
        if sequences is not None:
            rows = db.execute(
                select(events)
                .where(and_(events.c.run == run, events.c.sequence.in_(sequences)))
                .order_by(events.c.sequence)
            ).mappings()
            result["messages"] = [
                dict(row) | {"data": json.loads(row["data"])} for row in rows
            ]
        return result

    def _record_receipts(
        self, db: Connection, run: str, actor: str, sequences: list[int]
    ) -> list[int]:
        """Retain exactly the newly delivered messages, including noncontiguous pages."""
        if not sequences:
            return []
        recorded: set[int] = set(
            db.execute(
                select(receipts.c.sequence).where(
                    and_(
                        receipts.c.run == run,
                        receipts.c.actor == actor,
                        receipts.c.sequence.in_(sequences),
                    )
                )
            ).scalars()
        )
        unseen = set(sequences) - recorded
        if unseen:
            db.execute(
                insert(receipts),
                [
                    {"run": run, "actor": actor, "sequence": seq}
                    for seq in sorted(unseen)
                ],
            )
        return sorted(unseen)

    def unread(self, run: str, actor: str) -> dict[str, int]:
        """Count visible peer messages without delivering text or writing journal events."""
        visible = select(members.c.room).where(
            and_(members.c.run == run, members.c.actor == actor)
        )
        delivered = select(receipts.c.sequence).where(
            and_(receipts.c.run == run, receipts.c.actor == actor)
        )
        query = (
            select(rooms.c.kind, func.count().label("count"))
            .select_from(
                events.join(
                    rooms,
                    and_(events.c.run == rooms.c.run, events.c.room == rooms.c.id),
                )
            )
            .where(
                and_(
                    events.c.run == run,
                    events.c.kind == "message",
                    events.c.actor != actor,
                    (rooms.c.kind == "global")
                    | ((rooms.c.kind == "dm") & rooms.c.id.in_(visible)),
                    events.c.sequence.not_in(delivered),
                )
            )
            .group_by(rooms.c.kind)
        )
        with self.engine.connect() as db:
            counts: dict[str, int] = {
                row["kind"]: row["count"] for row in db.execute(query).mappings()
            }
        return {"global": counts.get("global", 0), "direct": counts.get("dm", 0)}

    def provision(self, roster: dict[str, dict[str, str]]) -> None:
        """Create or verify run-scoped slots using credential hashes supplied by the operator."""
        with self.engine.begin() as db:
            for run, slots in roster.items():
                if db.execute(select(runs).where(runs.c.id == run)).first() is None:
                    db.execute(
                        insert(runs).values(
                            id=run,
                            sequence=0,
                            retained_bytes=retained_size({"id": run, "sequence": 0}),
                        )
                    )
                    self._retain(
                        db,
                        run,
                        retained_size(
                            {
                                "run": run,
                                "id": "global",
                                "kind": "global",
                                "title": "Global",
                                "creator": "system",
                            }
                        ),
                    )
                    db.execute(
                        insert(rooms).values(
                            run=run,
                            id="global",
                            kind="global",
                            title="Global",
                            creator="system",
                        )
                    )
                self._lock_run(db, run)
                for actor, digest in slots.items():
                    row = (
                        db.execute(
                            select(actors).where(
                                and_(actors.c.run == run, actors.c.id == actor)
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if row is None:
                        if db.execute(
                            select(events.c.sequence)
                            .where(
                                and_(events.c.run == run, events.c.kind == "message")
                            )
                            .limit(1)
                        ).first():
                            raise ValueError(
                                "Cannot extend a roster after messages have been posted; provision a fresh run"
                            )
                        self._retain(
                            db,
                            run,
                            retained_size(
                                {
                                    "run": run,
                                    "id": actor,
                                    "token_hash": digest,
                                    "name": None,
                                }
                            ),
                        )
                        db.execute(
                            insert(actors).values(run=run, id=actor, token_hash=digest)
                        )
                    elif row["token_hash"] != digest:
                        raise ValueError("Provisioned slot has a different credential")

    def authenticate(self, token: str) -> tuple[str, str]:
        """Resolve the trusted run and actor from a credential rather than model arguments."""
        with self.engine.connect() as db:
            row = (
                db.execute(
                    select(actors).where(actors.c.token_hash == token_hash(token))
                )
                .mappings()
                .first()
            )
            if row is None:
                raise BoardError("Invalid credential")
            return row["run"], row["id"]

    def _event(
        self,
        db: Connection,
        run: str,
        actor: str,
        kind: str,
        room: str | None,
        data: dict[str, Any],
        prepaid: bool = False,
    ) -> dict[str, Any]:
        """Append an event while the run row is locked, so cursors follow commit order."""
        sequence: int = db.execute(
            update(runs)
            .where(runs.c.id == run)
            .values(sequence=runs.c.sequence + 1)
            .returning(runs.c.sequence)
        ).scalar_one()
        entry = {
            "run": run,
            "sequence": sequence,
            "actor": actor,
            "room": room,
            "kind": kind,
            "data": data,
            "time": datetime.now(UTC).isoformat(),
        }
        stored = entry | {"data": json.dumps(data)}
        if not prepaid:
            charge = retained_size(stored)
            if kind == "message":
                charge += self._delivery_reserve(db, run, cast(str, room))
            self._retain(db, run, charge)
        db.execute(insert(events).values(**stored))
        return entry

    def _room(self, db: Connection, run: str, actor: str, room: str) -> dict[str, Any]:
        """Return a visible conversation while enforcing private membership."""
        result = (
            db.execute(
                select(rooms).where(and_(rooms.c.run == run, rooms.c.id == room))
            )
            .mappings()
            .first()
        )
        if result is None or result["kind"] not in ("global", "dm"):
            raise BoardError("Conversation not found")
        if (
            result["kind"] == "dm"
            and db.execute(
                select(members).where(
                    and_(
                        members.c.run == run,
                        members.c.room == room,
                        members.c.actor == actor,
                    )
                )
            ).first()
            is None
        ):
            raise BoardError("Conversation not found")
        return dict(result)

    def _join(self, db: Connection, run: str, actor: str, room: str) -> None:
        """Subscribe a participant exactly once to an accessible conversation."""
        if (
            db.execute(
                select(members).where(
                    and_(
                        members.c.run == run,
                        members.c.room == room,
                        members.c.actor == actor,
                    )
                )
            ).first()
            is None
        ):
            self._retain(
                db, run, retained_size({"run": run, "room": room, "actor": actor})
            )
            db.execute(insert(members).values(run=run, room=room, actor=actor))
            self._event(db, run, actor, "join", room, {})

    def _target(
        self, db: Connection, run: str, actor: str, room: str, recipient: str
    ) -> str:
        """Resolve one fixed peer to a deterministic private room without changing the board."""
        if not recipient:
            return room
        if room != "global":
            raise BoardError("Choose recipient or conversation, not both")
        if (
            recipient == actor
            or db.execute(
                select(actors.c.id).where(
                    and_(actors.c.run == run, actors.c.id == recipient)
                )
            ).first()
            is None
        ):
            raise BoardError("Choose one known peer agent ID")
        return (
            "dm-"
            + hashlib.sha256(
                json.dumps(sorted([actor, recipient])).encode()
            ).hexdigest()[:24]
        )

    def _open_dm(
        self, db: Connection, run: str, actor: str, peer: str, room: str
    ) -> None:
        """Open exactly one private room for a pair while the run transaction is locked."""
        if (
            db.execute(
                select(rooms.c.id).where(and_(rooms.c.run == run, rooms.c.id == room))
            ).first()
            is not None
        ):
            return
        title = " ↔ ".join(sorted([actor, peer]))
        self._retain(
            db,
            run,
            retained_size(
                {"run": run, "id": room, "kind": "dm", "title": title, "creator": actor}
            ),
        )
        db.execute(
            insert(rooms).values(
                run=run, id=room, kind="dm", title=title, creator=actor
            )
        )
        self._event(
            db,
            run,
            actor,
            "create",
            room,
            {"kind": "dm", "title": title, "members": sorted([actor, peer])},
        )
        for member in sorted([actor, peer]):
            self._join(db, run, member, room)

    def _lock_run(self, db: Connection, run: str) -> None:
        """Serialize roster and participant changes before charging run retention."""
        if self.engine.dialect.name == "sqlite":
            db.execute(
                update(runs).where(runs.c.id == run).values(sequence=runs.c.sequence)
            )
        else:
            db.execute(
                select(runs.c.id).where(runs.c.id == run).with_for_update()
            ).scalar_one()

    def execute(self, run: str, actor: str, request: dict[str, Any]) -> dict[str, Any]:
        """Execute a request once, including safe retries after a lost response."""
        request_id = request["request_id"]
        fingerprint = hashlib.sha256(
            json.dumps(request, sort_keys=True).encode()
        ).hexdigest()
        with self.engine.begin() as db:
            self._lock_run(db, run)
            now = time.time()
            db.execute(
                delete(requests).where(
                    and_(requests.c.run == run, requests.c.expires_at <= now)
                )
            )
            previous = (
                db.execute(
                    select(requests).where(
                        and_(
                            requests.c.run == run,
                            requests.c.actor == actor,
                            requests.c.id == request_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise BoardError("Request ID reused with different arguments")
                return self._cached_result(db, run, previous["result"])
            result = self._execute(db, run, actor, request)
            observational = request["action"] in (
                "read",
                "wait",
                "agents",
                "conversations",
            )
            cached = dict(result)
            if "messages" in cached:
                cached["message_sequences"] = [
                    entry["sequence"] for entry in cached.pop("messages")
                ]
            row = {
                "run": run,
                "actor": actor,
                "id": request_id,
                "fingerprint": fingerprint,
                "result": json.dumps(cached),
                "expires_at": now + self.limits["read_cache_seconds"]
                if observational
                else None,
            }
            charge = retained_size(row)
            if observational:
                used: int = db.execute(
                    select(func.coalesce(func.sum(requests.c.storage_bytes), 0)).where(
                        and_(requests.c.run == run, requests.c.expires_at.is_not(None))
                    )
                ).scalar_one()
                if used + charge > self.limits["read_cache_bytes"]:
                    raise BoardBusy("Board busy; try again later")
            else:
                self._retain(db, run, charge)
            db.execute(insert(requests).values(**row, storage_bytes=charge))
            return result

    def _execute(
        self, db: Connection, run: str, actor: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply one validated operation with attribution and visibility enforced by the server."""
        action = request["action"]
        room = request.get("conversation", "global")
        if action == "register":
            name = request["name"].strip()
            if not name or len(name) > self.limits["name_characters"]:
                raise BoardError("Name is empty or too long")
            other = db.execute(
                select(actors.c.id).where(
                    and_(
                        actors.c.run == run, actors.c.name == name, actors.c.id != actor
                    )
                )
            ).first()
            if other:
                raise BoardError("Name already taken")
            current: str | None = db.execute(
                select(actors.c.name).where(
                    and_(actors.c.run == run, actors.c.id == actor)
                )
            ).scalar_one()
            if current is not None and current != name:
                raise BoardError("This slot already registered a name")
            if current is None:
                self._retain(db, run, len(json.dumps(name).encode()))
                db.execute(
                    update(actors)
                    .where(and_(actors.c.run == run, actors.c.id == actor))
                    .values(name=name)
                )
                self._event(db, run, actor, "register", None, {"name": name})
                self._join(db, run, actor, "global")
            return {"agent_id": actor, "name": name, "run_id": run}
        if action == "agents":
            after = request.get("after", 0)
            rows = (
                db.execute(
                    select(actors.c.id, actors.c.name)
                    .where(actors.c.run == run)
                    .order_by(actors.c.id)
                    .offset(after)
                    .limit(self.limits["page_messages"] + 1)
                )
                .mappings()
                .all()
            )
            return {
                "agents": [dict(row) for row in rows[: self.limits["page_messages"]]],
                "total_agents": db.execute(
                    select(func.count()).select_from(actors).where(actors.c.run == run)
                ).scalar_one(),
                "next_after": after + min(len(rows), self.limits["page_messages"]),
                "has_more": len(rows) > self.limits["page_messages"],
            }
        if action == "conversations":
            visible = select(members.c.room).where(
                and_(members.c.run == run, members.c.actor == actor)
            )
            query = select(rooms).where(
                and_(
                    rooms.c.run == run,
                    (rooms.c.kind == "global")
                    | ((rooms.c.kind == "dm") & rooms.c.id.in_(visible)),
                )
            )
            after = request.get("after", 0)
            rows = (
                db.execute(
                    query.order_by(rooms.c.id)
                    .offset(after)
                    .limit(self.limits["page_messages"] + 1)
                )
                .mappings()
                .all()
            )
            return {
                "conversations": [
                    dict(row) for row in rows[: self.limits["page_messages"]]
                ],
                "next_after": after + min(len(rows), self.limits["page_messages"]),
                "has_more": len(rows) > self.limits["page_messages"],
            }
        if action in ("send", "read", "wait"):
            recipient = request.get("recipient", "")
            room = self._target(db, run, actor, room, recipient)
            if recipient:
                self._open_dm(db, run, actor, recipient, room)
        if action == "send":
            details = self._room(db, run, actor, room)
            text = request["message"]
            if not text.strip() or len(text) > self.limits["message_characters"]:
                raise BoardError("Message is empty or too long")
            reply_to = request.get("reply_to")
            if (
                reply_to is not None
                and db.execute(
                    select(events).where(
                        and_(
                            events.c.run == run,
                            events.c.room == room,
                            events.c.sequence == reply_to,
                            events.c.kind == "message",
                        )
                    )
                ).first()
                is None
            ):
                raise BoardError("Reply must reference a message in this conversation")
            return self._event(
                db,
                run,
                actor,
                "message",
                room,
                {
                    "text": text,
                    "reply_to": reply_to,
                    "conversation_kind": details["kind"],
                },
            )
        if action in ("read", "wait"):
            details = self._room(db, run, actor, room)
            after = request.get("after", 0)
            rows = (
                db.execute(
                    select(events)
                    .where(
                        and_(
                            events.c.run == run,
                            events.c.room == room,
                            events.c.sequence > after,
                            events.c.kind == "message",
                        )
                    )
                    .order_by(events.c.sequence)
                    .limit(self.limits["page_messages"] + 1)
                )
                .mappings()
                .all()
            )
            result = self._page(
                rows, "messages", after, conversation_kind=details["kind"]
            )
            newly_delivered = self._record_receipts(
                db, run, actor, [entry["sequence"] for entry in result["messages"]]
            )
            if newly_delivered:
                self._event(
                    db,
                    run,
                    actor,
                    "read",
                    room,
                    {
                        "after": after,
                        "next_after": result["next_after"],
                        "delivered": newly_delivered,
                    },
                    prepaid=True,
                )
            return result
        raise BoardError("Unknown action")

    def pending(
        self, run: str, actor: str, room: str, after: int, recipient: str = ""
    ) -> bool:
        """Check for new visible messages without writing a polling event."""
        with self.engine.connect() as db:
            room = self._target(db, run, actor, room, recipient)
            if (
                recipient
                and db.execute(
                    select(rooms.c.id).where(
                        and_(rooms.c.run == run, rooms.c.id == room)
                    )
                ).first()
                is None
            ):
                return False
            self._room(db, run, actor, room)
            return (
                db.execute(
                    select(events.c.sequence)
                    .where(
                        and_(
                            events.c.run == run,
                            events.c.room == room,
                            events.c.sequence > after,
                            events.c.kind == "message",
                        )
                    )
                    .limit(1)
                ).first()
                is not None
            )

    def export(self, run: str, after: int) -> dict[str, Any]:
        """Return a bounded observer page including private messages and delivery records."""
        with self.engine.connect() as db:
            rows = (
                db.execute(
                    select(events)
                    .where(and_(events.c.run == run, events.c.sequence > after))
                    .order_by(events.c.sequence)
                    .limit(self.limits["page_messages"] + 1)
                )
                .mappings()
                .all()
            )
            return self._page(rows, "events", after)
