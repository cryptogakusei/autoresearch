# Phase 4 Candidate Hybrid Lite Fast

Source: manual Phase 4 hybrid from `neuraloperator_fno_gpu` artifacts `135`
and `031`.

Design: latent multiresolution wavelet correction from `135` plus stronger
derivative-aware objective ideas from `031`.

This is a parameter- and efficiency-control version of the hybrid. The model
keeps the hybrid architecture/loss idea, but reduces `hidden_channels` from 80
to 40 and uses Lie-Trotter split ordering so the parameter count and train time
are comparable to the current Phase 4 winner.

The benchmark config sets `baseline_hidden_channels` separately so the plain
FNO comparator is matched by parameter count rather than hidden width.
