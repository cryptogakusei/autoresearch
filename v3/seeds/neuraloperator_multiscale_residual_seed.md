# NeuralOperator FNO Seed: Multi-Scale High-Frequency Residual Branch

Create one concrete implementation variant for the tiny Darcy smoke benchmark.

Idea:
Replace or extend the current single local residual branch in
`HighFrequencyResidualFNO` with a multi-scale local residual path. Use parallel
small convolution branches, such as 3x3 and 5x5 kernels, to capture fine local
features at more than one spatial scale. Keep the implementation compact enough
for CPU smoke testing.

Allowed implementation target:
`workspace/current/neuraloperator_fno/neuraloperator_variant/high_frequency_residual_fno.py`

Optional config target:
`workspace/current/neuraloperator_fno/benchmarks/smoke_config.json`

Do not change benchmark code. Do not run or call the benchmark from the LLM
implementation. The control layer will run the benchmark after applying the
allowed file edit.
