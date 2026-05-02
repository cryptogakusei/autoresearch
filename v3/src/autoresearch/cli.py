from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import ensure_default_config
from .controller import Controller
from .descriptor import load_descriptor
from .providers import build_llm_client
from .storage import Storage


def build_controller(args: argparse.Namespace) -> Controller:
    root = Path(args.root).resolve()
    descriptor = load_descriptor(args.type)
    llm = build_llm_client(root, descriptor, provider_override=args.llm_provider)
    return Controller(root, descriptor, llm)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoresearch")
    parser.add_argument("--root", default=".")
    parser.add_argument("--type", default="example")
    parser.add_argument("--llm-provider", help="Override LLM provider, e.g. mock or openai-compatible")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    sub.add_parser("seed")

    seed_file = sub.add_parser("seed-from-file")
    seed_file.add_argument("path")
    seed_file.add_argument("--accept-top", type=int)

    research = sub.add_parser("research")
    research.add_argument("--count", type=int, default=1)
    research.add_argument("--idea-id", help="Run one specific pending idea id")

    autoloop = sub.add_parser("autoloop")
    autoloop.add_argument("count", type=int)

    sub.add_parser("ideas")
    sub.add_parser("best")

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.command == "init":
        Storage(root).init()
        config_path = ensure_default_config(root)
        print(f"initialized {root / '.autoresearch'}")
        print(f"config {config_path}")
        return 0

    controller = build_controller(args)
    if args.command == "seed":
        ideas = controller.seed_default()
        for idea in ideas:
            print(f"seeded {idea.id}: {idea.hypothesis}")
        return 0
    if args.command == "seed-from-file":
        path = Path(args.path)
        text = path.read_text(encoding="utf-8")
        ideas = controller.seed_from_text(str(path), text, accept_top=args.accept_top)
        for idea in ideas:
            print(f"seeded {idea.id}: {idea.hypothesis}")
        return 0
    if args.command == "research":
        results = [controller.run_one(args.idea_id)] if args.idea_id else controller.autoloop(args.count)
        results = [idea for idea in results if idea is not None]
        for idea in results:
            print(f"{idea.id} {idea.status}: {idea.hypothesis}")
        if not results:
            print("no pending ideas")
        return 0
    if args.command == "autoloop":
        results = controller.autoloop(args.count)
        for idea in results:
            print(f"{idea.id} {idea.status}: {idea.hypothesis}")
        return 0
    if args.command == "ideas":
        for idea in controller.ideas():
            print(f"{idea.id} {idea.status} parent={idea.parentId or '-'} {idea.hypothesis}")
        return 0
    if args.command == "best":
        for idea in controller.best():
            print(f"{idea.id} {controller.descriptor.format_perf_short(idea)} {idea.hypothesis}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
