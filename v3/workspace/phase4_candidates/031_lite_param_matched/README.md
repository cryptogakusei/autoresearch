# Phase 4 Candidate 031 Lite Parameter Matched

Source: `neuraloperator_fno_gpu` artifact `031`.

Design: U-shaped Fourier neural operator with derivative-aware composite loss.

This is a parameter-control version of candidate `031`. The model code is
copied from the Phase 4 candidate, but `hidden_channels` is reduced from 80 to
36 and `max_channels` is reduced accordingly.

The benchmark config sets `baseline_hidden_channels` separately so the plain
FNO comparator is matched by parameter count rather than hidden width.
