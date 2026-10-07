"""Draw seeded hidden networks with a planted proper colouring using Multi-Agent-Bench's rules."""

import random
from typing import Any

PALETTE = ["red", "green", "blue", "yellow", "purple", "orange", "pink", "brown"]
TOPOLOGIES = ("random", "ring", "grid")


def planted_graph(
    count: int, rng: random.Random, topology: str, degree: float, k: int
) -> tuple[list[int], list[tuple[int, int]]]:
    """Return a hidden class per node and edges that only join different classes."""
    if topology == "ring":
        classes = [i % k for i in range(count)]
        if count % k == 1 and count > 1:  # the wrap edge would join class 0 to class 0
            if k == 2:
                raise ValueError("An odd ring cannot be coloured with 2 colours")
            classes[-1] = 1
        edges = {
            tuple(sorted((i, (i + 1) % count)))
            for i in range(count)
            if count > 1 and i != (i + 1) % count
        }
    elif topology == "grid":
        width = max(1, round(count**0.5))
        classes = [((i % width) + (i // width)) % 2 for i in range(count)]
        edges = {(i, i + 1) for i in range(count - 1) if (i + 1) % width} | {
            (i, i + width) for i in range(count - width)
        }
    else:
        # No isolated nodes: an isolated agent can do nothing useful yet must still
        # set a colour before the network counts as solved. Repair rather than redraw:
        # join each isolated node to a node of another class, then drop an edge whose
        # ends both keep a neighbour, so the edge count and planted colouring hold.
        classes = [i % k for i in range(count)]
        rng.shuffle(classes)
        candidates = [
            (i, j)
            for i in range(count)
            for j in range(i + 1, count)
            if classes[i] != classes[j]
        ]
        target = round(degree * count / 2)
        edges = set(rng.sample(candidates, min(len(candidates), target)))
        degree_of = [0] * count
        for a, b in edges:
            degree_of[a] += 1
            degree_of[b] += 1
        for v in [v for v in range(count) if degree_of[v] == 0 and count > 1]:
            partners = [u for u in range(count) if classes[u] != classes[v]]
            if not partners:
                continue
            u = rng.choice(partners)
            edges.add(tuple(sorted((v, u))))
            degree_of[v] += 1
            degree_of[u] += 1
            removable = [
                e
                for e in edges
                if v not in e and degree_of[e[0]] > 1 and degree_of[e[1]] > 1
            ]
            if removable and len(edges) > target:
                a, b = rng.choice(sorted(removable))
                edges.discard((a, b))
                degree_of[a] -= 1
                degree_of[b] -= 1
    return classes, sorted(edges)  # type: ignore[arg-type]


def draw_network(
    agents: int, seed: int, colours: int, topology: str, degree: float
) -> dict[str, Any]:
    """Map a seeded planted graph onto the team's fixed board IDs."""
    if type(colours) is not int or not 2 <= colours <= len(PALETTE):
        raise ValueError(f"colours must be an integer from 2 to {len(PALETTE)}")
    if topology not in TOPOLOGIES:
        raise ValueError("topology must be random, ring or grid")
    if topology == "random" and degree <= 0:
        raise ValueError("degree must be positive for a random network")
    names = PALETTE[:colours]
    classes, edges = planted_graph(
        agents, random.Random(seed), topology, degree, colours
    )
    actors = [f"agent_{i}" for i in range(agents)]
    neighbours: dict[str, list[str]] = {actor: [] for actor in actors}
    for a, b in edges:
        neighbours[actors[a]].append(actors[b])
        neighbours[actors[b]].append(actors[a])
    return {
        "colours": names,
        "topology": topology,
        "degree": degree,
        "edges": [[actors[a], actors[b]] for a, b in edges],
        # sorted edges already list each node's neighbours in ascending ID order
        "neighbours": neighbours,
        "planted": {actors[i]: names[c] for i, c in enumerate(classes)},
    }
