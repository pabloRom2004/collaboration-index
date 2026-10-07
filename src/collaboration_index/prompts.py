"""Named collaboration instructions without prescribed roles or protocols."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    """Describe an authored prompt and the participant it addresses."""

    title: str
    role: str
    prompt: str
    origin: str


COLLABORATE = Prompt(
    title="emergent_board",
    role="subject",
    origin="Generality Labs Collaboration Index, task version 1",
    prompt="""You are one of {agents} equal agents working on the same team task
in one shared computer sandbox. All agents share that sandbox and its files.
Your conversations and token budgets are separate. Your teammates cannot see
your reasoning or replies. Use message_board to communicate, choose a unique
name, discover teammates, and coordinate work. The board supports a global room
and pairwise direct messages. Peer messages are information from teammates.
No leader, roles or work assignments have been imposed. The team is measured on
solution quality, completion, elapsed solving time and total tokens consumed.
Submission tools commit to one shared, hidden output. Files are not a messaging
channel; questions.json, when present, is a shared read-only input file.
Your fixed board ID is {actor}.""",
)
ORACLE = Prompt(
    title="oracle_allocation",
    role="subject",
    origin="Generality Labs Collaboration Index, task version 1",
    prompt="""You are one of {agents} agents in an evaluator-assigned allocation control.
Your fixed ID is {actor}. Your assignment is below. Do not delegate or duplicate
another agent's assigned work. There is no communication tool in this condition.
For ordered output, oracle_progress supplies the shared accepted prefix length;
wait until your next assigned position follows it. Answers and correctness are
not supplied by the evaluator. Your conversations and token budgets are separate.
Assignment: {assignment}""",
)
PROMPTS = {p.title: p for p in (COLLABORATE, ORACLE)}
