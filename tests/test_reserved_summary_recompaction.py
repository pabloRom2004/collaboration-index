"""Reproduce native summary estimates that fit Inspect but exceed the reserved-input guard."""

import copy
from types import SimpleNamespace

import pytest
from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import Sample
from inspect_ai.model import (
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
from inspect_ai.tool import ToolInfo, ToolParams

from collaboration_index.model_context import (
    ContextEnvelope,
    ContextEnvelopeExceeded,
    ReservedCompactionAuto,
    estimate_envelope,
    require_fitted_request,
)


@pytest.fixture
def reserved_model():
    """Keep the original full-output context policy on a scripted local model."""
    model = get_model(
        "mockllm/reserved_recompaction", config=GenerateConfig(max_tokens=943718)
    )
    set_model_info(str(model), ModelInfo(context_length=1048576, output_tokens=943718))
    return model


async def summary_text(model, envelope, target):
    """Construct a deterministic summary with native and conservative counts on opposite sides."""
    low, high = 1, 100000
    while low < high:
        middle = (low + high) // 2
        messages = [ChatMessageUser(content="authored fact " * middle)]
        if await estimate_envelope(model, messages, []) < target:
            low = middle + 1
        else:
            high = middle
    text = "authored fact " * low
    messages = [ChatMessageUser(content=text)]
    assert await model.count_tokens(messages) < envelope.trigger_tokens
    assert await estimate_envelope(model, messages, []) >= target
    return text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "growth_then_fit", "no_progress", "bounded"])
@pytest.mark.parametrize("with_tools", [False, True])
async def test_conservative_summary_iteration(
    reserved_model, monkeypatch, failure, with_tools
):
    """Allow stochastic growth before fitting while bounding persistently oversized rewrites."""
    envelope = ContextEnvelope(1048576, 943718, 0.75)
    targets = {
        None: [79457, 1000],
        "growth_then_fit": [78700, 78776, 1000],
        "no_progress": [79457, 79457, 79457, 79457],
        "bounded": [84000, 83000, 82000, 81000],
    }[failure]
    texts = [await summary_text(reserved_model, envelope, n) for n in targets]
    calls = []
    tools = (
        [ToolInfo(name="fixture", description="schema " * 500, parameters=ToolParams())]
        if with_tools
        else []
    )

    async def scripted(input, **kwargs):
        """Return only the authored summaries after checking each original input allowance."""
        assert len(calls) < len(texts), "Compaction exceeded its safe iteration bound"
        assert reserved_model.config.max_tokens == 943718
        assert (
            await estimate_envelope(reserved_model, input, []) <= envelope.input_tokens
        )
        text = texts[len(calls)]
        calls.append(text)
        return ModelOutput.from_content(str(reserved_model), text)

    monkeypatch.setattr(reserved_model, "generate", scripted)
    history = [ChatMessageUser(content="Authored invariant request.")]
    before = copy.deepcopy(history)
    native = compaction(
        ReservedCompactionAuto(envelope),
        prefix=history,
        model=reserved_model,
        tools=tools,
    )
    if failure in ("no_progress", "bounded"):
        with pytest.raises(ContextEnvelopeExceeded, match="Compaction remains"):
            await native.compact_input(history, force=True)
    else:
        fitted, supplemental = await native.compact_input(history, force=True)
        await require_fitted_request(reserved_model, fitted, tools, envelope)
        assert supplemental is not None
    assert len(calls) == len(texts)
    assert history == before


@pytest.mark.asyncio
async def test_fitted_summary_checkpoint_roundtrip(reserved_model, monkeypatch):
    """Restore a fitted native view without repeating either completed summary or changing history."""
    envelope = ContextEnvelope(1048576, 943718, 0.75)
    oversized = await summary_text(reserved_model, envelope, 79457)
    calls, values, callbacks = [], {}, {}

    async def scripted(input, **kwargs):
        """Produce one oversized and then one fitted native summary on the same model."""
        calls.append(True)
        return ModelOutput.from_content(
            str(reserved_model),
            oversized if len(calls) == 1 else "Authored fitted summary.",
        )

    def track(key, callback, initial_value, **kwargs):
        """Capture the existing native compaction schema without adding recovery fields."""
        callbacks[key] = callback
        return values.get(key, initial_value)

    monkeypatch.setattr(reserved_model, "generate", scripted)
    prefix = [ChatMessageUser(content="Authored immutable prefix.")]
    history = list(prefix)
    cp = SimpleNamespace(track=track)
    native = compaction(
        ReservedCompactionAuto(envelope), prefix, model=reserved_model, checkpointer=cp
    )
    fitted, supplemental = await native.compact_input(history, force=True)
    assert len(calls) == 2 and supplemental is not None
    history.append(supplemental)
    output = ModelOutput.from_content(str(reserved_model), "Authored turn.")
    output.usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    await native.record_output(fitted, output)
    values.update(
        {key: copy.deepcopy(callback()) for key, callback in callbacks.items()}
    )
    restored = compaction(
        ReservedCompactionAuto(envelope), prefix, model=reserved_model, checkpointer=cp
    )
    after, extra = await restored.compact_input(history)
    assert after == fitted and extra is None and len(calls) == 2
    assert set(values) == {"compaction"}


@pytest.mark.parametrize("token_limit", [None, 30])
def test_mock_eval_compaction_metering(reserved_model, tmp_path, token_limit):
    """Exercise native logging, scoring and response-boundary token limits through the real model API."""
    import asyncio

    envelope = ContextEnvelope(1048576, 943718, 0.75)
    oversized = asyncio.run(summary_text(reserved_model, envelope, 79457))
    outputs = [
        ModelOutput.from_content(str(reserved_model), content)
        for content in [oversized, "Authored fitted summary.", "done"]
    ]
    for output in outputs:
        output.usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    model = get_model(
        str(reserved_model),
        config=GenerateConfig(max_tokens=943718),
        custom_outputs=outputs,
    )

    @solver
    def fitted_turn():
        """Run the genuine reserved strategy before the ordinary scripted model turn."""

        async def solve(state, generate):
            """Keep the real model API and native transcript active for every summary and turn."""
            native = compaction(
                ReservedCompactionAuto(envelope), prefix=state.messages, model=model
            )
            fitted, _ = await native.compact_input(state.messages, force=True)
            await require_fitted_request(model, fitted, [], envelope)
            state.output = await model.generate(fitted)
            return state

        return solve

    log = inspect_eval(
        Task(
            dataset=[Sample(input="Authored invariant request.", target="done")],
            solver=fitted_turn(),
            scorer=match(),
            token_limit=token_limit,
        ),
        model=model,
        log_dir=str(tmp_path),
        display="none",
    )[0]
    assert log.status == "success" and log.samples[0].error is None
    events = [e for e in log.samples[0].events if e.event == "model"]
    assert len(events) == (3 if token_limit is None else 2)
    assert all(e.config.max_tokens == 943718 for e in events)
    assert log.stats.model_usage[str(model)].total_tokens == 20 * len(events)
    if token_limit is None:
        assert log.samples[0].scores["match"].value == "C"
    else:
        assert log.samples[0].limit.type == "token"


@pytest.mark.asyncio
async def test_iteration_tokenizer_failure_is_not_retried(reserved_model, monkeypatch):
    """Preserve unrelated tokenizer errors and restore the scoped summary-model binding."""
    import collaboration_index.model_context as context

    envelope = ContextEnvelope(1048576, 943718, 0.75)
    calls = []
    original_estimate = context.estimate_envelope

    async def scripted(input, **kwargs):
        """Complete one authored native summary before the failing validation."""
        calls.append(True)
        return ModelOutput.from_content(str(reserved_model), "Authored summary.")

    async def failing(model, messages, tools):
        """Fail only the post-generation estimate without interfering with input fitting."""
        if calls:
            raise RuntimeError("authored tokenizer failure")
        return await original_estimate(model, messages, tools)

    monkeypatch.setattr(reserved_model, "generate", scripted)
    monkeypatch.setattr(context, "estimate_envelope", failing)
    strategy = ReservedCompactionAuto(envelope)
    original_summary_model = strategy._summary.model
    with pytest.raises(RuntimeError, match="authored tokenizer failure"):
        await strategy.compact(
            reserved_model, [ChatMessageUser(content="Authored invariant request.")], []
        )
    assert calls == [True] and strategy._summary.model is original_summary_model
