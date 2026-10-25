"""Fit checkpointed requests while preserving the configured full output allowance."""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from inspect_ai._util.notgiven import NOT_GIVEN
from inspect_ai.log import transcript
from inspect_ai.model import (
    ChatMessage,
    ChatMessageUser,
    CompactionAuto,
    GenerateConfig,
    Model,
    ModelOutput,
)
from inspect_ai.model._call_tools import get_tools_info, resolve_tools
from inspect_ai.model._compaction.summary import (
    _MAX_TRUNCATION_ITERATIONS,
    _largest_shrinkable,
    _shrink,
)
from inspect_ai.tool import ToolInfo


class ContextEnvelopeExceeded(RuntimeError):
    """Identify only an estimated context overflow that can trigger native compaction."""


@dataclass(frozen=True)
class ContextEnvelope:
    """Keep the true context and full output reservation separate from usable input."""

    context_window: int
    output_tokens: int
    compaction_threshold: float

    def __post_init__(self) -> None:
        """Reject a request envelope that leaves no input or has an invalid trigger."""
        if not 0 < self.output_tokens < self.context_window:
            raise ValueError(
                "Full output reservation must leave positive input capacity"
            )
        if not 0 < self.compaction_threshold < 1:
            raise ValueError("Reserved-input compaction threshold must be within (0,1)")

    @property
    def input_tokens(self) -> int:
        """Return the input capacity remaining after the complete output reservation."""
        return self.context_window - self.output_tokens

    @property
    def trigger_tokens(self) -> int:
        """Leave configured headroom below the input capacity for estimation differences."""
        return int(self.input_tokens * self.compaction_threshold)

    def metadata(self) -> dict[str, Any]:
        """Describe the policy without claiming that estimated token counts are exact."""
        return {
            "context_window": self.context_window,
            "full_output_tokens": self.output_tokens,
            "usable_input_tokens": self.input_tokens,
            "compaction_threshold": self.compaction_threshold,
            "compaction_trigger_tokens": self.trigger_tokens,
            "estimator": "maximum of native messages plus tools and serialized envelope including reasoning; provider text tokenizer",
            "additional_envelope_estimate_buffer": 0.1,
            "estimated_counts_are_exact": False,
            "summary_tool_fitting": "native middle truncation of copied tool outputs only",
        }


async def estimate_envelope(
    model: Model, messages: list[ChatMessage], tools: list[ToolInfo]
) -> int:
    """Count message framing, replayed reasoning and complete tool schemas conservatively."""
    native = await model.count_tokens(messages) + await model.count_tool_tokens(tools)
    wire = {
        "messages": [
            message.model_dump(
                mode="json", exclude={"id", "metadata", "source"}, exclude_none=True
            )
            for message in messages
        ],
        "tools": [
            {
                "type": "function",
                "function": tool.model_dump(mode="json", exclude_none=True),
            }
            for tool in tools
        ],
    }
    serialized = await model.api.count_text_tokens(json.dumps(wire, ensure_ascii=False))
    # Providers may already buffer their token estimates; this additional
    # margin also covers framing and tokenizer differences in summary requests.
    return math.ceil(1.1 * max(native, serialized))


async def require_fitted_request(
    model: Model,
    messages: list[ChatMessage],
    tools: list[Any],
    envelope: ContextEnvelope,
    *,
    summary: bool = False,
) -> int:
    """Reject an oversized estimated request before sending an invalid provider call."""
    info = get_tools_info(await resolve_tools(tools))
    count = await estimate_envelope(model, messages, info)
    limit = envelope.input_tokens if summary else envelope.trigger_tokens
    if count > limit:
        raise ContextEnvelopeExceeded(
            f"Reserved-input request is not fitted: estimated {count} input tokens exceed "
            f"{limit} limit ({envelope.input_tokens} input capacity)"
        )
    return count


async def fit_summary_input(
    model: Model, messages: list[ChatMessage], envelope: ContextEnvelope
) -> list[ChatMessage]:
    """Reuse native copied-tool fitting against the actual remaining input allowance."""
    fitted = list(messages)
    for _ in range(_MAX_TRUNCATION_ITERATIONS + 1):
        count = await estimate_envelope(model, fitted, [])
        if count <= envelope.input_tokens:
            return fitted
        candidate = _largest_shrinkable(fitted)
        if candidate is None:
            break
        _shrink(fitted, candidate)
    raise RuntimeError(
        f"Reserved-input summary cannot fit immutable messages: estimated {count} "
        f"input tokens exceed {envelope.input_tokens} input capacity"
    )


class SummaryEnvelopeModel(Model):
    """Keep native summary generation on its original model with reservation-aware fitting."""

    def __init__(self, target: Model, envelope: ContextEnvelope) -> None:
        """Share the exact model API and generation configuration without creating a new route."""
        super().__init__(target.api, target.config, target.model_args)
        self.target, self.envelope = target, envelope

    async def generate(
        self,
        input: str | list[ChatMessage],
        tools: Any = [],
        tool_choice: Any = None,
        config: GenerateConfig = GenerateConfig(),
        cache: Any = NOT_GIVEN,
        on_stream: Any = None,
    ) -> ModelOutput:
        """Fit only native summary tool copies, preserving the full output and original history."""
        if tools or config.model_dump(exclude_none=True):
            raise RuntimeError(
                "Native summary envelope expects the unchanged model configuration"
            )
        messages: list[ChatMessage] = (
            [ChatMessageUser(content=input)] if isinstance(input, str) else input
        )
        fitted = await fit_summary_input(self.target, messages, self.envelope)
        count = await require_fitted_request(
            self.target, fitted, [], self.envelope, summary=True
        )
        transcript().info(
            {
                "reserved_input_request": "summary",
                "estimated_input_tokens": count,
                **self.envelope.metadata(),
            }
        )
        return await self.target.generate(
            fitted, tool_choice=tool_choice, cache=cache, on_stream=on_stream
        )


def _restore_system_prefix(
    messages: list[ChatMessage], prefix: Sequence[ChatMessage]
) -> tuple[list[ChatMessage], int]:
    """Restore exact trusted prefix identities after native system-message merging."""
    systems = [message for message in messages if message.role == "system"]
    if (
        not prefix
        or len(systems) != 1
        or any(not isinstance(message.content, str) for message in prefix)
    ):
        return messages, 0
    merged = systems[0]
    if not isinstance(merged.content, str) or not (merged.metadata or {}).get(
        "combined_from"
    ):
        return messages, 0
    block = "\n".join(message.text for message in prefix)
    unit = block + "\n"
    copies = (len(merged.content) + 1) // len(unit)
    if copies < 1 or merged.content + "\n" != unit * copies:
        return messages, 0
    restored: list[ChatMessage] = []
    for message in messages:
        if message is merged:
            restored.extend(item.model_copy(deep=True) for item in prefix)
        else:
            restored.append(message)
    return restored, copies - 1


class ReservedCompactionAuto(CompactionAuto):
    """Preserve native compaction behavior while fitting its summary requests correctly."""

    def __init__(
        self, envelope: ContextEnvelope, *, prefix: list[ChatMessage] | None = None
    ) -> None:
        """Use the configured fraction of usable input as the native compaction trigger."""
        super().__init__(threshold=envelope.trigger_tokens)
        self.envelope = envelope
        self._system_prefix = [
            message.model_copy(deep=True)
            for message in prefix or []
            if message.role == "system"
        ]

    async def compact(
        self, model: Model, messages: list[ChatMessage], tools: list[ToolInfo]
    ) -> tuple[list[ChatMessage], ChatMessageUser | None]:
        """Fit native summaries against the same conservative envelope as ordinary requests."""
        if model.api.collapse_system_messages():
            messages, removed = _restore_system_prefix(messages, self._system_prefix)
            if removed:
                transcript().info(
                    {"native_system_prefix_duplicate_copies_removed": removed}
                )
                if (
                    await estimate_envelope(model, messages, tools)
                    <= self.envelope.trigger_tokens
                ):
                    return messages, None
        original = self._summary.model
        self._summary.model = SummaryEnvelopeModel(model, self.envelope)
        try:
            inputs, summary = await super().compact(model, messages, tools)
            if model.api.collapse_system_messages():
                inputs, _ = _restore_system_prefix(inputs, self._system_prefix)
            count = await estimate_envelope(model, inputs, tools)
            # A stochastic rewrite can grow before a later one fits. Use the
            # bounded three further attempts without relaxing the input trigger.
            for _ in range(3):
                if count <= self.envelope.trigger_tokens:
                    return inputs, summary
                inputs, summary = await super().compact(model, list(inputs), tools)
                if model.api.collapse_system_messages():
                    inputs, _ = _restore_system_prefix(inputs, self._system_prefix)
                count = await estimate_envelope(model, inputs, tools)
            if count > self.envelope.trigger_tokens:
                raise ContextEnvelopeExceeded(
                    f"Compaction remains above the reserved-input trigger: estimated "
                    f"{count} input tokens exceed {self.envelope.trigger_tokens} limit "
                    f"({self.envelope.input_tokens} input capacity)"
                )
            return inputs, summary
        finally:
            self._summary.model = original
