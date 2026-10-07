# Coding-agent handoff: Collaboration Index

This file is the project context for an agent starting without the earlier
conversation. The repository is **collaboration-index**, a separate project from
ExploitBench. Read this file, [README.md](README.md),
[docs/architecture.md](docs/architecture.md), [docs/design.md](docs/design.md),
[docs/handoff.md](docs/handoff.md), then the source/configs relevant to your task.
Use the implementation and fresh checks to resolve drift in the dated handoff.

## Research goal and accepted scope

The user wants a standardized suite that tests coordination among copies of a
model. Start with HLE division of labour, ordered counting and spelling with
complementary private information. Eventually compare approximately 5–10
benchmarks at N = 2–32 and estimate a collaboration index/ECI. Current results
remain separate quality, completion, time, tokens and communication measurements.
Do not invent weights, normalization or a headline ECI without a justified
experimental design and user agreement.

The implemented topology is one shared computer: N native Inspect subagents,
one sandbox, one team sample and one `.eval` per task invocation. Peers have
separate model histories and per-peer budgets. The agents are symmetric; do not
impose a leader, fixed roles or a designated benchmark operator in the
collaborative condition. Agents know they share the computer and files, and
communicate through the common board. Private spelling hands are an intentional
task asymmetry, not operator-assigned roles.

Independent computers are a desired future topology ablation. They are not
implemented here yet. Keep model, provider, task selection, seed, budget,
generation, tool affordances and grading matched before attributing differences
to topology. Do not copy the old eight-independent-sandbox ExploitBench launch
configuration and call it this shared-computer experiment.

An oracle allocation control exists, with assigned work and no participant board
tool. It has privileged ordering/assignment information and is not a proven
optimal upper bound. Preserve that distinction. A fixed per-agent budget also
increases total available compute with N; fixed-total and fixed-per-agent studies
are different conditions.

## Current implementation and status

- Python 3.12; locked Inspect AI 0.3.277; package name `collaboration-index`;
  registered task namespace `collaboration_index`.
- Task IDs: `hle_collaboration`, `counting`, `spelling`, `colouring` under that
  namespace.
- Colouring peers are nodes of a hidden planted graph. They communicate only by
  board DMs to their graph neighbours through `send_message`/`read_messages`;
  the neighbour check lives in the trusted tool closure, and there is no global
  room. Its sandbox is off by default because it exposes no file or shell tool.
- Team sizes 1–32; default two; one peer is useful as a baseline.
- Maintained default epoch count is one; repeated epochs are separate attempts
  with mean reduction, not best-of-N or a union.
- HLE is a batch of pinned gold/text CAIS records, with original CAIS references.
  Authenticated loading yielded 575 questions on 2026-10-07. No real records are
  committed. A question limit changes batch work, not the Inspect sample count.
- Counting defaults to target 2N (two numbers per agent) and ceil(target/N) per-peer quota.
- Spelling preserves reusable private character hands; historical candidate
  count scales with N. Set a constant `candidate_count` for fixed-work studies.
- One separate loopback board service per attempt; one common replay frontend.
  The board client is used by native Inspect tools; this is not an MCP server.
- Local mock orchestration, Docker, board, scoring and replay are verified.
  Real-provider runs, Hawk deployment, resource scaling and checkpoint resume
  are not established by those mocks. See the dated handoff for exact evidence.

No paid run, remote publication or deployment is authorized merely because a
configuration or command exists. Work requested in this repository does not
inherit approvals for older ExploitBench jobs or its main-branch repair exception.
Carry forward actual approvals within the current task, and keep remaining
external actions within their stated scope. Use `codex/` branches; do not merge
or push a default branch without authorization.

## Framework and configuration conventions

On this machine, load the canonical Inspect skill before Inspect task work:
`/Users/pabloromero/.claude/skills/inspect-ai/SKILL.md`. For task creation or
adaptation, also read its architecture, HLE worked example and validation guides.
For task QA, tool/scorer additions or hardening, load the canonical
`qa-inspect-task` skill. Personal skills are canonical under `.claude/skills`,
not copied versions. On another machine, use the corresponding canonical skill
if available and follow this repository's explicit contracts.

Each task's public entrypoint is `task.py`. Adjustable defaults belong in that
task's `run_configs/default.yaml`, with explicit public Python parameters reading
those values. Keep configs in the wheel. Do not add a second constants/defaults
system or decorative YAML that consumers ignore. Use native run-config blocks
and one blank line between top-level blocks. Experiment copies belong under
`run-artifacts`; committed `run_configs` contains only `default.yaml` and a
complete `original.yaml` if verified historical differences justify it. These
new variants currently have no original configuration to reproduce.

Keep `Task.setup` invariant, a replaceable team solver, benchmark-specific input,
trusted submission logic and scoring distinct. Alternate agents must preserve
the injected tools, model binding, continuation and compaction contract. Every
authored Python function/method, including tests and callbacks, needs a concise
one-sentence summary docstring. Prefer intuitive functions and focused changes
to speculative abstractions or compatibility shims.

The native compaction setting is 0.75. Verify a new model's effective context,
provider route and harness consumption before launch. Do not silently select a
model, reasoning effort or grader. Graders use an explicit `grader` model role;
there is no fallback to subject self-grading.

## Trusted state and concurrency contracts

`TeamHistory` and `BoardHistory` are namespaced Inspect `StoreModel` records.
Final scoring reads these authoritative records, not transcript text or model
claims. Each submission tool closes over a fixed evaluator participant ID.
Never accept a caller-supplied actor as authorization.

`TeamGame` owns a sample-local async lock. Validation and irreversible writes
occur under that lock. HLE's first accepted answer is final; duplicates cannot
overwrite it. Counting preserves arrival order, including wrong numbers.
Spelling checks the caller's private hand, and a held return ends the attempt.
This lock is controller-process-local; it is not a distributed synchronization
solution for future independent controllers.

Be careful with `StoreModel` collection mutation. Read a collection once,
mutate that reference, then assign it back. Earlier construction that re-read the
same coercing store field inside an append expression lost records; tests cover
the repaired path. Do not bypass typed state to repair this with chat parsing.

All peers prepare and verify the shared sandbox before a release barrier starts
solving time. Cancellation joins peer tasks before the board closes. Preserve
partial submissions on the native per-peer budget/deadline boundaries. Do not
add an aggregate sample token limit that prevents finalization. A response can
overshoot a native token allowance at its final response boundary; planned
N-times-budget accounting is not a provider spending guarantee.

Checkpoint continuation deliberately fails. Do not enable it until private peer
histories, usage limits, shared state, pending actions and board identity/history
restore together in a tested implementation. A fresh attempt gets a fresh board;
never clear a board while its peers are active.

## Privacy, credentials and environment

Keep answer references, other peers' private hands, provider keys and operator
files out of participant inputs/tools. The participant file tool reads only
immutable `questions.json`, including numbered ranges. No arbitrary shell or
host file tool is currently exposed. Native logs retain trusted metadata and
may contain reference answers; they are not public sanitized exports.

For real HLE or other sensitive evaluation data, use structural checks and emit
counts, timestamps, statuses, numeric scores, hashes and sanitized errors. Do
not inspect or print real questions, references, model reasoning/transcripts,
generated payloads or board message bodies unless the user explicitly requests
that review. Authored harmless fixtures are used for development tests. Do not
weaken safeguards, disguise requests or substitute providers to overcome refusals.

Hugging Face access was authorized and configured in its private local cache.
Never print tokens or copy them into this repository, artifacts, Git, model
messages or documentation. Verify access without exposing content. A local
credential does not establish remote access.

Future paid work uses the designated work provider identity. On this machine
the canonical OpenRouter work credential is configured outside the repository;
verify billing and route without printing it. Open-weight models require their
original developer's actual inference route, with no third-party fallback.
These are launch requirements, not instructions to spend money now.

For future Hawk work, load its canonical skills and access guidance first. Never
use macOS Keychain: reuse/probe a protected memory-only session, and use only the
designated Generality work Chrome profile for necessary login. This prototype
has no Hawk deployment receipt. Long evaluations should not depend on a
travelling laptop controller; choose an authorized supported remote controller.

The Docker image is digest-pinned, unprivileged, network-disabled and has bounded
tmpfs, CPU and memory. The board is a separate loopback process with filtered
environment and scoped tokens; only token hashes persist. Keep that separation.
Measure actual resources, grading and worker packing before scaling unfamiliar
workloads; do not label the current 2 CPU / 1 GiB setting a measured minimum.

## Development workflow and handoff

From the repository root:

```bash
uv sync --extra hle --group dev
uv run ruff check src tests templates
uv run ruff format --check src tests templates
uv run mypy src
uv run pytest -q
uv run pytest -m docker -q
uv build --wheel
```

Default pytest excludes Docker tests. For changed tools/mechanics/scoring, add
meaningful edge-case and mock end-to-end tests, then verify the changed surface
through real Docker when relevant. Do not write tests that merely mirror an
implementation or turn documentation work into paid runs. Inspect package assets
when changing configs, the visualiser or starter. Update both
`src/collaboration_index/assets/starter/` and `templates/example-exam/` when
changing shared starter content; the former is what the generator distributes.

Use `uv run collaboration-smoke --agents 8` to verify all four authored tasks.
Its scripted oracle knowledge and synthetic tokens cannot support research
claims. Verify real provider activity, task grading, collaboration and durable
results separately before expanding a real experiment.

Keep root `logs/` flat and `.eval`-only. Everything else goes under
`run-artifacts/<run-name>/`; do not put secrets there. Delete only logs/artifacts
owned by disposable mock checks. Preserve real results and active or unrelated
resources. Do not mutate the old ExploitBench repository, message boards, jobs
or cloud hosts as part of routine work here.

When unattended work stalls, use bounded evidence checks and controlled A/B
diagnostics. Distinguish provider work from status labels, change one optional
component at a time, reconcile owned resources before retrying, and report a
verified external blocker promptly. Do not poll unchanged state for hours or
blindly repeat paid submissions. Dependency workarounds require checking current
upstream compatibility and testing a plausible upgrade first.

At handoff, report source/config identity, what changed, exact checks and their
limits, real versus mock results, remaining resources and actionable next steps.
Refresh the dated handoff when verified state changes. The user should be able
to open this folder in a fresh chat and continue without the previous transcript.
