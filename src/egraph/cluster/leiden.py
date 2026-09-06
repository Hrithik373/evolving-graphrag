"""Global community detection - the compaction half of the LSM analogy.

Leiden (via ``leidenalg``) when the optional extra is installed; otherwise the pure-Python
Louvain in ``cluster/louvain.py``, so compaction still runs in CI, in a slim container and
on a machine that cannot build igraph. ``EGRAPH_CLUSTER_BACKEND`` pins the choice; ``auto``
prefers Leiden and falls back silently.

Label propagation is kept here for the ablation, but it is *not* the fallback: on a small
dense graph it collapses every entity into one community, and compaction driven by that
would erase the structure incremental placement built.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict

from egraph.cluster.louvain import louvain
from egraph.store.base import GraphStore

log = logging.getLogger(__name__)


def _graph(store: GraphStore) -> tuple[list[str], list[tuple[int, int, float]]]:
    entities = sorted(e.entity_id for e in store.all_entities())
    index = {entity_id: i for i, entity_id in enumerate(entities)}
    edges: list[tuple[int, int, float]] = []
    for relation in store.all_relations():
        source = index.get(relation.source_id)
        target = index.get(relation.target_id)
        if source is None or target is None or source == target:
            continue
        # Weight by support: a relation asserted by five chunks binds harder than one.
        edges.append((source, target, relation.weight * len(relation.provenance) or 1.0))
    return entities, edges


def label_propagation(
    entities: list[str],
    edges: list[tuple[int, int, float]],
    seed: int = 1337,
    iterations: int = 20,
) -> list[int]:
    """Deterministic weighted label propagation. Seeded shuffle + lowest-label tie-break
    make the output reproducible, which the eval requires."""
    labels = list(range(len(entities)))
    adjacency: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for source, target, weight in edges:
        adjacency[source].append((target, weight))
        adjacency[target].append((source, weight))
    rng = random.Random(seed)
    order = list(range(len(entities)))
    for _ in range(iterations):
        rng.shuffle(order)
        changed = False
        for node in order:
            neighbours = adjacency.get(node)
            if not neighbours:
                continue
            weights: dict[int, float] = defaultdict(float)
            for other, weight in neighbours:
                weights[labels[other]] += weight
            best = min(weights.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if best != labels[node]:
                labels[node] = best
                changed = True
        if not changed:
            break
    return labels


def _leiden(
    entities: list[str], edges: list[tuple[int, int, float]], resolution: float, seed: int
) -> list[int] | None:
    try:
        import igraph as ig
        import leidenalg
    except ImportError:
        return None
    graph = ig.Graph(n=len(entities), edges=[(s, t) for s, t, _ in edges], directed=False)
    weights = [w for _, _, w in edges]
    partition = leidenalg.find_partition(
        graph,
        leidenalg.RBConfigurationVertexPartition,
        weights=weights,
        resolution_parameter=resolution,
        seed=seed,
    )
    return list(partition.membership)


def cluster(
    store: GraphStore,
    backend: str = "auto",
    resolution: float = 1.0,
    seed: int = 1337,
) -> dict[str, int]:
    """Cluster the whole entity graph. Returns ``entity_id -> cluster index``."""
    entities, edges = _graph(store)
    if not entities:
        return {}
    labels: list[int] | None = None
    if backend == "label-propagation":
        labels = label_propagation(entities, edges, seed=seed)
    else:
        labels = _leiden(entities, edges, resolution, seed)
        if labels is None:
            if backend == "leiden":
                log.warning("leidenalg not installed; using the Louvain fallback")
            labels = louvain(len(entities), edges, resolution=resolution)
    return dict(zip(entities, labels, strict=True))


def active_backend(backend: str = "auto") -> str:
    """Which algorithm will actually run, given what is installed."""
    if backend == "label-propagation":
        return "label-propagation"
    try:
        import leidenalg  # noqa: F401
    except ImportError:
        return "louvain"
    return "leiden"
