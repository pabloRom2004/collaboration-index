# Fresh-session handoff

This snapshot was written on **2026-10-07**. Read [AGENTS.md](../AGENTS.md) and
[README.md](../README.md) first. Recheck source, environment and receipts before
relying on dated statements. This document records the prototype's starting
point; it does not authorize new model calls or external actions.

## Budget-driven MirrorCode rerun and completed baseline (2026-10-09)

The user explicitly requested removal of the terminal submit tool and a reminder
when a native peer stops making tool calls with budget remaining. MirrorCode
version 2 defaults to `allow_submit=false`; it removes upstream's submit
instruction, retains each private history and board identity, and counts
ExploitBench-derived no-tool continuation reminders in `Peer.nudges`. Every
peer ends independently at its native cap. The final shared codebase is graded
after all peers join. `allow_submit=true` retains the prior stopping policy for
explicit controls. This does not implement checkpoint continuation.

Two- and 64-peer Docker mock trajectories resumed after no-tool turns, used the
same board, reached their individual 300-token synthetic caps, and saved final
100% grades across all 208 authored `rev` cases. Other peers finished before the
writer, which continued to edit and test. These establish wrapper behavior, not
model capability or resource minima. Ruff lint/format, mypy, 99 non-Docker
tests, all 11 Docker checks and wheel asset checks passed. One legacy notification
fixture needed an explicit voluntary-stop flag; its focused rerun passed after
the other ten Docker checks. Verification and fixture hashes are recorded in `run-artifacts/hawk-mirrorcode-haiku55-budget-driven-250m-20261009/local-qa.json`.

The earlier voluntary-stop Haiku sweep is complete at runtime source
`77f2937d7d76c67f65560501fb0b2af718cab2cf`. Every attempt has a verified durable
`.eval`, embedded board history, all 1,553 Mailauth cases and zero testing or
reference errors; owned active pods are zero. End-to-end durations below include
setup and finalization. Recoverable participant tool errors are recorded
separately. These are one-epoch observations with hardware growing with N.

| Agents | Final all | Visible | Hidden | End to end | Actual tokens |
| --- | --- | --- | --- | --- | --- |
| 1 | 92.21% | 93.65% | 90.46% | 52.36 min | 27,400,846 |
| 2 | 98.78% | 99.88% | 97.44% | 27.76 min | 28,687,797 |
| 4 | 94.33% | 96.94% | 91.17% | 34.31 min | 50,081,687 |
| 8 | 96.39% | 99.53% | 92.59% | 52.87 min | 62,738,508 |
| 16 | 97.49% | 99.29% | 95.30% | 29.33 min | 150,035,725 |
| 32 | 96.14% | 99.06% | 92.59% | 40.64 min | 107,702,599 |
| 64 | 74.63% | 71.09% | 78.92% | 25.97 min | 103,586,238 |

Baseline receipts, immutable configs, hashes and visually verified PNG/PDF/SVG
plots are in `run-artifacts/hawk-mirrorcode-haiku55-sweep-250m-20261009/`.
The new budget-driven condition has separate artifacts under
`run-artifacts/hawk-mirrorcode-haiku55-budget-driven-250m-20261009/`; read its
manifest for current launch status and exact tested source. Authorized settings
remain Haiku 5.5 only, N=1,2,4,8,16,32,64, one epoch each and 250M total planned
allowance per team, equally divided into non-transferable caps. Preserve both
conditions and do not pool scores. No six-model follow-up is authorized until
Pablo explicitly approves after review. The existing heartbeat monitors both
conditions via the protected memory-only Hawk operator; never use Keychain.
The Hawk controllers are remote; local monitoring needs the Mac and app available.

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

| [counting 32, IDs hidden, Claude Haiku 5.5](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-32-haiku55-20261-rp6up6tpm8yrrjib) | `7bcaee7` | 32, target 64 | 0.08 | 884 s | 161.5M |

The Sol row ran through Generality's Middleman with its full 128000-token output
limit and the raised board limits; its other settings match the GLM rows. The
Haiku row ran through OpenRouter pinned to Anthropic with a 128000 output limit,
compaction at 750,000 of a 1,000,000 window, and the `0b9ae1d` harness, where
peers get a time update every turn and keep working until the deadline. It is
not directly comparable with the earlier rows.

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
- Claude Haiku 5.5 placed 1 to 5 in order, then stalled. All 32 peers spent
  their 5M budgets in 884 s on 271 global messages, no DMs and only 10
  submissions; 1,047 of 2,209 turns had no tool call. Only 2.3M of 158.6M
  input tokens were cache reads, so prompt caching barely engaged on that route.
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

## MirrorCode addition (2026-10-07)

`collaboration_index/mirrorcode` runs N peers in one upstream
[MirrorCode](https://github.com/epoch-research/MirrorCode) workspace, pinned at
`5c9d7b0`, with the common board. Epoch's prebuilt GHCR images refused anonymous
pulls, so the `rev` images were built locally. A Docker mock with two peers
shares one workspace, scores at the same moment and submits; it scores 1.0.
Without the scoring lock, two of three such runs crashed the sample.

Local real-model runs, Claude Haiku 5.5 via OpenRouter pinned to Anthropic,
reasoning effort xhigh, `rev` in Python:

| Run | Agents | Tokens per agent | Score | Time to end | Tokens |
| --- | --- | --- | --- | --- | --- |
| [upstream single agent](../logs/2026-10-07T20-45-25-00-00_MirrorCode_o8QNwdoEwsyeNVHoCoz5Ka.eval) | 1 | 100,000 | 0.00 | 46 s | 125,653 |
| [team task, 1-hour deadline](../logs/2026-10-07T21-04-32-00-00_mirrorcode_6eNJRfHEzgHqkaaBNejGhn.eval) | 2 | 100,000 | 0.00 | 87 s | 216,197 |
| [team task, no deadline](../logs/2026-10-07T21-59-39-00-00_mirrorcode_B7MEwMFfTQH7x66Z9XPq5u.eval) | 4 | 5,000,000 | 1.00 | 279 s | 5,402,174 |
| [same, on Hawk](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/mirrorcode-rev-4x5m-haiku-ew01ird4fak2ew6t) at `bf8f6ab` | 4 | 5,000,000 | 1.00 | 337 s | 2,280,584 |

At 100,000 tokens every agent spent its budget reading the docs and probing
the reference binary, so `/workdir/src` stayed empty. The four-agent team passed
all 208 cases, hidden ones included, and one peer submitted. All four peers
sent board messages, nine in total, though only two registered a name; 4% of
input tokens were cache reads. The two peers registered and
exchanged a DM. 100,000 tokens is too small for this task to measure quality or
speed. Hawk runs the task with the images published to
`ghcr.io/pablorom2004/mirrorcode` (see README); its runner uses Python 3.13.

[Hawk sweep](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/mirrorcode-rev-sweep-250m-i306z3nmwo6xtybn)
at `bf8f6ab`, same settings, 250,000,000 tokens per agent and no deadline. Every
team passed all 208 cases and ended by a submit; costs use OpenRouter's
under-100K-prompt prices, so they are lower bounds. Time runs from release to
the submit, as in `team_score`; peers then finish their current turns. One seed
per row:

| Agents | Score | Time to submit | Tokens | Cache reads | Board messages | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 1.00 | 362 s | 1,115,777 | 4.5% | 0 | $0.12 |
| 2 | 1.00 | 204 s | 743,958 | 8.2% | 0 | $0.09 |
| 4 | 1.00 | 447 s | 6,846,492 | 3.5% | 10 | $0.73 |
| 8 | 1.00 | 186 s | 8,079,144 | 4.7% | 37 | $0.82 |

`rev` is too easy to separate team sizes reliably: one agent solves it in six
minutes, and a single seed cannot separate team effects from run variance.

### Mailauth scoring repair and shared-workspace smokes (2026-10-08)

The approved smoke scope was Haiku 5.5 on mailauth/Python at 32 and 64 peers, one epoch
each, followed by review. The user chose **32M total tokens per smoke team**:
1M per peer at N=32 and 500K at N=64, with equal, non-transferable allowances.
The 250M comparison and six-model sweep remain future work. The user wants to
understand the budget, grading, isolation and recovery boundaries and explain
them back before expanding the study. The subsequent authorization resumed
the two smokes and tested failure-repair iteration.

The failed [64-peer pipeline smoke](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/mc-mailauth-64-pipelines--calsm3n51oq5y3di)
at `e9147dd` produced a durable error `.eval`, 20 testing calls with 20 errors,
476 board events and no numeric final grade. Its sample error was
`ReferenceExecutionError` with a reference-case timeout. The original scorer
was verified byte-for-byte against upstream `5c9d7b0`: candidate timeouts fail
cases, whereas a missing reference result aborts grading. The testing tool
reports a recoverable infrastructure error; the final scorer propagates it.
This behavior is upstream's single-agent behavior too, not a new team rule.

The local repair configures the workspace and every scoring service to use
the unrouted IPv6 resolver `100::1`, preserving upstream's immediate offline
DNS failure instead of waiting for dropped packets. A controlled probe with
one reference binary and no agents reproduced a timeout on an unresponsive
resolver. All 1,553 reference outputs matched exactly across upstream's Docker
`network_mode: none`, that network with the repair, and a Docker bridge with
the repair; all three completed without case timeouts. This supports an
environment-dependent failure that can also affect N=1. The later Hawk retry
below verifies the repaired grading path with a real 64-peer team.

Local checks on the working tree based on `e9147dd`:

- Ruff check and format check, mypy and wheel build passed; non-Docker pytest
  passed all 97 tests.
- Seven distinct Docker tests passed across focused checks: the existing shared
  workspace/board/unread checks, including 64 peers, and two new mailauth
  regressions with initially unresponsive DNS. The latter exercised two
  simultaneous testing calls through one or two pipelines, then the upstream
  final scorer; each graded all 1,553 cases. Their authored incomplete program
  correctly scored 0.0, not an infrastructure error.
- Scripted mocks were repaired to give all waiting peers model-call slots;
  otherwise their barriers could stall behind an adaptive connection pool.
  The MirrorCode regression also reports fixture timeouts as assertions and
  enters its coordination barrier only once per peer.
- A further Docker stress test passed with all 64 scripted peers editing and
  posting to the board, nine testing calls using the eight-pipeline pool, and
  a successful final grade. It emitted 204 tool calls without errors and graded
  the authored `rev` fixture at 1.0. Sampled warm-image local usage reached
  about 270 MiB across containers and 798% CPU; this small fixture is not a
  real-model resource requirement. Evidence is in
  `run-artifacts/hawk-mirrorcode-haiku55-smokes-20261008/active64-verification.json`.

Evidence is under `run-artifacts/mirrorcode-dns-repair-20261008/`, including
`reference-probe-summary.json` and `verification.json`. These are local mock
and reference checks, not measured real-model performance or worst-case
resource minima. Reference outputs and model payloads were not printed.

The board still runs as a separate controller-side loopback process, not in a
dedicated VM. The requested board isolation design, practical resource cap,
interruption cleanup and unsupported checkpoint continuation remain open.
Do not call this repair an escape-proof or unattended-recovery validation.
See [scaling-plan.md](scaling-plan.md) for the updated scope and launch gate.

The subsequent authorized N=64 Hawk smoke at `4c18068` failed in setup with
zero model calls: `/etc/resolv.conf` is a read-only ConfigMap mount. Its durable
error log is preserved under
`run-artifacts/hawk-mirrorcode-haiku55-smokes-20261008/downloads/`.
Image-pull errors appeared transiently during startup, but the final exception
was the attempted resolver write. No 32-agent attempt was launched on that
commit.

The follow-up repair sets the resolver before pod creation. The current k8s
Compose converter offers no resolver setting, and Hawk discards custom chart
selection when patching a task's sandbox. In the dedicated runner only
(`HAWK_JOB_ID` is present), the task therefore atomically replaces the single
resolver line in its installed chart's ConfigMap template. It checks the exact
expected line, accepts an already configured template, breaks uv cache
hardlinks, and fails if the chart format differs. Setup reads the configured
file and skips writing it. This is a scoped dependency-resource workaround,
not an upstream configuration feature; remove it when that feature exists.
Local Helm rendering verified that only `data.resolv.conf` changes, while
pod security and network policy remain identical. A new Docker regression
exercises a genuinely read-only resolver mount through testing and final grading.
The `77f2937` retry completed successfully on Hawk. All 64 peers used tools
and reached their 500K allowances; actual total usage was 33,656,454 tokens
because native limits stop after a response finishes. Ten testing calls
completed without reference errors, then the final scorer graded all 1,553
cases. The final all-case score was 0.010946555 (17/1,553), visible
0.019976498 (17/851), hidden 0.0. The team made no submit call and ended when
all peers reached their limits. Launch-to-log completion was 784.74 s,
release-to-peer join 468.63 s, and peer join-to-log completion 47.98 s.
The `.eval` embeds 61 registrations, 82 messages and 204 board reads. All
60 board tool errors were rejected name collisions or name changes, with
no board-connection errors; there were also three existing-file errors and
five shell timeouts. These recoverable tool failures are retained as experiment
observations, not grounds for retrying a successfully graded low score.

The owned sandbox pods were removed after completion. Hawk CPU/RAM metrics
were unavailable, so this run establishes successful execution at the configured
bounds rather than a measured minimum or matched hardware speedup. Checkpoint
continuation and interrupt cleanup remain unverified. The 32-peer comparison
used the same source and 32M planned team allowance with 1M per peer, and also
completed successfully. All 32 peers used tools and reached their limits;
actual usage was 33,155,189 tokens. Twelve testing calls completed without
reference errors and the final scorer graded all 1,553 cases. Its final all-case
score was 0.349645847 (543/1,553), visible 0.357226792 (304/851), hidden
0.340455840 (239/702). Its best intermediate all-case score was 0.352221507.
Launch-to-log completion was 825.00 s, release-to-peer join 456.57 s, and
peer join-to-log completion 113.85 s. The board journal contains 32
registrations, 103 messages, 216 reads and 10 DM creations. The 29 board
errors were 25 name collisions, two rejected name changes, a self-recipient
request and an unknown conversation; two editor calls attempted to create
existing files. No board-connection error occurred.

Both jobs are complete with zero remaining sandbox pods and successful logs
in root `logs/`. Monitoring was paused after both durable results were verified.
These one-epoch results do not show a collaboration scaling law: the
32-peer team scored much higher, the actual token totals differ slightly,
and the grading and workspace allocations differ. Their elapsed times describe
budget-bound attempts rather than time to an agreed quality threshold.
Results and configuration receipts are under
`run-artifacts/hawk-mirrorcode-haiku55-smokes-20261008/`; the full model sweep
has not started. That folder's `smoke-results.md` is the readable comparison,
and `results.json` retains sanitized timing and numeric score events.

### Overnight Haiku-only Mailauth sweep (2026-10-09)

After reviewing the smokes, the user authorized N=1,2,4,8,16,32,64 with one
epoch each, autonomous overnight collection and plots. The prior 250M total
team allowance applies: peer caps are 250M, 125M, 62.5M, 31.25M, 15.625M,
7.8125M and 3.90625M respectively. Every team uses `77f2937`, the same
Mailauth/Python task, native ReAct, Anthropic-only OpenRouter work route,
generation/compaction settings, workspace scaling formula and scoring pool
formula as the completed smokes. Each is an independent Hawk eval-set and
runner with a fresh board; no checkpoint continuation was enabled.

| Peers | Owned Hawk evaluation |
| --- | --- |
| 1 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/mc-h55-250m-n01-1009-v1-asetz5j4ojxmgql7) |
| 2 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/mc-h55-250m-n02-1009-v1-4x5nmrs2c7w9ky7o) |
| 4 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/mc-h55-250m-n04-1009-v1-cbpzui5ks4ejbc1o) |
| 8 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/mc-h55-250m-n08-1009-v1-y0p4x4auhhq7hwg1) |
| 16 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/mc-h55-250m-n16-1009-v1-0mov2vlqfeg23hiy) |
| 32 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/mc-h55-250m-n32-1009-v1-hozp7at0nou2eljt) |
| 64 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/mc-h55-250m-n64-1009-v1-wkreq3hjjntlp4ng) |

At 2026-10-09 00:00:42 UTC all seven had actual token usage and tool activity.
The live sweep had 13 completed testing calls and zero reference errors.
This is launch-health evidence, not a final result. Configs, receipts, protected
operator requests, metadata reductions and plotting helpers live under
`run-artifacts/hawk-mirrorcode-haiku55-sweep-250m-20261009/`.
`monitor_once.py` performs one bounded collection, final-log reduction, plot
update and `progress.md` refresh. Use the repo `.venv` to invoke it; it uses
its own verified Matplotlib environment without changing the project lockfile.
The plotter was rendered and checked against the completed 32M smoke data,
kept separately from this sweep. Pending/unscored attempts are not plotted
as zeros. Final plots will show scores, attempt duration and actual token use.

The existing automation `mirrorcode-haiku-smoke-monitoring` was updated to
"MirrorCode Haiku overnight sweep", active every five minutes. It remains
quiet without actionable changes, checks final grading/board history/durable
logs, and pauses after all seven results and cleanup are verified. The remote
Hawk controllers survive laptop sleep. Local monitoring, fixes and plots need
the Mac and app available; the Mac was on battery at handoff, so this was
explicitly explained to the user. Scope permits compatible tested failure
repairs and fresh retries of failed configurations, preserving original
attempts and avoiding duplicate live work; normally completed low scores stand.

The user explicitly narrowed tonight to Haiku only. **Do not launch any of the
six-model follow-up runs until explicit approval tomorrow after reviewing the
Haiku results and plots.** One epoch per N cannot estimate variance, hardware
grows with N, and actual usage can overshoot native allowances. Keep those
limits in the comparison; do not call the result a causal scaling law.

## 16-agent counting model sweep (2026-10-08)

Nine models counted to 32 with 16 agents, a three-hour team deadline, xhigh
reasoning (Opus 4.5 at high, its top level), each route's full output limit,
compaction at 75% of each verified context window, the sandbox off and one seed
per row. Open models ran on their developers' own APIs through OpenRouter
(`moonshotai`, `z-ai/fp8`), Claude models on OpenRouter pinned to Anthropic, and
GPT models through Middleman. Time runs from release to the last accepted
submission.

| Model | Run | Commit | Log | Score | Time | Tokens | Board messages (DMs) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Opus 4.5 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-16-opus45-202610-6rk6tlun3709pycj) | `551b1bb` | [log](../logs/2026-10-08T13-58-22-00-00_counting_4NDQB5rmotCGzvv7dSTZJh.eval) | 1.00 | 152 s | 6.0M | 61 (0) |
| Claude Opus 5.5 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-16-opus55-202610-b2tzqsqp1u1n90hj) | `551b1bb` | [log](../logs/2026-10-08T13-58-48-00-00_counting_8G7iNpXZqQDQcLUfsGCiU4.eval) | 1.00 | 243 s | 6.8M | 37 (0) |
| GPT-6 Luna | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-16-luna-nobudget-2ponsu9imu6zkfz7) | `cc1207a` | [log](../logs/2026-10-08T14-42-49-00-00_counting_n8ot2SfxYNXYZ6JWem5ejU.eval) | 1.00 | 267 s | 9.0M | 135 (36) |
| GPT-6.1 Sol | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-16-sol61-2026100-xqfc7xycgfhftmoe) | `551b1bb` | [log](../logs/2026-10-08T13-58-20-00-00_counting_bcgofusCyeYNynLMyTSNDB.eval) | 1.00 | 311 s | 2.8M | 79 (35) |
| GPT-6 Astra | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-16-astra-2026100-m6m1i6cfscdutdef) | `551b1bb` | [log](../logs/2026-10-08T13-58-12-00-00_counting_eJM6qpWp4FKnUTomvJcFSY.eval) | 1.00 | 449 s | 2.6M | 69 (42) |
| Claude Haiku 5.5 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-16-haiku55-nobud-z6tb859rggmts5fq) | `cc1207a` | [log](../logs/2026-10-08T14-42-47-00-00_counting_6yv8MsN5iE4zCYwa8fhv6D.eval) | 1.00 | 708 s | 217.1M | 368 (0) |
| Kimi K3 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-16-kimik3-202610-n1b8gdnbijqjz6k5) | `551b1bb` | [log](../logs/2026-10-08T13-58-28-00-00_counting_hN9fBLBzBsUMyjCvXYPoXy.eval) | 1.00 | 1208 s | 47.0M | 250 (85) |
| GLM 5.3 | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-16-glm53-nobudge-y6ou73exzdn49abr) | `cc1207a` | [log](../logs/2026-10-08T14-42-53-00-00_counting_5VVDX3bi8jVP9C3CjVbxBA.eval) | 0.94 | 731 s | 45.0M | 157 (34) |
| GLM 5.3 Flash | [eval-set](https://viewer.hawk.hawk.generalitylabs.ai/eval-set/counting-16-glm53flash-no-py06t5oe5m4cht46) | `cc1207a` | [log](../logs/2026-10-08T14-42-36-00-00_counting_3hcqR5QrXCguxGckzNiPDT.eval) | 0.75 | 689 s | 36.2M | 135 (29) |

The first launch at `551b1bb` gave every agent a 5M-token budget. That budget
bound four models (GLM 5.3 Flash, GPT-6 Luna, Haiku 5.5 and GLM 5.3), which were
rerun at `cc1207a` with no budget; the table shows the reruns. No agent reached
5M in the other five, and agents never see their budget, so those rows stand.
The budget-bound logs are kept in `logs/`: Luna placed 5 numbers in 3080 s and
GLM 5.3 Flash 22 in 1701 s. The first GLM 5.3 run counted 1 to 32 exactly in
about 600 s but crashed exporting the board journal; `a1045e3` retries that
export. Single seeds: GLM 5.3 scored 1.00 there and 0.94 in the rerun.

Claude Haiku 5.5 read 1.2% of its input from cache because only the Opus
configs carried the top-level `cache_control` setting described in README;
Opus 4.5 and 5.5 read about 90%. Claude Sonnet 3.5 is retired everywhere, and
GPT-5.6 Sol had no working route (README, Routes). Configs, probes and the
manifest are in `run-artifacts/hawk-counting-16-sweep-20261008/`.

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
