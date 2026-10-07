# Collaboration Index

Collaboration Index is an Inspect AI research prototype for measuring how well
multiple copies of a model coordinate. It currently contains four tasks:
[Humanity's Last Exam](https://huggingface.co/datasets/cais/hle) answered as a
team, ordered counting, spelling using private character hands, and graph
colouring where each agent is a network node that can only DM its neighbours.
Counting, spelling and colouring adapt tasks from Multi-Agent-Bench. Communication
and replay reuse the authenticated message board developed in ExploitBench.

The initial question is whether a team can divide work, share information and
sequence actions efficiently without a prescribed leader or delegation
protocol. The longer-term aim is a suite of roughly 5–10 tasks, evaluated with
teams of 2–32 agents, from which a defensible collaboration index or ECI might
be estimated. A single aggregate index is **not implemented yet**. Quality,
completion, wall time, tokens and communication are retained separately so that
normalization and weighting can follow measured evidence.

One team attempt is one Inspect sample. All N peers are native Inspect subagents
inside the same controller, sharing **one Docker sandbox and one `.eval` file
for that task invocation**. Each peer has its own model history and token
budget. Every collaborative task uses the same board API and visualiser. The
oracle allocation control intentionally withholds the communication tool.

This is a new collaboration variant, not a reproduction of standard HLE
leaderboard scores or historical Multi-Agent-Bench results. See
[the measurement contract](docs/design.md) and [provenance](NOTICE.md).

## Usage

### Installation

The current project is a local source checkout. It has no published package or
configured remote repository. Use Python 3.12, `uv`, and a running Docker daemon:

```bash
cd /Users/pabloromero/Documents/Generality-Labs/Benchmark-Audits/collaboration-index
uv sync --extra hle --group dev
docker version
uv run python -c 'from importlib.metadata import version; print(version("inspect-ai"))'
```

The lockfile and project pin Inspect AI to **0.3.277**. The `hle` extra installs
dataset loading; it is unnecessary for authored fixture questions. The dev
group provides pytest, Ruff and mypy. On another machine, replace the `cd` path
with the checkout's location. Do not change the global Docker context to run
these tasks; use the intended daemon explicitly if more than one is configured.

Another local project can depend on this source checkout through an editable
`uv` path dependency. The installed starter below demonstrates that arrangement.
The wheel also includes task configs, the sandbox definition, sentence pool,
visualiser and starter assets; no external ExploitBench package is required.

### Start with the free infrastructure smoke

```bash
uv run collaboration-smoke --agents 8
uv run collaboration-view --artifacts run-artifacts/mock-suite --port 14368
```

Open <http://127.0.0.1:14368/>. The four links show the HLE fixture, counting,
spelling and colouring replays. Each displays peer identities, collective
progress, messages and token counts. The first three draw one collective
computer with a global board; colouring draws the network as coloured dots, and
clicking a dot opens that agent's DMs with its neighbours.

The smoke uses **authored questions and scripted mock model output**. It runs
the real native-agent orchestration, Docker sandbox, board HTTP service,
submission tools, scorer and renderer. Its token usage is synthetic. Fixture
scripts know the answers and coordinate their tool actions externally; success
tests infrastructure, not emergent collaboration or model capability. The
command retains replays and a verification receipt, then deletes only the exact
mock `.eval` files it created.

For a container-free development control:

```bash
uv run collaboration-smoke --agents 8 --no-sandbox
```

That control still exercises agents and the board, but cannot establish that
peers shared an actual container. Use the Docker smoke before relying on the
shared-computer condition.

### Running evaluations

Registered task IDs are:

| Task ID | Work in one team sample |
| --- | --- |
| `collaboration_index/hle_collaboration` | The entire selected numbered exam |
| `collaboration_index/counting` | One shared ordered sequence |
| `collaboration_index/spelling` | One shared sentence attempt |
| `collaboration_index/colouring` | One hidden network to colour |

`--limit 1` selects a team sample, not one HLE question and not one peer. Use
`question_limit` to reduce the exam. Calling the four task factories separately
creates four task logs; the N peers within each task do not get N `.eval` files.
Repeated epochs remain repeated team attempts, with fresh boards and stores.

Real model runs require an explicitly selected subject model, a positive
per-agent token budget and verified provider credentials. HLE's default judge
also requires an explicitly bound `grader` role. No subject, judge, paid token
budget or reasoning effort is silently selected. The following are interface
examples **to use after a real run has been authorized and its provider route
verified**; this documentation update does not launch them.

Set `CI_SUBJECT_MODEL` to a valid Inspect model identifier and `CI_AGENT_TOKENS`
to the authorized per-peer ceiling. Then a counting run is:

```bash
uv run inspect eval \
  --run-config src/collaboration_index/counting/run_configs/default.yaml \
  --model "${CI_SUBJECT_MODEL:?Set the authorized subject model}" \
  -T agents=8 \
  -T token_limit_per_agent="${CI_AGENT_TOKENS:?Set the authorized per-agent budget}" \
  -T artifact_dir=run-artifacts/counting-team \
  --log-dir logs
```

A small real-data HLE run additionally uses `CI_GRADER_MODEL`:

```bash
uv run inspect eval \
  --run-config src/collaboration_index/hle/run_configs/default.yaml \
  --model "${CI_SUBJECT_MODEL:?Set the authorized subject model}" \
  --model-role "grader=${CI_GRADER_MODEL:?Set the authorized grader model}" \
  -T agents=8 \
  -T question_limit=10 \
  -T token_limit_per_agent="${CI_AGENT_TOKENS:?Set the authorized per-agent budget}" \
  -T artifact_dir=run-artifacts/hle-team \
  --log-dir logs
```

The 10-question example is a small selection, not a default. Omit that override
to select the full maintained gold/text batch. Keep the same selection across
team sizes and controls. The current HLE scorer grades accepted answers
sequentially after the peers finish; grading a full batch can take appreciable
additional time even when solving has ended.

For spelling, substitute its config path and consider a fixed `candidate_count`
when comparing different N. Provider concurrency also matters: N peer tasks are
released together, but a provider connection limit or rate limit can serialize
inference. Record generation and connection settings when comparing teams.

The Python interface is supported as well:

```python
import os
from inspect_ai import eval
from collaboration_index.counting import counting

task = counting(
    agents=8,
    token_limit_per_agent=int(os.environ["CI_AGENT_TOKENS"]),
    artifact_dir="run-artifacts/counting-team",
)
logs = eval(task, model=os.environ["CI_SUBJECT_MODEL"], log_dir="logs")
```

Inspect's [task and run-config documentation](https://inspect.aisi.org.uk/tasks.html#run-config-file)
describes its native CLI and model-role interface. Project-specific behavior is
defined by the source and YAML in this repository.

### Configurations

Each task owns a complete maintained configuration:

- [HLE](src/collaboration_index/hle/run_configs/default.yaml)
- [Counting](src/collaboration_index/counting/run_configs/default.yaml)
- [Spelling](src/collaboration_index/spelling/run_configs/default.yaml)
- [Colouring](src/collaboration_index/colouring/run_configs/default.yaml)

Public Python defaults are read from these files. CLI overrides take precedence
when using `--run-config`. Keep task arguments under `task.args`, subject
configuration under `model`/`generate_config`, judge bindings under
`model_roles`, and framework settings under `eval_config`. Do not put `epochs`
in `task.args`; use `--epochs` or `eval_config.epochs`.

For an experiment, copy a complete config to `run-artifacts/<experiment>/` and
record the effective settings and source commit. Committed `run_configs/`
directories contain maintained `default.yaml` and, only when a verified original
configuration warrants it, `original.yaml`. These are new collaboration tasks,
so there is no fabricated original configuration or claimed upstream parity.

The common default is two peers, one epoch, collaborative communication,
native ReAct and 75% context compaction. `model`, grader bindings and the token
ceiling are operator-supplied. Verify the actual model context and provider
route before a real-model launch; the YAML fraction alone does not establish
provider compatibility.

Task `seed` and model generation seed are separate. Spelling uses task seed to
draw candidates and hands, and colouring uses it to draw the network. Counting uses it as an attempt identifier, and HLE
retains it as metadata without shuffling the pinned batch. Varying that argument
alone does not produce new counting problems or randomized HLE selections.

### Controls and team-size comparisons

`condition=collaborative` gives every peer equal tools and the same collaboration
instructions, with no leader or assigned work. Private spelling hands differ
because complementary information is the task's mechanic.

`condition=oracle_allocation` supplies an evaluator allocation and removes the
board tool. HLE assigns question numbers round-robin. Counting assigns
consecutive blocks. Spelling supplies a feasible target and character owners.
Colouring gives each peer its node's colour in the hidden planted colouring.
Ordered controls receive `oracle_progress`, which reveals only accepted prefix
length so they can sequence actions without messaging.

This is an allocation control, **not a proven optimum**. It has extra task
information, ignores unknown per-question difficulty and model latency, and can
have lower accuracy than collaboration. HLE assignment ownership is enforced
by its submission tool; ordered tasks retain their quota/hand restrictions and
use the disclosed assignment protocol. Do not describe all assignments as
equally privileged to the collaborative condition.

Use one peer as a baseline and compare N = 2, 4, 8, 16, 32 across matched
model/provider, task work, seed, generation settings and budget conventions.
At a fixed per-agent ceiling, the planned aggregate ceiling grows with N. A
fixed-total-budget comparison is a different experiment. Counting defaults to
fixed work; spelling's historical candidate count scales with N unless overridden.
The current prototype implements the shared-sandbox topology only. Independent
computers are a future matched ablation, not a hidden mode already implemented.

### Viewing and preserving results

Native logs can be opened with:

```bash
uv run inspect view --log-dir logs
```

Keep the repository's `logs/` flat and containing only `.eval` files. Preserve
real evaluation logs. Config snapshots, board databases/journals, receipts and
HTML belong under `run-artifacts/`. The team setup creates a unique
`team-<uuid>` directory inside the selected artifact directory.

To render a retained run, set `CI_EVAL_FILE` to its `.eval` path:

```bash
uv run collaboration-replay \
  --eval "${CI_EVAL_FILE:?Set the retained evaluation path}" \
  --html run-artifacts/replays/team.html
uv run collaboration-view --artifacts run-artifacts/replays --port 14368
```

The sample store keeps the exported board journal, so a remote run whose
artifact directory was discarded can still be replayed. Older logs lack it; pass
their `board.jsonl` with `--journal`.

Create the output directory first if necessary. For repeated epochs, add
`--epoch <number>` and, when needed, `--sample <sample-id>` to select exactly one
team attempt. The renderer verifies run and actor identities; a journal from
another attempt must fail rather than produce a plausible mixed replay.

The visualiser exports board events, trusted submissions, statuses and team
measurements. It does not export private model histories or fabricate individual
benchmark grades. HLE correctness appears after final grading. Saved replays
show authored mock provenance when applicable. Board messages and submissions
are still run data; the HTML is not a guarantee of content redaction.

### New benchmark starter

An installed example is in [templates/example-exam](templates/example-exam/README.md).
It has its own task, question file, YAML, local environment and smoke script:

```bash
cd templates/example-exam
uv sync
uv run python smoke.py
```

Its portable replay is `run-artifacts/replay.html` inside that project. Generate
a separate starter from the core checkout with:

```bash
uv run collaboration-new /absolute/path/my-benchmark
cd /absolute/path/my-benchmark
uv sync
uv run python smoke.py
```

Use `--core-path /absolute/path/collaboration-index` when creating from an
installed package or moving machines. The generated dependency points to that
source checkout; it is not an independently published package.

Replace the starter's trusted `questions.json` with records containing `id`,
`question` and `answer`. Optional `answer_type` is retained as metadata. The
loader assigns contiguous question numbers. Nonempty strings and unique IDs
are required; image records are excluded. Reference answers remain in the
controller, while only public fields reach the participant file/tool. The
starter's exact judge is appropriate for authored fixtures; open-ended answers
need `hle_json_judge` and a bound grader, or a separately validated scorer.

The starter reuses the installed board, harness, scorer and renderer. It is a
numbered-answer template. A different game mechanic requires trusted tools,
typed state, scoring and replay support in the core; changing only the question
file does not create an arbitrary game plugin. See
[architecture and extension contracts](docs/architecture.md).

## Security Considerations

### Task files and private references

The HLE batch lives in the shared `/workspace/questions.json`; peers can read
the whole batch or numbered ranges through the restricted `read_file` tool.
`team.json` identifies the shared attempt. No arbitrary file or shell tool is
exposed to these toy-task agents. Files are not an alternate communication
channel. Submission and board tools execute trusted controller-side Python.

Answers, other agents' private spelling hands and the colouring graph beyond
a peer's own neighbours are excluded from participant inputs. They are retained in trusted task metadata for scoring, so a native
`.eval` log is **not** a public, sanitized artifact. Keep credentials out of
logs, Git, receipts and model-visible files. Do not print real HLE records when
checking access or loading: report revisions, counts and hashes.

### Docker sandbox

The [Compose definition](src/collaboration_index/assets/sandbox/compose.yaml)
pins `python:3.12-slim` by digest. It runs as UID/GID 65534, with no network,
a read-only root filesystem, all capabilities dropped, no new privileges,
2 CPUs and a 1 GiB memory limit. `/workspace` and `/tmp` are bounded tmpfs mounts.
Every peer verifies the same hostname and run file before inference begins.

These are bounded prototype settings, not measured minimum resources for
arbitrary future workloads. Real grading, provider concurrency, cold/warm caches
and peak CPU/RAM/disk use need measurement before scaling unfamiliar tasks.

Hawk's Compose conversion rejects several of these keys. `sandbox_type=k8s`
selects [Helm values](src/collaboration_index/assets/sandbox/values.yaml) for the
`inspect_k8s_sandbox` chart with the same image, user, limits and 16 MiB tmpfs
mounts. `networkIsolated` denies all egress through Cilium. Hawk's `standard`
isolation accepts the file and adds its runtime class, labels and node selector.
Its `strict` level refuses the literal volume definitions.

### Message board and credentials

Each attempt starts its own loopback HTTP service and SQLite database. Participant
IDs are fixed by the evaluator; agents may choose unique display names. Bearer
credentials are scoped to run and participant. Only credential hashes are
written to private files, and the board process does not inherit model/cloud
secrets. The service supports global messages, pairwise DMs, ordered events,
pagination, waits, read receipts and request idempotency. It is stopped after
peers are joined, including cancellation paths.

The board is a separate controller-side process, not a server inside the
no-network participant container. Agents access it through trusted tool closures.
These are native Inspect tools backed by HTTP, not an MCP integration. The
viewer is also loopback-only and serves selected top-level replay HTML files,
not the whole artifact directory.

The local Hugging Face account already has CAIS HLE access. An approved
fine-grained read token named `collaboration-index-hle-read` is stored in the
standard private Hugging Face cache, outside this repo. On a new machine,
authenticate through a hidden token/login flow and verify dataset access;
never copy the credential into a config or documentation. Local access does not
automatically supply remote workers with credentials or cached data.

## Options

Use `uv run inspect eval --help` for installed framework options. Useful controls
include `--epochs`, `--temperature`, `--max-tokens` and `--max-connections`.
These differ from task arguments supplied with `-T`. `--max-tokens` caps an
individual generated response; it does not replace `token_limit_per_agent`.

`team_time_limit` is the task's solving deadline in seconds, beginning only after
all peers are ready. A generic Inspect `--time-limit` or sample-level token limit
can interrupt controller finalization; do not silently substitute it for the
team/per-peer limits. Use explicit bounded solving limits when supervising
real smoke tests, and retain partial work and infrastructure errors.

## Parameters

### Shared arguments

| Argument | Maintained default | Meaning |
| --- | --- | --- |
| `agents` | `2` | Integer 1–32; all peers share one sandbox. |
| `condition` | `collaborative` | Collaborative board or `oracle_allocation` control. |
| `seed` | `0` | Stable task draw identifier; spelling uses it for dealing. |
| `token_limit_per_agent` | `null` | Required positive native token ceiling for each peer. |
| `team_time_limit` | `null` | Optional positive solving deadline in seconds. |
| `agent` | `react` | Native ReAct, or a compatible dotted Python factory. |
| `agent_args` | `{}` | Factory options; trusted tools/lifecycle/model/compaction cannot be replaced here. |
| `artifact_dir` | `run-artifacts` | Parent of fresh `team-<uuid>` attempt directories. |
| `sandbox_enabled` | `true` | Shared sandbox by default; false is a development control. |
| `sandbox_type` | `docker` | `docker` uses Compose; `k8s` uses the packaged Helm values for Hawk. |
| `compaction_threshold` | `0.75` | Fraction passed to native `CompactionAuto`. |

An alternate agent factory must accept the injected native tools, `submit`,
`on_continue` and compaction contract. Preserve the invariant setup and trusted
game/scorer. This interface does not promise compatibility with every external
agent framework without adaptation.

### HLE collaboration

| Argument | Maintained default | Meaning |
| --- | --- | --- |
| `title` | `HLE collaboration` | Task/replay display title; the starter uses its own title. |
| `records_file` | `null` | Trusted local JSON records; null loads pinned CAIS data. |
| `gold_only` | `true` | Intersect CAIS IDs with HLE-Verified's `Gold subset`. |
| `question_limit` | `null` | Optional positive prefix length; null keeps the selected batch. |
| `answer_characters` | `64000` | Maximum characters in each accepted answer. |
| `answer_judge` | `hle_json_judge` | Bound JSON equivalence judge; `exact` for suitable fixtures. |
| `max_grader_attempts` | `3` | Bounded attempts to obtain a parseable judge verdict. |

Pinned revisions are CAIS `5a81a4c7271a2a2a312b9a690f0c2fde837e4c29` and
HLE-Verified `0bc83643672d4f68a5f89998617a639d85e7318b`. They are explicit
`dataset_revision` and `verified_revision` parameters. The authenticated
2026-10-07 preflight loaded **575 gold/text questions**. Images are always
excluded by the current loader. Gold annotations select IDs; reference answers
remain the original CAIS answers. "Diamond" ordinarily refers to GPQA Diamond,
a separate benchmark, not this selection.

Agents call `submit_answer(question_number, answer)`. The first valid accepted
answer is final. Empty, out-of-range or oversized answers are rejected;
duplicates are counted and cannot overwrite an answer. There is no correctness
feedback during solving. `read_file(path="questions.json", start=1, count=...)`
reads question ranges without exposing references.

### Counting

| Argument | Maintained default | Meaning |
| --- | --- | --- |
| `target` | `null` | Null counts to 2N, two numbers per agent; a positive integer fixes the sequence 1..target. |
| `submissions_per_agent` | `null` | Null gives ceil(target/N); an explicit quota must permit the team target. |

`submit_number(number)` atomically appends a signed 64-bit integer in arrival
order. The output is hidden, irreversible and never sorted. Wrong integers still
occupy slots and consume quota. The team ends at `target` accepted entries or
the solving boundary. Global success therefore depends on sequencing, not
merely getting every peer to submit its intended numbers eventually. Peers are
told they have a budget of accepted submissions but not its size, and never
learn the team size or their evaluator IDs; the board shows only the names
they register.

### Spelling

| Argument | Maintained default | Meaning |
| --- | --- | --- |
| `sentences_file` | `null` | Null selects the packaged 1,000-line sentence pool. |
| `candidate_count` | `null` | Null shows 2N candidates; set a fixed positive count for fixed-work comparisons. |
| `copies` | `3` | Copies of distinct required character cards before dealing. |
| `minimum_hand` | `2` | Minimum distinct-character hand size where possible. |
| `max_characters` | `null` | Optional shared output bound; null adds no character ceiling. |

All peers see the candidate sentences; each sees only its own reusable character
hand. A seeded candidate determines the dealt character pool. Characters are
not consumed when submitted. `submit_letter(character)` accepts a held
character or the aliases `space`, `comma`, `period`, `return`. Only a peer holding
return can end the line. Any exact shown sentence followed by return is complete;
the deal's selected sentence is not the only sentence the scorer accepts.

### Graph colouring

| Argument | Maintained default | Meaning |
| --- | --- | --- |
| `colours` | `3` | Palette size, 2–8, taken in order from red, green, blue, yellow, purple, orange, pink, brown. |
| `topology` | `random` | `random` planted graph, `ring` or `grid`. |
| `degree` | `3.0` | Target average degree of a random network. |
| `sandbox_enabled` | `false` | Peers have no file or shell tool, so no container is started by default. |

Every peer is one node of a hidden graph drawn with a planted proper colouring,
so a solution exists; random networks have no isolated nodes. A peer's prompt
names its fixed ID and its neighbours' IDs, never the team size or the rest of
the graph. `set_colour(colour)` sets or changes the caller's colour at any time.
`send_message(neighbour, text)` sends a board DM and refuses anyone who is not a
neighbour. `read_messages(wait_seconds)` collects new neighbour DMs, waiting up
to the board's wait bound when none is pending. There is no global room. The
attempt ends the moment every node holds a colour and no edge joins two equal
colours. The defaults and planted-graph rules follow Multi-Agent-Bench's Hawk
`colouring_local` task; that version delivered messages automatically before
each decision and hid IDs behind random numbers.

## Scoring

The scorer reads authoritative `TeamHistory` in Inspect's typed store. It never
grades a peer's claimed success or reconstructs submissions from chat text.
Counting and spelling use normalized Levenshtein similarity:

```text
quality = max(0, 1 - edit_distance(actual, expected) / len(expected))
```

Counting compares ordered integers with 1..target. Spelling compares the shared
line with the closest shown sentence, excluding a final return from text
similarity. HLE quality is correct answers divided by all selected questions;
unanswered questions receive zero credit. HLE `completed` means every question
has an accepted answer, not that every answer is correct. Ordered-task completion
requires the exact correct sequence or an exact shown sentence plus return.
Colouring quality is the fraction of edges whose two ends hold different colours
at the end; an uncoloured end counts as a clash, and an edgeless network scores
1 once its node is coloured. Colouring `completed` means a proper colouring of
every node, and `coverage` is the coloured fraction of nodes.

| Measurement | Interpretation |
| --- | --- |
| `quality` | Team accuracy/similarity, normally 0–1. |
| `coverage` | Submitted fraction/length; it does not guarantee correctness. |
| `completed` | Task-specific completion indicator. |
| `elapsed_seconds` | Release barrier to final required submission, or joined partial end. |
| `tokens`, `input_tokens`, `output_tokens` | Aggregate subject-model usage across peers. |
| `message_count` | Successful board sends, with global/direct counts also recorded. |
| `communication_calls` | Recorded board operations, including non-send actions. |
| `duplicate_submissions` | HLE attempts to answer an already answered question. |
| `unscored_questions` | Judge verdicts unavailable after bounded parsing attempts. |

Per-peer tokens, turns, tool calls and statuses are retained in the typed store.
`peer_quiescence_seconds` is in the sample score value; it is not currently a
declared headline aggregate metric. Rejections, end reasons and judgments are
also retained structurally. Default score metrics and epoch reduction use means;
they are not best-of-N, pass-at-k or a union of successes.

HLE's simplified configurable equivalence judge is not the exact upstream
leaderboard judge. If a verdict remains malformed, its correctness is `null` and
the team quality is `NaN` rather than treating failed grading as zero capability.
The portable replay converts nonfinite numbers to JSON null. Judge transport
errors and deterministic infrastructure failures raise. Report unscored attempts
and failures explicitly when aggregating; do not silently drop them.

Solving time and subject token counts exclude grading. Native per-peer limits
are checked at response boundaries, so a final in-flight response can exceed the
remaining allowance. N × per-peer budget is a planned ceiling, not a hard billing
cap. There is deliberately no second aggregate sample token limit that interrupts
finalization. Partial work survives native peer-budget exhaustion and the team
deadline. Checkpoint continuation is rejected until all peer histories, limits,
board and irreversible game state can be restored together.

## Development and verification

```bash
uv run ruff check src tests templates
uv run ruff format --check src tests templates
uv run mypy src
uv run pytest -q
uv run pytest -m docker -q
uv build --wheel
```

Default pytest excludes Docker tests; the explicit second pytest command runs them.
The 2026-10-07 baseline passed **45 local tests plus one Docker integration test**.
Eight-peer Docker mocks completed all three tasks; a 32-peer counting Docker
mock and the installed starter smoke also completed. Wheel assets, Ruff and
mypy passed. Dataset access/loading was verified separately without printing
records. No real-model capability results or collaboration ECI exist yet.

Start a fresh coding session with [AGENTS.md](AGENTS.md), then
[architecture](docs/architecture.md), [measurement design](docs/design.md) and
[the dated handoff](docs/handoff.md). Useful next tasks are controlled real-model
smokes and matched scaling studies. Number-sequence coordination is a promising
fourth task; Python line assembly needs additional execution/scoring validation
before import. Keep benchmark diversity in mind before adding close variants.
