"""Serve the message board separately from evaluation runners and sandboxes."""

import asyncio
import hmac
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from collaboration_index.board.database import (
    BoardBusy,
    BoardDatabase,
    BoardError,
    BoardFull,
    token_hash,
)
from collaboration_index.board.service import require_unprivileged

Result = TypeVar("Result")

LIMITS = yaml.safe_load(
    (Path(__file__).parents[1] / "assets/forum/default.yaml").read_text()
)


class BoardRequest(BaseModel):
    """Accept communication operations without accepting caller-supplied identities."""

    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    action: Literal["register", "agents", "conversations", "send", "read", "wait"]
    name: str = Field(default="", max_length=64)
    conversation: str = Field(default="global", max_length=128)
    recipient: str = Field(default="", max_length=128)
    message: str = Field(default="", max_length=LIMITS["message_characters"])
    after: int = Field(default=0, ge=0, le=2**63 - 1)
    reply_to: int | None = Field(default=None, ge=1, le=2**63 - 1)
    wait_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Admission:
    """Reject excess work immediately without creating an unbounded queue."""

    def __init__(self, limit: int) -> None:
        """Retain a positive process-local concurrency bound."""
        if limit < 1:
            raise ValueError("Concurrency limits must be positive")
        self.limit = limit
        self.active = 0

    def acquire(self) -> None:
        """Reserve one slot atomically on the service's event loop."""
        if self.active >= self.limit:
            raise HTTPException(429, "Board busy; try again later")
        self.active += 1

    def release(self) -> None:
        """Return a slot after completion, errors or cancellation."""
        self.active -= 1


class RequestBounds:
    """Bound in-flight requests and POST bytes before the framework parses JSON."""

    def __init__(
        self, app: ASGIApp, admission: Admission, limits: dict[str, Any]
    ) -> None:
        """Share admission state with the service and retain upload bounds."""
        self.app = app
        self.admission = admission
        self.limits = limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Buffer only a bounded RPC body and always release request admission."""
        if scope["type"] != "http" or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return
        acquired = False
        try:
            self.admission.acquire()
            acquired = True
            if scope["path"] == "/rpc":
                body = bytearray()
                try:
                    async with asyncio.timeout(self.limits["request_body_seconds"]):
                        while True:
                            chunk = await receive()
                            if chunk["type"] == "http.disconnect":
                                return
                            data = chunk.get("body", b"")
                            if (
                                len(body) + len(data)
                                > self.limits["request_body_bytes"]
                            ):
                                raise HTTPException(413, "Board request too large")
                            body.extend(data)
                            if not chunk.get("more_body", False):
                                break
                except TimeoutError:
                    raise HTTPException(408, "Board request timed out") from None
                supplied = False

                async def bounded_receive() -> Message:
                    """Replay the bounded body once and forward later disconnect checks."""
                    nonlocal supplied
                    if not supplied:
                        supplied = True
                        return {
                            "type": "http.request",
                            "body": bytes(body),
                            "more_body": False,
                        }
                    return await receive()

                await self.app(scope, bounded_receive, send)
            else:
                await self.app(scope, receive, send)
        except HTTPException as error:
            await JSONResponse({"detail": error.detail}, status_code=error.status_code)(
                scope, receive, send
            )
        finally:
            if acquired:
                self.admission.release()


async def database_call(function: Callable[..., Result], *args: Any) -> Result:
    """Keep admission occupied until bounded database work finishes, even after cancellation."""
    operation = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        while not operation.done():
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if operation.done() and not operation.cancelled():
            operation.exception()
        raise
    except SQLAlchemyError:
        raise HTTPException(503, "Board busy; try again later") from None


def create_app(database: BoardDatabase, admin_hash: str) -> FastAPI:
    """Expose authenticated agent operations and a separate observer export endpoint."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    admission = Admission(database.limits["concurrent_requests"])
    waits = Admission(database.limits["concurrent_waits"])
    if waits.limit >= admission.limit:
        raise ValueError("Wait concurrency must leave request slots available")
    app.add_middleware(RequestBounds, admission=admission, limits=database.limits)

    @app.exception_handler(RequestValidationError)
    async def invalid_arguments(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """Hide untrusted query/header values in framework validation responses."""
        return JSONResponse({"detail": "Invalid board arguments"}, status_code=422)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Report liveness without revealing any run or participant data."""
        return {"status": "ok"}

    async def identity(authorization: str) -> tuple[str, str]:
        """Resolve participant scope from the bearer credential for every agent endpoint."""
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Invalid credential")
        try:
            return await database_call(database.authenticate, authorization[7:])
        except BoardError as error:
            raise HTTPException(401, str(error)) from error

    @app.get("/unread")
    async def unread(
        response: Response, authorization: str = Header(default="")
    ) -> dict[str, Any]:
        """Return fresh unread totals without exposing message contents or recording a read."""
        run, actor = await identity(authorization)
        response.headers["Cache-Control"] = "no-store"
        result = await database_call(database.unread, run, actor)
        return {"run_id": run, "agent_id": actor, "result": result}

    @app.post("/rpc")
    async def rpc(
        raw_request: Request, authorization: str = Header(default="")
    ) -> dict[str, Any]:
        """Bind each request to its credential and deliver bounded conversation pages."""
        try:
            request = BoardRequest.model_validate_json(await raw_request.body())
        except ValidationError:
            raise HTTPException(422, "Invalid board arguments") from None
        run, actor = await identity(authorization)
        payload = request.model_dump()
        admitted_wait = False
        try:
            if request.action == "wait":
                waits.acquire()
                admitted_wait = True
                duration = (
                    database.limits["wait_seconds"]
                    if request.wait_seconds is None
                    else min(request.wait_seconds, database.limits["wait_seconds"])
                )
                deadline = time.monotonic() + duration
                while not await database_call(
                    database.pending,
                    run,
                    actor,
                    request.conversation,
                    request.after,
                    request.recipient,
                ):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(remaining, database.limits["poll_seconds"]))
            result = await database_call(database.execute, run, actor, payload)
            return {"run_id": run, "agent_id": actor, "result": result}
        except BoardFull:
            raise HTTPException(409, "Message board full") from None
        except BoardBusy:
            raise HTTPException(429, "Board busy; try again later") from None
        except BoardError as error:
            raise HTTPException(400, str(error)) from error
        finally:
            if admitted_wait:
                waits.release()

    @app.get("/admin/export/{run}")
    async def export(
        run: str, after: int = 0, authorization: str = Header(default="")
    ) -> dict[str, Any]:
        """Export the observer journal using a credential unavailable to participant tools."""
        if not authorization.startswith("Bearer ") or not hmac.compare_digest(
            token_hash(authorization[7:]), admin_hash
        ):
            raise HTTPException(401, "Invalid credential")
        if not 0 <= after <= 2**63 - 1:
            raise HTTPException(400, "Invalid cursor")
        return await database_call(database.export, run, after)

    return app


def application() -> FastAPI:
    """Load operator credentials and database configuration only when starting the service."""
    require_unprivileged()
    credentials = json.loads(Path(os.environ["BOARD_CREDENTIALS_FILE"]).read_text())
    limits = dict(LIMITS)
    if path := os.environ.get("BOARD_LIMITS_FILE"):
        overrides = yaml.safe_load(Path(path).read_text())
        if not isinstance(overrides, dict) or set(overrides) - set(limits):
            raise ValueError("Board limits must contain known configuration keys")
        if set(overrides).intersection(
            ("request_timeout", "request_attempts", "retry_seconds", "unread_timeout")
        ):
            raise ValueError(
                "Client timing settings must remain in the shared package defaults"
            )
        limits.update(overrides)
    database = BoardDatabase(os.environ["BOARD_DATABASE_URL"], limits)
    database.provision(credentials["runs"])
    return create_app(database, credentials["admin_hash"])
