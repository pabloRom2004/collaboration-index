# Reserved input compaction

Output-reserved models retain their full configured output allowance. The
remaining input capacity and its configured compaction fraction determine the
input trigger. The adapter conservatively counts serialized messages, replayed
reasoning and tool schemas, including its existing estimation margin.

Inspect's native summary iteration uses a smaller message-token estimate. A
summary can pass that native check while remaining above the adapter's trigger.
The reserved strategy therefore checks each summary using the same conservative
estimate as ordinary requests. It allows at most three further summaries. A
single rewrite can grow before a later rewrite fits, so it uses the remaining
bounded passes instead of declaring failure on the first increase. An
insufficient result still raises before ordinary inference; the trigger is never
relaxed. The native summary prompt remains unchanged.

Every summary uses the same model API, provider and full output configuration.
Native token accounting includes all summary calls and can stop the process at
its configured response boundary. The adapter preserves immutable conversation
history and the existing native checkpoint schema. It does not trim summaries
or participant answers. Existing fitting of copied tool outputs for summary
input remains unchanged.

On a route that merges consecutive system messages, native merging replaces
their IDs. A later compaction can mistake the original system prefix for
missing content and prepend it again. The checkpoint adapter therefore passes
its trusted original prefix to the reserved strategy. When a single native
merged system message contains only exact repetitions of that prefix, the
strategy restores the original messages and IDs in the compacted view. It
preserves every original instruction word, all other messages, the private
history and the checkpoint schema. Distinct content, multiple system messages
and content without native merge provenance are retained unchanged. Restoring
duplicate prefix identities can make an existing view fit without another
summary call; normal summary generation and its original prompt remain intact.

Local regressions exercise successful repeated fitting, tool schemas, failure
without progress, the iteration bound, tokenizer errors and a checkpoint round
trip. Scripted native Inspect evaluations verify scoring, model-event accounting
and token-limit enforcement. These are local controller checks, not evidence of
remote sandbox grading or a completed benchmark result.

The prefix regression also exercises five real native compaction rounds,
checkpoint restoration and the observed 364-copy system-message shape. Native
prefix growth reproduces on pinned Inspect 0.3.277 and isolated 0.3.284; the
adapter keeps one original prefix on both. All model outputs in these checks
are scripted, with no external inference.

The Kimi Ruff study's original source and failed archives remain immutable.
Its N1 compatible continuation changes only the pinned source revision to apply
this repair; its original model, provider, context, output, compaction fraction,
per-peer ceiling, cases, sandbox resources and grading rules remain recorded in
the accepted configurations and separate recovery journal.
