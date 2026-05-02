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
    model_files = list(artifact_dir.rglob("high_frequency_residual_fno.py"))
    config_files = list(artifact_dir.rglob("smoke_config.json"))
    if not model_files:
        return None
    model_text = model_files[0].read_text(encoding="utf-8")
    config_text = config_files[0].read_text(encoding="utf-8") if config_files else ""
    return {
        "defines_high_frequency_residual_fno": "HighFrequencyResidualFNO" in model_text,
        "has_frequency_weighting": "frequency" in model_text.lower(),
        "has_residual_branch": "residual" in model_text.lower(),
        "model_lines": len(model_text.splitlines()),
        "config_chars": len(config_text),
    }


DESCRIPTOR = ExperimentDescriptor(
    name="neuraloperator_fno",
    artifact=ArtifactSpec(
        baselinePath="workspace/baselines/neuraloperator_fno",
        workingPath="workspace/current/neuraloperator_fno",
        allowedPaths=[
            "workspace/current/neuraloperator_fno/neuraloperator_variant/high_frequency_residual_fno.py",
            "workspace/current/neuraloperator_fno/benchmarks/smoke_config.json",
        ],
        archiveGlobs=[
            "workspace/current/neuraloperator_fno/neuraloperator_variant",
            "workspace/current/neuraloperator_fno/benchmarks/smoke_config.json",
            "workspace/current/neuraloperator_fno/README.md",
        ],
    ),
    benchmark=BenchmarkSpec(
        command=[
            sys.executable,
            "benchmarks/neuraloperator_fno/run_smoke.py",
            "{artifact_dir}",
            "{experiment_id}",
        ],
        timeoutSec=60,
        resultParser="canonical-json",
    ),
    validation=ValidationSpec(
        hidden=False,
        required=[
            MetricConstraint(metric="valid", op="==", value=True, dataset="darcy_16_smoke"),
            MetricConstraint(metric="peak_memory_gb", op="<=", value=8.0, dataset="darcy_16_smoke"),
            MetricConstraint(metric="train_time_sec", op="<=", value=120.0, dataset="darcy_16_smoke"),
        ],
    ),
    metrics={
        "primary": MetricSpec(
            name="relative_l2",
            unit="rel",
            direction="minimize",
            primarySize="darcy_16_smoke",
        ),
        "secondary": [
            MetricSpec(
                name="spectral_hf_error",
                unit="rel",
                direction="minimize",
            ),
            MetricSpec(
                name="train_time_sec",
                unit="sec",
                direction="minimize",
                constraint=MetricConstraint(
                    metric="train_time_sec",
                    op="<=",
                    value=120.0,
                    dataset="darcy_16_smoke",
                ),
            ),
            MetricSpec(
                name="peak_memory_gb",
                unit="GB",
                direction="minimize",
                constraint=MetricConstraint(
                    metric="peak_memory_gb",
                    op="<=",
                    value=8.0,
                    dataset="darcy_16_smoke",
                ),
            ),
        ],
    },
    implementInstructions=[
        "This is a Phase 0 Mac smoke target for NeuralOperator/FNO autoresearch.",
        "Only edit the allowed files under workspace/current/neuraloperator_fno.",
        "Do not clone repositories, download datasets, run training, run benchmarks, or inspect validators.",
        "Keep high_frequency_residual_fno.py import-light and suitable for syntax inspection.",
        "Implement architecture ideas as code/config changes that a later real NeuralOperator integration can port.",
        "The benchmark is a controller-only smoke proxy; it is not a real PDE training result.",
    ],
    parseStructure=parse_structure,
    llmProfiles={
        "seed": LlmProfile(model="mock-general", temperature=0.3),
        "implement": LlmProfile(model="mock-code", temperature=0.1),
        "diagnose": LlmProfile(model="mock-general", temperature=0.2),
        "incremental": LlmProfile(model="mock-general", temperature=0.7),
        "divergent": LlmProfile(model="mock-general", temperature=0.9),
        "evaluate": LlmProfile(model="mock-general", temperature=0.2),
    },
)

