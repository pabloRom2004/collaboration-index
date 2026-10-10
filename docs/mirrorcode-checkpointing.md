# MirrorCode checkpoint continuation

Checkpoint continuation is opt-in; verified deployment status and source identities
are recorded in `docs/handoff.md`.
Existing attempts without checkpoints cannot be continued. Active runs retain
their immutable source and configuration.

A durable checkpoint preserves state, but cannot make an invalid provider request
valid. Five retained Kimi Ruff attempts failed on 2026-10-10 with explicit HTTP
400 context-limit rejections despite having 4–10 committed checkpoints. Their
full-output reservation plus accumulated input/reasoning exceeded the route
context before the configured compaction threshold. All archives, boards and
checkpoints are preserved and owned pods are zero; unchanged continuation is
blocked, with no fresh replacement or silent generation-setting change. Recovery
classification uses explicit status fields and exception types, never unqualified
numbers from traceback text. Authentication and integrity failures remain fatal.

Set `checkpoint_enabled: true` and `checkpoint_interval_seconds: 600` in the
MirrorCode task arguments for a future approved run. Native Inspect checkpoint
storage must remain enabled and durable on the controller. Keep logs and their
checkpoint directories under the run's `run-artifacts` folder while running;
move only finalized `.eval` files to the flat root `logs` folder. Retain the
checkpoint directories and failed logs for recovery with native `eval_retry`.
Use `incomplete_action="error"` for native retries so unavailable/incomplete
checkpoints fail visibly instead of silently starting a fresh paid trajectory.
Verify the equivalent strict resume behavior in the Hawk launch path.

One coordinator stops all live peers after complete model/tool turns. It saves
private conversations and namespaced native compaction, trusted team records,
cumulative peer usage, and the full controller-only SQLite board. Board restore
keeps identities, membership, unread delivery receipts and request deduplication;
it rotates bearer hashes before reopening the service. Plain bearer credentials
are not persisted. Finished peers do not spend more tokens on continuation.

The workspace archive captures `/workdir`, `/workspace`, `/root` and `/home/coder`. Scoring
containers are recreated from the same pinned images. Existing participant
processes are stopped during capture and resumed afterward, with PID/start-time
checks. Process RAM and files outside the listed paths are not captured. Peers
receive an explicit restore notice and must restart background processes they
still need. Final agent-complete checkpoints support grading-only continuation.
The implementation currently refuses custom agents, submit-enabled attempts and
team deadlines. Its process freeze requires an isolated Linux container PID
namespace. Docker restore is verified locally, and actual-model N2 Ruff restore
and native-compaction restore have verified resumed finals and physical cleanup
on Hawk. The representative N64 Hawk failure/restore has also reached an
authoritative resumed final with all 64 native caps and zero owned pods. The
separate actual-provider finished-peer arm has also completed without more model
work; the fifth QA arm also verified the native `resume_for_scoring` branch
after an injected scorer failure, retaining exactly the same private state,
5,866 logical tokens and two ModelEvents through full grading and cleanup. Full study
batches require all five resumed final proofs and cleanup. Model smokes can
proceed after the first four backend proofs.

A checkpoint can wait for every in-flight turn, including a long provider retry.
It does not interrupt or save a partially completed request. Calls and filesystem
work after the last committed checkpoint can be lost and repeated. Original
logical peer caps continue from committed usage; total billed tokens can exceed
them because rolled-back work was already paid for. Preserve physical-attempt
usage and failures separately. The 300 model-request retries and 900-second
attempt timeout reduce transient failures but do not guarantee success.

The barrier retains the first peer or snapshot failure when other peers abort in
response. Native Inspect logs therefore keep the original exception as the cause
of a secondary checkpoint-abort error. Local native Ruff Docker reproduced and
verified this diagnostic behavior. Already accepted jobs keep their original
source; a failed attempt with no committed checkpoint still cannot be restored.
The retained Opus 4.6 N64 failure on source `8da8680` demonstrates that checkpoint
configuration alone does not guarantee a recoverable state before the first commit.

Archive checkpoints copy the captured filesystem rather than incrementally
backing it up. The local small-fixture checks do not establish storage capacity,
checkpoint overhead or resource minima for 64 busy peers. Before scaling on Hawk, verify durable restore, real-provider compatibility
and representative resource/storage overhead through the actual pipeline.

Before the SQLite snapshot, the controller waits up to 60 seconds for the board service to drain all admitted requests, including database operations whose client reply was lost. A failure to drain aborts the checkpoint rather than saving a partial board. The health counter exposes counts only.

The new six-model pipeline preserves a distinct failed-physical-attempt journal
and resumes only the same owned eval-set, original configuration and source.
It reconciles pending OWN responses before another operation, verifies terminal
archives and zero owned pods, and requires a durable checkpoint. Fatal
authentication/integrity failures, absent checkpoints and repeated failures
without checkpoint progress stop automatic continuation for diagnosis.
Normally completed scores are never retried. Failed-log tokens may include
restored cumulative usage, so they cannot simply be added to resumed-log tokens
as an independent billing total. Preserve checkpoint rollback and every physical
archive when accounting for actual work.

The reporting helper verifies retained physical archives and each numeric checkpoint
baseline before adding discarded rollback tokens to final cumulative usage.
Missing usage, including compacted summary usage without ModelEvents, remains
unavailable for physical accounting rather than being filled with zero.

New v2 study configs pin image-pinning release
`2ab240e594e0109d6d8436ad2a0edd1d29285967` and every workspace/grading service to
the original registry manifest digest. Nine edge tests and a real Ruff Docker
fixture verified all three original images, 822-case final grading and exact
owned container/board cleanup. Tag-only configs are preserved as unlaunched revisions
when tag drift is found; existing accepted jobs remain immutable. A metadata
image reference is not an observed pod image ID, so report unavailable actual
image IDs separately.

A reachable native host-backup race was reproduced when restic traversed a live
transcript SQLite spool. The scoped adapter in `checkpoint_backup.py` freezes
only validated regular restore JSON exports, uses official SHA-pinned restic
0.19.1 with `--no-cache`, and accepts only a complete successful backup.
Native checkpoint commit and restore remain unchanged; missing or invalid
required exports and authentication/integrity failures remain fatal. The adapter
is guarded to verified Inspect 0.3.277 and replaces one entered checkpointer's
host-backup method, without changing global dependency files or the resolver.
Local pressure, finished-peer and native scoring-only Docker restoration passed.
Its additional actual-provider Hawk failure/restore arm has a verified resumed
final and zero owned pods as of 2026-10-10 14:29 UTC, with exact state/caps,
42,939 cumulative tokens, 822-case grading and 1,702 authored concurrent spool
writes without write errors. Full launch readiness is derived from that preserved
initial/final proof and exact source/config/log hashes.
