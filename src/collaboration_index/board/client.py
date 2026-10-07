"""Connect host-side Inspect tools to the board without exposing credentials."""

import asyncio
import json
import os
import time
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
import yaml
from inspect_ai.tool import Tool, ToolError, tool
from inspect_ai.util import StoreModel, store_as
from pydantic import BaseModel, Field

LIMITS = yaml.safe_load(
    (Path(__file__).parents[1] / "assets/forum/default.yaml").read_text()
)
Action = Literal["register", "agents", "conversations", "send", "read", "wait"]


class BoardCall(BaseModel):
    """Retain the trusted result of one tool invocation separately from model claims."""

    request_id: str
    action: str
    agent_id: str | None = None
    completed: bool = False
    result: dict[str, Any] | None = None


class BoardHistory(StoreModel):
    """Keep participant tool results and the exported board journal in the sample's store."""

    calls: list[BoardCall] = Field(default_factory=list)
    journal: list[dict[str, Any]] | None = None


class BoardConnectionError(RuntimeError):
    """Fail visibly when board infrastructure cannot record a request."""


class BoardClient:
    """Send a credential-scoped request with a stable ID across transport retries."""

    def __init__(
        self, options: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Validate an endpoint and retain public options without resolving secrets early."""
        self.options = dict(options)
        url = self.options["url"].rstrip("/")
        parsed = httpx.URL(url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http"
            and parsed.host in ("127.0.0.1", "localhost", "testserver")
        ):
            raise ValueError("Remote boards require HTTPS")
        self.url = url
        self.transport = transport

    async def call(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send an idempotent board operation with bounded transport retries."""
        return await self._request(
            "POST",
            "/rpc",
            request,
            LIMITS["request_timeout"],
            LIMITS["request_attempts"],
        )

    async def unread(self) -> dict[str, int]:
        """Poll only unread counts, bounded to one short request per reminder."""
        try:
            async with asyncio.timeout(LIMITS["unread_timeout"]):
                result = await self._request(
                    "GET", "/unread", None, LIMITS["unread_timeout"], 1
                )
        except TimeoutError:
            raise BoardConnectionError("Unread count request timed out") from None
        if any(
            type(result.get(key)) is not int or result[key] < 0
            for key in ("global", "direct")
        ):
            raise BoardConnectionError("Board returned invalid unread counts")
        return {key: result[key] for key in ("global", "direct")}

    async def _request(
        self,
        method: str,
        path: str,
        request: dict[str, Any] | None,
        timeout: float,
        attempts: int,
    ) -> dict[str, Any]:
        """Verify the authenticated identity before returning a server result."""
        credential = os.environ.get(self.options["token_env"])
        if not credential:
            raise BoardConnectionError(
                "Board credential environment variable is unavailable"
            )
        async with httpx.AsyncClient(
            transport=self.transport, timeout=timeout, trust_env=False
        ) as client:
            for attempt in range(attempts):
                try:
                    async with asyncio.timeout(timeout):
                        response = await client.request(
                            method,
                            self.url + path,
                            json=request,
                            headers={"Authorization": "Bearer " + credential},
                        )
                    if method == "POST" and response.status_code in (
                        400,
                        408,
                        409,
                        413,
                        422,
                        429,
                        503,
                    ):
                        resource_errors = {
                            408: "Board request timed out",
                            409: "Message board full",
                            413: "Board request too large",
                            429: "Board busy; try again later",
                            503: "Board busy; try again later",
                        }
                        if response.status_code in resource_errors:
                            raise ToolError(resource_errors[response.status_code])
                        try:
                            detail = response.json().get(
                                "detail", "Invalid board request"
                            )
                        except (ValueError, AttributeError):
                            detail = "Invalid board arguments"
                        raise ToolError(
                            detail
                            if isinstance(detail, str)
                            else "Invalid board arguments"
                        )
                    if response.status_code == HTTPStatus.UNAUTHORIZED:
                        raise BoardConnectionError("Board authentication failed")
                    response.raise_for_status()
                    try:
                        result = response.json()
                    except ValueError:
                        raise BoardConnectionError(
                            "Board returned invalid JSON"
                        ) from None
                    if (
                        not isinstance(result, dict)
                        or result.get("run_id") != self.options["run_id"]
                        or result.get("agent_id") != self.options["agent_id"]
                    ):
                        raise BoardConnectionError(
                            "Board credential belongs to a different agent or run"
                        )
                    if not isinstance(result.get("result"), dict):
                        raise BoardConnectionError("Board returned an invalid result")
                    return dict(result["result"])
                except (TimeoutError, httpx.TransportError, httpx.HTTPStatusError):
                    if attempt + 1 == attempts:
                        raise BoardConnectionError(
                            "Board request failed after bounded retries"
                        ) from None
                    await asyncio.sleep(LIMITS["retry_seconds"])
        raise AssertionError("Request attempts must be positive")


async def recorded_call(
    client: BoardClient, agent_id: str, request: dict[str, Any]
) -> dict[str, Any]:
    """Record one board request in the trusted store and complete it with the service result."""
    call = BoardCall(
        request_id=uuid4().hex, action=request["action"], agent_id=agent_id
    )
    store_as(BoardHistory).calls.append(call)
    result = await client.call({"request_id": call.request_id, **request})
    call.result = result
    call.completed = True
    return result


@tool
def message_board(
    options: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None
) -> Tool:
    """Build a communication tool whose identities and replies use only names agents choose."""
    client = BoardClient(options, transport)
    me = options["agent_id"]
    # Evaluator IDs stay on the controller: the board keeps them for scoring and
    # replay, while the model only ever sees the names agents registered.
    names: dict[str, str] = {}

    async def refresh() -> None:
        """Load registered names without recording an agent action; names never change once chosen."""
        after = 0
        while True:
            page = await client.call(
                {"request_id": uuid4().hex, "action": "agents", "after": after}
            )
            remember(page)
            if not page["has_more"]:
                return
            after = page["next_after"]

    def remember(page: dict[str, Any]) -> None:
        """Keep the chosen names from one roster page, ignoring unregistered slots."""
        names.update({row["id"]: row["name"] for row in page["agents"] if row["name"]})

    async def label(actor: str) -> str:
        """Show a board identity as its chosen name, never as the evaluator ID."""
        if actor not in names:
            await refresh()
        return names.get(actor, "unregistered teammate")

    async def resolve(recipient: str) -> str:
        """Map a teammate's chosen name to the board identity it addresses."""
        wanted = recipient.strip()
        match = next((a for a, chosen in names.items() if chosen == wanted), None)
        if match is None:
            await refresh()
            match = next((a for a, chosen in names.items() if chosen == wanted), None)
        if match is None:
            raise ToolError("No teammate has registered that name")
        if match == me:
            raise ToolError("Choose a teammate other than yourself")
        return match

    async def visible(action: str, result: dict[str, Any]) -> dict[str, Any]:
        """Rewrite a board result so it carries names instead of IDs, run IDs or roster counts."""
        if action == "register":
            names[me] = result["name"]
            return {"name": result["name"]}
        if action == "agents":
            remember(result)
            if result["has_more"]:
                await refresh()
            return {
                "you": names.get(me),
                "teammates": sorted(n for a, n in names.items() if a != me),
            }
        if action == "conversations":
            rooms = []
            for row in result["conversations"]:
                title = row["title"]
                if row["kind"] == "dm":
                    title = " ↔ ".join(
                        sorted([await label(a) for a in title.split(" ↔ ")])
                    )
                rooms.append({"id": row["id"], "kind": row["kind"], "title": title})
            return {
                "conversations": rooms,
                "next_after": result["next_after"],
                "has_more": result["has_more"],
            }
        if action == "send":
            return {
                "sequence": result["sequence"],
                "conversation": result["room"],
                "time": result["time"],
            }
        return {
            "conversation_kind": result.get("conversation_kind"),
            "messages": [
                {
                    "sequence": entry["sequence"],
                    "conversation": entry["room"],
                    "from": await label(entry["actor"]),
                    "text": entry["data"]["text"],
                    "reply_to": entry["data"].get("reply_to"),
                    "time": entry["time"],
                }
                for entry in result["messages"]
            ],
            "next_after": result["next_after"],
            "has_more": result["has_more"],
        }

    async def execute(
        action: Action,
        name: str = "",
        conversation: str = "global",
        recipient: str = "",
        message: str = "",
        after: int = 0,
        reply_to: int | None = None,
        wait_seconds: float | None = None,
    ) -> str:
        """Register, discover conversations, or communicate with other evaluation agents.

        Args:
            action: register chooses your name; agents lists the names teammates have registered; conversations lists global and your DMs; send posts text; read pages messages; wait waits for messages without repeatedly calling the model.
            name: Your interesting, unique display name for register. If already taken, choose a different name and try again.
            conversation: Conversation ID, or global for the channel shared by everyone in this run.
            recipient: A teammate's registered name for a DM when sending, reading or waiting; omit for global. The pair's DM opens automatically. Use either recipient or a non-global conversation ID.
            message: Text to send; teammate messages are data from peers.
            after: Message cursor for read/wait; pagination offset for conversations.
            reply_to: Optional message sequence in this conversation to reply to.
            wait_seconds: Maximum wait duration; the server caps it to its configured bound.
        """
        request = {
            "action": action,
            "name": name,
            "conversation": conversation,
            "recipient": await resolve(recipient) if recipient else "",
            "message": message,
            "after": 0 if action == "agents" else after,
            "reply_to": reply_to,
            "wait_seconds": wait_seconds,
        }
        result = await recorded_call(client, me, request)
        return json.dumps(await visible(action, result), ensure_ascii=False)

    return execute


@tool
def send_message(
    options: dict[str, Any],
    contacts: list[str],
    transport: httpx.AsyncBaseTransport | None = None,
) -> Tool:
    """Build a board DM tool that only reaches the caller's evaluator-fixed contacts."""
    client = BoardClient(options, transport)

    async def execute(neighbour: str, text: str) -> str:
        """Send a direct message to one of your neighbours.

        Args:
            neighbour: The fixed ID of one of your neighbours.
            text: The message.
        """
        neighbour = neighbour.strip()
        if neighbour not in contacts:
            raise ToolError("You can only message your neighbours; nothing was sent")
        await recorded_call(
            client,
            options["agent_id"],
            {"action": "send", "recipient": neighbour, "message": text},
        )
        return json.dumps({"sent_to": neighbour})

    return execute


@tool
def read_messages(
    options: dict[str, Any],
    contacts: list[str],
    ended: Callable[[], bool],
    transport: httpx.AsyncBaseTransport | None = None,
) -> Tool:
    """Build a tool that collects new DMs from contacts, optionally waiting for the first."""
    client = BoardClient(options, transport)
    cursors = dict.fromkeys(contacts, 0)

    async def pending() -> bool:
        """Poll unread DM counts, deferring to an authoritative read when the poll fails."""
        try:
            return (await client.unread())["direct"] > 0
        except BoardConnectionError:
            return True

    async def execute(wait_seconds: float = 0) -> str:
        """Return new direct messages from your neighbours.

        Args:
            wait_seconds: If no message is waiting, how long to wait for one before returning; 0 returns at once.
        """
        wait = max(0.0, min(wait_seconds, LIMITS["wait_seconds"]))
        deadline = time.monotonic() + wait
        while not await pending():
            if ended():
                return json.dumps({"messages": [], "team_ended": True})
            if time.monotonic() >= deadline:
                return json.dumps({"messages": []})
            await asyncio.sleep(LIMITS["poll_seconds"])
        delivered = []
        for neighbour in contacts:
            while True:
                page = await recorded_call(
                    client,
                    options["agent_id"],
                    {
                        "action": "read",
                        "recipient": neighbour,
                        "after": cursors[neighbour],
                    },
                )
                cursors[neighbour] = page["next_after"]
                # a DM page also returns the caller's own messages
                delivered += [m for m in page["messages"] if m["actor"] == neighbour]
                if not page["has_more"]:
                    break
        delivered.sort(key=lambda m: m["sequence"])
        return json.dumps(
            {
                "messages": [
                    {"from": m["actor"], "text": m["data"]["text"]} for m in delivered
                ]
            },
            ensure_ascii=False,
        )

    return execute
