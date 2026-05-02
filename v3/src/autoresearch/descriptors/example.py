from __future__ import annotations

import sys
from pathlib import Path

from autoresearch.descriptor import (
    ArtifactSpec,
    BenchmarkSpec,
    ExperimentDescriptor,
    LlmProfile,
    MetricConstraint,
    MetricSpec,
    ValidationSpec,
)


def parse_structure(artifact_dir: Path) -> dict[str, object] | None:
    files = list(artifact_dir.rglob("solution.txt"))
    if not files:
        return None
    text = files[0].read_text(encoding="utf-8")
    return {
        "line_count": len(text.splitlines()),
        "char_count": len(text),
        "has_experiment_note": "# Experiment" in text,
    }


DESCRIPTOR = ExperimentDescriptor(
    name="example",
    artifact=ArtifactSpec(
        baselinePath="workspace/baselines/example",
        workingPath="workspace/current/example",
        allowedPaths=["workspace/current/example/solution.txt"],
        archiveGlobs=["workspace/current/example/solution.txt"],
    ),
    benchmark=BenchmarkSpec(
        command=[
            sys.executable,
            "benchmarks/example/run.py",
            "{artifact_dir}",
            "{experiment_id}",
        ],
        timeoutSec=30,
        resultParser="canonical-json",
    ),
    validation=ValidationSpec(
        hidden=False,
        required=[
            MetricConstraint(metric="valid", op="==", value=True, dataset="main"),
            MetricConstraint(metric="length", op="<=", value=5000, dataset="main"),
        ],
    ),
    metrics={
        "primary": MetricSpec(
            name="score",
            unit="points",
            direction="maximize",
            primarySize="main",
        ),
        "secondary": [
            MetricSpec(
                name="length",
                direction="minimize",
                constraint=MetricConstraint(
                    metric="length", op="<=", value=5000, dataset="main"
                ),
            )
        ],
    },
    implementInstructions=[
        "Only edit workspace/current/example/solution.txt.",
        "Do not run benchmarks or validators.",
        "Keep the artifact as plain text.",
    ],
    parseStructure=parse_structure,
    llmProfiles={
        "implement": LlmProfile(model="mock-code", temperature=0.1),
        "diagnose": LlmProfile(model="mock-general", temperature=0.2),
        "incremental": LlmProfile(model="mock-general", temperature=0.7),
        "divergent": LlmProfile(model="mock-general", temperature=0.9),
        "evaluate": LlmProfile(model="mock-general", temperature=0.2),
        "seed": LlmProfile(model="mock-general", temperature=0.3),
    },
)

