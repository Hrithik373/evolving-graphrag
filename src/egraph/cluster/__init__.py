"""Community detection: incremental placement on the hot path, Leiden on compaction."""

from egraph.cluster.leiden import active_backend, cluster, label_propagation
from egraph.cluster.louvain import louvain
from egraph.cluster.placement import new_community_id, place, place_all

__all__ = [
    "active_backend",
    "cluster",
    "label_propagation",
    "louvain",
    "new_community_id",
    "place",
    "place_all",
]
