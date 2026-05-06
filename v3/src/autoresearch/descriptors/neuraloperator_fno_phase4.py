from __future__ import annotations

from pathlib import Path

from autoresearch.descriptor import (
    ArtifactSpec,
    BenchmarkSpec,
    ExperimentDescriptor,
    MetricConstraint,
    MetricSpec,
    ValidationSpec,
)
from autoresearch.preflight import run_neuraloperator_fno_gpu_preflight


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
        "has_wavelet": "wavelet" in model_text.lower() or "dwt" in model_text.lower(),
        "has_derivative_loss": "h1" in model_text.lower() or "gradient" in model_text.lower(),
        "model_lines": len(model_text.splitlines()),
        "config_chars": len(config_text),
    }


DESCRIPTOR = ExperimentDescriptor(
    name="neuraloperator_fno_phase4",
    artifact=ArtifactSpec(
        baselinePath="workspace/baselines/neuraloperator_fno_phase4",
        workingPath="workspace/current/neuraloperator_fno_phase4",
        allowedPaths=[
            "workspace/current/neuraloperator_fno_phase4/neuraloperator_variant/high_frequency_residual_fno.py",
            "workspace/current/neuraloperator_fno_phase4/benchmarks/smoke_config.json",
        ],
        archiveGlobs=[
            "workspace/current/neuraloperator_fno_phase4/neuraloperator_variant",
            "workspace/current/neuraloperator_fno_phase4/benchmarks/smoke_config.json",
            "workspace/current/neuraloperator_fno_phase4/README.md",
        ],
    ),
    benchmark=BenchmarkSpec(
        command=[
            "python3",
            "benchmarks/neuraloperator_fno/run_multi_pde_gpu.py",
            "{artifact_dir}",
            "{experiment_id}",
            "--data-dir",
            "/home/ubuntu/data",
            "--datasets",
            "darcy_128,navier_stokes_128",
            "--experiment-type",
            "{experiment_type}",
        ],
        timeoutSec=7200,
    ),
    validation=ValidationSpec(
        hidden=False,
        required=[
            MetricConstraint(metric="valid", op="==", value=True, dataset="darcy_128"),
            MetricConstraint(metric="valid", op="==", value=True, dataset="navier_stokes_128"),
            MetricConstraint(metric="peak_memory_gb", op="<=", value=40.0, dataset="darcy_128"),
            MetricConstraint(metric="train_time_sec", op="<=", value=1800.0, dataset="darcy_128"),
            MetricConstraint(metric="train_time_sec", op="<=", value=1800.0, dataset="navier_stokes_128"),
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
            MetricSpec(name="spectral_hf_error", unit="rel", direction="minimize"),
            MetricSpec(name="h1_error", unit="rel", direction="minimize"),
            MetricSpec(name="param_count", unit="params", direction="minimize"),
            MetricSpec(
                name="train_time_sec",
                unit="sec",
                direction="minimize",
                constraint=MetricConstraint(
                    metric="train_time_sec",
                    op="<=",
                    value=1800.0,
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
        "This is a Phase 4 focused scale-up target for NeuralOperator/FNO autoresearch.",
        "It is a separate experiment type from neuraloperator_fno_gpu and must not modify Phase 2/3 artifacts or idea trees.",
        "The benchmarked PDEs are Darcy flow and Navier-Stokes at 128x128 resolution with a larger training budget.",
        "build_model(config) receives per-dataset config with in_channels and out_channels already set.",
        "NEVER change in_channels or out_channels in smoke_config.json; these are fixed by the benchmark harness.",
        "NEVER change resolution, train_samples, or val_samples in smoke_config.json unless the descriptor is explicitly updated.",
        "Only edit the allowed files under workspace/current/neuraloperator_fno_phase4.",
        "Do not clone repositories, download datasets, run training, run benchmarks, or inspect validators.",
        "Keep high_frequency_residual_fno.py self-contained with NeuralOperator imports.",
        "The model must define HighFrequencyResidualFNO, build_model(config), and frequency_weighted_mse.",
        "Prefer focused variants around promoted designs: latent multiresolution wavelet correction, derivative-aware loss, or their hybrid.",
        "You may tune safe model/training hyperparameters in smoke_config.json such as n_modes, hidden_channels, n_layers, learning_rate, frequency_loss_weight, residual_weight, and architecture-specific small hyperparameters.",
        "Config format: {\"shared\": {...}, \"datasets\": {\"darcy_128\": {...}, \"navier_stokes_128\": {...}}}.",
    ],
    parseStructure=parse_structure,
    protectedConfigPaths={
        "benchmarks/smoke_config.json": [
            "shared.resolution",
            "shared.train_samples",
            "shared.val_samples",
            "datasets.*.in_channels",
            "datasets.*.out_channels",
        ]
    },
    preflight=run_neuraloperator_fno_gpu_preflight,
)
