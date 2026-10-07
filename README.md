# Collaboration Index

Three Inspect AI prototypes measure emergent collaboration using one
authenticated message board and a portable replay: a batch of Humanity's Last
Exam questions, ordered counting, and spelling from private character hands.
One team attempt is one Inspect sample and one `.eval`: N concurrent native
Inspect subagents share one Docker sandbox, with separate model histories and
per-agent budgets. Every benchmark uses the same board client and visualiser.

## Usage

### Installation

```bash
uv sync --extra hle
uv run collaboration-smoke --agents 8
uv run collaboration-view --artifacts run-artifacts/mock-suite
uv run pytest
uv run pytest -m docker
```

Docker must be running for the default smoke and the Docker-marked test. The
unit suite also exercises 1, 2, 4, 8, 16 and 32 peers without containers.

The smoke command uses authored fixtures and mock models, with no paid calls.
Real runs require an explicit model, per-agent token budget, and appropriate
provider configuration; HLE also requires a bound `grader` model role.

### Running evaluations

Task IDs are `collaboration_index/hle_collaboration`,
`collaboration_index/counting`, and `collaboration_index/spelling`. Use Inspect's
native `--run-config` and `-T` options. A complete team, not each question or
agent, is the unit selected by `--limit`.

```bash
uv run inspect eval collaboration_index/counting --model MODEL \
  -T agents=8 -T token_limit_per_agent=100000 --log-dir logs
uv run inspect view --log-dir logs
```

These commands describe the interface and are not authorization for paid runs.

### New benchmark starter

An installed example lives in `templates/example-exam`; it passed a real Docker
mock smoke. Generate another project with the same infrastructure:

```bash
uv run collaboration-new /absolute/path/my-benchmark
cd /absolute/path/my-benchmark
uv sync
uv run python smoke.py
```

The starter owns its task, question data and `run_configs/default.yaml`. It
imports the shared core instead of copying the board or visualiser. It is a
numbered-answer starter; new game mechanics still need a trusted tool/scorer
implementation and end-to-end mock validation. The installed example's replay
is `templates/example-exam/run-artifacts/replay.html`.

### Configurations

Each evaluation owns `run_configs/default.yaml`. Defaults select two agents,
one epoch and emergent collaboration. The model, token budget and HLE judge
are intentionally operator-supplied. `condition=oracle_allocation` supplies
assignments and omits the board from participant tools, providing a matched
allocation baseline without delegation. It is not a proven optimal strategy.

These are new collaboration variants. There is no claimed original configuration
or reproduction of historic Multi-Agent-Bench/HLE scores.

## Environment

One minimal unprivileged Docker container serves the whole team. It has no
network, a read-only root filesystem, dropped capabilities and an ephemeral
workspace; every peer verifies the same hostname and run file. The 2 CPU / 1 GiB
allocation is a bounded prototype setting, not a measured requirement for future
command-heavy benchmarks. `sandbox_enabled=false` is a local unit-test control;
real prototype runs default to the shared sandbox.

The board is a separate loopback service process with its own SQLite database.
Only credential hashes reach its files. Peer tool closures receive scoped
credentials; the service does not inherit model or cloud credentials. No shell
or arbitrary host-file tool is available. HLE's public `questions.json` can be
read through a restricted file tool; reference answers stay in the controller.

Keep `logs/` flat and `.eval`-only. Supporting state, board journals, question
files and HTML replays belong under `run-artifacts/`.

## Parameters

Common controls include `agents`, `condition`, `seed`, `token_limit_per_agent`,
`team_time_limit`, and `agent`/`agent_args`. Team sizes 1–32 are supported for
the prototype; one agent is useful as a control. The task rejects a missing
positive token budget before starting inference.

HLE selects pinned CAIS data, optionally intersected with audited gold IDs;
the prototype is text-only. CAIS HLE is gated on Hugging Face: approve its dataset
access and provide an authorized HF login before loading real records. An
authenticated preflight verified both revision pins and loaded 575 gold/text
questions successfully. Dataset contents remain in the private local cache and
are not committed. `records_file` loads a trusted
local fixture.
Questions are numbered and submitted with `submit_answer(question_number,
answer)`. First accepted answers are final, and correctness is hidden until
scoring. Unanswered questions receive no credit; judge failures remain
explicitly unscored and cannot become model errors or zero capability.

Counting defaults to a fixed target of 64 and a per-agent quota of ceil(64/N).
Arrival order is irreversible and hidden. Spelling preserves the old private
reusable-character-hands mechanic and scores against the closest shown sentence;
only an agent holding return can end the line. Its candidate count is
min(2N,100), as in the source; candidate work changes across team sizes. Set
`candidate_count` to the same positive value across N to hold the shown
candidates and seeded target fixed; keep this separate from the historic
scaled-work condition. Both use the board instead of
shared text files. Dataset draws are seeded and retained in logs.

## Scoring

Report quality, completion/coverage, solving wall time, individual and total
subject tokens, communication calls, duplicate work and submission timelines
separately. HLE judge time and tokens are excluded from solving costs. Completion
time ends at the last required accepted submission; peer join latency is recorded
separately. The HLE equivalence prompt is a simplified configurable prototype
judge, rather than the exact CAIS/Inspect-Evals leaderboard judge. Ordered
tasks retain edit-distance partial credit. Deadline/budget exhaustion preserves
partial work. Per-peer limits use Inspect's native response-level accounting,
so one in-flight response may exceed its remaining budget; the planned aggregate
budget is N times the per-peer budget, not a provider billing hard cap. No second
sample limit interrupts finalization; infrastructure errors raise and are not
scored as ordinary losses.

## Verification

The prototype passed 45 local tests and the shared Docker integration test.
Eight-peer authored mocks completed all three benchmarks through native Inspect,
the real sandbox, authenticated board, scorer and replay. A 32-peer counting
mock also completed in one sandbox, and the installed starter passed its Docker
smoke. Ruff, mypy and the packaged wheel checks passed. These are infrastructure
checks with synthetic model output and token usage; no paid capability evaluation
has been run. Authenticated dataset preflight loaded the pinned 575-question HLE
gold/text batch without printing questions or answers.

See [the experiment design](docs/design.md), [provenance](NOTICE.md) and the
test suite. A single collaboration index and benchmark weights remain a research
decision; this prototype does not invent an aggregate score.
