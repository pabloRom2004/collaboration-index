# Shared-workspace discussion: MirrorCode and ExploitBench

Recorded on **2026-10-08** from a side conversation with Pablo. This records
design decisions, source inspection, and local concurrency probes. No benchmark
implementation was changed and no paid evaluation was launched in that
discussion. Recheck current source and run evidence before relying on the dated
details below. The experiment scope and launch gate remain in
[scaling-plan.md](scaling-plan.md).

## Decision

- Keep the current shared-computer topology for the MirrorCode pilot, including
  64 peers. Separate agent VMs are not needed before the pilot.
- Peers keep private model histories and token budgets, share `/workdir/src`,
  and coordinate through the message board. Do not impose a leader, assignments,
  or a mandatory patch/merge workflow. Agents are responsible for coordinating
  edits; infrastructure must preserve reliable grading.
- Retain the existing grading protections. Do not remove them to make the
  environment unrestricted, and do not add a whole-VM freeze as a prerequisite.
  Let bounded tests establish whether a specific additional protection is needed.
- This is a reasonable architecture for smoke testing, not a finding that the
  full overnight sweep is ready or that all 64 agents will contribute useful work.
- This note does not authorize paid launches, publication, deferred refactors,
  or changes to the separate ExploitBench repository. Preserve the existing
  explanation, teach-back, resource agreement, and launch gates.

## Implementation inspected

MirrorCode asks the team to recreate a program's behavior in its chosen language
using the supplied documentation and reference executable. The scorer compares
candidate and reference stdout, stderr, and normalized exit codes on visible and
hidden cases; agents receive feedback on visible cases.

The [harness](../src/collaboration_index/harness.py) verifies that every peer sees
the same sandbox hostname and team identity. Model inference uses external APIs;
the shared computer runs agent commands, files, and grading rather than hosting
64 model weights. Sharing a workspace on Hawk does not establish that every
associated container is scheduled on one physical VM.

The [MirrorCode wrapper](../src/collaboration_index/mirrorcode/task.py) supplies
eight scoring pipelines at N=64 by default. Each has reference, visible-agent,
and hidden-agent grading containers. Calls queue when all pipelines are occupied.
A separate lock protects the fixed workspace tar path from other grading calls;
it does not exclude file edits or background writers.

Upstream grading runs the reference first, then archives the shared source and
copies that archive to the candidate grading containers. Later workspace edits
do not change the captured archive, but edits during copying can affect what it
contains. Source is not captured at the start of the tool call.

Any peer can call `submit`, and the opening prompt explicitly states that this
ends the team attempt. It sets the end flag; peers stop after their current turn,
and final scoring follows peer join. In-flight operations and background writers
are not frozen at the instant of submission.

The board is a separate loopback process alongside the Inspect controller,
accessed through trusted native tools. With a Hawk controller it runs remotely,
not through a laptop proxy. Normal finalization exports its journal into the
`.eval`. It is not currently a separate VM or an MCP server. Public GHCR image
publication under the user's GitHub namespace lets Hawk obtain the runtime;
that namespace is not the computer running the agents.

At N=64 the inspected defaults generate one workspace with 17 CPU and 18 GiB
memory limits, plus 24 grading containers limited to 2 GiB each. The combined
memory limits total 66 GiB; this is not measured peak consumption or a proven
minimum machine size. Grading containers have no explicit Compose CPU limits.
Verify the actual Hawk allocation and scheduling before drawing capacity claims.

## Checks and their limits

Ad hoc probes ran production helper/wrapper code against controlled in-memory
storage or harmless fixtures. No real benchmark grading containers or model
inference were exercised by these probes, and the probes were not added as
persistent regression tests.

| Probe | Observation |
| --- | --- |
| Unprotected MirrorCode scoring contexts sharing simulated storage | One call overwrote another's configuration; cleanup removed files needed by the other call. |
| Current MirrorCode pool, 64 synthetic calls across eight pipelines | All completed with no overlap within a pipeline. |
| Editing during an emulated source-copy interleaving | The archive contained different versions of two files; later edits left the completed archive unchanged. |
| Two unprotected calls to an ExploitBench-style singleton MCP fixture | One completed and one returned a collision tool error; this is not a deployed-grader result. |
| Existing ExploitBench collective broker, real MCP transport and 64 synthetic grade calls | All completed with one active grade at a time. |
| Foreground edits through that broker during fixture grading | Edits waited until grading completed. |
| A background writer started before fixture grading | It continued changing the fixture during grading. |

The ExploitBench checks establish broker behavior, not safety of its deployed
grader. Its exact benchmark image was not cached locally. Current upstream
grading creates per-call temporary directories, so do not claim that every pair
of overlapping `grade` calls necessarily crashes every image; test the pinned
image. Sequential repeated grading is a separate case from overlapping calls.

The existing [64-peer Docker test](../tests/test_mirrorcode.py) scripts two peers
to write and grade while the other 62 register and wait. That behavior is authored
by the mock model, not an observed decision by real agents. It checks team
plumbing, not capacity for 64 busy peers. Measure per-peer editing, command,
grading and communication activity alongside team results to establish actual
participation.

## Next steps before the pilot

1. Confirm mailauth reference grading on the exact local and Hawk images,
   including successful tool feedback and a final score. The earlier 20 failed
   testing calls and absent final score were infrastructure evidence, not a
   model-quality result.
2. Exercise overlapping grading requests on real containers, then all eight
   pipelines and queueing. Check result isolation and cleanup after timeouts.
3. Run a bounded rehearsal with 64 actively working scripted peers on the
   intended allocation. Measure peak CPU/RAM/disk, command latency, grading
   queue time, and cold/warm image effects.
4. Exercise edits during copying, background writers, and submission while
   another peer is busy. Establish the artifact actually graded and expose
   scoring errors separately from coordination mistakes.
5. Verify failure cleanup and durable `.eval` output. Do not assume checkpoint
   continuation or interrupted-attempt recovery is supported.
6. Verify provider concurrency/rate limits, prompt caching, and total-team budget
   division. Record setup, release, submission, peer join and final grading
   timestamps; hardware and grader capacity can affect end-to-end time.
7. After the existing launch gate is satisfied, run the planned Haiku 32- and
   64-agent smokes, one epoch each. Review actual work, collaboration, scores,
   errors, timings, resources and durable results before the larger model sweep.

## Deferred questions

Private agent VMs, a separately hosted task/board, and a reusable benchmark
adapter remain future work. A common wrapper can mediate native and MCP tool
calls, but cannot alone stop existing background processes or direct SSH access.
Pausing an entire environment can also pause the grader or services it needs.
Therefore a universal freeze is not a verified drop-in solution for arbitrary
benchmarks. Investigate ExploitBench's exact grading and stopping contracts
separately before extending this experiment there.
