"""Critical-Path-Method (CPM) — pure stdlib, no networkx/numpy.

Input: list of step dicts {id, duration_days} and dep dicts {step_id, depends_on_step_id}.
Output: dict[step_id -> {es, ef, ls, lf, slack, critical}].
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


def compute_cpm(
    steps: list[dict[str, Any]],
    deps: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return CPM result keyed by step id (string).

    Steps without predecessors start at day 0.  Steps not reachable via DAG
    (e.g., in a cycle) get slack=None and critical=False.
    """
    if not steps:
        return {}

    ids = [str(s["id"]) for s in steps]
    dur = {str(s["id"]): int(s.get("duration_days") or 1) for s in steps}

    # successors[A] = list of Bs that A must finish before B can start
    successors: dict[str, list[str]] = defaultdict(list)
    predecessors: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {sid: 0 for sid in ids}

    for dep in deps:
        a = str(dep["depends_on_step_id"])
        b = str(dep["step_id"])
        if a in in_degree and b in in_degree:
            successors[a].append(b)
            predecessors[b].append(a)
            in_degree[b] += 1

    # Kahn topological sort
    queue: deque[str] = deque(sid for sid in ids if in_degree[sid] == 0)
    topo: list[str] = []
    remaining = dict(in_degree)

    while queue:
        node = queue.popleft()
        topo.append(node)
        for succ in successors[node]:
            remaining[succ] -= 1
            if remaining[succ] == 0:
                queue.append(succ)

    # Nodes not in topo are part of a cycle — treat gracefully
    topo_set = set(topo)

    # Forward pass: earliest start (ES) and finish (EF)
    es: dict[str, int] = {}
    ef: dict[str, int] = {}
    for sid in topo:
        if not predecessors[sid]:
            es[sid] = 0
        else:
            es[sid] = max(ef[p] for p in predecessors[sid] if p in ef)
        ef[sid] = es[sid] + dur[sid]

    if not ef:
        # all nodes in a cycle
        return {sid: {"es": None, "ef": None, "ls": None, "lf": None, "slack": None, "critical": False} for sid in ids}

    project_end = max(ef.values())

    # Backward pass: latest start (LS) and finish (LF)
    ls: dict[str, int] = {}
    lf: dict[str, int] = {}
    for sid in reversed(topo):
        if not successors[sid] or all(s not in topo_set for s in successors[sid]):
            lf[sid] = project_end
        else:
            lf[sid] = min(ls[s] for s in successors[sid] if s in ls)
        ls[sid] = lf[sid] - dur[sid]

    result: dict[str, dict[str, Any]] = {}
    for sid in ids:
        if sid not in topo_set:
            result[sid] = {"es": None, "ef": None, "ls": None, "lf": None, "slack": None, "critical": False}
        else:
            sl = ls[sid] - es[sid]
            result[sid] = {
                "es": es[sid],
                "ef": ef[sid],
                "ls": ls[sid],
                "lf": lf[sid],
                "slack": sl,
                "critical": sl == 0,
            }

    return result
