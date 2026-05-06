# Phase 4 Candidate 135 Lite Fast

Source: `neuraloperator_fno_gpu` artifact `135`.

Design: latent multiresolution Haar wavelet correction inserted into a split
FNO operator.

This is an efficiency-control version of candidate `135_lite`. It keeps the
same parameter-matched split-wavelet artifact and candidate width, but changes
the split ordering from Strang to Lie-Trotter so each block performs one
spectral update instead of two.

Purpose: test whether the split-wavelet idea can stay below the Phase 4
train-time cap while retaining the parameter-matched accuracy gains.

The benchmark config sets `baseline_hidden_channels` separately from the
candidate width so the plain FNO comparator is capacity-matched by parameter
count rather than by hidden width.
