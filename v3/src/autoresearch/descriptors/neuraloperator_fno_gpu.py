from __future__ import annotations

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
    name="neuraloperator_fno_gpu",
    artifact=ArtifactSpec(
        baselinePath="workspace/baselines/neuraloperator_fno_gpu",
        workingPath="workspace/current/neuraloperator_fno_gpu",
        allowedPaths=[
            "workspace/current/neuraloperator_fno_gpu/neuraloperator_variant/high_frequency_residual_fno.py",
            "workspace/current/neuraloperator_fno_gpu/benchmarks/smoke_config.json",
        ],
        archiveGlobs=[
            "workspace/current/neuraloperator_fno_gpu/neuraloperator_variant",
            "workspace/current/neuraloperator_fno_gpu/benchmarks/smoke_config.json",
            "workspace/current/neuraloperator_fno_gpu/README.md",
        ],
    ),
    benchmark=BenchmarkSpec(
        command=[
            "python3",
            "benchmarks/neuraloperator_fno/run_darcy_gpu.py",
            "{artifact_dir}",
            "{experiment_id}",
            "--data-dir", "/home/ubuntu/data/darcy",
            "--experiment-type", "{experiment_type}",
        ],
        timeoutSec=600,
        resultParser="canonical-json",
    ),
    validation=ValidationSpec(
        hidden=False,
        required=[
            MetricConstraint(metric="valid", op="==", value=True, dataset="darcy_128"),
            MetricConstraint(metric="peak_memory_gb", op="<=", value=40.0, dataset="darcy_128"),
            MetricConstraint(metric="train_time_sec", op="<=", value=600.0, dataset="darcy_128"),
        ],
    ),
    metrics={
        "primary": MetricSpec(
            name="relative_l2",
            unit="rel",
            direction="minimize",
            primarySize="darcy_128",
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
                    value=600.0,
                    dataset="darcy_128",
                ),
            ),
            MetricSpec(
                name="peak_memory_gb",
                unit="GB",
                direction="minimize",
                constraint=MetricConstraint(
                    metric="peak_memory_gb",
                    op="<=",
                    value=40.0,
                    dataset="darcy_128",
                ),
            ),
        ],
    },
    implementInstructions=[
        "This is a Phase 1 GPU target for NeuralOperator/FNO autoresearch.",
        "Only edit the allowed files under workspace/current/neuraloperator_fno_gpu.",
        "Do not clone repositories, download datasets, run training, run benchmarks, or inspect validators.",
        "The benchmark runs on a remote GPU with real Darcy flow data at 128x128 resolution.",
        "Keep high_frequency_residual_fno.py self-contained with NeuralOperator imports.",
        "The model must define HighFrequencyResidualFNO, build_model(config), and frequency_weighted_mse.",
        "You may tune hyperparameters in smoke_config.json (epochs, learning_rate, n_modes, hidden_channels, etc.).",
    ],
    parseStructure=parse_structure,
    llmProfiles={},
)
