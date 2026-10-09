# Team-size scaling plan

This records the intended MirrorCode comparison and the current launch gate.
On 2026-10-08 the user narrowed the next paid work to Haiku 5.5 smokes at 32 and
64 agents, one epoch each, followed by review. Repair and local checks come first;
the user wants an explanation and a successful teach-back before launch. The
seven-configuration, six-model sweep and ExploitBench remain later phases.
After the side conversation, the user explicitly resumed the shared-computer
approach and authorized the 32/64-agent Haiku smokes and failure-repair iteration.
This replaces the temporary launch hold for those smokes; expansion follows
review of their actual results.

Both smokes subsequently completed at `77f2937`, with no testing or
reference-grading errors and durable `.eval` files. N=32 scored 34.96% overall
in 825.00 s from launch to final log, using 33.16M actual tokens; N=64 scored
1.09% in 784.74 s, using 33.66M. Both ended on native peer limits rather than
submit. Final scoring covered all 1,553 cases, and the board journals are
embedded. See [handoff.md](handoff.md#mailauth-scoring-repair-and-shared-workspace-smokes-2026-10-08)
and the local `run-artifacts/hawk-mirrorcode-haiku55-smokes-20261008/smoke-results.md`.
The six-model sweep remains at the evidence/resource-policy review phase.

On 2026-10-09 the user authorized an overnight Haiku-only sweep at all seven
team sizes, **one epoch each**, using the existing **250M total allowance per
team** plan. All seven are submitted, and real model/tool activity is verified
for each. Their independent Hawk controllers use the same source and resource
formula as the successful smokes. Receipts, current status and plotting helpers
are under `run-artifacts/hawk-mirrorcode-haiku55-sweep-250m-20261009/`.
The monitor collects final logs and plots before the next review. **No other
model may launch until the user explicitly approves it tomorrow.** This
authorization does not expand the later six-model study to paid preflights.

The user then explicitly authorized a fresh Haiku rerun with no submit tool and
an ExploitBench-style reminder when a peer stops calling tools with budget left.
The new condition keeps all seven team sizes, one epoch and the same 250M
aggregate allowance. Its artifacts live separately under
`run-artifacts/hawk-mirrorcode-haiku55-budget-driven-250m-20261009/`.
Preserve the earlier voluntary-stop attempts; these conditions answer different
stopping-policy questions and must not be pooled. Active-loop continuation keeps
the peer's history, usage and board; checkpoint continuation remains unsupported.
No six-model follow-up is authorized.

All seven hidden-size budget-driven Haiku attempts were launched at runtime commit
`5dc9b9c54037aab303cb57935e6b18da2581eaa3`; real model/tool work and the absence
of submit are verified for every peer. Testing and final grading continue under
the existing five-minute heartbeat. The earlier voluntary-stop sweep has all
seven verified final results and visually checked plots; see the dated handoff.

The user subsequently requested that MirrorCode peers know their team size and
explicitly authorized stopping these seven attempts and freshly relaunching
them. Version 3 states the total team size and number of other agents, with a
solo opening at N=1. The old attempts were interrupted and remain unscored;
their raw snapshots, receipts and verified cleanup are retained. Replacement
artifacts live under `run-artifacts/hawk-mirrorcode-haiku55-team-aware-250m-20261009/`.
They keep the seven sizes, one epoch, budget-driven policy and 250M per team,
with fresh boards and peer histories. The voluntary-stop baseline also hid
team size, so comparing it with the replacements changes both information and
stopping policy and cannot isolate either effect. The six-model review gate
still applies.

The latest authorization expands only this disclosed-size budget-driven Haiku
condition to three planned attempts per team size. After an approximately
three-hour infrastructure-health observation (not before 04:41:37 UTC on
2026-10-09), run two additional fresh one-epoch attempts at each N. Each retains
250M total team tokens. The two repeats run consecutively per size after the
preceding final grade and cleanup are verified, keeping at most seven active
teams. These are planned replicates regardless of valid score. The first
cohort's `repeat-plan.json` and the dated handoff contain the exact schedule,
prepared configs and authorization. Keep all three results and summarize mean
and sample standard deviation; the six-model gate remains closed.

## Questions

For a fixed total token budget, does adding agents to one shared box make a
team finish MirrorCode faster, score higher, or both, and how does that change
across models? The same design then runs on ExploitBench with open-weight
models, also in one shared box.

## Design

| Setting | Value |
| --- | --- |
| Benchmark | MirrorCode team task, `collaboration_index/mirrorcode` |
| Target | `mailauth` in Python; one target reused for every run |
| Team sizes | 1, 2, 4, 8, 16, 32, 64 |
| Budget | Immediate smokes: 32M total allowance each, giving 1M per peer at N=32 and 500K at N=64; planned comparison: 250M total per attempt, divided into equal, non-transferable peer caps |
| Deadline | None; the new budget-driven condition ends after every peer reaches its cap; the preserved voluntary-stop condition also permits any peer to submit |
| Agent | Native Inspect `react`, as now |
| Topology | Every agent in the same MirrorCode workspace container |
| Communication | The existing message board |
| Epochs | One each for the immediate 32/64-agent Haiku smokes; three per configuration for the later model sweep |
| Platform | Generality Hawk |

Each run reports MirrorCode's pass rate (all, visible and hidden cases), total
and per-agent tokens, cost, and board activity. The user wants end-to-end time
to achieving the task. Record setup, release, submit, peer join and final grading
separately so the chosen time endpoint is explicit. Current release-to-submit
measurements alone do not include all of those stages.

Do not assume 100% is achievable or define success that way yet. The user wants
score and timing retained for later analysis of whether more agents reach
similar quality sooner under the same total allowance. An infrastructure error
is unscored, never a zero or an ordinary task failure. A faster submit at lower
quality does not by itself show a better team. If later analysis uses a
time-to-quality threshold, declare that threshold and its comparable grading
signal, and account for attempts that never reach it.

The user prefers CPU/RAM to grow with team size up to a practical cap, with
larger teams sharing the available machine. The cap and measured allocations
remain to agree for the full comparison. The smokes reuse the current Compose
limits: N=32 has a 9-CPU/10-GiB workspace and four grading pipelines; N=64 has a
17-CPU/18-GiB workspace and eight pipelines. Each pipeline has three 2-GiB
grading containers, whose peak consumption and placement must be observed.
These are bounds, not measured minimum allocations or a single physical VM.
This compares team size together with its hardware allocation;
attributing speed differences to coordination alone needs a matched hardware
control. Record scoring-pipeline capacity and queue time alongside CPU/RAM.

## Target choice

The target should have medium difficulty, enough tests for smooth partial
scores, and work that a team can actually divide. From the MirrorCode paper's
per-target results (arXiv 2606.30182, Figure 2):

| Target | Bucket | Tests (visible / hidden) | Paper result | Fit |
| --- | --- | --- | --- | --- |
| `mailauth` | M, 16k lines of Rust | 851 / 702 | No model reached 99% even with 1B tokens; most runs scored 90 to 95% | Chosen: separate SPF, DKIM, DMARC and ARC commands offer work to divide; newer models' attainable scores remain unknown |
| `gotree` | M, 16k lines of Go, 40+ subcommands | 1,899 / 102 | Opus 4.7 and GPT-5.5 reach 99% but not 100%; Gemini 3.1 Pro does not reach 99% | Easier fallback; newer models may saturate it |
| `wren_cli` | M, interpreter | 853 / 694 | Opus 4.7 72% of runs solved; GPT-5.5 none | One shared interpreter core is harder to divide |
| `sed` | M, scripting language | 470 / 324 | Never reached 99% for any model | Harder; weaker models may score near zero |
| `rev` | Excluded from the paper | 156 / 52 | One Haiku 5.5 agent solves it in six minutes | Too easy to separate team sizes |

`mailauth` tests split across twelve commands, led by `received-spf` (326),
`dmarc-verify` (306), `auth-results` (298), `arc-verify` (233), `dkim-sign`
(132) and `dkim-verify` (121). Per-command pass rates can describe which parts
were implemented, but do not establish who divided the work or how effectively
they collaborated. The planned allowance is a quarter of the paper's 1B tokens
for this bucket; that does not establish a ceiling for these newer models.

## Models

Routes checked on OpenRouter on 2026-10-08. Open-weight models use their
developer's own endpoint with no fallback. Set `max_tokens` to each route's
full output limit and compaction at 75% of its context window.

| Model | Route | Context | Max output |
| --- | --- | --- | --- |
| Claude Haiku 5.5 (pilot) | OpenRouter, provider `anthropic` | 1,000,000 | 128,000 |
| Claude Opus 4.6 | OpenRouter, provider `anthropic` | 1,000,000 | 128,000 |
| Claude Opus 5.5 | OpenRouter, provider `anthropic` | 1,000,000 | 128,000 |
| GPT-5.6 Sol | Generality Middleman (the work key cannot reach OpenAI on OpenRouter) | verify | verify |
| GPT-6.1 Sol | Generality Middleman | verify | 128,000 |
| Kimi K3 | OpenRouter, provider `moonshotai/mxfp4` only | 1,048,576 | 943,718 |
| GLM 5.3 | OpenRouter, provider `z-ai/fp8` only | 1,048,576 | 131,072 |

## Readiness before the pilot

- Raise the MirrorCode team-size limit from 32 to 64 and verify the board,
  unread reminders and Hawk runner at 64 peers with a mock run.
- Publish the chosen target's Python images with the image workflow.
- Scale the workspace container's CPU and memory with team size. MirrorCode's
  compose file gives every service a fixed 2 GiB, so a 64-agent team would
  otherwise be measured against the box rather than its coordination.
- Verify the mailauth reference-scoring repair locally and on Hawk. The failed
  64-agent smoke had 20 testing errors and no final grade because reference
  execution timed out; those errors are not evidence about model quality.
- Measure `evaluate_testcases` latency and queueing under intended concurrency.
  Scoring uses a pool of pipelines (one per eight peers by default); tar packing
  remains serialized, and each pipeline serves one call at a time.
- Confirm prompt caching on each route. Without caching most of a run's input
  is billed at full price, which can make an Anthropic run several times more
  expensive than with it.
- Preflight each model route with one real call before its first run.

## Phases

1. Keep the explanation and recorded budget, infrastructure, scoring and recovery
   limits available; the user has authorized the current shared-computer smokes.
2. Fix and locally verify the readiness items above.
3. Haiku 5.5 smokes on Hawk at 32 and 64 agents, one epoch each; review their
   grades, errors, timings and resource usage before expanding.
4. Freeze the quality/time endpoints and resource policy, then review the
   six-model sweep with seven team sizes and three epochs per configuration.
5. Investigate ExploitBench's multi-agent grading and isolation separately,
   including concurrent/repeated grade calls, before applying the design there.
