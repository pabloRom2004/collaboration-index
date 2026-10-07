"""Send count-only board reminders without delivering messages or marking them read."""

from typing import Any

from collaboration_index.board.client import BoardClient, BoardConnectionError
from collaboration_index.prompts import (
    UNREAD_DIRECT_REMINDER,
    UNREAD_REMINDER,
    UNREAD_UNAVAILABLE,
)


async def unread_reminder(
    options: dict[str, Any] | None, *, direct_only: bool = False
) -> str:
    """Describe unread traffic using the peer's own scoped credential and read tool."""
    if options is None:
        return ""
    try:
        counts = await BoardClient(options).unread()
    except BoardConnectionError:
        return UNREAD_UNAVAILABLE.prompt.format(
            read_tool="read_messages" if direct_only else "message_board"
        )
    if direct_only:
        return UNREAD_DIRECT_REMINDER.prompt.format(direct_count=counts["direct"])
    return UNREAD_REMINDER.prompt.format(
        global_count=counts["global"], direct_count=counts["direct"]
    )
