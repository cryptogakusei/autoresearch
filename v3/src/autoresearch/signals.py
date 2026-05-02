from __future__ import annotations

from itertools import combinations
from math import prod
from typing import Any

from .descriptor import ExperimentDescriptor
from .models import Idea, IdeaTree


def update_signals(tree: IdeaTree, descriptor: ExperimentDescriptor) -> dict[str, Any]:
    completed = [
        idea
        for idea in tree.ideas.values()
        if idea.status == "done" and idea.metrics and idea.elements
    ]
    completed.sort(
        key=lambda idea: descriptor.sort_key(descriptor.get_idea_value(idea)),
        reverse=True,
    )
    if len(completed) < 4:
        return {"elements": {}, "pairs": {}, "count": len(completed)}

    midpoint = len(completed) // 2
    good = completed[:midpoint]
    bad = completed[midpoint:]
    element_ratios = _ratios(good, bad)
    pair_ratios: dict[str, float] = {}
    if len(good) >= 3 and len(bad) >= 3:
        pair_ratios = {
            k: v
            for k, v in _ratios(good, bad, pairs=True).items()
            if v > 3.0 or v < 0.33
        }
    return {"elements": element_ratios, "pairs": pair_ratios, "count": len(completed)}


def score_candidate(elements: list[str], signals: dict[str, Any]) -> float:
    ratios = [float(signals.get("elements", {}).get(el, 1.0)) for el in elements]
    if not ratios:
        return 1.0
    return prod(ratios) ** (1 / len(ratios))


def _ratios(good: list[Idea], bad: list[Idea], pairs: bool = False) -> dict[str, float]:
    keys = sorted(_collect_keys(good + bad, pairs=pairs))
    out: dict[str, float] = {}
    for key in keys:
        good_count = sum(1 for idea in good if key in _keys_for(idea, pairs=pairs))
        bad_count = sum(1 for idea in bad if key in _keys_for(idea, pairs=pairs))
        p_good = good_count / len(good) if good_count else 0.1
        p_bad = bad_count / len(bad) if bad_count else 0.1
        out[key] = p_good / p_bad
    return out


def _collect_keys(ideas: list[Idea], pairs: bool) -> set[str]:
    result: set[str] = set()
    for idea in ideas:
        result.update(_keys_for(idea, pairs=pairs))
    return result


def _keys_for(idea: Idea, pairs: bool) -> set[str]:
    if not pairs:
        return set(idea.elements)
    return {"+".join(pair) for pair in combinations(sorted(idea.elements), 2)}

