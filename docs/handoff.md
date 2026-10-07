# Fresh-session handoff

This snapshot was written on **2026-10-07**. Read [AGENTS.md](../AGENTS.md) and
[README.md](../README.md) first. Recheck source, environment and receipts before
relying on dated statements. This document records the prototype's starting
point; it does not authorize new model calls or external actions.

## User intent

Build `collaboration-index` as a reusable home for collaboration evaluations.
Initial tasks are HLE as one numbered question batch, counting to 64 and spelling
with private character resources. Agents should coordinate through the same
message board and visualiser. They should be equal peers with no imposed leader,
and their coordination structure should emerge from interaction.

The immediate topology is N native Inspect subagents sharing one computer,
one team sample and one `.eval`. Future independent-computer comparisons are
desired, with matched experimental controls. The eventual suite should span
roughly 5–10 tasks and N = 2–32, with an evidence-based collaboration index/ECI.
We have not chosen index weights or launched that study.

## What already exists

The baseline prototype was committed locally as
`e60c258581d27ba3ab6aad2334678e473159cad1` on `codex/initial-suite`.
There is no configured publication/remote in that baseline. Documentation may
have subsequent commits; inspect Git rather than treating this baseline hash as
the permanently current HEAD.

Implemented components include YAML-owned task interfaces, native concurrent
ReAct peers, a real single Docker container with per-peer identity verification,
typed shared submission state, atomic tools, per-peer limits, a release barrier,
fresh authenticated boards, cancellation/cleanup, collective scoring, common
portable replays and a local replay chooser. The installed starter has its own
venv/config/data/smoke and imports the core rather than duplicating it.

The allocation control here is **oracle allocation**, not independent
sandboxes: it assigns work and withholds messaging. See the design document for
its privileged information and limitations. Do not conflate those axes.

## Verified evidence

| Check | Verified baseline result |
| --- | --- |
| Local suite | 45 tests passed; Docker test excluded by default. |
| Explicit Docker integration | One test passed, verifying eight peers, one sandbox and one task log. |
| Eight-peer Docker mocks | HLE fixture, counting and spelling completed with correct authored outputs. |
| Scaling plumbing check | 32-peer counting mock completed in one Docker sandbox. |
| Installed starter | Shared-sandbox mock and portable replay passed. |
| Static checks | Ruff lint/format and mypy passed. |
| Packaging | Wheel contained configs, sandbox, pool, frontend and starter assets; isolated install checked. |
| HLE access and selection | Authorized fine-grained read access; pinned 575 gold/text records loaded; one real task constructed. |
| Real-model study | None performed; no measured capability, provider concurrency or ECI result. |

Mock outputs and token usage are synthetic; fixture code uses evaluator knowledge
to exercise submissions. Dataset preflight established access/schema/selection,
not model accuracy. Preserve both distinctions when describing the project.

Ignored local evidence includes:

- `run-artifacts/final-verification.json`
- `run-artifacts/mock-suite/verification.json` and its three HTML replays
- `run-artifacts/scale32/verification.json`
- `run-artifacts/hle-dataset-preflight.json`
- `templates/example-exam/run-artifacts/verification.json` and `replay.html`

These receipts may not accompany another checkout. The smoke commands can
recreate authored verification. Completed mock logs were removed by exact owned
paths after rendering; real `.eval` results must be retained when they exist.

The replay chooser was available at <http://127.0.0.1:14368/> during development.
It is a local process, not hosted infrastructure; restart it with the documented
`collaboration-view` command if unavailable. Do not treat a stale browser tab as
proof of a running eval. No evaluation-owned containers remained after the
baseline checks.

## Colouring addition (2026-10-07)

`collaboration_index/colouring` ports Multi-Agent-Bench's `colouring_local`
from `james/leader` commit `e633b04cd109b6616a0273a3dd27724711814a5c`, with
neighbour-only board DMs in place of automatic inbox delivery. Authored mocks
completed at 1, 2, 8 and 32 peers and in the oracle control, with every DM
delivered to its neighbour. The eight-peer Docker smoke completed all four tasks.
At that point 67 local tests and the Docker integration test passed, along with
Ruff, mypy and the wheel build. No real model has played it.

## Credential and source context

The user approved creating and privately saving the fine-grained
`collaboration-index-hle-read` Hugging Face token. It selects read access to
`cais/hle` alongside Hugging Face's automatically allowed public repositories.
The account already had CAIS access. Credentials remain in Hugging Face's
standard private cache, not this repo or its artifacts. Verify authentication
without printing tokens or loading dataset previews into output. A different
machine or remote worker needs its own authorized access arrangement.

CAIS revision: `5a81a4c7271a2a2a312b9a690f0c2fde837e4c29`.
HLE-Verified revision: `0bc83643672d4f68a5f89998617a639d85e7318b`.
Selection: `Gold subset` IDs intersected with original CAIS text records.
The official Inspect Evals adapter informed selection/pins, but its full package
is not a dependency. Original answer references are retained; no answer-key
dataset is vendored. The loader selects only needed columns to avoid decoding
unused image objects.

Board provenance and working-tree source hashes are in
[import-provenance.json](import-provenance.json). Counting/spelling derive from
local Multi-Agent-Bench commit `a3d55c0679eb08c0b47a9976b5a2bc059d6f9659`.
Original source repositories were preserved. This project does not depend on
old ExploitBench jobs, board history or model sessions, and they should not be
modified to start work here.

## Known limits and useful next decisions

Real subject/provider and HLE judge choices remain operator decisions. Before a
paid smoke, agree the work/seed, team size, condition, per-peer budget, bounded
solving time, route and grading contract. Verify original-developer inference
for open-weight models, correct work billing, effective context and 75%
compaction. Model connection limits need enough concurrency for the claimed
team size. The existing native/mock path does not prove these provider settings.

Measure CPU/RAM/disk and real grading on small reliable workloads before broad
scaling. The Docker allocation is a prototype bound, not a resource study. Hawk
deployment has not been validated, and long jobs should not depend on a travelling
laptop. Do not change sandbox protections merely to get a launch through.

Checkpoint continuation is intentionally unsupported. Implementing it requires
restoring all private histories, native limits, board and irreversible game
state together. The HLE judge is a simplified JSON equivalence judge and currently
sequential; it is not a standard HLE leaderboard implementation. First-final
answers are a deliberate prototype contract; revisions would change the task
and need explicit design/measurement updates.

A useful next benchmark is private-number sequence ordering. Python line assembly
needs separate execution/scoring QA. Avoid filling a ten-task suite with trivial
variants of the same coordination mechanic. For the index, decide normalization,
quality thresholds, benchmark weights and uncertainty using seeded team-level
evidence, not the number of peer trajectories.

Task seed randomizes spelling and colouring draws, but currently labels counting
attempts and HLE metadata without changing their work. Do not assume a seed
sweep gives independent problem draws for every task. Record model-generation settings
and use repeated team attempts appropriate to the intended uncertainty estimate.

## Starting the next coding session

Open the `collaboration-index` folder, not the old ExploitBench checkout. A
suitable initial instruction is:

> Read AGENTS.md, README.md, docs/architecture.md, docs/design.md and
> docs/handoff.md. Verify the current repository and environment. Continue the
> collaboration-index task I specify, preserving symmetric peers, one shared
> sandbox/team sample, the common board and visualiser, typed scoring and the
> stated experimental controls. Distinguish authored mock evidence from real
> model results, and keep paid/external actions within my authorization.

Start with the free smoke if a code/environment change needs confirmation. Use
bounded diagnostics for a stall and verify cleanup before retrying; do not infer
useful model work from `running` alone or spend hours polling unchanged state.
