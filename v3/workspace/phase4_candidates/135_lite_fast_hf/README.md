# Phase 4 Candidate 135 Lite Fast HF

Source: `neuraloperator_fno_gpu` artifact `135`.

Design: latent multiresolution Haar wavelet correction inserted into a split
FNO operator.

This is a high-frequency loss follow-up to candidate `135_lite_fast`. It keeps
the same parameter-matched split-wavelet architecture, Lie-Trotter ordering,
candidate width, and capacity-matched baseline comparator.

Purpose: test whether a small Fourier high-frequency loss term can improve the
Navier-Stokes spectral high-frequency metric while preserving the L2/H1 gains
and Phase 4 train-time pass from `135_lite_fast`.

The benchmark config sets `baseline_hidden_channels` separately from the
candidate width so the plain FNO comparator is capacity-matched by parameter
count rather than by hidden width.
