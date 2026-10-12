"""Verify full-output request fitting on harmless oversized native histories."""

import copy
from types import SimpleNamespace

import pytest
from inspect_ai._util.content import ContentReasoning
from inspect_ai.model import (
    ChatMessageAssistant,
    ChatMessageTool,
    ChatMessageUser,
    GenerateConfig,
    ModelInfo,
    ModelOutput,
    ModelUsage,
    compaction,
    get_model,
    set_model_info,
)
from inspect_ai.tool import ToolInfo, ToolParams

from collaboration_index.model_context import (
    ContextEnvelope,
    ContextEnvelopeExceeded,
    ReservedCompactionAuto,
    SummaryEnvelopeModel,
    estimate_envelope,
    fit_summary_input,
    require_fitted_request,
)


@pytest.fixture
def envelope_model():
    """Expose a harmless model with the exact problematic full-output envelope."""
    result = get_model(
        "mockllm/context_fixture", config=GenerateConfig(max_tokens=943718)
    )
    set_model_info(
        "mockllm/context_fixture",
        ModelInfo(context_length=1048576, output_tokens=943718),
    )
    return result


@pytest.fixture
def envelope():
    """Use three-quarter compaction of the remaining input capacity."""
    return ContextEnvelope(1048576, 943718, 0.75)


def test_input_capacity_and_trigger(envelope):
    """Keep true context and full output while resolving the smaller valid input trigger."""
    assert envelope.input_tokens == 104858
    assert envelope.trigger_tokens == 78643
    assert envelope.metadata()["estimated_counts_are_exact"] is False
    with pytest.raises(ValueError):
        ContextEnvelope(1048576, 1048576, 0.75)


@pytest.mark.asyncio
async def test_replayed_reasoning_and_tools_are_counted(envelope_model, envelope):
    """Catch reasoning and tool schemas omitted by a plain native message estimate."""
    messages = [
        ChatMessageAssistant(
            content=[ContentReasoning(reasoning="authored reasoning " * 60000)]
        )
    ]
    tools = [
        ToolInfo(
            name="fixture",
            description="authored schema " * 10000,
            parameters=ToolParams(),
        )
    ]
    before = await envelope_model.count_tokens(messages)
    count = await estimate_envelope(envelope_model, messages, tools)
    assert before < envelope.trigger_tokens < count
    with pytest.raises(ContextEnvelopeExceeded, match="not fitted"):
        await require_fitted_request(envelope_model, messages, tools, envelope)


@pytest.mark.asyncio
@pytest.mark.parametrize("pieces", [1, 4])
async def test_summary_tool_overshoot_fits_without_mutation(
    envelope_model, envelope, pieces
):
    """Fit one huge tool output or several outputs without changing native history objects."""
    messages = [ChatMessageUser(content="Authored invariant prompt.")]
    messages.extend(
        ChatMessageTool(
            content="authored tool fixture " * (120000 // pieces),
            tool_call_id=f"fixture-{index}",
        )
        for index in range(pieces)
    )
    original = [message.model_dump(mode="json") for message in messages]
    fitted = await fit_summary_input(envelope_model, messages, envelope)
    count = await estimate_envelope(envelope_model, fitted, [])
    assert count <= envelope.input_tokens
    assert [message.model_dump(mode="json") for message in messages] == original
    assert fitted[0] is messages[0]
    assert [message.tool_call_id for message in fitted[1:]] == [
        f"fixture-{index}" for index in range(pieces)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        ChatMessageUser(content="authored prefix " * 100000),
        ChatMessageAssistant(content="authored assistant " * 100000),
    ],
)
async def test_unfit_immutable_content_never_calls_model(
    envelope_model, envelope, monkeypatch, message
):
    """Reject an oversized invariant prefix or assistant turn without an invalid paid request."""
    calls = []

    async def unexpected(*args, **kwargs):
        """Detect any model dispatch after the fit guard should have failed."""
        calls.append(True)
        pytest.fail("Unfitted content must not be dispatched")

    monkeypatch.setattr(envelope_model, "generate", unexpected)
    original = message.model_dump(mode="json")
    with pytest.raises(RuntimeError, match="immutable messages"):
        await SummaryEnvelopeModel(envelope_model, envelope).generate([message])
    assert calls == [] and message.model_dump(mode="json") == original


@pytest.mark.asyncio
async def test_summary_keeps_model_config_and_full_output(
    envelope_model, envelope, monkeypatch
):
    """Pass the original model and full output through native summary fitting unchanged."""
    calls = []

    async def checked(input, **kwargs):
        """Record only the fitted envelope and unchanged output allowance."""
        calls.append(await estimate_envelope(envelope_model, input, []))
        assert envelope_model.config.max_tokens == 943718
        return ModelOutput.from_content(
            "mockllm/context_fixture", "Authored compact summary."
        )

    monkeypatch.setattr(envelope_model, "generate", checked)
    wrapper = SummaryEnvelopeModel(envelope_model, envelope)
    assert wrapper.api is envelope_model.api and wrapper.config is envelope_model.config
    result = await wrapper.generate(
        [ChatMessageTool(content="authored tool " * 100000, tool_call_id="fixture")]
    )
    assert result.completion == "Authored compact summary."
    assert len(calls) == 1 and calls[0] <= envelope.input_tokens


@pytest.mark.asyncio
async def test_native_compaction_restore_does_not_repeat_summary(
    envelope_model, envelope, monkeypatch
):
    """Restore native compacted state and original history without replaying successful summaries."""
    values, callbacks, calls = {}, {}, []

    def track(key, callback, initial_value, **kwargs):
        """Track the exact native state schema for a harmless restore check."""
        callbacks[key] = callback
        return values.get(key, initial_value)

    async def summary(input, **kwargs):
        """Check every fitted summary envelope before returning the authored fixture."""
        calls.append(await estimate_envelope(envelope_model, input, []))
        return ModelOutput.from_content(
            "mockllm/context_fixture", "Authored native summary."
        )

    monkeypatch.setattr(envelope_model, "generate", summary)
    prefix = [ChatMessageUser(content="Authored fixed prefix.")]
    history = prefix + [
        ChatMessageTool(
            content="authored oversized tool " * 120000, tool_call_id="fixture"
        )
    ]
    original = [message.model_dump(mode="json") for message in history]
    cp = SimpleNamespace(track=track)
    native = compaction(
        ReservedCompactionAuto(envelope),
        prefix=prefix,
        model=envelope_model,
        checkpointer=cp,
    )
    fitted, supplemental = await native.compact_input(history)
    assert supplemental is not None and len(calls) == 1
    history.append(supplemental)
    output = ModelOutput.from_content("mockllm/context_fixture", "Authored next turn.")
    output.usage = ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30)
    await native.record_output(fitted, output)
    values.update(
        {key: copy.deepcopy(callback()) for key, callback in callbacks.items()}
    )
    restored = compaction(
        ReservedCompactionAuto(envelope),
        prefix=prefix,
        model=envelope_model,
        checkpointer=cp,
    )
    after, supplemental = await restored.compact_input(history)
    assert supplemental is None and len(calls) == 1
    assert after == fitted
    assert [message.model_dump(mode="json") for message in history[:-1]] == original
    assert set(values) == {"compaction"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["envelope", "tokenizer"])
async def test_generator_only_compacts_classified_envelope_errors(
    envelope_model, envelope, monkeypatch, failure
):
    """Force only an estimated overflow while leaving unrelated tokenizer failures fatal."""
    from inspect_ai.agent import AgentState

    import collaboration_index.checkpoints as checkpoints
    from collaboration_index.checkpoints import TeamCheckpoints, TeamSnapshot

    calls, forces = [], []

    class NativeFixture:
        """Expose a harmless native handler that records forced compaction boundaries."""

        async def compact_input(self, messages, force=False):
            """Return unchanged authored messages and record explicit forced boundaries."""
            forces.append(force)
            return messages, None

        async def record_output(self, inputs, output):
            """Accept a successful authored output without touching checkpoint storage."""

    async def boundary(actor):
        """Provide a completed safe boundary without remote storage or model work."""

    async def checked(*args):
        """Raise only the selected first-call failure before any model request."""
        if len(forces) == 1:
            if failure == "envelope":
                raise ContextEnvelopeExceeded("authored estimated overflow")
            raise RuntimeError("authored tokenizer failure")
        return 20

    async def generate(*args, **kwargs):
        """Record one successful model turn after a properly fitted envelope."""
        calls.append(True)
        return ModelOutput.from_content(
            "mockllm/context_fixture", "Authored ordinary turn."
        )

    owner = object.__new__(TeamCheckpoints)
    owner.cp = SimpleNamespace()
    owner.snapshot = TeamSnapshot()
    owner.envelope, owner.current = envelope, {}
    owner.barrier = SimpleNamespace(boundary=boundary)
    monkeypatch.setattr(
        checkpoints, "compaction", lambda *args, **kwargs: NativeFixture()
    )
    monkeypatch.setattr(checkpoints, "get_model", lambda: envelope_model)
    monkeypatch.setattr(checkpoints, "require_fitted_request", checked)
    monkeypatch.setattr(envelope_model, "generate", generate)
    current = AgentState(messages=[ChatMessageUser(content="Authored fixed prompt.")])
    handler = owner.generation("fixture", 78643, None)
    if failure == "tokenizer":
        with pytest.raises(RuntimeError, match="authored tokenizer failure") as caught:
            await handler(current, [])
        assert not isinstance(caught.value, ContextEnvelopeExceeded)
        assert forces == [False] and calls == []
    else:
        await handler(current, [])
        assert forces == [False, True] and calls == [True]
