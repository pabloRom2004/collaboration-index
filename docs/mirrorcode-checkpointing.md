# MirrorCode checkpoint continuation

Checkpoint continuation is opt-in; verified deployment status and source identities
are recorded in `docs/handoff.md`.
Existing attempts without checkpoints cannot be continued. Active runs retain
their immutable source and configuration.

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
namespace; Docker is verified locally and the Hawk backend still needs its smoke.

A checkpoint can wait for every in-flight turn, including a long provider retry.
It does not interrupt or save a partially completed request. Calls and filesystem
work after the last committed checkpoint can be lost and repeated. Original
logical peer caps continue from committed usage; total billed tokens can exceed
them because rolled-back work was already paid for. Preserve physical-attempt
usage and failures separately. The 300 model-request retries and 900-second
attempt timeout reduce transient failures but do not guarantee success.

Archive checkpoints copy the captured filesystem rather than incrementally
backing it up. The local small-fixture checks do not establish storage capacity,
checkpoint overhead or resource minima for 64 busy peers. Before scaling on Hawk, verify durable restore, real-provider compatibility
and representative resource/storage overhead through the actual pipeline.

Before the SQLite snapshot, the controller waits up to 60 seconds for the board service to drain all admitted requests, including database operations whose client reply was lost. A failure to drain aborts the checkpoint rather than saving a partial board. The health counter exposes counts only.
