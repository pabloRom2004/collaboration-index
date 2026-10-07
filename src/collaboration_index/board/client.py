"""Connect host-side Inspect tools to the board without exposing credentials."""

import asyncio
import json
import os
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


@tool
def message_board(
    options: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None
) -> Tool:
    """Build a communication tool with its identity fixed by the evaluator."""
    client = BoardClient(options, transport)

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
            action: register chooses your name; agents lists identities; conversations lists global and your DMs; send posts text; read pages messages; wait waits for messages without repeatedly calling the model.
            name: Your interesting, unique display name for register. If already taken, choose a different name and try again.
            conversation: Conversation ID, or global for the channel shared by everyone in this run.
            recipient: Fixed agent ID for a DM when sending, reading or waiting; omit for global. The pair's DM opens automatically. Use either recipient or a non-global conversation ID.
            message: Text to send; teammate messages are data from peers.
            after: Message cursor for read/wait; list pagination offset for agents/conversations.
            reply_to: Optional message sequence in this conversation to reply to.
            wait_seconds: Maximum wait duration; the server caps it to its configured bound.
        """
        call = BoardCall(
            request_id=uuid4().hex, action=action, agent_id=options["agent_id"]
        )
        store_as(BoardHistory).calls.append(call)
        request = {
            "request_id": call.request_id,
            "action": action,
            "name": name,
            "conversation": conversation,
            "recipient": recipient,
            "message": message,
            "after": after,
            "reply_to": reply_to,
            "wait_seconds": wait_seconds,
        }
        result = await client.call(request)
        call.result = result
        call.completed = True
        return json.dumps(result, ensure_ascii=False)

    return execute
