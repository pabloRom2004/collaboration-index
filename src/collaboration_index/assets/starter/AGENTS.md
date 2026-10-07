# Coding-agent context: collaboration benchmark starter

Read README.md, task.py and run_configs/default.yaml first. This project is a
numbered-answer benchmark using the shared `collaboration-index` core. Locate the
core checkout through the editable source in pyproject.toml, then read its
AGENTS.md, README.md and docs/architecture.md, docs/design.md and docs/handoff.md.
Those files explain the research goal, contracts and verified/unverified state.

## Preserve the experiment

The goal is emergent collaboration among equal peers, with one shared sandbox,
one team sample and one `.eval` per task invocation. Separate model histories
and per-agent budgets do not imply separate computers. Use the core board for
chosen names, global messages, DMs and counts; do not introduce text-file
communication or assign a leader by default. Oracle allocation is a distinct
privileged control, not proof of an optimal strategy.

The common harness supplies the team clock and unread global/DM counts before
each collaborative peer decision. Counts use scoped metadata polls and leave
messages unread until explicit reads; oracle allocation has no count reminders.
Reuse this behavior instead of adding a separate notification loop.

The supplied questions and smoke are authored fixtures. Mock outputs and token
usage verify plumbing and cannot establish model collaboration ability or an
ECI. A positive per-peer budget and explicit subject model are required for real
runs. Creating a config or generating this project does not authorize spending,
publication or deployment; carry forward the actual user's authorized scope.

## Implementation boundaries

Keep adjustable defaults in run_configs/default.yaml and explicit parameters in
task.py. Reuse installed core tools, board, harness, scorer and visualiser rather
than copying them. The current template wraps HLE-shaped numbered submission;
other game mechanics need a trusted core extension and validated scoring.
All authored Python functions/methods, tests and callbacks need summary docstrings.
Load the canonical Inspect and task QA skills for corresponding framework work
where available. Check current source/config behavior before changing defaults.

Reference answers belong in trusted controller records, never participant
messages/files. Other peers' private information and credentials must also stay
out of tools. Fixed IDs come from evaluator closures. Do not infer grades from
chat text: final scoring reads the typed core store. First accepted answers are
final, missing answers are losses, and failed grading is explicitly unscored.

Keep immutable public file access restricted. Preserve the unprivileged,
network-disabled Docker sandbox and separate scoped loopback board process.
Do not silently choose a fallback model/provider, relax safeguards or expose
operator files to make a task work. Future real-model runs need verified
provider/billing, effective context, compaction and appropriate resource evidence.
Checkpoint continuation and independent-sandbox topology are not supported here.

## Checks and artifacts

Use `uv sync` and `uv run python smoke.py` for the starter's free real-Docker path.
The smoke keeps verification/replay and deletes only its owned mock log. For core
changes run the core's documented Ruff, mypy, pytest, Docker and wheel checks,
with meaningful edge cases for changed mechanics. Run the core smoke after a
change that affects shared orchestration, communication or scoring.

Keep logs/ flat and .eval-only; all other run material belongs under
run-artifacts/. Preserve real evaluation logs. Keep credentials in private
external caches/environment, never Git or artifacts. Inspect sensitive real
runs through structural metadata and sanitized errors unless the user explicitly
asks for content review. Preserve unrelated repositories, boards and cloud jobs.

When handing off, state what was verified with fixtures versus real models,
which files/configs changed, known limits, and remaining owned resources. Use
bounded diagnosis for a stalled run instead of prolonged unchanged polling or
blind paid retries. Maintain these shared instructions in AGENTS.md; CLAUDE.md
is only a pointer.
