from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .benchmark import BenchmarkError, run_benchmark
from .config import ensure_default_config, load_dotenv
from .config_guard import (
    ConfigContractError,
    raise_for_config_violations,
    validate_protected_config_paths,
)
from .controller import Controller
from .descriptor import ExperimentDescriptor, load_descriptor
from .preflight import PreflightError
from .providers import build_llm_client
from .storage import Storage
from .tree_display import render_idea_tree
from .validation import validate_result

DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
RESET = "\033[0m"


class TerminalReporter:
    def __init__(self, descriptor: ExperimentDescriptor):
        self.descriptor = descriptor
        self._experiment_start: float = 0.0
        self._loop_start: float = 0.0
        self._results: list[dict[str, Any]] = []
        self._total_cost: float = 0.0
        self._total_input: int = 0
        self._total_output: int = 0

    def start_loop(self, count: int, budget: float | None = None) -> None:
        self._loop_start = time.time()
        self._results = []
        self._total_cost = 0.0
        self._total_input = 0
        self._total_output = 0
        budget_str = f" {DIM}| budget ${budget:.2f}{RESET}" if budget else ""
        print(f"\n{BOLD}AutoResearch{RESET} {DIM}|{RESET} {self.descriptor.name} {DIM}|{RESET} {count} experiment{'s' if count != 1 else ''}{budget_str}\n")

    def __call__(self, phase: str, status: str, data: dict[str, Any]) -> None:
        idea_id = data.get("id", "???")

        if phase == "pick" and status == "done":
            self._experiment_start = time.time()
            hyp = _truncate(data.get("hypothesis", ""), 80)
            parent = data.get("parent")
            parent_str = f" {DIM}(parent {parent}){RESET}" if parent else ""
            print(f"{BOLD}[{idea_id}]{RESET} {hyp}{parent_str}")

        elif phase == "implement" and status == "start":
            _phase_dot("implement")

        elif phase == "implement" and status == "done":
            summary = _truncate(data.get("summary", ""), 70)
            n_files = len(data.get("files", []))
            tok = _format_tokens(data.get("tokens"))
            print(f" {GREEN}ok{RESET} {DIM}({n_files} file{'s' if n_files != 1 else ''}){RESET} {summary} {tok}")

        elif phase == "benchmark" and status == "start":
            _phase_dot("benchmark")

        elif phase == "benchmark" and status == "done":
            metrics = data.get("metrics", {})
            passed = data.get("passed", False)
            tag = f"{GREEN}pass{RESET}" if passed else f"{RED}FAIL{RESET}"
            metric_str = _format_metrics(metrics, self.descriptor)
            print(f" {tag} {metric_str}")

        elif phase == "diagnose" and status == "done":
            outcome = data.get("outcome", "?")
            reason = _truncate(data.get("reason", ""), 70)
            color = GREEN if outcome == "improved" else (RED if outcome == "regressed" else YELLOW)
            tok = _format_tokens(data.get("tokens"))
            print(f"  {DIM}diagnosis:{RESET} {color}{outcome}{RESET} {DIM}{reason}{RESET} {tok}")

        elif phase == "scout" and status == "start":
            _phase_dot("scout")

        elif phase == "scout" and status == "done":
            n = data.get("papers", 0)
            queries = data.get("queries", [])
            print(f" {n} papers {DIM}({len(queries)} queries){RESET}")

        elif phase == "generate" and status == "done":
            children = data.get("children", [])
            if children:
                print(f"  {DIM}spawned:{RESET}", end="")
                for cid, hyp in children:
                    print(f" {CYAN}{cid}{RESET}", end="")
                print()

        elif phase == "experiment" and status == "done":
            elapsed = time.time() - self._experiment_start
            usage = data.get("usage", {})
            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            cost = usage.get("cost", 0.0)
            self._total_cost += cost
            self._total_input += inp
            self._total_output += out
            print(f"  {DIM}completed in {elapsed:.0f}s | {inp:,}+{out:,} tokens | ${cost:.4f}{RESET}\n")
            self._results.append({"id": idea_id, "status": "done", "time": elapsed, "cost": cost})

        elif phase == "experiment" and status == "failed":
            reason = _truncate(data.get("reason", ""), 80)
            phase_name = data.get("failed_phase", "?")
            elapsed = time.time() - self._experiment_start
            usage = data.get("usage", {})
            cost = usage.get("cost", 0.0)
            self._total_cost += cost
            self._total_input += usage.get("input_tokens", 0)
            self._total_output += usage.get("output_tokens", 0)
            print(f"  {RED}FAILED{RESET} at {phase_name}: {reason}")
            print(f"  {DIM}failed after {elapsed:.0f}s | ${cost:.4f}{RESET}\n")
            self._results.append({"id": idea_id, "status": "failed", "time": elapsed, "phase": phase_name, "cost": cost})

        elif phase == "loop" and status == "budget_exceeded":
            spent = data.get("spent", 0.0)
            budget = data.get("budget", 0.0)
            print(f"  {YELLOW}Budget reached: ${spent:.4f} / ${budget:.2f} — stopping loop{RESET}\n")

    def end_loop(self, ideas: list[Any], tree: Any = None) -> None:
        elapsed = time.time() - self._loop_start
        done = [r for r in self._results if r["status"] == "done"]
        failed = [r for r in self._results if r["status"] == "failed"]

        print(f"{BOLD}{'=' * 60}{RESET}")
        print(f"{BOLD}Summary{RESET} {DIM}|{RESET} {len(done)} done, {len(failed)} failed, {elapsed:.0f}s total")
        print(f"{DIM}Tokens: {self._total_input:,} in + {self._total_output:,} out | Cost: ${self._total_cost:.4f}{RESET}\n")

        if done:
            done_ideas = [i for i in ideas if i.id in {r["id"] for r in done}]
            done_ideas.sort(key=lambda i: self.descriptor.sort_key(self.descriptor.get_idea_value(i)), reverse=True)
            for idea in done_ideas:
                perf = self.descriptor.format_perf_short(idea)
                outcome = idea.diagnosis.outcome if idea.diagnosis else "?"
                color = GREEN if outcome == "improved" else (RED if outcome == "regressed" else YELLOW)
                cost = next((r["cost"] for r in self._results if r["id"] == idea.id), 0.0)
                print(f"  {idea.id}  {perf:<12s}  {color}{outcome:<10s}{RESET}  ${cost:.4f}  {_truncate(idea.hypothesis, 45)}")

        if failed:
            for r in failed:
                cost = r.get("cost", 0.0)
                print(f"  {r['id']}  {'FAILED':<12s}  {RED}{'failed':<10s}{RESET}  ${cost:.4f}  at {r.get('phase', '?')}")

        best = _find_best(ideas, self.descriptor)
        if best:
            perf = self.descriptor.format_perf_short(best)
            print(f"\n{BOLD}Best overall:{RESET} {best.id} ({perf})")
        print()

        if tree is not None:
            render_idea_tree(tree, self.descriptor)


def _phase_dot(name: str) -> None:
    print(f"  {DIM}{name}...{RESET}", end="", flush=True)


def _format_tokens(tokens: dict[str, Any] | None) -> str:
    if not tokens:
        return ""
    inp = tokens.get("input", 0)
    out = tokens.get("output", 0)
    cost = tokens.get("cost", 0.0)
    return f"{DIM}[{inp:,}+{out:,} ${cost:.4f}]{RESET}"


def _truncate(text: str, max_len: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_metrics(metrics: dict[str, Any], descriptor: ExperimentDescriptor) -> str:
    primary = descriptor.primary_metric()
    label = primary.primarySize or next(iter(metrics), "")
    if not label or label not in metrics:
        return ""
    m = metrics[label]
    val = m.get(primary.name)
    parts = [f"{primary.name}={val:.4f}" if val is not None else ""]
    baseline_key = f"baseline_{primary.name}"
    baseline = m.get(baseline_key)
    if val is not None and baseline is not None and baseline != 0:
        pct = (baseline - val) / abs(baseline) * 100
        sign = "+" if pct > 0 else ""
        parts.append(f"{DIM}({sign}{pct:.1f}% vs baseline){RESET}")
    for sec in descriptor.secondary_metrics():
        sv = m.get(sec.name)
        if sv is not None:
            unit = f" {sec.unit}" if sec.unit else ""
            parts.append(f"{DIM}{sec.name}={sv:.3g}{unit}{RESET}")
    return "  ".join(p for p in parts if p)


def _find_best(ideas: list[Any], descriptor: ExperimentDescriptor) -> Any | None:
    done = [i for i in ideas if i.status == "done" and i.metrics]
    if not done:
        return None
    return max(done, key=lambda i: descriptor.sort_key(descriptor.get_idea_value(i)))


def build_controller(args: argparse.Namespace, on_progress: Any = None) -> Controller:
    root = Path(args.root).resolve()
    descriptor = load_descriptor(args.type)
    llm = build_llm_client(root, descriptor, provider_override=args.llm_provider)
    return Controller(root, descriptor, llm, on_progress=on_progress)


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
    research.add_argument("--budget", type=float, help="Max dollar spend for this run")

    autoloop = sub.add_parser("autoloop")
    autoloop.add_argument("count", type=int)
    autoloop.add_argument("--budget", type=float, help="Max dollar spend for this run")

    benchmark_candidate = sub.add_parser(
        "benchmark-candidate",
        help="Benchmark an existing artifact directory without mutating the idea tree",
    )
    benchmark_candidate.add_argument(
        "candidate",
        help="Artifact directory path, or name under workspace/phase4_candidates/<name>",
    )
    benchmark_candidate.add_argument(
        "--id",
        help="Artifact id expected in the benchmark JSON; defaults to candidate name",
    )

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

    descriptor = load_descriptor(args.type)
    reporter = TerminalReporter(descriptor)

    if args.command == "seed":
        controller = build_controller(args)
        ideas = controller.seed_default()
        for idea in ideas:
            print(f"seeded {idea.id}: {idea.hypothesis}")
        return 0
    if args.command == "seed-from-file":
        controller = build_controller(args)
        path = Path(args.path)
        text = path.read_text(encoding="utf-8")
        ideas = controller.seed_from_text(str(path), text, accept_top=args.accept_top)
        for idea in ideas:
            print(f"seeded {idea.id}: {idea.hypothesis}")
        return 0
    if args.command in {"research", "autoloop"}:
        controller = build_controller(args, on_progress=reporter)
        count = args.count if args.command == "autoloop" else args.count
        budget = getattr(args, "budget", None)
        if args.command == "research" and args.idea_id:
            reporter.start_loop(1, budget=budget)
            results = [controller.run_one(args.idea_id)]
        else:
            reporter.start_loop(count, budget=budget)
            results = controller.autoloop(count, budget=budget)
        results = [idea for idea in results if idea is not None]
        if not results:
            print("no pending ideas")
        else:
            tree = controller.storage.load_tree(descriptor.name)
            reporter.end_loop(results, tree=tree)
        return 0
    if args.command == "benchmark-candidate":
        return _benchmark_candidate(root, descriptor, args.candidate, args.id)
    if args.command == "ideas":
        controller = build_controller(args)
        tree = controller.storage.load_tree(descriptor.name)
        render_idea_tree(tree, descriptor)
        return 0
    if args.command == "best":
        controller = build_controller(args)
        for idea in controller.best():
            print(f"{idea.id} {descriptor.format_perf_short(idea)} {idea.hypothesis}")
        return 0
    return 1


def _benchmark_candidate(
    root: Path,
    descriptor: ExperimentDescriptor,
    candidate: str,
    experiment_id: str | None,
) -> int:
    load_dotenv(root)
    candidate_path = _resolve_candidate_path(root, candidate)
    artifact_id = experiment_id or candidate_path.name
    preflight: dict[str, Any] | None = None

    if not candidate_path.exists() or not candidate_path.is_dir():
        output = {
            "id": artifact_id,
            "candidate": str(candidate_path),
            "passed": False,
            "failures": [{"reason": "candidate directory not found"}],
        }
        _persist_benchmark_candidate(root, descriptor, artifact_id, output)
        _print_json(output)
        return 2

    try:
        _validate_candidate_config_contract(root, descriptor, candidate_path)
        if descriptor.preflight is not None:
            preflight = descriptor.preflight(root, candidate_path)
        result = run_benchmark(descriptor, candidate_path, artifact_id, root)
        passed, failures, validator = validate_result(result, descriptor, artifact_id)
        output = {
            "id": artifact_id,
            "candidate": str(candidate_path),
            "passed": passed,
            "failures": failures,
            "validator": validator,
            "preflight": preflight,
            "metrics": result.get("metrics", {}),
            "metadata": result.get("metadata", {}),
            "rawValidation": result.get("validation", {}),
        }
        _persist_benchmark_candidate(root, descriptor, artifact_id, output)
        _print_json(output)
        return 0 if passed else 2
    except (BenchmarkError, ConfigContractError, PreflightError) as exc:
        output = {
            "id": artifact_id,
            "candidate": str(candidate_path),
            "passed": False,
            "failures": [{"reason": str(exc)}],
            "preflight": preflight,
        }
        _persist_benchmark_candidate(root, descriptor, artifact_id, output)
        _print_json(output)
        return 2


def _resolve_candidate_path(root: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if path.is_absolute():
        return path.resolve()
    direct = (root / path).resolve()
    if direct.exists():
        return direct
    return (root / "workspace" / "phase4_candidates" / candidate).resolve()


def _validate_candidate_config_contract(
    root: Path, descriptor: ExperimentDescriptor, candidate_path: Path
) -> None:
    if not descriptor.protectedConfigPaths:
        return
    baseline_path = root / descriptor.artifact.baselinePath
    violations = validate_protected_config_paths(
        baseline_path, candidate_path, descriptor.protectedConfigPaths
    )
    raise_for_config_violations(violations)


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _persist_benchmark_candidate(
    root: Path,
    descriptor: ExperimentDescriptor,
    artifact_id: str,
    output: dict[str, Any],
) -> None:
    safe_id = "".join(
        ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in artifact_id
    ).strip("._")
    if not safe_id:
        safe_id = "benchmark_candidate"
    path = (
        root
        / ".autoresearch"
        / "artifacts"
        / descriptor.name
        / safe_id
        / "benchmark_candidate.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
