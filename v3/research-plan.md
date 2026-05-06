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

Run baseline FNO vs variant on multiple 2D PDE benchmarks at 128×128 resolution.

#### Datasets

- **Darcy flow** (elliptic PDE) — coefficient-to-solution mapping. Data from neuraloperator Zenodo. 10K train / 2K test samples.
- **Navier-Stokes** (time-dependent, turbulent) — vorticity at t → vorticity at t+1. Data from neuraloperator Zenodo (nsforcing_128). 10K train / 2K test samples.

Both cached as `.pt` tensors at `/home/ubuntu/data/{darcy,navier_stokes}/`.

#### Evaluation methodology

Following standard practice from Anima Anandkumar's group (FNO, TFNO, FFNO papers):

- **Separate model per PDE.** Each dataset gets its own `build_model(config)` call with dataset-specific `in_channels`, `out_channels`, and hyperparameters. This matches the literature — papers show the architecture family generalizes, not that a single model instance does.
- **Per-PDE hyperparameters.** Config supports per-dataset overrides for modes, channels, epochs, learning rate. Architecture template is shared.
- **Darcy drives search ranking.** Primary metric is `relative_l2` on `darcy_128`. CCTS scoring, idea priority, and candidate selection all use Darcy as the objective.
- **Navier-Stokes is a generalization signal.** N-S metrics are collected, displayed, and visible to the diagnosis LLM, but do not influence search direction. If a variant improves Darcy but tanks N-S, the diagnosis can flag it.
- **No multi-objective optimization.** The one exception in the literature is DPOT (ICML 2024), a foundation model pre-trained across 12 PDEs — but that's a different paradigm. Standard neural operator papers optimize per-PDE.

#### Metrics table

```text
model
relative_l2          (primary, drives search)
spectral_hf_error    (relative error on high-frequency Fourier band, cutoff ≥ 0.25)
h1_error             (relative H1 Sobolev error — captures gradient accuracy)
param_count          (total learnable parameters)
train_time_sec       (wall-clock training time, constraint ≤ 600s)
peak_memory_gb       (GPU peak memory, constraint ≤ 40 GB)
```

H1 error and param_count replace inference_latency_ms from the original plan — these are more standard in the literature (TFNO reports H1, most papers report param count, almost none report inference latency).

#### Benchmark

Script: `benchmarks/neuraloperator_fno/run_multi_pde_gpu.py`
Timeout: 900s (both datasets complete in ~150s on A100).
Baseline comparison built into each run — both baseline FNO and variant are trained and evaluated.

### Phase 3: Stabilization and Promotion

Purpose: turn noisy exploration results into reliable promoted candidates before making any published-result claims.

This phase should happen after Phase 2 has produced a few promising artifacts, and before larger/canonical benchmark claims. The goal is not to search broadly. The goal is to make the benchmark contract stricter, remove obvious failure modes, and re-run the best candidates under controlled conditions.

#### Control-layer-owned benchmark contract

The LLM implementation call must not be allowed to change dataset interface fields. These are owned by the descriptor and benchmark runner:

```text
dataset name
in_channels
out_channels
resolution
train_samples
val_samples
dataset paths
required metric names
validation gates
```

The LLM may tune model and training hyperparameters only when the descriptor allows them, for example:

```text
n_modes
hidden_channels
n_layers
learning_rate
frequency_loss_weight
residual_weight
architecture-specific small hyperparameters
```

If an implementation returns `smoke_config.json` with changed dataset contract fields, the controller should reject or repair the config before benchmarking.

#### Deterministic artifact preflight

Before launching the full benchmark, the controller should run a cheap deterministic artifact smoke check:

1. Import the generated `high_frequency_residual_fno.py`.
2. Load the controller-owned dataset configs.
3. Instantiate `build_model(config)` for each dataset.
4. Run a dummy forward pass with expected tensor shapes.
5. Run `frequency_weighted_mse(pred, target, weight).backward()`.
6. Check output shape, nonzero parameter count, finite loss, and no NaN/Inf.

This is not a scientific benchmark and does not decide whether the idea is good. It is a compile/shape/runtime sanity check owned by deterministic control code. Failed preflight should produce a clear failure reason and should avoid spending GPU benchmark time.

#### Preserve raw benchmark failures

The benchmark runner should preserve the original exception or failure reason in stored results. Controller validation may add failed constraints, but it should not erase raw benchmark failure details. This prevents opaque `999` sentinel metrics without traceback.

Recommended stored fields:

```json
{
  "metadata": {
    "failureReason": "...",
    "traceback": "...",
    "benchmarkVersion": "..."
  },
  "validation": {
    "failedConstraints": [...]
  }
}
```

#### Domain and novelty filtering

Before adding or running generated children, the controller should filter obvious off-domain ideas. For this NeuralOperator track, ideas should mention or clearly imply FNO, neural operators, PDEs, spectral methods, wavelets, local residuals, operators, grids, rollouts, conservation, or related numerical concepts.

Ideas about CCTS, user segments, conversion, friction moments, product prompts, or unrelated behavioral experiments should be rejected or quarantined before they enter the active experiment queue.

#### Candidate promotion protocol

Promote only a small set of stable candidates, for example the top 3-5 by primary metric and diagnosis quality.

For each promoted candidate:

- re-run with fixed seeds
- run 2-3 seeds if budget allows
- compare against matched fresh baseline FNO
- compare against parent and current best artifact
- require no validation failures
- require acceptable secondary metrics such as `spectral_hf_error`, `h1_error`, memory, time, and parameter count

Promotion output should be a table of candidate id, architecture summary, mean/std metrics, runtime, memory, and reason for promotion or rejection.

#### Phase 3 MVP implementation plan

For the immediate next step, keep Phase 3 lean. Do not implement multi-seed promotion tables, full domain filtering, or cross-branch recombination yet. The MVP scope is:

```text
1. Lock config
2. Add deterministic artifact preflight
3. Preserve better failure logging
```

##### 1. Lock config

Protected benchmark-contract fields should be owned by the descriptor/control layer, not by the LLM implementation call:

```text
datasets.*.dataset
datasets.*.in_channels
datasets.*.out_channels
shared.resolution
shared.train_samples
shared.val_samples
dataset paths
benchmark dataset list
required metrics
validation gates
```

In the current MVP, the benchmark dataset list itself is locked by the descriptor's benchmark command (`--datasets darcy_128,navier_stokes_128`). Config validation protects the dataset block keys indirectly through required `datasets.{name}.in_channels` and `datasets.{name}.out_channels` paths, while allowing older artifacts that do not contain a redundant `datasets.{name}.dataset` field.

After the LLM edits `smoke_config.json`, the controller should compare protected fields against the baseline or descriptor-owned contract.

Recommendation: reject the experiment before benchmark if protected fields changed, instead of silently repairing them. Rejection keeps the violation visible and avoids training a model whose code may assume the changed interface.

Example failure:

```text
implementation failed: protected config field changed:
datasets.navier_stokes_128.in_channels expected 3 got 10
```

Editable fields may include:

```text
n_modes
hidden_channels
n_layers
learning_rate
frequency_loss_weight
residual_weight
small architecture-specific hyperparameters
```

##### 2. Deterministic artifact preflight

Run a cheap deterministic check after implementation and config-lock validation, but before the full benchmark:

```text
LLM implement
-> controller applies allowed edits
-> config lock validation
-> artifact preflight
-> archive artifact
-> full benchmark
```

Preflight checks:

```text
import high_frequency_residual_fno.py
for each dataset config:
  build_model(config)
  create dummy x/y tensors
  pred = model(x)
  assert pred.shape == y.shape
  loss = frequency_weighted_mse(pred, y, weight)
  assert loss is finite
  loss.backward()
  assert param_count > 0
```

Use small dummy tensors such as `16x16` or `32x32`; do not use real benchmark data. This check is not a scientific benchmark and must not decide whether the idea is good. It only checks import, shape compatibility, differentiability, finite loss, and nonzero parameters.

Example failure:

```text
preflight failed for navier_stokes_128:
expected output shape (2, 1, 16, 16), got (2, 10, 16, 16)
```

##### 3. Better failure logging

Preserve raw benchmark failure details. If the benchmark runner emits an exception, traceback, or failure reason, controller validation should not overwrite it.

Recommended stored shape:

```json
{
  "benchmarkValidation": {
    "passed": false,
    "failedConstraints": [
      {"reason": "RuntimeError: expected input channels=3 but got 10"}
    ]
  },
  "controllerValidation": {
    "passed": false,
    "failedConstraints": [
      {"metric": "valid", "expected": true, "actual": false}
    ]
  },
  "metadata": {
    "benchmarkFailureReason": "...",
    "benchmarkTraceback": "...",
    "benchmarkVersion": "..."
  }
}
```

Acceptance criteria:

```text
- LLM cannot change protected dataset/config contract fields without failing before benchmark
- generated artifact must pass dummy import/forward/backward before GPU benchmark
- failed benchmarks preserve readable failure reason or traceback
- existing smoke tests pass
- valid artifacts still run normally
```

### Phase 4: Larger A100 Runs

Purpose: scale only after the loop finds promising variants.

Phase 4 is a focused scale-up phase, not another broad tree-wide search. It should carry forward the strongest Phase 3 designs and test whether they survive a larger training budget.

#### Isolation rule

Phase 4 should be implemented as a separate experiment type:

```text
neuraloperator_fno_phase4
```

It must use separate state, workspace, baseline, and artifact directories:

```text
workspace/baselines/neuraloperator_fno_phase4/
workspace/current/neuraloperator_fno_phase4/
.autoresearch/state/neuraloperator_fno_phase4.ideas.json
.autoresearch/state/neuraloperator_fno_phase4.signals.json
.autoresearch/artifacts/neuraloperator_fno_phase4/
```

The existing Phase 2/3 experiment type remains untouched:

```text
neuraloperator_fno_gpu
```

Phase 4 may copy or port promoted designs from Phase 3 artifacts, but it must not edit the original archived artifacts or mutate the Phase 2/3 idea tree.

#### Shared-code rule

Avoid global changes to shared benchmark behavior. Prefer descriptor/config knobs over modifying `benchmarks/neuraloperator_fno/run_multi_pde_gpu.py` in ways that affect `neuraloperator_fno_gpu`.

If shared runner changes are unavoidable, they must be backward-compatible with the existing Phase 2/3 descriptor.

#### Promoted starting set

Initial Phase 4 candidates:

```text
135  latent multiresolution wavelet correction
031  derivative-aware loss / strong high-frequency behavior
hybrid_135_031  latent wavelet correction plus derivative/Sobolev/Fourier-H1 loss
```

Keep `046` and `047` as lightweight loss-only baselines, not primary scale-up candidates.

#### Scale-up knobs

Use A100 for controlled increases such as:

- larger mode counts
- more epochs
- more train samples
- larger validation set
- larger batch sizes
- larger hidden width
- longer rollouts
- 3D PDEs
- PDEBench / The Well-scale experiments

Suggested first Phase 4 budget:

```text
datasets: darcy_128, navier_stokes_128
train_samples: 4000
val_samples: 500
epochs: 100
batch_size: 32
n_modes: [24, 24] or [32, 32]
hidden_channels: 48 or 64
timeout: 3600 sec
```

Dataset contract fields remain protected by the controller:

```text
dataset names
in_channels
out_channels
resolution
train_samples
val_samples
dataset paths
validation gates
```

#### Execution rule

Run deterministic promoted-candidate benchmarks first:

```text
1. phase4_candidate_135
2. phase4_candidate_031
3. phase4_candidate_hybrid_135_031
```

Only after those results are available should the system run any Phase 4 autoloop. If autoloop is used, keep it focused:

```text
autoloop 5-10 from promoted/hybrid branches only
```

Do not run broad tree-wide Phase 4 exploration until the promoted candidates have been scaled and compared.

#### Phase 4 decision criteria

Promote to Phase 5 only if a candidate:

```text
- beats matched fresh FNO baseline clearly at larger budget
- passes preflight and validation
- does not rely on changed dataset contract fields
- has acceptable train time and memory
- does not badly regress key secondary metrics
- remains interpretable enough to compare against published methods
```

Do not spend A100 time debugging path layout, result parsing, environment setup, malformed LLM output, config contract violations, or artifact import failures. Those should be resolved in Phases 0-3.

### Phase 5: Canonical Published-Result Benchmarking

Purpose: determine whether promoted candidates beat published baselines under canonical settings.

This phase should not start until Phase 3 produces stable candidates. It should reproduce official or literature-standard benchmark settings as closely as possible:

- official dataset versions
- official train/test splits
- canonical preprocessing
- canonical optimizer and learning-rate schedules
- published model widths, modes, ranks, layers, and training budgets
- multiple seeds
- tables with confidence intervals or mean/std
- parameter count, training time, peak memory, and inference cost

This phase answers a different question from Phase 2:

```text
Phase 2: Did the artifact beat our matched internal baseline?
Phase 5: Did the promoted artifact beat published NeuralOperator/FNO-style results under canonical settings?
```

Claims about beating published results should only be made from Phase 5 results.

## First Concrete Codex Task

```text
Inspect the NeuralOperator repo and identify the cleanest place to add a new FNO variant called HighFrequencyResidualFNO. Implement it with minimal changes, using an FNO backbone plus an optional residual branch trained with frequency-weighted loss. Add a small benchmark script comparing baseline FNO vs HighFrequencyResidualFNO on a 2D PDE dataset. Include config flags for n_modes, hidden_channels, n_layers, batch_size, mixed precision, and loss weighting.
```
