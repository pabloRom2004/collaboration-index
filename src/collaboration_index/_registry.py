"""Register evaluation and development checkpoint QA entrypoints with Inspect."""

from collaboration_index.checkpoint_finished_qa import checkpoint_finished_qa
from collaboration_index.checkpoint_provider_compaction_qa import (
    checkpoint_provider_compaction_qa,
)
from collaboration_index.checkpoint_qa import checkpoint_ruff_qa
from collaboration_index.colouring import colouring
from collaboration_index.counting import counting
from collaboration_index.exploitbench import exploitbench
from collaboration_index.hle import hle_collaboration
from collaboration_index.inferencebench import inferencebench
from collaboration_index.mirrorcode import mirrorcode
from collaboration_index.spelling import spelling

__all__ = [
    "checkpoint_finished_qa",
    "checkpoint_provider_compaction_qa",
    "checkpoint_ruff_qa",
    "colouring",
    "counting",
    "exploitbench",
    "hle_collaboration",
    "inferencebench",
    "mirrorcode",
    "spelling",
]
