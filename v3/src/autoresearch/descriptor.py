from __future__ import annotations

from dataclasses import dataclass, field
import importlib
from pathlib import Path
from typing import Any, Callable, Literal


Direction = Literal["maximize", "minimize"]


@dataclass(frozen=True)
class MetricConstraint:
    metric: str
    op: str
    value: int | float | str | bool
    dataset: str | None = None
    size: str | None = None


@dataclass(frozen=True)
class MetricSpec:
    name: str
    direction: Direction
    unit: str | None = None
    primarySize: str | None = None
    constraint: MetricConstraint | None = None


@dataclass(frozen=True)
class ArtifactSpec:
    baselinePath: str
    workingPath: str
    allowedPaths: list[str]
    archiveGlobs: list[str]


@dataclass(frozen=True)
class BenchmarkSpec:
    command: list[str]
    timeoutSec: int
    resultParser: str = "canonical-json"


@dataclass(frozen=True)
class ValidationSpec:
    required: list[MetricConstraint] = field(default_factory=list)
    command: list[str] | None = None
    hidden: bool = False


@dataclass(frozen=True)
class LlmProfile:
    model: str
    temperature: float | None = None
    maxOutputTokens: int | None = None
    promptTemplate: str | None = None
    repairRetries: int = 1


@dataclass(frozen=True)
class ExperimentDescriptor:
    name: str
    artifact: ArtifactSpec
    benchmark: BenchmarkSpec
    validation: ValidationSpec
    metrics: dict[str, Any]
    implementInstructions: list[str]
    parseStructure: Callable[[Path], dict[str, Any] | None]
    llmProfiles: dict[str, LlmProfile] = field(default_factory=dict)
    protectedConfigPaths: dict[str, list[str]] = field(default_factory=dict)
    preflight: Callable[[Path, Path], dict[str, Any] | None] | None = None

    def primary_metric(self) -> MetricSpec:
        return self.metrics["primary"]

    def secondary_metrics(self) -> list[MetricSpec]:
        return self.metrics.get("secondary", [])

    def get_value(self, result: dict[str, Any]) -> float:
        primary = self.primary_metric()
        label = primary.primarySize or next(iter(result["metrics"]))
        value = result["metrics"][label][primary.name]
        return float(value)

    def get_idea_value(self, idea: Any) -> float:
        if not idea.metrics:
            return 0.0
        return self.get_value({"metrics": idea.metrics})

    def format_perf_short(self, idea: Any) -> str:
        if not idea.metrics:
            return "no metrics"
        primary = self.primary_metric()
        value = self.get_idea_value(idea)
        unit = f" {primary.unit}" if primary.unit else ""
        return f"{value:.4g}{unit}"

    def is_better(self, current: float, previous: float) -> bool:
        if self.primary_metric().direction == "maximize":
            return current > previous
        return current < previous

    def sort_key(self, value: float) -> float:
        return value if self.primary_metric().direction == "maximize" else -value


def load_descriptor(name: str) -> ExperimentDescriptor:
    module = importlib.import_module(f"autoresearch.descriptors.{name}")
    descriptor = getattr(module, "DESCRIPTOR")
    if not isinstance(descriptor, ExperimentDescriptor):
        raise TypeError(f"Descriptor {name!r} did not expose DESCRIPTOR")
    return descriptor
