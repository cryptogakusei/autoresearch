# NeuralOperator FNO Phase 4 Baseline

This is the isolated Phase 4 baseline for focused scale-up runs.

It intentionally uses a separate experiment type from `neuraloperator_fno_gpu`:

```text
neuraloperator_fno_phase4
```

Phase 4 must not mutate Phase 2/3 artifacts, state, or idea trees. Promoted
Phase 3 designs can be copied or ported into this workspace, then benchmarked
with the larger Phase 4 budget.

Initial promoted designs:

- `135`: latent multiresolution wavelet correction
- `031`: derivative-aware loss / strong high-frequency behavior
- `hybrid_135_031`: latent wavelet correction plus derivative/Sobolev/Fourier-H1 loss

The deterministic controller owns benchmark execution, dataset contracts,
artifact archival, validation, and state updates.
