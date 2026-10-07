"""Register the three evaluation entrypoints with Inspect."""

from collaboration_index.counting import counting
from collaboration_index.hle import hle_collaboration
from collaboration_index.spelling import spelling

__all__ = ["counting", "hle_collaboration", "spelling"]
