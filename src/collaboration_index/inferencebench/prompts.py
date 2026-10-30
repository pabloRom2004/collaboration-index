"""Collaboration instructions added to the pinned InferenceBench task prompt."""

from typing import Any

from collaboration_index.inferencebench.session import Session, current
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


LEASED_GPUS = Prompt(
    title="inferencebench_leased_gpus_and_submissions",
    role="subject",
    origin="Collaboration Index adaptation of InferenceBench with GPU leases and graded submissions",
    prompt="""{context}

GPUs: {gpus}

Development feedback: evaluate measures {evaluate_target} with
{requests} visible development requests and {quality} quality questions
(quick mode: {quick_requests} and {quick_quality}). It is fast and approximates
the final evaluation.

Submissions: submit snapshots /home/agent/task for the hidden final
evaluation. Snapshots are graded later, one at a time, on a separate GPU of the same type:
the evaluator starts ./start_server.sh fresh with PORT=8000 on one GPU and
measures held-out requests and the quality gate. This is slower than evaluate
and is the real measurement. Everything the server needs, including
installed packages and changed weights, must be inside /home/agent/task (for
example a virtual environment in /home/agent/task/.venv); files elsewhere are
not submitted. {submitters} submit at any time, any number of times, but
while {pending} snapshots wait for or are in grading, submit refuses new
content until a result arrives. A snapshot identical to an earlier one reuses
its grade. Every submission is graded, and {recipients} its outcome and
speedup; results contain no logs or inputs from the final evaluation.
{scoring} Without a valid submission the score is the 1.0 baseline. After
the session ends, the submission that sets the score is measured once more
with an integrity review. The Output Contract and Final Evaluation sections
above apply to each submission; a server that is still running when the
session ends is not scored.""",
)

# the GPU paragraph for each gpu_access mode; {shared} adds the team sentences
GPU_TEXT = {
    "scheduler": (
        "the gpu tool gives you exclusive GPUs; ask for several with count. Your "
        "bash and python commands see only the GPUs you hold (CUDA_VISIBLE_DEVICES). "
        "PORT is your first GPU's server port, and INFERENCEBENCH_GPUS and "
        "INFERENCEBENCH_PORTS list all of yours; GPU number k serves on port 8000+k. "
        "To run one server per GPU, set CUDA_VISIBLE_DEVICES and PORT for each server. "
        "Without GPUs, CUDA_VISIBLE_DEVICES is empty, and GPU use outside your GPUs is "
        "recorded. The final evaluation runs your server on one GPU. Give back GPUs you "
        "do not need. When one of several GPUs leaves you, your GPU processes whose "
        "CUDA_VISIBLE_DEVICES includes it are stopped; when your last GPU leaves you, "
        "every process you started while holding GPUs is stopped.{shared}"
    ),
    "unmanaged": (
        "your bash and python commands see every GPU of the machine "
        "(CUDA_VISIBLE_DEVICES lists all of them), and nothing reserves a GPU for you. "
        "INFERENCEBENCH_GPUS and INFERENCEBENCH_PORTS list every GPU and its port; by "
        "convention GPU number k serves on port 8000+k, and PORT is 8000. To run a "
        "server on chosen GPUs, set CUDA_VISIBLE_DEVICES and PORT for it. The final "
        "evaluation runs your server on one GPU.{shared}"
    ),
}
EVALUATE_TARGET = {
    "scheduler": "the server on your leased GPU",
    "unmanaged": "the server on a port you choose (default 8000)",
}
SCORING_TEXT = {
    "best_submit": (
        "The valid submission with the highest speedup sets the score; a later, "
        "slower submission does not lower it."
    ),
    "last_submit": (
        "The most recent valid submission sets the score, even when an earlier one "
        "was faster; a submission that is not valid never replaces it."
    ),
}

# sentences that differ between one agent and a team; the rest of LEASED_GPUS is shared
SOLO_TEXT = {
    "machine": "You have one development machine with {gpus} GPU(s); your files are in /home/agent/task.",
    "scheduler": "",
    "unmanaged": "",
    "submitters": "You may",
    "recipients": "you receive",
}
TEAM_TEXT = {
    "machine": (
        "Use message_board to choose a unique name, discover teammates and coordinate. "
        "All agents share one development machine with {gpus} GPU(s) and the files "
        "in /home/agent/task. Your model histories are separate; teammates cannot see "
        "your reasoning or replies. No agent has an assigned role."
    ),
    "scheduler": (
        " An agent without a GPU is always served before anyone gets an extra GPU: "
        "extra GPUs are lent only while nobody waits and are taken back first when "
        "someone does. A lease older than {lease_minutes:g} minutes can be taken back "
        "while another agent waits. No GPU is taken back while one of your commands "
        "or evaluate calls runs. gpu status shows who holds each GPU and their note. "
        "Files and background processes are shared, so coordinate edits and server "
        "lifecycles."
    ),
    "unmanaged": (
        " Teammates' commands run at the same time as yours and can use the same "
        "GPUs, ports and files, so agree on the message board who uses which GPU and "
        "port, and coordinate edits and server lifecycles."
    ),
    "submitters": "Any agent may",
    "recipients": "all agents receive",
}

TEAM_WORKSPACE = Prompt(
    title="inferencebench_team_workspace",
    role="subject",
    origin="Collaboration Index adaptation of InferenceBench with private workspaces on shared GPUs",
    prompt="""{team_context} {board}

Machine: one development machine with {gpus} H100 GPU(s). {sharing} By convention
GPU number k serves on port 8000+k; set CUDA_VISIBLE_DEVICES and PORT for each
server you start.

Your workspace: your bash and python commands start in your own directory
($AGENT_WORKDIR), a copy of the starter files from /home/agent/task, with its
Python environment ($AGENT_WORKDIR/.venv) active, so pip and uv pip install
into it. Build your server there.{others}

Development feedback: evaluate measures the server on a port you choose (default
8000) with the development requests and as many quality questions as the final
evaluation; quick mode uses fewer and is only a smoke test.

Submission: submit saves your workspace's code and the package list of each
Python environment in it, not the environments themselves; a workspace over
{megabytes} MB without them is refused, so keep model weights and caches outside
it or have start_server.sh create them. {replaces} After the deadline the
submission is restored at the same path, its environments are rebuilt from the
package lists, and ./start_server.sh runs fresh with PORT=8000 on GPU 0 for the
held-out requests and the quality gate, as the Final Evaluation section above
describes. Only the submission is graded: changes to /home/agent/task or other
folders are not, and the snapshot holds nothing outside your workspace. Without a
submission the score is the 1.0 baseline.""",
)

CONTINUE_TEAM = Prompt(
    title="inferencebench_continue_team",
    role="subject",
    origin="InferenceBench continuation for the team submission rule",
    prompt=(
        "You stopped without tool calls while time remains. {status} Only the newest "
        "submission is graded, so a later, worse submission lowers the score; a better "
        "one raises it. Continue improving and measuring with evaluate, and submit when "
        "a version beats the current submission."
    ),
)

CONTINUE_SUBMIT = Prompt(
    title="inferencebench_continue_submit",
    role="subject",
    origin="InferenceBench token-budget continuation adapted for graded submissions",
    prompt=(
        "You stopped without tool calls while budget remains. Continue improving and "
        "measuring the inference server with evaluate, and submit versions you want "
        "graded. Every submission is graded."
    ),
)


def team_context(agents: int) -> str:
    """State the team size without exposing evaluator participant identities."""
    if agents == 1:
        return "You are working alone on this task."
    return f"You are one of {agents} collaborating agents, with {agents - 1} teammates."


def budget_text(plan: dict[str, Any] | None, agents: int) -> str:
    """Describe the team token budget to each peer."""
    if plan is None:
        return ""
    if agents == 1:
        return f"You have your own budget of {plan['tokens']:,} tokens."
    if plan["mode"] == "shared":
        return (
            f"Your team shares one pool of {plan['team_pool']:,} tokens; every agent's "
            "usage draws from it, and all agents stop when it is used up."
        )
    return (
        f"You have your own budget of {plan['per_agent_cap']:,} tokens; the team's "
        f"{plan['planned_team_total']:,} tokens are split equally and cannot be transferred."
    )


def workspace_prompt(agents: int) -> str:
    """Describe the shared workspace and the team size to every peer."""
    from collaboration_index.inferencebench import team_workspace

    session = current()
    team = team_workspace.active()
    if team is not None:
        return team_workspace_prompt(agents, team.options)
    if session is None:
        return WORKSPACE.prompt.format(team_context=team_context(agents))
    return leased_gpu_prompt(agents, session)


def leased_gpu_prompt(agents: int, session: Session) -> str:
    """Render the GPU and submission instructions for the access mode and scoring rule, without team wording for a single agent."""
    options, dev = session.options, session.options["dev_eval"]
    access = options["gpu_access"]
    text = SOLO_TEXT if agents == 1 else TEAM_TEXT
    notes = "You can use message_board for your own notes." if agents == 1 else ""
    context = [
        team_context(agents),
        notes,
        text["machine"].format(gpus=options["workspace_gpus"]),
        budget_text(session.budget, agents),
    ]
    shared = text[access].format(lease_minutes=options["lease_seconds"] / 60)
    return LEASED_GPUS.prompt.format(
        context=" ".join(part for part in context if part),
        gpus=GPU_TEXT[access].format(shared=shared),
        evaluate_target=EVALUATE_TARGET[access],
        scoring=SCORING_TEXT[options["scoring"]],
        submitters=text["submitters"],
        recipients=text["recipients"],
        requests=dev["requests"],
        quality=dev["quality_samples"],
        quick_requests=dev["quick_requests"],
        quick_quality=dev["quick_quality_samples"],
        pending=options["max_pending_submissions"],
    )


def team_workspace_prompt(agents: int, options: dict[str, Any]) -> str:
    """Describe the shared GPUs, the agent's own workspace and the team submission rule."""
    solo = agents == 1
    return TEAM_WORKSPACE.prompt.format(
        team_context=team_context(agents),
        board=(
            "You can use message_board for your own notes."
            if solo
            else "Use message_board to choose a unique name, discover teammates and "
            "coordinate. Your model histories are separate; teammates cannot see your "
            "reasoning or replies. No agent has an assigned role."
        ),
        gpus=options["gpus"],
        sharing=(
            "Nothing reserves a GPU; your bash and python commands see every GPU."
            if solo
            else "All agents share every GPU and nothing reserves one: your bash and "
            "python commands see every GPU and run at the same time as teammates' "
            "commands, so agree on the message board who uses which GPU and port."
        ),
        others="" if solo else (
            " Teammates' workspaces are in /team/agents/ and you can read them; "
            "/team/shared is a common folder for notes and files."
        ),
        megabytes=options["max_snapshot_bytes"] // 1_000_000,
        replaces=(
            "Each submission replaces your earlier one."
            if solo
            else "The newest submission from any agent replaces earlier ones and becomes "
            "the team's submission, and every agent is told."
        ),
    )


def continue_prompt() -> str:
    """Select the continuation reminder for the current submission policy."""
    from collaboration_index.inferencebench import team_workspace

    if team_workspace.active() is not None:
        return CONTINUE_TEAM.prompt.format(status=team_workspace.status())
    return (CONTINUE if current() is None else CONTINUE_SUBMIT).prompt
