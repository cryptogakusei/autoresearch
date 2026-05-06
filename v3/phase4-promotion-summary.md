# Phase 4 Promotion Summary

Date: 2026-05-03

## Current promoted candidate

`workspace/phase4_candidates/135_lite_fast`

Design: parameter-matched split-wavelet FNO using Lie-Trotter split ordering.

Comparison:

| Metric | Parameter-matched FNO | 135_lite_fast |
| --- | ---: | ---: |
| Params | 7.63M | 7.45M |
| Peak memory | - | 20.06 GB |
| Darcy relative L2 | 0.058708 | 0.036771 |
| Darcy H1 | 0.059307 | 0.037115 |
| Darcy spectral HF | 0.764893 | 0.442419 |
| Darcy train time | - | 1603.48 sec |
| Navier-Stokes relative L2 | 0.070882 | 0.030119 |
| Navier-Stokes H1 | 0.075717 | 0.033726 |
| Navier-Stokes spectral HF | 1.021098 | 1.691244 |
| Navier-Stokes train time | - | 1601.33 sec |

Outcome: passes Phase 4 validation constraints and is the current promoted
candidate for relative L2 and H1 accuracy at parameter-matched capacity.

Important caveat: Navier-Stokes spectral high-frequency error worsens versus
the matched FNO baseline. The next candidate should target that specific metric
without losing the L2/H1 gains or exceeding the train-time cap.

## Follow-up candidate

`workspace/phase4_candidates/135_lite_fast_hf`

Goal: keep the same architecture, parameter count, and capacity-matched
baseline comparator as `135_lite_fast`, but add a small Fourier high-frequency
loss term to improve Navier-Stokes spectral high-frequency error.

Recovered result: the local SSH command reported a transport failure after a
connection reset, but the remote benchmark completed with `exit_status=0` and
persisted valid JSON at:

`/home/ubuntu/autoresearch/.autoresearch/logs/phase4_135_lite_fast_hf.stdout.json`

Comparison:

| Metric | Parameter-matched FNO | 135_lite_fast | 135_lite_fast_hf |
| --- | ---: | ---: | ---: |
| Params | 7.63M | 7.45M | 7.45M |
| Peak memory | - | 20.06 GB | 20.06 GB |
| Darcy relative L2 | 0.058708 | 0.036771 | 0.036982 |
| Darcy H1 | 0.059307 | 0.037115 | 0.037312 |
| Darcy spectral HF | 0.764893 | 0.442419 | 0.409122 |
| Darcy train time | - | 1603.48 sec | 1605.08 sec |
| Navier-Stokes relative L2 | 0.070882 | 0.030119 | 0.027345 |
| Navier-Stokes H1 | 0.075717 | 0.033726 | 0.030048 |
| Navier-Stokes spectral HF | 1.021098 | 1.691244 | 1.098529 |
| Navier-Stokes train time | - | 1601.33 sec | 1603.18 sec |

Outcome: `135_lite_fast_hf` passes the Phase 4 constraints and materially
improves the Navier-Stokes spectral high-frequency regression relative to
`135_lite_fast` while also improving Navier-Stokes relative L2 and H1. It still
does not beat the matched FNO baseline on Navier-Stokes spectral HF.

## Additional fair candidates staged

`workspace/phase4_candidates/031_lite_param_matched`

Purpose: parameter-controlled version of the `031` derivative-aware branch.

- Candidate params: 7.80M
- Candidate width: 36
- Plain FNO comparator: `baseline_hidden_channels=92`
- Preflight: passed

`workspace/phase4_candidates/hybrid_lite_fast`

Purpose: parameter- and efficiency-controlled hybrid of the `135` split-wavelet
architecture and `031` derivative-aware objective.

- Candidate params: 7.45M
- Candidate width: 40
- Plain FNO comparator: `baseline_hidden_channels=78`
- Split ordering: Lie-Trotter
- Preflight: passed
