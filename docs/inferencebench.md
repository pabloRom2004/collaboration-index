# Shared-GPU InferenceBench

The `collaboration_index/inferencebench` task adapts the public
[InferenceBench Inspect port](https://github.com/pabloRom2004/inferencebench-eval/tree/8241a435ebe1cbb7fe5355f3b2ee3b7a85be884b).
Each scenario and seed pair is one team sample: native peers have separate model
histories and token ceilings, and share one RunPod H100, one workspace and one
message board. They choose names and coordinate without evaluator-assigned roles.

This is the default mode. The [leased-GPU mode](#leased-gpu-mode) gives the
team 1 to 8 leased workspace GPUs, a separate grading pod and graded
submissions.

## Usage

Install the optional runtime with:

```bash
uv sync --extra hle --extra mirrorcode --extra inferencebench --group dev
```

Real evaluations require separately verified RunPod and subject-model work
credentials, plus an explicitly bound `integrity` model role. The controller
must run on a supported remote host. On Hawk, select `gpu_management: controller`:
the task owns the external RunPod pod and its remote tools, with no native Inspect
sandbox. This uses Hawk's standard controller mode; Hawk's Kubernetes sandbox
isolation does not apply to RunPod. Strict isolation is rejected by Hawk.
The default `gpu_management: inspect` retains the original custom sandbox for
other supported controllers. A run is not authorized merely by this example:

```bash
uv run inspect eval \
  --run-config src/collaboration_index/inferencebench/run_configs/default.yaml \
  --model "${CI_SUBJECT_MODEL:?Set the authorized subject route}" \
  --model-role "integrity=${CI_INTEGRITY_MODEL:?Set the authorized judge route}" \
  -T agents=4 -T token_limit_per_agent=100000 \
  -T context_window=1000000 \
  --log-dir logs
```

The requested smoke uses Haiku 5.5 through the official Anthropic OpenRouter
endpoint and GPT-6.1 Sol through Generality Middleman. Routing, full output
limits and work credentials are launch settings; the task selects no model or
reasoning effort. Native compaction is configured at 75% of the verified context.
`context_window` and `grader_context_window` accept verified effective windows
for the peers and integrity judge respectively. The requested judge's 1.05M
window gives a 787,500-token threshold; Haiku's 1M window gives 750,000.

## Configuration and environment

[default.yaml](../src/collaboration_index/inferencebench/run_configs/default.yaml)
owns team settings and the complete `workload` mapping. Copy it under
`run-artifacts/<run>/` for an experiment. Scenario A, Mistral-7B-Instruct-v0.3,
seeds 21/1337, ten speed requests, 500 MMLU-Pro questions and the 0.95 relative
quality gate retain this public port's defaults. The solver and stopping policy
are the new collaboration condition; it has no historical team configuration.
Independent epochs use the source's mean reducer, which preserves an unavailable
judgment rather than dropping it. The backend's preserved default/original files
document its historical inputs,
not a claim of parity with a single-agent run.

`gpu_config` selects a complete RunPod provider YAML. The bundled provider
allocates one H100 per team sample and deletes it after grading/cleanup.
It never attaches to or deletes an unrelated pod. The original bootstrap and
filesystem-restart behavior are retained. GPU, CPU, RAM and disk settings are
inherited bounds, not measured minimum requirements for larger teams.
Controller mode checks the explicit judge before allocation, binds each resource
to one sample, and joins peers before final grading. Setup failures and task
cleanup shield deletion of the owned pod; native mode retains Inspect's sandbox
cleanup. A durable store receipt records the external resource ID and teardown.
All controller receipts, baselines and submission artifacts live below the
configured `artifact_dir`; use an absolute writable path such as
`/tmp/run-artifacts/inferencebench` on Hawk, whose working directory is read-only.

Peers retain upstream root access and Internet access for installing inference
engines. Operator/provider credentials stay on the controller. Peers use bash,
Python, web search, `evaluate` and the board. Foreground bash,
Python and evaluator calls serialize under one sample-local lock. This avoids
simultaneous foreground tests using the same GPU and measurement paths. Shared
files and background processes remain unrestricted; agents coordinate server
lifecycle and edits themselves. The lock is not GPU process isolation.

Every decision receives its own cumulative token usage and count-only
unread-board reminders. A no-tool turn continues while allowance remains.
There is no default wall-clock deadline: supply an explicit per-peer token
budget. If Pablo requests a deadline, it begins after the readiness barrier;
peers also receive elapsed/remaining time, and setup and final grading remain
outside that interval. There is
no submit tool or aggregate sample token cap. Four 100K caps give a planned 400K
team allowance; native response boundaries can overshoot it.

Checkpoint continuation remains unsupported because the shared harness cannot
restore every peer history, limit and board together.

## Leased-GPU mode

Setting `orchestration.enabled: true` requires `gpu_management: controller`.
The settings are in the `orchestration` block of
[default.yaml](../src/collaboration_index/inferencebench/run_configs/default.yaml).
Each team sample then owns two RunPod pods:

- The workspace pod has `workspace_gpus` H100s (1 to 8). Peers work there. It
  gets development requests, MMLU-Pro questions from the separate
  `dev_eval.quality_seed` and a development Transformers baseline. It does not
  get the held-out requests, seeds or baseline.
- The grading pod has one H100. The upstream preparation measures the held-out
  baseline and quality reference there. Peers reach it only through `submit`.

Peers get these tools in addition to `bash`, `python`, `web_search` and the
board:

- `gpu` acquires, gives back or reports workspace GPUs. With `acquire`, a peer
  asks for `count` GPUs in total and waits up to `wait_seconds` for a first
  one; a `note` tells teammates what the GPUs are for. With `release`, it gives
  back one GPU or all of them. `status` lists every GPU with its holder's board
  name and note. While a peer holds GPUs, its commands receive
  `CUDA_VISIBLE_DEVICES` with those GPUs, `PORT=8000+<first GPU>`, and
  `INFERENCEBENCH_GPUS` and `INFERENCEBENCH_PORTS` with every GPU and port.
  Without GPUs, `CUDA_VISIBLE_DEVICES` is empty.
- `evaluate` measures the server of one of the caller's GPUs (`gpu`, default
  the first), which must listen on port 8000 plus that GPU number. It sends
  `dev_eval.requests` requests and `dev_eval.quality_samples` quality questions
  (`quick_requests` and `quick_quality_samples` with `quick`) and reports a
  proxy speedup over the development baseline.
- `submit` snapshots `/home/agent/task` and queues it for the hidden final
  evaluation on the grading pod.
- `submissions` lists every team submission with its status and result.

GPU sharing follows these rules:

- Each peer holds at most one lease, which can cover several GPUs.
- Peers without a GPU are served in arrival order, and each gets one GPU
  before any peer gets an extra one.
- A lease receives extra GPUs, up to its `count`, only while no peer waits.
- When peers wait, extra GPUs are taken back first. A lease's last GPU is taken
  back only when the lease is older than `lease_seconds`, counted from its
  first grant, and another peer waits.
- No GPU is taken from a lease while one of its holder's commands or `evaluate`
  calls runs.

Before a GPU goes to a new holder, the controller stops what the previous
holder left on it. When a lease ends, it stops every process started under the
lease. When one GPU leaves a lease that keeps others, it stops only that
lease's GPU processes whose `CUDA_VISIBLE_DEVICES` includes that GPU. A monitor
samples GPU memory, utilization, power and processes about every
`monitor_seconds`, records GPU use outside a lease, and with `enforce: kill`
stops it.

Submissions are graded one at a time on the grading pod. The grader starts
`./start_server.sh` with `PORT=8000` on one GPU, so everything the server needs
must be inside `/home/agent/task`; a snapshot above `max_submission_bytes` is
refused. An identical snapshot reuses the earlier grade unless that grading
failed. `submit` refuses new content while `max_pending_submissions` snapshots
wait for or are in grading. Each result has an outcome (`valid`,
`invalid_submission`, `quality_failed`, `no_successful_requests`,
`undefined_performance` or `grading_error`) and a speedup rounded to two
decimals, which is 1.0 for every outcome but `valid`. Peers see results in
their next update and through `submissions`, never logs or inputs from the
grading pod.

The latest submission is the team's final answer. After all peers finish and
the queue is graded, the upstream scorer restarts the grading pod and measures
the latest submission. It calls the integrity judge only when the submission is
valid, passes the quality gate and has successful requests in every profile;
otherwise the score is 1.0. A team without a submission scores 1.0.

`token_budget: {mode, tokens}` needs this mode and `token_limit_per_agent:
null`; `tokens` must be at least the number of agents. `split` gives each peer
`tokens // agents`. `shared` gives the team one pool: each peer stops after the
turn that brings the team's usage to the pool, so the total can exceed
`tokens`.

`InferenceHistory:orchestration` in the sample store holds the lease summary,
GPU usage, submission ledger and events. The sample's artifact directory holds
one line per monitor sample in `gpu-samples.jsonl`, every snapshot archive as
`submissions/<digest>.tar.gz`, and for each graded submission N its
`submissions/N/final.json`, `final-server.log` and `final-evaluator.log`.

`scripts/inferencebench_scaling.py plan DESIGN --out DIR` writes one run config
per attempt of a team-size design and a `manifest.json` with the commands in a
seeded random order; the script's docstring lists the design keys.
`scripts/inferencebench_scaling.py collect LOGS... --out FILE` writes one JSON
line per attempt with its score and the records above.

## Scoring and provenance

Final grading starts after all peers join, restarts the owned pod, restores
trusted evaluator inputs, and measures the final shared `start_server.sh` on
held-out requests. The original deterministic quality gate and explicitly
role-bound integrity judge remain in place. Speedup is relative to Transformers;
it is not tuning uplift relative to an untouched vLLM server. Invalid submissions
and failed quality gates retain the source port's 1× fallback. Unavailable judge
verdicts remain unscored; infrastructure failures raise.

Development `evaluate` calls populate `InferenceHistory:checks`. These values
are feedback from the agent workspace, separate from the final authoritative
score. Fixed-actor workspace calls, peer usage, final outcome and board journal
are retained in typed Inspect stores. The common replay displays speedup as a
multiplier and does not invent per-agent performance scores.

The runtime, evaluator assets and licenses are under
`src/collaboration_index/inferencebench/backend/`. The source commit and per-file
SHA-256 hashes are in
[upstream.json](../src/collaboration_index/inferencebench/upstream.json).
Python imports are scoped to the destination package. The backend's GPU selector
also accepts the explicitly managed sample resource. Artifact paths are
redirected below the configured writable directory. The upstream task and
subject harness are replaced by the adapter, and native judge compaction follows
the configured 75% policy. The destination uses its locked Inspect 0.3.277 instead
of the source port's 0.3.263 dependency. The public
snapshot is deliberately distinct from the newer local InferenceBench checkout.

Scripted local fixtures exercise the native four-peer lifecycle, shared files,
queueing, development feedback, original scoring control flow and durable log
serialization. Their model tokens, GPU and measurements are synthetic and do
not establish real-GPU reliability or benchmark performance.

## Verified four-peer smoke

On 2026-10-09, Hawk job `ci-inference-haiku55-4x10-qekps8uyfx4yo95p`
completed from destination commit `7676d860d52dfd94b77c175f934d8d0d545c64e8`.
Four Haiku 5.5 peers used the same workspace, registered on the board, sent eight
messages and completed 22 foreground bash calls without tool errors. Each ended
on its native 100K allowance; recorded usage was 100,396, 114,680, 113,057 and
104,994 tokens. The team ran for 540 seconds after preparation. This verifies
real model work and collaboration, alongside the local mock and Docker checks.

Final grading restarted the same H100. The candidate server returned HTTP 400
for all ten held-out speed requests and all 500 quality requests. The original
quality gate failed (0.00 candidate accuracy versus 0.31 reference), assigning
its 1x fallback. This is a valid graded attempt, without a measured candidate
speedup. GPT-6.1 Sol was bound to the integrity role but was not called because
quality failed first; that real judge path remains unverified by this smoke.
No peer used the development `evaluate` tool.

The `.eval` is retained in root `logs/`; configuration, numeric diagnostics,
checksums and downloaded Hawk artifacts are under
`run-artifacts/inferencebench-import-20261009/`. The owned H100
`h75uos37w9p0f8` was deleted after remote artifact preservation, independently
verified absent from the work account. The unrelated stopped pod was preserved.
Hawk resource peaks were unavailable; these allocations are not measured minima.
