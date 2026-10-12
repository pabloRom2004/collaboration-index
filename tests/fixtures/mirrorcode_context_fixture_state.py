"""Retain numerical context-fixture evidence across task factory reloads."""

from types import SimpleNamespace

STATE = SimpleNamespace(
    saves=0, failed=False, steps={}, restored=set(), summary_calls=0, requests=[]
)
