"""Register the five evaluation entrypoints with Inspect."""

from collaboration_index.colouring import colouring
from collaboration_index.counting import counting
from collaboration_index.exploitbench import exploitbench
from collaboration_index.hle import hle_collaboration
from collaboration_index.inferencebench import inferencebench
from collaboration_index.mirrorcode import mirrorcode
from collaboration_index.spelling import spelling

__all__ = [
    "colouring",
    "counting",
    "exploitbench",
    "hle_collaboration",
    "inferencebench",
    "mirrorcode",
    "spelling",
]
