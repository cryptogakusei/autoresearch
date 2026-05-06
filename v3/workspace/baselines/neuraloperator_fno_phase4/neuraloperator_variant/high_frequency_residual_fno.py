"""Phase 4 HighFrequencyResidualFNO baseline.

This baseline preserves the stable artifact interface used by the GPU
benchmark. Promoted Phase 3 designs can replace or extend this implementation
inside the isolated ``neuraloperator_fno_phase4`` workspace.
"""

from __future__ import annotations

import torch
from torch import nn

from neuralop.models import FNO


class HighFrequencyResidualFNO(nn.Module):
    """FNO backbone plus a compact local residual correction branch."""

    def __init__(
        self,
        n_modes=(24, 24),
        in_channels=3,
        out_channels=1,
        hidden_channels=48,
        n_layers=4,
        residual_weight=0.1,
    ):
        super().__init__()
        self.residual_weight = float(residual_weight)
        self.backbone = FNO(
            n_modes=tuple(n_modes),
            in_channels=in_channels,
            out_channels=out_channels,
            hidden_channels=hidden_channels,
            n_layers=n_layers,
            lifting_channel_ratio=1,
            projection_channel_ratio=1,
            use_channel_mlp=False,
        )
        self.local_residual = nn.Sequential(
            nn.Conv2d(in_channels, hidden_channels, kernel_size=3, padding=1),
            nn.GELU(),
            nn.Conv2d(hidden_channels, out_channels, kernel_size=3, padding=1),
        )

    def forward(self, x):
        return self.backbone(x) + self.residual_weight * self.local_residual(x)


def frequency_weighted_mse(pred, target, high_frequency_weight=0.0):
    """MSE plus optional high-frequency Fourier-domain error."""

    base = torch.mean((pred - target) ** 2)
    if high_frequency_weight <= 0:
        return base

    pred_fft = torch.fft.rfft2(pred, norm="ortho")
    target_fft = torch.fft.rfft2(target, norm="ortho")
    height = pred.shape[-2]
    width_half = pred_fft.shape[-1]
    fy = torch.fft.fftfreq(height, device=pred.device).abs().view(1, 1, height, 1)
    fx = torch.fft.rfftfreq((width_half - 1) * 2, device=pred.device).abs().view(
        1, 1, 1, width_half
    )
    weights = 1.0 + high_frequency_weight * (fx + fy)
    spectral = torch.mean(torch.abs(pred_fft - target_fft) ** 2 * weights)
    return base + spectral.real


def build_model(config):
    return HighFrequencyResidualFNO(
        n_modes=tuple(config.get("n_modes", [24, 24])),
        in_channels=int(config.get("in_channels", 3)),
        out_channels=int(config.get("out_channels", 1)),
        hidden_channels=int(config.get("hidden_channels", 48)),
        n_layers=int(config.get("n_layers", 4)),
        residual_weight=float(config.get("residual_weight", 0.1)),
    )
