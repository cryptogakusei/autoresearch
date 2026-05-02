# NeuralOperator FNO Autoresearch Plan

## Goal

Use the autoresearch loop to improve Fourier Neural Operator performance in the NeuralOperator repo:

https://github.com/neuraloperator/neuraloperator

The loop should read relevant FNO / neural operator references, propose architecture or training modifications, implement variants inside a controlled NeuralOperator workspace, run isolated benchmarks, compare against baselines, and iterate from measured results.

## Research Direction

Focus on improving FNO behavior on:

- high-frequency details
- local structures
- discontinuities
- non-periodic or local phenomena
- long-rollout stability

Standard FNOs are efficient and resolution-invariant because they operate in Fourier space and truncate to selected modes. That same truncation can weaken high-frequency reconstruction and sharp local feature handling. The first research direction should target this weakness directly.

## Candidate Variant Family

Initial family name:

```text
HighFrequencyResidualFNO
```

Possible variants:

- frequency-weighted loss
- residual branch focused on high-frequency error
- learned frequency masks
- dynamic mode allocation
- local convolution branch plus Fourier branch
- spectral adapters or LoRA-style adapters for fine-tuning
- TFNO / tensorized variants to reduce parameter count
- curriculum over Fourier modes or spatial resolution
- amortized Fourier kernels inspired by newer FNO variants

## Initial Baselines

Start with standard NeuralOperator models:

```python
FNO(
    n_modes=(16, 16),
    hidden_channels=64,
    in_channels=2 or 3,
    out_channels=1,
    n_layers=4,
)
```

Compare with:

```python
FNO(
    n_modes=(32, 32),
    hidden_channels=64,
    n_layers=4,
)
```

And possibly:

```python
TFNO(..., rank=0.1)
```

Parameter scale is moderate: typical 2D FNOs are millions to tens of millions of parameters, not LLM-scale. 3D FNOs grow much faster because Fourier modes multiply across dimensions.

## Benchmark Targets

Start small and practical:

- Darcy flow
- Burgers equation
- Shallow water
- 2D Navier-Stokes

Later scale to:

- PDEBench
- The Well dataset
- larger 3D PDEs
- multi-physics transfer experiments

## Metrics

Do not optimize only relative L2.

Primary metric for early runs:

```text
relative_l2
```

Secondary metrics and gates:

- spectral error by frequency band
- high-frequency reconstruction error
- conservation-law residuals where applicable
- rollout stability over time
- zero-shot resolution transfer
- robustness to shifted boundary or initial conditions
- training time
- peak GPU memory
- inference latency

Example canonical benchmark output:

```json
{
  "type": "neuraloperator_fno",
  "artifactId": "001",
  "passed": true,
  "metrics": {
    "darcy_64": {
      "relative_l2": 0.083,
      "spectral_hf_error": 0.19,
      "train_time_sec": 940,
      "peak_memory_gb": 21.3
    }
  },
  "validation": {
    "passed": true,
    "failedConstraints": [],
    "validatorVersion": "neuraloperator-fno-v1"
  }
}
```

## Autoresearch Descriptor

Add a new experiment descriptor:

```text
neuraloperator_fno
```

The descriptor should define:

- `artifact.baselinePath`: baseline NeuralOperator workspace/template
- `artifact.workingPath`: mutable working copy for the current experiment
- `artifact.allowedPaths`: only files the implementation LLM may edit
- `artifact.archiveGlobs`: files copied into immutable artifact history
- `benchmark.command`: local or remote benchmark runner
- `benchmark.resultParser`: parser for canonical benchmark JSON
- `validation.required`: correctness and metric gates
- `metrics.primary`: likely `relative_l2`, direction `minimize`
- `metrics.secondary`: spectral/high-frequency/runtime/memory/stability metrics
- `implementInstructions`: NeuralOperator-specific implementation constraints
- `parseStructure`: parser for model/config structure when useful

The LLM should not access benchmark internals, hidden data, or validator logic. It only edits allowed implementation files and returns structured output. The controller runs all benchmarks and writes all state.

## Seeding

Use pre-loop seeding to populate root ideas from papers and references:

```text
/seed-from-paper {paper_path_or_id}
/seed-from-file {reference_path}
/seed-from-ref {configured_reference_name}
```

Candidate seed references:

- original FNO paper
- TFNO / tensorized neural operator papers
- LocalNO-style work
- recent papers on high-frequency neural operator behavior
- spectral bias / high-frequency reconstruction papers
- PDEBench and The Well benchmark references

Seed ideas should become root pending ideas in the idea tree. The LLM may extract ideas, but the controller validates and writes them.

## Compute Plan

CPU/Mac is enough for:

- literature/reference ingestion
- repo inspection
- descriptor implementation
- code changes
- tiny smoke tests

GPU is needed for meaningful 2D/3D FNO training.

### A10

An NVIDIA A10 with 24GB VRAM should work for phase 1:

- 2D FNO benchmarks
- small ablations
- adapter/fine-tuning experiments
- small 3D experiments with low batch size/resolution

It is not ideal for large 3D or The Well-scale runs.

### A100

An A100 will work well:

- A100 40GB is strong for real NeuralOperator research
- A100 80GB is preferable for 3D, long rollouts, larger mode counts, and larger datasets

The main benefit over A10 is memory headroom and higher bandwidth.

## Lambda Cloud Plan

Lambda Cloud supports GPU instances through UI, CLI, and API, including A10 and A100 instance types. Use Lambda only through deterministic controller infrastructure.

The LLM must not launch, terminate, or inspect Lambda instances.

Controller-owned Lambda flow:

```text
1. Launch instance through Lambda API
2. Sync repo/artifact/dataset cache
3. Run benchmark command remotely
4. Collect benchmark.json and logs
5. Store results under .autoresearch/artifacts/{type}/{id}/
6. Terminate instance unless keepalive is explicitly configured
```

Lambda credentials should live in `.env`, not in tracked files.

Example future `.env` entries:

```bash
LAMBDA_API_KEY=...
AUTORESEARCH_REMOTE_PROVIDER=lambda
```

## Milestones

### Phase 0: Mac Smoke Benchmark

Purpose: validate autoresearch plumbing, not research quality.

Implemented MVP target:

```text
descriptor: src/autoresearch/descriptors/neuraloperator_fno.py
baseline: workspace/baselines/neuraloperator_fno/
benchmark: benchmarks/neuraloperator_fno/run_smoke.py
```

This target now attempts a real local NeuralOperator CPU smoke benchmark when `external/neuraloperator` and the minimal Python dependencies are present. It imports `neuralop.models.FNO`, generates a tiny Darcy-style variable-coefficient elliptic PDE dataset locally, trains/evaluates baseline FNO and the `HighFrequencyResidualFNO` artifact on held-out generated samples, and emits canonical benchmark JSON.

If `external/neuraloperator` or dependencies are missing, it falls back to a deterministic proxy benchmark so isolated controller tests still run. The proxy fallback is for plumbing only and should not be treated as research signal.

Local setup:

```bash
git clone --depth 1 https://github.com/neuraloperator/neuraloperator external/neuraloperator
python3 -m pip install -r requirements-neuraloperator-smoke.txt
```

PyTorch must also be installed for the local platform.

Run a tiny NeuralOperator benchmark locally:

- generated Darcy-style PDE subset
- 1 epoch / a few optimization steps
- low resolution
- tiny batch size
- small FNO

This proves the controller can:

1. prepare a NeuralOperator workspace
2. let the LLM implement only allowed files
3. archive the artifact
4. run a benchmark script
5. parse `benchmark.json`
6. validate metrics and gates
7. diagnose results
8. generate/select next ideas

### Phase 1: Single Remote GPU Benchmark

Purpose: validate remote execution.

Run the same benchmark contract on one Lambda A10 or A100:

- same descriptor
- same canonical benchmark JSON
- same controller isolation
- different benchmark backend

Local backend:

```text
benchmark.command -> run script on Mac
```

Remote backend:

```text
benchmark.command -> launch/sync/run/collect/terminate on Lambda
```

### Phase 2: Small Real 2D PDE Experiments

Purpose: get meaningful early research signal.

Run baseline FNO vs first `HighFrequencyResidualFNO` variant on a small 2D PDE benchmark.

Required output table:

```text
model
relative_l2
spectral_hf_error
train_time_sec
peak_memory_gb
inference_latency_ms
```

### Phase 3: Larger A100 Runs

Purpose: scale only after the loop finds promising variants.

Use A100 for:

- larger mode counts
- longer rollouts
- larger batch sizes
- 3D PDEs
- PDEBench / The Well-scale experiments

Do not spend A100 time debugging path layout, result parsing, environment setup, or malformed LLM output. Those should be resolved in Phase 0.

## First Concrete Codex Task

```text
Inspect the NeuralOperator repo and identify the cleanest place to add a new FNO variant called HighFrequencyResidualFNO. Implement it with minimal changes, using an FNO backbone plus an optional residual branch trained with frequency-weighted loss. Add a small benchmark script comparing baseline FNO vs HighFrequencyResidualFNO on a 2D PDE dataset. Include config flags for n_modes, hidden_channels, n_layers, batch_size, mixed precision, and loss weighting.
```
