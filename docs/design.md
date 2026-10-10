# Measurement contract

HLE is a bag of independent questions and tests division of labour. Counting
is an ordered shared output and tests turn coordination. Spelling adds private
complementary resources and tests information sharing and ordered assembly.
Colouring restricts each peer to its graph neighbours and tests whether local
DM negotiation converges on a global constraint; its peers cannot use the
global room or discover non-neighbours.
Peers do not learn their evaluator IDs. MirrorCode version 3 tells peers the
total team size and number of other agents; N=1 explicitly works alone. The
authored coordination tasks keep team size hidden. The board shows only
names agents choose for themselves and permits global messages, name-addressed
DMs, pagination, blocking waits and read receipts, equally in every emergent arm.
This deliberately changes the anonymous/shared-files conditions in the old repo.

An oracle allocation is supplied by the evaluator without revealing solutions.
HLE assigns disjoint question numbers round-robin. Counting assigns consecutive
blocks within the same quotas; agents still have to wait for earlier blocks.
Spelling supplies a feasible target and per-character ownership under the same
hands. The ordered-task oracle exposes trusted prefix progress so it can sequence
work without messages. Record these extra affordances: this baseline removes
coordination difficulty and is not merely another prompt with equal information.

The oracle does not know question difficulty or model latency, so round-robin
is not a makespan optimum. For durations t_i, ideal unlimited-worker time is
max(t_i); with N workers, max(max(t_i), sum(t_i)/N) is a scheduling lower bound
only under the fixed-duration/no-overhead assumptions. Measured oracle accuracy
can be worse than collaborative accuracy. Compare Pareto curves at matched
quality, using model/source/settings/budget-matched controls and repeated seeded
teams at 1, 2, 4, 8, 16, 32. Keep fixed-work curves distinct from scaled-work curves.

The initial HLE choice is the audited gold/text subset at Inspect Evals' pinned
revisions. "Diamond" refers to GPQA Diamond, a different question benchmark.
No real HLE data or answers are committed. No model/provider or judge is silently
selected. The mock smoke is a test of plumbing, not a capability estimate.

Additional candidates from Multi-Agent-Bench: number_sequence has private
numbers and pairwise-order scoring, making it a useful fourth coordination
task. Python line assembly adds complementary work, but alternating-line rules
and a hidden generated program are artificial; port only after validating the
execution/scoring sandbox and USACO pool. Do not count multiple close variants
of counting/spelling as independent evidence for one broad capability axis.

Task time starts only after every peer is prepared and released. It ends at the
last required accepted submission, or at the joined partial attempt if incomplete.
Subject tokens include input, output and any provider-reported reasoning counted
as output; grading costs are excluded. Tool calls are retained as counts alongside
the durable board journal. Native token limits are checked at response boundaries.

MirrorCode reports final codebase quality and launch-to-final-log duration,
including setup and grading. Its budget-driven condition removes submit and
continues every peer to its native cap, with explicit reminders after no-tool
turns. The earlier voluntary-stop condition lets any peer end the team and
can leave allowance unused. Preserve and compare these stopping policies
separately: neither guarantees improved final quality, and continued edits can
regress the shared program. Budget exhaustion time does not establish the time
at which a chosen quality was first reached. Intermediate testing scores and
final authoritative grades are distinct measurements.

Team-size disclosure changes the information available to peers. Preserve the
earlier hidden-size attempts and label them separately from disclosed-size
runs; comparisons changing both disclosure and stopping policy cannot isolate
either effect.

MirrorCode has opt-in coordinated checkpoint continuation: private peer histories
and compaction, cumulative native limits, trusted state, shared files and the
complete authenticated board resume together at a completed-turn barrier. Other
task families retain their continuation rejection. A fresh attempt owns a fresh
board; a resumed attempt preserves the saved board and rotates credential hashes.
Local authored mocks do not establish real-provider resource minima or cluster
capacity. Actual Hawk verification and source identities are in the dated handoff.

Any future index needs preregistered quality constraints, normalization against
single-agent and oracle controls, benchmark weights and uncertainty estimates.
Keep failures, unscored judgments and provider errors visible. Bootstrap at the
team/seed level, not the individual-agent level. Require shared-board replay
and authoritative StoreModel records for every benchmark.
