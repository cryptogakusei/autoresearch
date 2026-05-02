from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


IdeaStatus = Literal["pending", "running", "done", "failed"]
Outcome = Literal["improved", "regressed", "neutral", "error"]
Direction = Literal["maximize", "minimize"]
SelectionMode = Literal["deterministic", "ai_evaluated"]


@dataclass
class Failure:
    phase: str
    reason: str
    retryable: bool
    details: dict[str, Any] | None = None
    occurredAt: str = ""


@dataclass
class Diagnosis:
    outcome: Outcome
    reason: str
    vsParent: dict[str, Any] | None = None
    vsBest: dict[str, Any] | None = None


@dataclass
class ValidationRecord:
    passed: bool
    failedConstraints: list[dict[str, Any]] = field(default_factory=list)
    validatorVersion: str | None = None


@dataclass
class Idea:
    id: str
    hypothesis: str
    plan: str
    experimentType: str
    status: IdeaStatus = "pending"
    parentId: str | None = None
    expectedImpact: str | None = None
    created: str | None = None
    completed: str | None = None
    metrics: dict[str, dict[str, Any]] | None = None
    validation: ValidationRecord | None = None
    diagnosis: Diagnosis | None = None
    elements: list[str] = field(default_factory=list)
    sourceRef: str | None = None
    implementationSummary: str | None = None
    structure: dict[str, Any] | None = None
    tokenCost: dict[str, Any] | None = None
    children: list[str] = field(default_factory=list)
    failure: Failure | None = None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Idea":
        data = dict(data)
        if isinstance(data.get("validation"), dict):
            data["validation"] = ValidationRecord(**data["validation"])
        if isinstance(data.get("diagnosis"), dict):
            data["diagnosis"] = Diagnosis(**data["diagnosis"])
        if isinstance(data.get("failure"), dict):
            data["failure"] = Failure(**data["failure"])
        return cls(**data)


@dataclass
class SelectionRecord:
    mode: SelectionMode
    triggerReasons: list[str]
    deterministicRanking: list[str]
    finalRanking: list[str]
    removed: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str | None = None


@dataclass
class IdeaTree:
    version: int
    nextId: int
    ideas: dict[str, Idea] = field(default_factory=dict)
    bestIdPerSize: dict[str, str] = field(default_factory=dict)
    elementVocabulary: list[str] = field(default_factory=list)
    selections: dict[str, SelectionRecord] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "IdeaTree":
        return cls(version=1, nextId=1)

    def allocate_id(self) -> str:
        idea_id = f"{self.nextId:03d}"
        self.nextId += 1
        return idea_id

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "nextId": self.nextId,
            "ideas": {k: v.to_json() for k, v in self.ideas.items()},
            "bestIdPerSize": self.bestIdPerSize,
            "elementVocabulary": self.elementVocabulary,
            "selections": {k: asdict(v) for k, v in self.selections.items()},
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "IdeaTree":
        return cls(
            version=data.get("version", 1),
            nextId=data.get("nextId", 1),
            ideas={k: Idea.from_json(v) for k, v in data.get("ideas", {}).items()},
            bestIdPerSize=data.get("bestIdPerSize", {}),
            elementVocabulary=data.get("elementVocabulary", []),
            selections={
                k: SelectionRecord(**v) for k, v in data.get("selections", {}).items()
            },
        )


@dataclass
class RunState:
    activeType: str | None = None
    currentExperimentId: str | None = None
    phase: str | None = None
    phaseStatus: str = "not_started"
    lastCompletedStep: str | None = None
    continuousMode: bool = False
    budgetLimit: float | None = None
    experimentLimit: int | None = None
    updatedAt: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RunState":
        return cls(**data)

