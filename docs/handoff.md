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
That baseline had no remote. On 2026-10-07 the repository was published at
<https://github.com/pabloRom2004/collaboration-index> (public, commit metadata
rewritten to the work address before the first push). Inspect Git rather than
treating any hash here as the current HEAD.

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
| Real-model study | Single-seed Hawk smokes only (next sections); no capability estimate or ECI result. |

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
Ruff, mypy and the wheel build. Its first real-model runs are below.

## Hawk smoke runs and changes (2026-10-07)

Three Claude sessions worked here in parallel, kept apart by a coordinator
session. Unless marked, every run used GLM 5.3 Flash through OpenRouter pinned to `z-ai/fp8`
with no fallbacks, the OpenRouter work key, `xhigh` reasoning, a 16,000-token
output cap per turn, 5M tokens per agent, a 60-minute team limit, seed 0, the
sandbox off and one attempt per row. These smokes show the Hawk path working
end to end. One seed per row cannot support capability claims.

| Eval-set | Commit | Team | Result | Solving time | Tokens |
| --- | --- | --- | --- | --- | --- |
| [colouring smoke](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/colouring-glm53-flash-smo-hibrw1x1ed8qwwq7) | `c399d7d` | 4 (5 edges) | solved | 26 s | 18K |
| same | `c399d7d` | 8 (12 edges) | solved | 232 s | 663K |
| [counting smoke](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-glm53-flash-smok-r2bp770ch2hvw72k) | `c399d7d` | 4, target 64 | 1.00 | 287 s | 2.29M |
| same | `c399d7d` | 8, target 64 | 0.33 | 386 s | 1.94M |
| [counting no-sandbox prompt](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-glm53-flash-nosa-dj7gn0ir37jai6p0) | `7797eb0` | 4, target 64 | 0.50 | 343 s | 3.41M |
| same | `7797eb0` | 8, target 64 | 0.75 | 681 s | 22.3M |
| [spelling smoke](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/spelling-glm53-flash-smok-pm18a4e22s79jhx3) | `c399d7d` | 4 | 0.98 | 1160 s | 18.1M |
| same | `c399d7d` | 8 | 1.00 | 1258 s | 30.1M |
| [spelling no-sandbox prompt](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/spelling-glm53-flash-nosa-pesplllj3qnjizuj) | `7797eb0` | 4 | 0.96 | 633 s | 12.6M |
| same | `7797eb0` | 8 | 0.95 | 841 s | 25.2M |
| [counting 32, IDs visible](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-32-glm53-flash-2-g75vuoqtxtejdprc) | `23333f2` | 32, target 64 | 0.03 | 810 s | 3.83M |
| [counting 32, IDs hidden](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-32-names-glm53-2-jpctmiepwpm4zjzr) | `8131bae` | 32, target 64 | 0.00 | 263 s | 10.6M |
| [counting 32, IDs hidden, GPT 6.1 Sol](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-32-sol-20261007-gtn2ygbytx3qcegy) | `9f99012` | 32, target 64 | 1.00 | 642 s | 6.27M |

The Sol row ran through Generality's Middleman with its full 128000-token output
limit and the raised board limits; its other settings match the GLM rows.

Every run finished with zero errors and real model, board and submission
activity. Agents that hit their 5M limit stopped while the team still finished
and was scored. Logs are in `logs/`; replays render with `collaboration-replay`.

What the runs showed:

- Every counting failure had one mechanism: two agents claimed the same block,
  the list filled at its target length, and the remaining range never arrived.
  At 32 agents with IDs visible, the agents split the work but all submitted at
  once, so the list was shuffled. With IDs and team size hidden, agents could
  not divide the range, many claimed the same popular blocks, and 28 numbers
  never arrived. They found their two-submission budget by hitting it.
- GPT 6.1 Sol solved the same hidden-ID 32-agent count exactly with fewer
  tokens than GLM. It sequenced submissions turn by turn, using 122 DMs and 448
  blocking waits where GLM's team sent 6 DMs.
- Token use varied widely within teams; single agents used most of a team's
  tokens in several runs.
- No agent tried to use files when the sandbox was off, even under the old
  prompt that still described a shared computer.
- The board never reported "Board busy" at 32 agents under the original limits
  (32 concurrent requests, 8 concurrent waits).

Changes made that day, in order on `codex/initial-suite`:

1. Colouring task and neighbour-only DMs (`a93415c`), merged with the Hawk
   sandbox branch that added `sandbox_type: k8s` and stored the board journal
   in the `.eval` (`c399d7d`).
2. The collaborative prompt lost "No leader, roles or work assignments have
   been imposed", and sandbox-off runs got a variant without the shared
   computer (`7797eb0`, `23333f2`).
3. Replay views: counting and spelling target and progress grids, letters and
   numbers flying into a shared grid on the map, private spelling hands
   (`78b655b`, `b573ad7`, `102d03a`, `7a99fc3`), and links from each replay to
   its source `.eval` in Hawk and locally (`1409e67`).
4. Counting defaults to target 2N and spelling to 2N candidates, with no cap
   (`d25dcb6`).
5. Peers no longer see evaluator IDs, the team size or their submission quota.
   The board shows only registered names and addresses DMs by name, and the
   counting input uses the user's wording (`8131bae`).
6. Board capacity raised to 128 concurrent requests and 64 concurrent waits
   (`5f301cf`).

HLE on Hawk was attempted and then dropped by the user. The first preflight
failed at install because of Hawk's seven-day package cooldown. The second
(`058a5b1`, `k8s` sandbox, GLM through Middleman) installed, loaded gated HLE
data, booted the sandbox pod and started the board. Then each agent's first
non-streaming answer request stalled past Inspect's 900-second
`attempt_timeout` and restarted from scratch. A streamed call with the same
settings finished in 29 seconds, so the stall was in the response, not the
generation. Stopping that run exposed the interrupt hang recorded in AGENTS.md.
Z.AI attribution and the HLE grader were never verified on Hawk.

The 16,000-token output cap in these runs came from Multi-Agent-Bench's
colouring config. At the time a peer stopped at its first turn without a tool
call, so a turn truncated by the cap ended that agent. 4 of the 3,139 GLM
responses hit it: three in the 32-agent run with IDs visible, each ending its
agent, and one in the 8-agent spelling smoke. Later runs set the route's full
output limit, and `0b9ae1d` made peers keep working until the task ends or the
deadline passes, with a time update before every decision. Earlier runs, where
some GLM peers quit after two to five turns, are not comparable on that point.

A 32-agent GPT 6.1 Sol counting run was prepared. Middleman through the Hawk
token served `gpt-6.1-sol` with a working tool call. The OpenRouter work key
could not: pinned to OpenAI it returned 404, because the workspace guardrails
exclude that provider and require zero data retention, and unpinned it routed
to Azure, which rejected the board tool's optional parameters.

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
deployment is validated only for sandbox-off tasks, and long jobs should not
depend on a travelling laptop. Do not change sandbox protections merely to get
a launch through.

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
