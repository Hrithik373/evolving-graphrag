"""Pure-Python Louvain modularity optimisation - the dependency-free compaction fallback.

Why this exists: label propagation is simpler, but on a small dense graph it collapses
everything into one community. Compaction driven by a degenerate clusterer would *destroy*
the structure incremental placement built, which is worse than not compacting at all. So
the fallback has to be a real modularity optimiser, not a toy.

Deterministic by construction: nodes are visited in index order and ties break on the
lowest community index, so the same graph always yields the same partition - which the
reproducibility requirement depends on. Prefers ``leidenalg`` when installed; this is what
runs in a slim container or a CI image without igraph.
"""

from __future__ import annotations

from collections import defaultdict


def _renumber(labels: list[int]) -> list[int]:
    mapping: dict[int, int] = {}
    out: list[int] = []
    for label in labels:
        if label not in mapping:
            mapping[label] = len(mapping)
        out.append(mapping[label])
    return out


def _one_level(
    n: int,
    adjacency: dict[int, dict[int, float]],
    weights: list[float],
    total_weight: float,
    resolution: float,
    max_passes: int = 20,
) -> list[int]:
    """Phase 1: move each node to the neighbouring community with the best modularity gain."""
    community = list(range(n))
    # Sum of incident edge weights per community (self-loops counted twice, as in Louvain).
    tot = list(weights)
    two_m = 2.0 * total_weight
    if two_m == 0:
        return community

    for _ in range(max_passes):
        moved = False
        for node in range(n):
            own = community[node]
            k_i = weights[node]

            # Weight from this node into each neighbouring community.
            into: dict[int, float] = defaultdict(float)
            for neighbour, weight in adjacency[node].items():
                if neighbour != node:
                    into[community[neighbour]] += weight

            # Take the node out of its community before scoring alternatives.
            tot[own] -= k_i
            best_community = own
            best_gain = into.get(own, 0.0) - resolution * tot[own] * k_i / two_m

            for candidate, k_i_in in sorted(into.items()):
                if candidate == own:
                    continue
                gain = k_i_in - resolution * tot[candidate] * k_i / two_m
                if gain > best_gain + 1e-12:
                    best_gain = gain
                    best_community = candidate

            tot[best_community] += k_i
            if best_community != own:
                community[node] = best_community
                moved = True
        if not moved:
            break
    return community


def louvain(
    node_count: int,
    edges: list[tuple[int, int, float]],
    resolution: float = 1.0,
    max_levels: int = 10,
) -> list[int]:
    """Return a community index per node. Deterministic for a given input."""
    if node_count == 0:
        return []
    if not edges:
        return list(range(node_count))

    # Working graph for the current level; starts as the input graph.
    mapping = list(range(node_count))  # original node -> current super-node
    current_n = node_count
    current_edges = edges

    for _ in range(max_levels):
        adjacency: dict[int, dict[int, float]] = {i: defaultdict(float) for i in range(current_n)}
        weights = [0.0] * current_n
        total_weight = 0.0
        for source, target, weight in current_edges:
            adjacency[source][target] += weight
            adjacency[target][source] += weight
            weights[source] += weight
            weights[target] += weight
            total_weight += weight

        partition = _renumber(_one_level(current_n, adjacency, weights, total_weight, resolution))
        communities = max(partition) + 1
        if communities == current_n:
            break  # nothing merged; we are done

        mapping = [partition[node] for node in mapping]

        aggregated: dict[tuple[int, int], float] = defaultdict(float)
        for source, target, weight in current_edges:
            a, b = partition[source], partition[target]
            aggregated[(a, b) if a <= b else (b, a)] += weight
        current_edges = [(a, b, w) for (a, b), w in aggregated.items()]
        current_n = communities
        if communities == 1:
            break

    return _renumber(mapping)
