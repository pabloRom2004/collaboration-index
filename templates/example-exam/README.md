# Numbered-answer collaboration starter

This is a small benchmark project built on the installed `collaboration-index`
core. It gives a team one numbered question batch, equal native Inspect peers,
one shared Docker sandbox, one team `.eval`, a fresh authenticated board and the
same collective replay used by the other benchmarks. It imports that machinery;
it does not copy another board, harness or visualiser into this project.

The included two questions are authored harmless fixtures. Their smoke verifies
infrastructure, not model capability. Replace them with a trusted question set
and validated grading contract to prototype another division-of-labour task.
For actual pinned HLE data, use the core `hle_collaboration` task instead.

## Installation and free smoke

Use Python 3.12, `uv` and a running Docker daemon:

```bash
uv sync
uv run python smoke.py
uv run collaboration-view --artifacts run-artifacts --port 14369
```

Open <http://127.0.0.1:14369/replay.html>. The smoke uses scripted native mock
agents, the real shared container, board, atomic submissions, scorer and renderer.
It saves `run-artifacts/verification.json` and `run-artifacts/replay.html`, then
removes only its own mock `.eval` file. Token counts are synthetic and fixture
code knows the expected answers. Choose another unused port if 14369 is occupied.

The generated `pyproject.toml` contains an editable path dependency pointing to
the local core checkout. If you move machines, update that path or regenerate
with `collaboration-new --core-path /absolute/path/collaboration-index`. The
installed example inside the core repo uses `../..`. A generated project is
locally runnable; neither this starter nor the core has a published package
remote supplied by the generator.

## Files and ownership

| File | Purpose |
| --- | --- |
| `task.py` | Public `toy_exam` entrypoint wrapping the common numbered-answer task. |
| `run_configs/default.yaml` | All adjustable task defaults and native run configuration. |
| `questions.json` | Trusted authored records, including controller-only references. |
| `smoke.py` | Free native/mock/Docker verification and replay production. |
| `AGENTS.md`, `CLAUDE.md` | Fresh coding-agent context and shared-instruction pointer. |
| `pyproject.toml` | Dependency on the reusable collaboration core. |
| `logs/` | Flat directory containing only retained real `.eval` files. |
| `run-artifacts/` | Boards, journals, receipts, public question file and replay. |

The core owns authenticated communication, shared orchestration, submission
mechanics, native limits, scorer and frontend. Find its source path in
`pyproject.toml`, then read its README, AGENTS and architecture/design documents
before modifying those components.

Every collaborative agent gets the team clock and unread global-board/DM counts
before its first decision and each later decision. The shared core polls only
counts; messages stay unread until the agent reads them with `message_board`.
Unavailable counts are reported explicitly, and oracle controls have no count
reminders. The starter inherits this without extra tools or configuration.

## Supplying a question batch

The file is a JSON list. A valid authored record looks like:

```json
{
  "id": "authored-france",
  "question": "What is the capital of France?",
  "answer": "Paris",
  "answer_type": "exactMatch"
}
```

`id`, `question` and `answer` must be nonempty strings, and IDs must be unique.
`answer_type` is optional metadata; it does not independently select a judge.
The loader assigns contiguous one-based question numbers. References remain in
trusted controller metadata. The public shared file includes only number, ID,
question and answer type. Participants read the entire exam or numbered ranges
through `read_file`; they cannot use it to read arbitrary paths.

`submit_answer(question_number, answer)` records the first valid answer
atomically. Duplicate answers cannot replace earlier ones. The team gets no
correctness feedback until scoring. Missing answers earn zero; an accepted
answer is not necessarily correct. All peers use the same task/tools and their
own histories and token budgets. Collaborative peers coordinate through global
messages and DMs instead of shared text files.

## Configuring and running a real model

The maintained config selects two collaborative agents, one epoch, the title
`Example exam`, automatic compaction at 0.75, this local question file and exact
fixture judging. Model, grader binding and per-agent token budget remain unset.
Exact matching is only appropriate when it matches the question contract.
For open-ended answers, select `answer_judge=hle_json_judge` and explicitly bind
an authorized grader; that judge is a simplified equivalence prototype.

After the user authorizes a real run and its provider/billing is verified, set
`CI_SUBJECT_MODEL` and `CI_AGENT_TOKENS` and use:

```bash
uv run inspect eval \
  --run-config run_configs/default.yaml \
  --model "${CI_SUBJECT_MODEL:?Set the authorized subject model}" \
  -T agents=8 \
  -T token_limit_per_agent="${CI_AGENT_TOKENS:?Set the per-agent budget}" \
  --log-dir logs
```

For open-ended grading also add:

```bash
-T answer_judge=hle_json_judge \
--model-role "grader=${CI_GRADER_MODEL:?Set the authorized grader model}"
```

These are interface examples, not launch authorization. Use task `team_time_limit`
for a bounded solving deadline when appropriate; do not silently substitute a
sample token limit for the native peer budgets. Generic run settings belong in
native run-config blocks, and experiment copies belong under `run-artifacts/`.
The complete team is the sample selected by `--limit`; it is not one question.
Default epoch aggregation is a mean of team attempts, not best-of-N.

## Measurements and further extension

Team scoring records quality, coverage, completion, solving time, subject tokens,
message counts, communication calls, duplicates and unavailable judgments.
HLE-shaped completion means all questions were answered; it does not mean all
answers were correct. Judge cost/time are excluded from solving measurements.
The collective replay shows those results plus peer statuses and tokens, with
no fabricated individual benchmark scores or exported private model histories.
Board messages/submissions remain run data, and native logs can contain private
reference metadata. Preserve real logs and keep secrets outside the project.

For another numbered-answer task, change the data/title and validate the judge.
For another game mechanic, extend trusted core state/tools/scoring/replay with
meaningful tests; the starter is not a generic arbitrary-game plugin. Keep the
shared board and renderer rather than forking them. Checkpoint continuation and
independent-computer topology are not implemented by this starter.
