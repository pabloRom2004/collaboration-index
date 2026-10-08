# Team-size scaling plan

This is the agreed plan for measuring how team size changes MirrorCode accuracy,
time and cost, and then repeating it on ExploitBench. Settings marked
*proposed* still need the user's agreement before any paid run.

## Questions

For a fixed total token budget, does adding agents to one shared box make a
team finish MirrorCode faster, score higher, or both, and how does that change
across models? The same design then runs on ExploitBench with open-weight
models, also in one shared box.

## Design

| Setting | Value |
| --- | --- |
| Benchmark | MirrorCode team task, `collaboration_index/mirrorcode` |
| Target | *Proposed:* `mailauth` in Python (see below); one target reused for every run |
| Team sizes | 1, 2, 4, 8, 16, 32, 64 |
| Budget | One shared 250M-token budget per run: `token_limit_per_agent = 250_000_000 // agents` |
| Deadline | None; a run ends when an agent submits or every budget is spent |
| Agent | Native Inspect `react`, as now |
| Topology | Every agent in the same MirrorCode workspace container |
| Communication | The existing message board |
| Epochs | One for the Haiku pilot; three per configuration for the model sweep |
| Platform | Generality Hawk |

Each run reports MirrorCode's pass rate (all, visible and hidden cases), time
from release to submit, total and per-agent tokens, cost, and board activity.

## Target choice

The target should have medium difficulty, enough tests for smooth partial
scores, and work that a team can actually divide. From the MirrorCode paper's
per-target results (arXiv 2606.30182, Figure 2):

| Target | Bucket | Tests (visible / hidden) | Paper result | Fit |
| --- | --- | --- | --- | --- |
| `mailauth` | M, 16k lines of Rust | 851 / 702 | No model reached 99% even with 1B tokens; most runs scored 90 to 95% | Proposed: separate SPF, DKIM, DMARC and ARC commands to divide, room above every model |
| `gotree` | M, 16k lines of Go, 40+ subcommands | 1,899 / 102 | Opus 4.7 and GPT-5.5 reach 99% but not 100%; Gemini 3.1 Pro does not reach 99% | Easier fallback; newer models may saturate it |
| `wren_cli` | M, interpreter | 853 / 694 | Opus 4.7 72% of runs solved; GPT-5.5 none | One shared interpreter core is harder to divide |
| `sed` | M, scripting language | 470 / 324 | Never reached 99% for any model | Harder; weaker models may score near zero |
| `rev` | Excluded from the paper | 156 / 52 | One Haiku 5.5 agent solves it in six minutes | Too easy to separate team sizes |

`mailauth` tests split across twelve commands, led by `received-spf` (326),
`dmarc-verify` (306), `auth-results` (298), `arc-verify` (233), `dkim-sign`
(132) and `dkim-verify` (121). Per-command pass rates show how a team divided
the work. Our budget is a quarter of the paper's 1B tokens for this bucket, so
every model should stay below its ceiling.

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
- Measure how long one `evaluate_testcases` call takes on the chosen target. The team
  lock serializes these calls, so a long call becomes a queue at large teams.
- Confirm prompt caching on each route. Without caching most of a run's input
  is billed at full price, which can make an Anthropic run several times more
  expensive than with it.
- Preflight each model route with one real call before its first run.

## Phases

1. Agree the target and these settings.
2. Fix the readiness items above.
3. Haiku 5.5 smoke on Hawk at a small budget, then the seven team sizes at one
   epoch each.
4. The six-model sweep, three epochs per configuration.
5. ExploitBench with the same design, agents in one shared box, open-weight
   models on their official routes.
