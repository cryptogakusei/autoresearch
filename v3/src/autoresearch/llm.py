from __future__ import annotations

from pathlib import Path
from typing import Any

from .descriptor import ExperimentDescriptor
from .models import Idea
from .utils import normalize_elements


class LlmClient:
    applies_file_edits = False

    def seed_ideas(
        self, descriptor: ExperimentDescriptor, source: str, text: str
    ) -> dict[str, Any]:
        raise NotImplementedError

    def implement(self, descriptor: ExperimentDescriptor, idea: Idea, workspace: Path) -> dict[str, Any]:
        raise NotImplementedError

    def diagnose(
        self,
        descriptor: ExperimentDescriptor,
        idea: Idea,
        benchmark_result: dict[str, Any],
        parent: Idea | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def propose_incremental(
        self, descriptor: ExperimentDescriptor, idea: Idea, signals: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    def scout_queries(
        self, descriptor: ExperimentDescriptor, idea: Idea, signals: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    def propose_divergent(
        self, descriptor: ExperimentDescriptor, idea: Idea, signals: dict[str, Any],
        paper_context: str = "",
    ) -> dict[str, Any]:
        raise NotImplementedError

    def evaluate_candidates(
        self,
        descriptor: ExperimentDescriptor,
        candidates: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


class MockLlmClient(LlmClient):
    """Deterministic local client for smoke tests and offline development."""

    applies_file_edits = True

    def seed_ideas(
        self, descriptor: ExperimentDescriptor, source: str, text: str
    ) -> dict[str, Any]:
        snippet = " ".join(text.split())[:120] or "baseline"
        return {
            "ideas": [
                {
                    "hypothesis": f"Try a simple change inspired by {source}",
                    "plan": f"Append a concise implementation note based on: {snippet}",
                    "elements": ["seeded", "simple-change"],
                    "expectedImpact": "May improve the primary metric by changing artifact content.",
                    "source": source,
                    "rationale": "Mock extraction creates a concrete root idea from the provided source.",
                }
            ]
        }

    def implement(self, descriptor: ExperimentDescriptor, idea: Idea, workspace: Path) -> dict[str, Any]:
        allowed = descriptor.artifact.allowedPaths[0]
        target = Path(allowed.format(type=descriptor.name, experiment_type=descriptor.name))
        if not target.is_absolute():
            target = workspace.parents[2] / target
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        target.write_text(existing.rstrip() + f"\n# Experiment {idea.id}: {idea.plan}\n", encoding="utf-8")
        return {
            "summary": f"Applied plan for experiment {idea.id} by updating the allowed artifact.",
            "elements": normalize_elements(idea.elements or ["mock-implementation"]),
        }

    def diagnose(
        self,
        descriptor: ExperimentDescriptor,
        idea: Idea,
        benchmark_result: dict[str, Any],
        parent: Idea | None,
    ) -> dict[str, Any]:
        outcome = "neutral"
        if parent and parent.metrics:
            cur = descriptor.get_value(benchmark_result)
            prev = descriptor.get_idea_value(parent)
            outcome = "improved" if descriptor.is_better(cur, prev) else "regressed"
        elif benchmark_result.get("passed"):
            outcome = "neutral"
        return {"outcome": outcome, "reason": "Mock diagnosis based on primary metric comparison."}

    def propose_incremental(
        self, descriptor: ExperimentDescriptor, idea: Idea, signals: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "ideas": [
                {
                    "hypothesis": f"Small variation of {idea.id}",
                    "plan": "Make a minor textual change to the current artifact.",
                    "elements": normalize_elements([*idea.elements, "incremental"]),
                }
            ]
        }

    def scout_queries(
        self, descriptor: ExperimentDescriptor, idea: Idea, signals: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "queries": [
                f"{descriptor.name} neural operator improvements",
                f"{' '.join(idea.elements[:2])} machine learning",
            ]
        }

    def propose_divergent(
        self, descriptor: ExperimentDescriptor, idea: Idea, signals: dict[str, Any],
        paper_context: str = "",
    ) -> dict[str, Any]:
        return {
            "ideas": [
                {
                    "hypothesis": f"Divergent alternative after {idea.id}",
                    "plan": "Try a different artifact note style.",
                    "elements": ["divergent", "alternate-structure"],
                    "source": "mock",
                }
            ]
        }

    def evaluate_candidates(
        self,
        descriptor: ExperimentDescriptor,
        candidates: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "ranked": [c["label"] for c in candidates[:3]],
            "removed": [],
            "reasoning": "Mock evaluation preserves deterministic order.",
        }
