# Phase 4 Candidate 135 Lite

Source: `neuraloperator_fno_gpu` artifact `135`.

Design: latent multiresolution Haar wavelet correction inserted into a split
FNO operator.

This is a parameter-control version of candidate `135`. The model code is the
same split-wavelet artifact, but `hidden_channels` is reduced from 80 to 40 so
the parameter count is below the Phase 4 FNO baseline.

Purpose: test whether the split-wavelet idea improves accuracy at roughly the
same capacity as the matched baseline, rather than relying on a larger model.

The benchmark config sets `baseline_hidden_channels` separately from the
candidate width so the plain FNO comparator is capacity-matched by parameter
count rather than by hidden width.
