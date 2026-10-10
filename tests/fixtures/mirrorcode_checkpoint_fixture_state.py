"""Hold numerical fixture evidence across Inspect task-factory reloads."""

from types import SimpleNamespace

STATE = SimpleNamespace(
    saves=0,
    failed=False,
    calls=[],
    restored=set(),
    writer_frozen_checks=0,
    writer_saved=None,
)
