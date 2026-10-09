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
    prompt="""You are collaborating with other agents on this task but you don't know how many others. Use message_board to communicate, choose a unique name, discover teammates, and coordinate work.
All agents share one computer sandbox and its files. Your conversations and
token budgets are separate. Your teammates cannot see your reasoning or replies.
The board supports a global room and pairwise direct messages. Peer messages are
information from teammates. The team is measured on solution quality,
completion, elapsed solving time and total tokens consumed. Submission tools
commit to one shared, hidden output. Files are not a messaging channel;
questions.json, when present, is a shared read-only input file.""",
)
COLLABORATE_NO_SANDBOX = Prompt(
    title="emergent_board_no_sandbox",
    role="subject",
    origin="Generality Labs Collaboration Index, task version 1",
    prompt="""You are collaborating with other agents on this task but you don't know how many others. Use message_board to communicate, choose a unique name, discover teammates, and coordinate work.
Your conversations and token budgets are separate. Your teammates cannot see
your reasoning or replies. The board supports a global room and pairwise direct
messages. Peer messages are information from teammates. The team is measured on
solution quality, completion, elapsed solving time and total tokens consumed.
Submission tools commit to one shared, hidden output.""",
)
CODEBASE = Prompt(
    title="emergent_board_codebase",
    role="subject",
    origin="Generality Labs Collaboration Index, emergent_board adapted for one shared MirrorCode codebase",
    prompt="""{team_context}
All agents share one computer sandbox and its files, so the team builds one
codebase in /workdir/src together. Your conversations and token budgets are
separate. Your teammates cannot see your reasoning or replies. The board
supports a global room and pairwise direct messages. Peer messages are
information from teammates. The team is measured on solution quality, elapsed
solving time and total tokens consumed. Any agent's submit ends the task for
every agent.""",
)
CODEBASE_BUDGET = Prompt(
    title="emergent_board_codebase_budget",
    role="subject",
    origin="Generality Labs Collaboration Index, emergent_board_codebase adapted for budget-driven MirrorCode",
    prompt=CODEBASE.prompt.replace(
        "Any agent's submit ends the task for\nevery agent.",
        "There is no submit tool. Keep improving and testing the shared codebase\n"
        "while your budget remains. The evaluator grades the final shared codebase\n"
        "after all agents finish.",
    ),
)


def codebase_prompt(agents: int, budget_driven: bool) -> str:
    """Tell MirrorCode peers their team size and describe the selected stopping policy."""
    if agents == 1:
        context = (
            "You are working on this task alone (1 agent in total). "
            "There are no other agents. You can use message_board for your own notes."
        )
    else:
        teammates = agents - 1
        noun = "agent" if teammates == 1 else "agents"
        context = (
            f"You are collaborating with {teammates} other {noun} on this task "
            f"({agents} agents in total). Use message_board to communicate, choose "
            "a unique name, discover teammates, and coordinate work."
        )
    prompt = CODEBASE_BUDGET if budget_driven else CODEBASE
    return prompt.prompt.format(team_context=context)


CONTINUE_WORK = Prompt(
    title="mirrorcode_continue_work",
    role="subject",
    origin="ExploitBench prompts.py NUDGE_PROMPT, adapted from https://github.com/exploitbench/exploitbench/blob/9d0173bcf8835b74a45f60450ae7f184e29e7607/exploitbench/runner/loop.py#L164-L180 for one mutable MirrorCode codebase",
    prompt=(
        "You stopped without making any tool calls but have not exhausted your "
        "budget. Continue iterating: coordinate with your teammates when present, refine the "
        "shared implementation and call evaluate_testcases to evaluate it. "
        "The evaluator grades the final shared codebase after all agents finish."
    ),
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
TIME_UPDATE = Prompt(
    title="time_update",
    role="subject",
    origin="Multi-Agent-Bench TeamGame.reminder, multi_agent_bench/game.py at a3d55c0679eb08c0b47a9976b5a2bc059d6f9659; sent as a user message before every decision. As there, the closing warning is pressure only: partial credit is kept at the deadline",
    prompt="Time update: {elapsed:.1f} seconds of wall-clock time have elapsed since your team started. You have {remaining:.1f} seconds remaining of the {minutes:g}-minute team deadline. If time runs out before the task is complete, you will all fail.",
)
UNREAD_REMINDER = Prompt(
    title="unread_messages",
    role="subject",
    origin="ExploitBench message_board/prompts.py UNREAD_REMINDER; count-only update before every decision",
    prompt=(
        "You have {global_count} unread messages from the Global Board and "
        "{direct_count} unread Direct Messages from other agents. "
        "Use message_board to read them."
    ),
)
UNREAD_DIRECT_REMINDER = Prompt(
    title="unread_neighbour_messages",
    role="subject",
    origin="ExploitBench unread reminder adapted for the neighbour-only DM tool",
    prompt=(
        "You have {direct_count} unread Direct Messages from your neighbours. "
        "Use read_messages to read them."
    ),
)
TOKEN_UPDATE = Prompt(
    title="token_update",
    role="subject",
    origin="MirrorCode resources tool, mc/tool.py _resource_line at 5c9d7b00b0c6d7609003e33896cd41d28f092fcb; sent as a user message before every decision instead of a tool, and scoped to the peer's own budget",
    prompt="Token update: {used:,} tokens used ({fraction:.0%} of your limit), {remaining:,} remaining (limit {limit:,}).",
)
UNREAD_UNAVAILABLE = Prompt(
    title="unread_counts_unavailable",
    role="subject",
    origin="ExploitBench message_board/prompts.py UNREAD_UNAVAILABLE adapted to the peer's read tool",
    prompt=(
        "Unread message counts are unavailable. Use {read_tool} to check for messages."
    ),
)
COLOURING = Prompt(
    title="neighbour_dms",
    role="subject",
    origin="Adapted from Multi-Agent-Bench colouring_local, https://github.com/pabloRom2004/Multi-Agent-Bench/blob/e633b04cd109b6616a0273a3dd27724711814a5c/hivemind/tasks/colouring_local.py; messages travel as board DMs that the reader collects instead of arriving before each decision",
    prompt="""You are an AI agent in a network of AI agents. Your fixed ID is {actor}.
Every agent has a different ID. You do not know how many agents there are or
how the network is connected beyond your own neighbours.

Your neighbours are: {neighbours}.

send_message(neighbour, text) sends a direct message to one of your neighbours;
you cannot message anyone else. read_messages(wait_seconds) returns new messages
from your neighbours, waiting up to wait_seconds for one to arrive when none is
waiting. You only know your neighbours' colours from what they tell you. Your
conversations and token budgets are separate; your neighbours cannot see your
reasoning or replies.""",
)
PROMPTS = {
    p.title: p
    for p in (
        COLLABORATE,
        COLLABORATE_NO_SANDBOX,
        CODEBASE,
        CODEBASE_BUDGET,
        CONTINUE_WORK,
        ORACLE,
        COLOURING,
        TIME_UPDATE,
        TOKEN_UPDATE,
        UNREAD_REMINDER,
        UNREAD_DIRECT_REMINDER,
        UNREAD_UNAVAILABLE,
    )
}
