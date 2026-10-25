"""Keep trusted system-prefix identities stable across native collapse and restore."""

import copy
from types import SimpleNamespace

import pytest
from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.agent._types import DEFAULT_ASSISTANT_PROMPT
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessageSystem,
    ChatMessageUser,
    GenerateConfig,
    ModelInfo,
    ModelOutput,
    ModelUsage,
    compaction,
    get_model,
    set_model_info,
)
from inspect_ai.scorer import match
from inspect_ai.solver import solver

from collaboration_index.model_context import (
    ContextEnvelope,
    ReservedCompactionAuto,
    _restore_system_prefix,
    estimate_envelope,
    require_fitted_request,
)


@pytest.fixture
def prefix_model(monkeypatch):
    """Use the production output reservation and native provider system-collapse behavior."""
    model = get_model("mockllm/system_prefix", config=GenerateConfig(max_tokens=943718))
    set_model_info(str(model), ModelInfo(context_length=1048576, output_tokens=943718))
    monkeypatch.setattr(model.api, "collapse_system_messages", lambda: True)
    return model


def original_prefix():
    """Return two distinct trusted system instructions and an immutable task input."""
    return [
        ChatMessageSystem(content=DEFAULT_ASSISTANT_PROMPT, source="input"),
        ChatMessageSystem(
            content="\nYou are a helpful assistant solving a programming task.\n"
            "The present working directory is /workdir\n",
            source="input",
        ),
        ChatMessageUser(content="Authored immutable task request.", source="input"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("restore", [False, True])
async def test_repeated_native_compaction_keeps_one_prefix(
    prefix_model, monkeypatch, restore
):
    """Prevent prefix multiplication through actual native compaction and checkpoint restoration."""
    envelope = ContextEnvelope(1048576, 943718, 0.75)
    prefix = original_prefix()
    before = copy.deepcopy(prefix)
    history, values, callbacks, calls = list(prefix), {}, {}, []

    def track(key, callback, initial_value, **kwargs):
        """Restore only the existing native checkpoint schema."""
        callbacks[key] = callback
        return copy.deepcopy(values.get(key, initial_value))

    async def summary(input, **kwargs):
        """Produce an unchanged authored summary without network inference."""
        calls.append(True)
        return ModelOutput.from_content(str(prefix_model), "Authored private fact.")

    monkeypatch.setattr(prefix_model, "generate", summary)
    cp = SimpleNamespace(track=track)

    def handler():
        """Use the trusted original prefix for both the strategy and native handler."""
        return compaction(
            ReservedCompactionAuto(envelope, prefix=prefix),
            prefix=prefix,
            model=prefix_model,
            checkpointer=cp,
        )

    native = handler()
    for index in range(5):
        inputs, supplemental = await native.compact_input(history, force=True)
        systems = [message for message in inputs if message.role == "system"]
        assert len(systems) == 1
        assert systems[0].text == "\n".join(message.text for message in prefix[:2])
        await require_fitted_request(prefix_model, inputs, [], envelope)
        assert supplemental is not None
        history.extend(
            [supplemental, ChatMessageUser(content=f"Framework update {index}.")]
        )
        assert history[:3] == before
        if restore and index == 2:
            values.update(
                {key: copy.deepcopy(callback()) for key, callback in callbacks.items()}
            )
            native = handler()
    assert len(calls) == 5
    assert set(callbacks) == {"compaction"}
    assert prefix == before


@pytest.mark.asyncio
async def test_restore_364_copies_preserves_private_history_without_generation(
    prefix_model, monkeypatch
):
    """Repair a historically sized merged prefix while retaining every private fact and original word."""
    prefix = original_prefix()
    block = "\n".join(message.text for message in prefix[:2])
    merged = ChatMessageSystem(
        content="\n".join([block] * 364),
        source="input",
        metadata={"combined_from": ["prior-merged", "original"]},
    )
    fact = ChatMessageUser(content="Authored retained private detail. " * 5000)
    history = [merged, prefix[2], fact]
    before = copy.deepcopy(history)
    envelope = ContextEnvelope(1048576, 943718, 0.75)
    assert await estimate_envelope(prefix_model, history, []) > envelope.trigger_tokens

    async def no_generation(*args, **kwargs):
        """Reject inference when restoring identities alone makes the view fit."""
        raise AssertionError("Exact prefix repair must not require another summary")

    monkeypatch.setattr(prefix_model, "generate", no_generation)
    native = compaction(
        ReservedCompactionAuto(envelope, prefix=prefix),
        prefix=prefix,
        model=prefix_model,
    )
    inputs, supplemental = await native.compact_input(history, force=True)
    assert supplemental is None
    assert [message.text for message in inputs if message.role == "system"] == [block]
    assert inputs[-1] == fact and inputs[-2] == prefix[2]
    assert history == before
    await require_fitted_request(prefix_model, inputs, [], envelope)


@pytest.mark.parametrize(
    "alteration", ["extra_instruction", "missing_provenance", "second_system"]
)
def test_prefix_repair_retains_unproven_content(alteration):
    """Preserve distinct instructions and repeated text without native merge provenance."""
    prefix = original_prefix()
    block = "\n".join(message.text for message in prefix[:2])
    merged = ChatMessageSystem(
        content="\n".join([block] * 3),
        source="input",
        metadata={"combined_from": ["a", "b"]},
    )
    messages = [merged, prefix[2]]
    if alteration == "extra_instruction":
        merged.content += "\nDistinct critical instruction."
    elif alteration == "missing_provenance":
        merged.metadata = None
    else:
        messages.insert(1, ChatMessageSystem(content="Distinct critical instruction."))
    before = copy.deepcopy(messages)
    after, removed = _restore_system_prefix(messages, prefix[:2])
    assert after == before and removed == 0


def test_mock_eval_native_prefix_scoring_and_accounting(
    prefix_model, tmp_path, monkeypatch
):
    """Run repeated native compaction, ordinary generation and scoring through Inspect with real usage events."""
    outputs = [
        ModelOutput.from_content(str(prefix_model), text)
        for text in ["Authored private fact."] * 5 + ["done"]
    ]
    for output in outputs:
        output.usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    model = get_model(
        str(prefix_model),
        config=GenerateConfig(max_tokens=943718),
        custom_outputs=outputs,
    )
    monkeypatch.setattr(model.api, "collapse_system_messages", lambda: True)

    @solver
    def compacted_turns():
        """Drive the native compaction path without altering original private messages."""

        async def solve(state, generate):
            """Perform five scripted compactions and one ordinary scored model turn."""
            prefix = original_prefix()
            history = list(prefix)
            native = compaction(
                ReservedCompactionAuto(
                    ContextEnvelope(1048576, 943718, 0.75), prefix=prefix
                ),
                prefix=prefix,
                model=model,
            )
            for index in range(5):
                inputs, supplemental = await native.compact_input(history, force=True)
                assert inputs[0].text == "\n".join(
                    message.text for message in prefix[:2]
                )
                assert supplemental is not None
                history.extend(
                    [
                        supplemental,
                        ChatMessageUser(content=f"Framework update {index}."),
                    ]
                )
            state.messages = inputs
            return await generate(state)

        return solve

    log = inspect_eval(
        Task(
            dataset=[Sample(input="Authored task", target="done")],
            solver=compacted_turns(),
            scorer=match(),
        ),
        model=model,
        log_dir=str(tmp_path),
        display="none",
    )[0]
    assert log.status == "success" and log.samples[0].error is None
    assert log.samples[0].scores["match"].value == "C"
    assert log.stats.model_usage[str(model)].total_tokens == 120
    assert (
        len([event for event in log.samples[0].events if event.event == "model"]) == 6
    )
