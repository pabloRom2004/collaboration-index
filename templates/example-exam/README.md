# Collaboration benchmark starter

This project uses the installed collaboration-index core: identical Inspect
subagents, one shared Docker sandbox, one team .eval, fresh authenticated board,
message counts, and the same portable visualiser. No communication plumbing is
copied into this project.

```bash
uv sync
uv run python smoke.py
uv run inspect eval --run-config run_configs/default.yaml --model MODEL -T agents=8 -T token_limit_per_agent=100000 --log-dir logs
```

The first two commands are local authored mocks. The third is an interface
example, not a paid launch. Docker must be running. The smoke renders
run-artifacts/replay.html and removes only its own mock .eval file.

Replace questions.json with trusted question/answer records to prototype a new
numbered-answer task. Only the public question fields reach the shared sandbox;
answer references remain controller-side. Exact matching is for these fixtures.
For open-ended answers use answer_judge=hle_json_judge and bind a grader model.
Use the core HLE task for pinned HLE data.

All adjustable task defaults live in run_configs/default.yaml. The model and
per-agent token budget have no paid defaults. The package source path in
pyproject.toml points to the local core checkout; regenerate with collaboration-new
or change that path when moving machines. For other game mechanics, use
make_task/prepare_team/team_agents and a trusted game/scorer extension; the current
core implements the three shipped mechanics and is not an arbitrary game plugin.
