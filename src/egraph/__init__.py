"""evolving-graphrag: an incrementally-maintained knowledge-graph RAG index.

The contribution is the maintenance layer: when a document changes we touch the minimal
affected subgraph and the minimal set of community summaries, and we never rebuild.
"""

__version__ = "0.1.0"
