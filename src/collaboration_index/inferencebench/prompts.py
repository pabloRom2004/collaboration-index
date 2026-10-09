"""Collaboration instructions added to the pinned InferenceBench task prompt."""

from collaboration_index.prompts import Prompt

WORKSPACE = Prompt(
    title="inferencebench_shared_gpu",
    role="subject",
    origin="Collaboration Index adaptation of InferenceBench",
    prompt="""{team_context}
Use message_board to choose a unique name, discover teammates and coordinate.
All peers share one RunPod H100 GPU and the files in /home/agent/task. You build
one standalone start_server.sh together. Your model histories and token budgets
are separate; teammates cannot see your reasoning or replies. The board has a
global room and name-addressed direct messages. No peer has an assigned role.
Foreground bash, python and evaluate calls queue behind one shared lock. Files
and background processes remain shared: coordinate edits, server lifecycle and
GPU use. Avoid competing servers or experiments on the same GPU. evaluate runs
the original development evaluator and records feedback; final scoring restarts
the pod and measures the final shared launcher on held-out requests after all
peers finish. There is no submit tool. Keep the standalone launcher ready while
working until your own token budget or the team deadline ends.""",
)

CONTINUE = Prompt(
    title="inferencebench_continue_work",
    role="subject",
    origin="InferenceBench token-budget continuation adapted for symmetric peers",
    prompt=(
        "You stopped without tool calls while budget remains. Continue coordinating, "
        "implementing and measuring the shared inference server with evaluate. Keep "
        "the best standalone start_server.sh ready for final scoring after peer join."
    ),
)


def workspace_prompt(agents: int) -> str:
    """Disclose the team size without exposing evaluator participant identities."""
    context = (
        "You are working alone on this task."
        if agents == 1
        else f"You are one of {agents} collaborating agents, with {agents - 1} teammates."
    )
    return WORKSPACE.prompt.format(team_context=context)
