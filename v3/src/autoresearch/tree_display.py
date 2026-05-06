from __future__ import annotations

from typing import Any

from .descriptor import ExperimentDescriptor
from .models import Idea, IdeaTree


def render_idea_tree(tree: IdeaTree, descriptor: ExperimentDescriptor) -> None:
    try:
        from rich.console import Console
        from rich.tree import Tree
        from rich.text import Text
        _render_rich(tree, descriptor, Console())
    except ImportError:
        _render_plain(tree, descriptor)


def _render_rich(
    tree: IdeaTree, descriptor: ExperimentDescriptor, console: Console
) -> None:
    from rich.text import Text
    from rich.tree import Tree

    console = console.__class__(highlight=False, soft_wrap=True)

    best_id = _find_best_id(tree, descriptor)
    best_lineage = _lineage(tree, best_id) if best_id else set()

    display = Tree(Text("Idea Tree", style="bold"))

    roots = [idea for idea in tree.ideas.values() if idea.parentId is None]
    roots.sort(key=lambda i: i.id)

    for root in roots:
        node = display.add(_format_node(root, descriptor, best_id, best_lineage, tree))
        _add_children(tree, root.id, node, descriptor, best_id, best_lineage)

    console.print()
    console.print(display)

    stats = _tree_stats(tree, descriptor)
    console.print()
    console.print(
        f"  [dim]{stats['total']} ideas[/] · "
        f"[green]{stats['done']} done[/] · "
        f"[yellow]{stats['running']} running[/] · "
        f"[dim]{stats['pending']} pending[/] · "
        f"[red]{stats['failed']} failed[/]"
    )
    if best_id:
        best = tree.ideas[best_id]
        perf = descriptor.format_perf_short(best)
        console.print(f"  [bold yellow]★[/] Best: [bold]{best_id}[/] ({perf})")
    console.print()


def _add_children(
    tree: IdeaTree,
    parent_id: str,
    parent_node: Any,
    descriptor: ExperimentDescriptor,
    best_id: str | None,
    best_lineage: set[str],
) -> None:
    children = [i for i in tree.ideas.values() if i.parentId == parent_id]
    children.sort(key=lambda i: i.id)
    for child in children:
        node = parent_node.add(_format_node(child, descriptor, best_id, best_lineage, tree))
        _add_children(tree, child.id, node, descriptor, best_id, best_lineage)


def _format_node(
    idea: Idea,
    descriptor: ExperimentDescriptor,
    best_id: str | None,
    best_lineage: set[str],
    tree: IdeaTree | None = None,
) -> "Text":
    from rich.text import Text

    is_best = idea.id == best_id
    on_best_path = idea.id in best_lineage

    status_icon = {
        "done": "●",
        "running": "◐",
        "pending": "○",
        "failed": "✗",
    }.get(idea.status, "?")

    status_color = {
        "done": "green",
        "running": "yellow",
        "pending": "dim",
        "failed": "red",
    }.get(idea.status, "white")

    line = Text()

    if is_best:
        line.append("★ ", style="bold yellow")

    line.append(f"{status_icon} ", style=status_color)
    id_style = "bold" if on_best_path else ""
    line.append(idea.id, style=id_style)

    if idea.status == "done" and idea.metrics:
        perf = descriptor.format_perf_short(idea)
        outcome = idea.diagnosis.outcome if idea.diagnosis else None
        outcome_color = {
            "improved": "green",
            "regressed": "red",
            "neutral": "yellow",
        }.get(outcome or "", "dim")
        line.append(f"  {perf}", style=outcome_color)
        _add_bar(line, idea, descriptor, tree)

    hyp = idea.hypothesis
    if len(hyp) > 40:
        hyp = hyp[:37] + "..."
    line.append(f"  {hyp}", style="dim" if idea.status == "pending" else "")

    if idea.sourceRef and idea.sourceRef.startswith("seeds/math_"):
        line.append(" [math]", style="magenta dim")

    return line


def _add_bar(
    line: "Text", idea: Idea, descriptor: ExperimentDescriptor, tree: IdeaTree | None = None
) -> None:
    from rich.text import Text

    value = descriptor.get_idea_value(idea)
    if value <= 0:
        return

    if tree is not None:
        done_values = [
            descriptor.get_idea_value(i)
            for i in tree.ideas.values()
            if i.status == "done" and i.metrics
        ]
        if done_values:
            worst = max(done_values) if descriptor.primary_metric().direction == "minimize" else min(done_values)
            best = min(done_values) if descriptor.primary_metric().direction == "minimize" else max(done_values)
            span = abs(worst - best) if worst != best else 1.0
            if descriptor.primary_metric().direction == "minimize":
                fill = max(0, min(10, int(((worst - value) / span) * 10)))
            else:
                fill = max(0, min(10, int(((value - worst) / span) * 10)))
            bar = "█" * fill + "░" * (10 - fill)
            line.append(f" {bar}", style="dim cyan")
            return

    if descriptor.primary_metric().direction == "minimize":
        fill = max(0, min(10, int((1 - value) * 10)))
    else:
        fill = max(0, min(10, int(value * 10)))

    bar = "█" * fill + "░" * (10 - fill)
    line.append(f" {bar}", style="dim cyan")


def _find_best_id(tree: IdeaTree, descriptor: ExperimentDescriptor) -> str | None:
    done = [i for i in tree.ideas.values() if i.status == "done" and i.metrics]
    if not done:
        return None
    best = max(done, key=lambda i: descriptor.sort_key(descriptor.get_idea_value(i)))
    return best.id


def _lineage(tree: IdeaTree, idea_id: str) -> set[str]:
    path: set[str] = set()
    cur = tree.ideas.get(idea_id)
    while cur:
        path.add(cur.id)
        if cur.parentId and cur.parentId in tree.ideas:
            cur = tree.ideas[cur.parentId]
        else:
            break
    return path


def _tree_stats(tree: IdeaTree, descriptor: ExperimentDescriptor) -> dict[str, int]:
    ideas = list(tree.ideas.values())
    return {
        "total": len(ideas),
        "done": sum(1 for i in ideas if i.status == "done"),
        "running": sum(1 for i in ideas if i.status == "running"),
        "pending": sum(1 for i in ideas if i.status == "pending"),
        "failed": sum(1 for i in ideas if i.status == "failed"),
    }


def _render_plain(tree: IdeaTree, descriptor: ExperimentDescriptor) -> None:
    roots = [i for i in tree.ideas.values() if i.parentId is None]
    roots.sort(key=lambda i: i.id)
    for root in roots:
        _print_plain_node(tree, root, descriptor, prefix="", is_last=True)


def _print_plain_node(
    tree: IdeaTree,
    idea: Idea,
    descriptor: ExperimentDescriptor,
    prefix: str,
    is_last: bool,
) -> None:
    icon = {"done": "✓", "running": "~", "pending": "·", "failed": "✗"}.get(
        idea.status, "?"
    )
    perf = ""
    if idea.status == "done" and idea.metrics:
        perf = f" {descriptor.format_perf_short(idea)}"
    hyp = idea.hypothesis
    if len(hyp) > 55:
        hyp = hyp[:52] + "..."

    connector = "└── " if is_last else "├── "
    print(f"{prefix}{connector}{icon} {idea.id}{perf}  {hyp}")

    children = sorted(
        [i for i in tree.ideas.values() if i.parentId == idea.id],
        key=lambda i: i.id,
    )
    child_prefix = prefix + ("    " if is_last else "│   ")
    for i, child in enumerate(children):
        _print_plain_node(
            tree, child, descriptor, child_prefix, is_last=(i == len(children) - 1)
        )
