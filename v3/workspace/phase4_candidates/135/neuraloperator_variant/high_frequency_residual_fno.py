"""Experiment 135: split FNO with a latent wavelet-operator correction.

The trunk keeps the current split/FNO blocks, lifting, projection, width,
Fourier modes, and padding-free channel projections.  A small correction is
inserted after the middle FNO block: latent feature maps are decomposed with
a cheap Haar 2D DWT, LL and detail bands are mixed by learned 1x1 channel
operators, and the inverse DWT reconstructs a scale-separated residual that
is added back with a learnable scalar initialized near zero.

The public API expected by the benchmark harness is preserved:
HighFrequencyResidualFNO, build_model(config), and frequency_weighted_mse.
"""

from __future__ import annotations

import math
from typing import Iterable, Tuple

import torch
import torch.nn.functional as F
from torch import nn

try:  # Keep the variant self-contained while remaining NeuralOperator-facing.
    from neuralop.models import FNO as _NeuralOperatorFNO  # noqa: F401
except Exception:  # pragma: no cover - benchmark environments provide neuralop.
    _NeuralOperatorFNO = None


def _as_2tuple(value: Iterable[int] | int) -> Tuple[int, int]:
    if isinstance(value, int):
        return (value, value)
    value = tuple(value)
    if len(value) != 2:
        raise ValueError("n_modes must be an int or a length-2 iterable")
    return (int(value[0]), int(value[1]))


class SpectralConv2d(nn.Module):
    """Compact 2D Fourier convolution on the lowest retained modes."""

    def __init__(self, in_channels: int, out_channels: int, n_modes=(16, 16)):
        super().__init__()
        self.in_channels = int(in_channels)
        self.out_channels = int(out_channels)
        self.n_modes = _as_2tuple(n_modes)
        scale = 1.0 / math.sqrt(max(1, self.in_channels * self.out_channels))
        self.weights_pos = nn.Parameter(
            scale
            * torch.randn(
                self.in_channels,
                self.out_channels,
                self.n_modes[0],
                self.n_modes[1],
                dtype=torch.cfloat,
            )
        )
        self.weights_neg = nn.Parameter(
            scale
            * torch.randn(
                self.in_channels,
                self.out_channels,
                self.n_modes[0],
                self.n_modes[1],
                dtype=torch.cfloat,
            )
        )

    @staticmethod
    def compl_mul2d(x: torch.Tensor, weights: torch.Tensor) -> torch.Tensor:
        return torch.einsum("bixy,ioxy->boxy", x, weights)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size, _, height, width = x.shape
        x_ft = torch.fft.rfft2(x, norm="ortho")
        out_ft = torch.zeros(
            batch_size,
            self.out_channels,
            height,
            width // 2 + 1,
            device=x.device,
            dtype=torch.cfloat,
        )

        modes_y = min(self.n_modes[0], height // 2)
        modes_x = min(self.n_modes[1], width // 2 + 1)
        if modes_y > 0 and modes_x > 0:
            out_ft[:, :, :modes_y, :modes_x] = self.compl_mul2d(
                x_ft[:, :, :modes_y, :modes_x],
                self.weights_pos[:, :, :modes_y, :modes_x],
            )
            out_ft[:, :, -modes_y:, :modes_x] = self.compl_mul2d(
                x_ft[:, :, -modes_y:, :modes_x],
                self.weights_neg[:, :, :modes_y, :modes_x],
            )

        return torch.fft.irfft2(out_ft, s=(height, width), norm="ortho")


class SplitFNOBlock(nn.Module):
    """One residual split layer with configurable operator ordering."""

    VALID_VARIANTS = {
        "lie_trotter",
        "lie-trotter",
        "trotter",
        "strang",
        "strang_split",
        "strang-split",
    }

    def __init__(
        self,
        channels: int,
        n_modes=(16, 16),
        split_variant: str = "strang",
        step_scale: float = 0.5,
        physical_kernel_size: int = 3,
        dropout: float = 0.0,
    ):
        super().__init__()
        split_variant = str(split_variant).lower()
        if split_variant not in self.VALID_VARIANTS:
            raise ValueError(
                f"Unsupported split_variant={split_variant!r}; "
                f"expected one of {sorted(self.VALID_VARIANTS)}"
            )
        kernel_size = int(physical_kernel_size)
        if kernel_size < 1:
            kernel_size = 1
        if kernel_size % 2 == 0:
            kernel_size += 1

        self.channels = int(channels)
        self.split_variant = split_variant
        self.step_scale = float(step_scale)
        self.spectral = SpectralConv2d(channels, channels, n_modes=n_modes)
        self.physical = nn.Sequential(
            nn.Conv2d(channels, channels, kernel_size=kernel_size, padding=kernel_size // 2),
            nn.GELU(),
            nn.Conv2d(channels, channels, kernel_size=1),
        )
        self.spectral_norm = nn.GroupNorm(1, channels)
        self.physical_norm = nn.GroupNorm(1, channels)
        self.activation = nn.GELU()
        self.dropout = nn.Dropout2d(float(dropout)) if dropout and dropout > 0 else nn.Identity()
        self.spectral_gate = nn.Parameter(torch.ones(1, channels, 1, 1) * self.step_scale)
        self.physical_gate = nn.Parameter(torch.ones(1, channels, 1, 1) * self.step_scale)

    @property
    def is_strang(self) -> bool:
        return self.split_variant in {"strang", "strang_split", "strang-split"}

    @property
    def spectral_calls_per_forward(self) -> int:
        return 2 if self.is_strang else 1

    def _spectral_update(self, x: torch.Tensor, fraction: float = 1.0) -> torch.Tensor:
        update = self.spectral(self.spectral_norm(x))
        return x + float(fraction) * self.dropout(update) * self.spectral_gate

    def _physical_update(self, x: torch.Tensor) -> torch.Tensor:
        update = self.physical(self.physical_norm(x))
        return x + self.dropout(update) * self.physical_gate

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.is_strang:
            x = self.activation(self._spectral_update(x, fraction=0.5))
            x = self.activation(self._physical_update(x))
            x = self.activation(self._spectral_update(x, fraction=0.5))
            return x

        x = self.activation(self._spectral_update(x, fraction=1.0))
        x = self.activation(self._physical_update(x))
        return x


class HaarDWT2d:
    """Static orthonormal Haar DWT/IDWT utilities for BCHW tensors."""

    @staticmethod
    def pad_to_even(x: torch.Tensor) -> tuple[torch.Tensor, int, int]:
        pad_h = x.shape[-2] % 2
        pad_w = x.shape[-1] % 2
        if pad_h == 0 and pad_w == 0:
            return x, 0, 0
        return F.pad(x, (0, pad_w, 0, pad_h), mode="replicate"), pad_h, pad_w

    @staticmethod
    def dwt(x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        x00 = x[..., 0::2, 0::2]
        x01 = x[..., 0::2, 1::2]
        x10 = x[..., 1::2, 0::2]
        x11 = x[..., 1::2, 1::2]
        ll = 0.5 * (x00 + x01 + x10 + x11)
        lh = 0.5 * (x00 - x01 + x10 - x11)
        hl = 0.5 * (x00 + x01 - x10 - x11)
        hh = 0.5 * (x00 - x01 - x10 + x11)
        return ll, lh, hl, hh

    @staticmethod
    def idwt(ll: torch.Tensor, lh: torch.Tensor, hl: torch.Tensor, hh: torch.Tensor) -> torch.Tensor:
        batch, channels, height, width = ll.shape
        out = ll.new_empty(batch, channels, height * 2, width * 2)
        out[..., 0::2, 0::2] = 0.5 * (ll + lh + hl + hh)
        out[..., 0::2, 1::2] = 0.5 * (ll - lh + hl - hh)
        out[..., 1::2, 0::2] = 0.5 * (ll + lh - hl - hh)
        out[..., 1::2, 1::2] = 0.5 * (ll - lh - hl + hh)
        return out


class WaveletLatentCorrection(nn.Module):
    """Scale-separated latent correction using DWT band channel mixers."""

    def __init__(
        self,
        channels: int,
        levels: int = 1,
        residual_init: float = 0.05,
        wavelet: str = "haar",
        dropout: float = 0.0,
    ):
        super().__init__()
        self.channels = int(channels)
        self.levels = max(0, min(2, int(levels)))
        self.wavelet = str(wavelet).lower().replace("-", "_")
        if self.wavelet not in {"haar", "db1"}:
            raise ValueError("WaveletLatentCorrection currently supports cheap exactly invertible Haar/db1 only")

        self.norm = nn.GroupNorm(1, self.channels)
        self.ll_mixer = nn.Conv2d(self.channels, self.channels, kernel_size=1)
        self.detail_mixers = nn.ModuleList(
            [
                nn.ModuleList(
                    [
                        nn.Conv2d(self.channels, self.channels, kernel_size=1),
                        nn.Conv2d(self.channels, self.channels, kernel_size=1),
                        nn.Conv2d(self.channels, self.channels, kernel_size=1),
                    ]
                )
                for _ in range(self.levels)
            ]
        )
        self.dropout = nn.Dropout2d(float(dropout)) if dropout and dropout > 0 else nn.Identity()
        self.residual_scale = nn.Parameter(torch.tensor(float(residual_init)))
        self.reset_parameters()

    def reset_parameters(self) -> None:
        # Start as an exact no-op correction while keeping a nonzero learnable gate.
        nn.init.zeros_(self.ll_mixer.weight)
        nn.init.zeros_(self.ll_mixer.bias)
        for mixers in self.detail_mixers:
            for mixer in mixers:
                nn.init.zeros_(mixer.weight)
                nn.init.zeros_(mixer.bias)

    def _decompose(
        self, x: torch.Tensor
    ) -> tuple[torch.Tensor, list[tuple[torch.Tensor, torch.Tensor, torch.Tensor, int, int, int, int]]]:
        coeffs = []
        current = x
        for _ in range(self.levels):
            if current.shape[-2] < 2 or current.shape[-1] < 2:
                break
            original_h, original_w = current.shape[-2], current.shape[-1]
            padded, pad_h, pad_w = HaarDWT2d.pad_to_even(current)
            ll, lh, hl, hh = HaarDWT2d.dwt(padded)
            coeffs.append((lh, hl, hh, original_h, original_w, pad_h, pad_w))
            current = ll
        return current, coeffs

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.levels <= 0:
            return x

        normalized = self.norm(x)
        ll, coeffs = self._decompose(normalized)
        if not coeffs:
            correction = self.ll_mixer(normalized)
        else:
            reconstruction = self.ll_mixer(ll)
            for level in reversed(range(len(coeffs))):
                lh, hl, hh, original_h, original_w, _pad_h, _pad_w = coeffs[level]
                lh_mixed = self.detail_mixers[level][0](lh)
                hl_mixed = self.detail_mixers[level][1](hl)
                hh_mixed = self.detail_mixers[level][2](hh)
                reconstruction = HaarDWT2d.idwt(reconstruction, lh_mixed, hl_mixed, hh_mixed)
                reconstruction = reconstruction[..., :original_h, :original_w]
            correction = reconstruction

        return x + self.residual_scale * self.dropout(correction)


class HighFrequencyResidualFNO(nn.Module):
    """Split-layer FNO with a middle latent wavelet correction."""

    def __init__(
        self,
        n_modes=(16, 16),
        in_channels=3,
        out_channels=1,
        hidden_channels=32,
        n_layers=4,
        residual_weight=0.1,
        split_variant="strang",
        split_step_scale=0.5,
        physical_kernel_size=3,
        dropout=0.0,
        wavelet_levels=1,
        wavelet_residual_init=0.05,
        wavelet_type="haar",
    ):
        super().__init__()
        self.n_modes = _as_2tuple(n_modes)
        self.in_channels = int(in_channels)
        self.out_channels = int(out_channels)
        self.hidden_channels = int(hidden_channels)
        self.n_layers = int(n_layers)
        self.residual_weight = float(residual_weight)
        self.split_variant = str(split_variant).lower()
        self.wavelet_levels = max(0, min(2, int(wavelet_levels)))
        self.wavelet_type = str(wavelet_type).lower()

        self.lifting = nn.Conv2d(self.in_channels, self.hidden_channels, kernel_size=1)
        self.blocks = nn.ModuleList(
            [
                SplitFNOBlock(
                    channels=self.hidden_channels,
                    n_modes=self.n_modes,
                    split_variant=self.split_variant,
                    step_scale=float(split_step_scale),
                    physical_kernel_size=int(physical_kernel_size),
                    dropout=float(dropout),
                )
                for _ in range(self.n_layers)
            ]
        )
        self.wavelet_correction_index = max(0, self.n_layers // 2 - 1)
        self.wavelet_correction = WaveletLatentCorrection(
            channels=self.hidden_channels,
            levels=self.wavelet_levels,
            residual_init=float(wavelet_residual_init),
            wavelet=self.wavelet_type,
            dropout=float(dropout),
        )
        self.projection = nn.Sequential(
            nn.Conv2d(self.hidden_channels, self.hidden_channels, kernel_size=1),
            nn.GELU(),
            nn.Conv2d(self.hidden_channels, self.out_channels, kernel_size=1),
        )

    @property
    def spectral_calls_per_forward(self) -> int:
        return sum(block.spectral_calls_per_forward for block in self.blocks)

    @property
    def compute_overhead_factor(self) -> float:
        """Approximate spectral-call overhead relative to Lie-Trotter ordering."""

        baseline = max(1, self.n_layers)
        return float(self.spectral_calls_per_forward) / float(baseline)

    def experiment_metadata(self):
        return {
            "experiment": "135_latent_wavelet_operator_correction_fno",
            "split_variant": self.split_variant,
            "spectral_calls_per_forward": self.spectral_calls_per_forward,
            "compute_overhead_factor_vs_lie_trotter": self.compute_overhead_factor,
            "ordering": "spectral-physical-spectral"
            if self.blocks and self.blocks[0].is_strang
            else "spectral-then-physical",
            "wavelet": self.wavelet_type,
            "wavelet_levels": self.wavelet_levels,
            "wavelet_correction_index": self.wavelet_correction_index,
            "wavelet_residual_init": float(self.wavelet_correction.residual_scale.detach().cpu()),
            "loss": "relative_l2_plus_0.05_finite_difference_gradient_relative_l2",
        }

    def forward(self, x):
        y = self.lifting(x)
        for index, block in enumerate(self.blocks):
            y = block(y)
            if index == self.wavelet_correction_index:
                y = self.wavelet_correction(y)
        return self.projection(y)


class LieTrotterSplitFNO(HighFrequencyResidualFNO):
    """Convenience wrapper for spectral-then-physical Lie-Trotter ordering."""

    def __init__(self, *args, **kwargs):
        kwargs["split_variant"] = "lie_trotter"
        super().__init__(*args, **kwargs)


class StrangSplitFNO(HighFrequencyResidualFNO):
    """Convenience wrapper for half-spectral/full-physical/half-spectral ordering."""

    def __init__(self, *args, **kwargs):
        kwargs["split_variant"] = "strang"
        super().__init__(*args, **kwargs)


def _relative_l2_loss(pred: torch.Tensor, target: torch.Tensor, eps: float = 1.0e-8) -> torch.Tensor:
    """Mean per-sample relative L2 error over all non-batch dimensions."""

    if pred.shape != target.shape:
        raise ValueError(f"pred and target must have the same shape, got {pred.shape} and {target.shape}")
    if pred.ndim == 0:
        return torch.abs(pred - target) / (torch.abs(target) + float(eps))

    error = (pred - target).reshape(pred.shape[0], -1)
    reference = target.reshape(target.shape[0], -1)
    numerator = torch.linalg.vector_norm(error, ord=2, dim=1)
    denominator = torch.linalg.vector_norm(reference, ord=2, dim=1).clamp_min(float(eps))
    return torch.mean(numerator / denominator)


def _finite_difference_gradient_relative_l2(
    pred: torch.Tensor,
    target: torch.Tensor,
    eps: float = 1.0e-8,
) -> torch.Tensor:
    """Mean relative L2 error of first-order finite differences in y and x."""

    losses = []
    if pred.shape[-2] > 1:
        pred_dy = pred[..., 1:, :] - pred[..., :-1, :]
        target_dy = target[..., 1:, :] - target[..., :-1, :]
        losses.append(_relative_l2_loss(pred_dy, target_dy, eps=eps))
    if pred.shape[-1] > 1:
        pred_dx = pred[..., :, 1:] - pred[..., :, :-1]
        target_dx = target[..., :, 1:] - target[..., :, :-1]
        losses.append(_relative_l2_loss(pred_dx, target_dx, eps=eps))

    if not losses:
        return pred.new_tensor(0.0)
    return torch.stack(losses).mean()


def frequency_weighted_mse(pred, target, high_frequency_weight=0.05):
    """Relative L2 data loss plus a lightly weighted finite-difference gradient loss.

    The benchmark harness imports this historical helper name as the custom
    loss entry point.  The third argument is interpreted as the spatial-gradient
    loss weight, preserving call compatibility with earlier experiment variants.
    """

    gradient_weight = 0.05 if high_frequency_weight is None else float(high_frequency_weight)
    data_loss = _relative_l2_loss(pred, target)
    if gradient_weight <= 0.0:
        return data_loss
    gradient_loss = _finite_difference_gradient_relative_l2(pred, target)
    return data_loss + gradient_weight * gradient_loss


def build_model(config):
    return HighFrequencyResidualFNO(
        n_modes=tuple(config.get("n_modes", [16, 16])),
        in_channels=int(config.get("in_channels", 3)),
        out_channels=int(config.get("out_channels", 1)),
        hidden_channels=int(config.get("hidden_channels", 32)),
        n_layers=int(config.get("n_layers", 4)),
        residual_weight=float(config.get("residual_weight", 0.1)),
        split_variant=str(config.get("split_variant", "strang")),
        split_step_scale=float(config.get("split_step_scale", 0.5)),
        physical_kernel_size=int(config.get("physical_kernel_size", 3)),
        dropout=float(config.get("dropout", 0.0)),
        wavelet_levels=int(config.get("wavelet_levels", 1)),
        wavelet_residual_init=float(config.get("wavelet_residual_init", 0.05)),
        wavelet_type=str(config.get("wavelet_type", "haar")),
    )
