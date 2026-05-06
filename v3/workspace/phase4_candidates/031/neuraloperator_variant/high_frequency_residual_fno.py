"""Phase 2 U-shaped Fourier neural operator with composite local-structure loss.

The benchmark harness supplies dataset-specific ``in_channels`` and
``out_channels`` through ``build_model(config)``.  Experiment 031 keeps
the gated compact U-NO style encoder-decoder architecture at the same
capacity as the previous GPU target and changes the supervised objective
instead: ``frequency_weighted_mse`` now returns a composite relative loss
made from field, gradient, and Laplacian errors.  Gradients and
Laplacians are computed on the output grid with fixed finite-difference
convolution kernels and configurable replicate or periodic padding.
"""

from __future__ import annotations

import torch
from torch import nn
import torch.nn.functional as F

from neuralop.models import FNO as _NeuralOperatorFNO  # noqa: F401


class SpectralConv2d(nn.Module):
    """Two-dimensional spectral convolution retaining a fixed mode budget.

    The layer keeps separate complex weights for the positive and negative
    vertical Fourier bands and uses the real FFT representation along the
    horizontal axis.  The requested mode count is clipped dynamically in
    ``forward`` so the same block can be used safely at multiple resolutions.
    """

    def __init__(self, in_channels, out_channels, n_modes):
        super().__init__()
        self.in_channels = int(in_channels)
        self.out_channels = int(out_channels)
        self.n_modes = tuple(int(m) for m in n_modes)
        scale = 1.0 / max(1, self.in_channels * self.out_channels)
        weight_shape = (self.in_channels, self.out_channels, self.n_modes[0], self.n_modes[1])
        self.weight_pos = nn.Parameter(scale * torch.randn(*weight_shape, dtype=torch.cfloat))
        self.weight_neg = nn.Parameter(scale * torch.randn(*weight_shape, dtype=torch.cfloat))

    @staticmethod
    def _compl_mul2d(inputs, weights):
        return torch.einsum("bixy,ioxy->boxy", inputs, weights)

    def forward(self, x):
        batch_size, _, height, width = x.shape
        x_ft = torch.fft.rfft2(x, norm="ortho")
        width_ft = x_ft.shape[-1]
        out_ft = torch.zeros(
            batch_size,
            self.out_channels,
            height,
            width_ft,
            dtype=x_ft.dtype,
            device=x.device,
        )

        modes_y = min(self.n_modes[0], max(1, height // 2))
        modes_x = min(self.n_modes[1], width_ft)
        if modes_y > 0 and modes_x > 0:
            out_ft[:, :, :modes_y, :modes_x] = self._compl_mul2d(
                x_ft[:, :, :modes_y, :modes_x],
                self.weight_pos[:, :, :modes_y, :modes_x],
            )
            out_ft[:, :, -modes_y:, :modes_x] = self._compl_mul2d(
                x_ft[:, :, -modes_y:, :modes_x],
                self.weight_neg[:, :, :modes_y, :modes_x],
            )

        return torch.fft.irfft2(out_ft, s=(height, width), norm="ortho")


class SpectralResidualBlock(nn.Module):
    """Fourier operator block with residual spectral and pointwise MLP paths."""

    def __init__(self, channels, n_modes, mlp_ratio=2):
        super().__init__()
        channels = int(channels)
        mlp_channels = max(channels, int(channels * mlp_ratio))
        self.spectral = SpectralConv2d(channels, channels, n_modes)
        self.pointwise = nn.Conv2d(channels, channels, kernel_size=1)
        self.channel_mlp = nn.Sequential(
            nn.Conv2d(channels, mlp_channels, kernel_size=1),
            nn.GELU(),
            nn.Conv2d(mlp_channels, channels, kernel_size=1),
        )
        self.activation = nn.GELU()

    def forward(self, x):
        operator_update = self.spectral(x) + self.pointwise(x)
        x = self.activation(x + operator_update)
        mlp_update = self.channel_mlp(x)
        return self.activation(x + mlp_update)


class DownsampleStage(nn.Module):
    """Strided convolutional downsampling between encoder resolutions."""

    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.downsample = nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel_size=3, stride=2, padding=1),
            nn.GELU(),
        )

    def forward(self, x):
        return self.downsample(x)


class UpsampleStage(nn.Module):
    """Upsample, gate the encoder skip, fuse, and apply an operator block."""

    def __init__(self, in_channels, skip_channels, n_modes):
        super().__init__()
        self.reduce = nn.Conv2d(in_channels, skip_channels, kernel_size=1)
        self.skip_gate = nn.Sequential(
            nn.Conv2d(skip_channels * 2, skip_channels, kernel_size=1),
            nn.GELU(),
            nn.Conv2d(skip_channels, skip_channels, kernel_size=1),
            nn.Sigmoid(),
        )
        self.fuse = nn.Conv2d(skip_channels * 2, skip_channels, kernel_size=1)
        self.block = SpectralResidualBlock(skip_channels, n_modes)

    def forward(self, x, skip):
        x = F.interpolate(x, size=skip.shape[-2:], mode="bilinear", align_corners=False)
        x = self.reduce(x)
        gate = self.skip_gate(torch.cat([x, skip], dim=1))
        gated_skip = skip * gate
        x = torch.cat([x, gated_skip], dim=1)
        x = self.fuse(x)
        return self.block(x)


class HighFrequencyResidualFNO(nn.Module):
    """U-NO style encoder-decoder with gated long skip connections.

    The public class name is preserved for compatibility with the benchmark
    harness, while the implementation is a multi-resolution neural operator.
    Inputs are lifted to a wider channel space, processed by spectral
    residual blocks across encoder and bottleneck scales, and decoded through
    gated skip-connected upsampling stages.
    """

    def __init__(
        self,
        n_modes=(16, 16),
        in_channels=3,
        out_channels=1,
        hidden_channels=32,
        n_layers=3,
        residual_weight=0.0,
        min_modes=4,
        blocks_per_scale=1,
        max_channels=None,
    ):
        super().__init__()
        self.residual_weight = float(residual_weight)
        self.n_modes = tuple(int(m) for m in n_modes)
        self.min_modes = int(min_modes)

        num_scales = max(2, int(n_layers))
        blocks_per_scale = max(1, int(blocks_per_scale))
        hidden_channels = int(hidden_channels)
        if max_channels is None:
            max_channels = hidden_channels * 4
        max_channels = int(max_channels)
        channels = [min(max_channels, hidden_channels * (2**level)) for level in range(num_scales)]

        self.lift = nn.Sequential(
            nn.Conv2d(in_channels, channels[0], kernel_size=1),
            nn.GELU(),
        )

        self.encoder_blocks = nn.ModuleList(
            [self._make_blocks(channels[level], self._scale_modes(level), blocks_per_scale) for level in range(num_scales - 1)]
        )
        self.downsamplers = nn.ModuleList(
            [DownsampleStage(channels[level], channels[level + 1]) for level in range(num_scales - 1)]
        )
        self.bottleneck = self._make_blocks(channels[-1], self._scale_modes(num_scales - 1), blocks_per_scale)
        self.upsamplers = nn.ModuleList(
            [
                UpsampleStage(channels[level + 1], channels[level], self._scale_modes(level))
                for level in range(num_scales - 1)
            ]
        )

        self.projection = nn.Sequential(
            nn.Conv2d(channels[0], hidden_channels, kernel_size=1),
            nn.GELU(),
            nn.Conv2d(hidden_channels, out_channels, kernel_size=1),
        )
        self.local_residual = nn.Sequential(
            nn.Conv2d(in_channels, hidden_channels, kernel_size=3, padding=1),
            nn.GELU(),
            nn.Conv2d(hidden_channels, out_channels, kernel_size=3, padding=1),
        )

    def _scale_modes(self, level):
        divisor = 2**int(level)
        return tuple(max(1, max(self.min_modes, mode // divisor)) for mode in self.n_modes)

    @staticmethod
    def _make_blocks(channels, n_modes, blocks_per_scale):
        return nn.Sequential(*[SpectralResidualBlock(channels, n_modes) for _ in range(blocks_per_scale)])

    def forward(self, x):
        lifted_input = x
        x = self.lift(x)
        skips = []

        for encoder, downsample in zip(self.encoder_blocks, self.downsamplers):
            x = encoder(x)
            skips.append(x)
            x = downsample(x)

        x = self.bottleneck(x)

        for upsample, skip in zip(reversed(self.upsamplers), reversed(skips)):
            x = upsample(x, skip)

        output = self.projection(x)
        if self.residual_weight == 0.0:
            return output
        return output + self.residual_weight * self.local_residual(lifted_input)


def _relative_l2_error(pred, target, eps=1.0e-8):
    """Return batch-averaged relative L2 error over all non-batch axes."""

    pred_flat = pred.reshape(pred.shape[0], -1)
    target_flat = target.reshape(target.shape[0], -1)
    numerator = torch.linalg.vector_norm(pred_flat - target_flat, ord=2, dim=1)
    denominator = torch.linalg.vector_norm(target_flat, ord=2, dim=1).clamp_min(float(eps))
    return torch.mean(numerator / denominator)


def _normalise_padding_mode(derivative_padding):
    """Map user/config padding aliases to modes accepted by ``F.pad``."""

    if isinstance(derivative_padding, bool):
        return "circular" if derivative_padding else "replicate"
    mode = str(derivative_padding).strip().lower()
    if mode in {"periodic", "circular", "wrap"}:
        return "circular"
    if mode in {"replicate", "nearest", "edge", "nonperiodic", "non-periodic"}:
        return "replicate"
    return "replicate"


def _fixed_kernel_conv2d(field, kernel, derivative_padding="replicate"):
    """Apply the same fixed 3x3 finite-difference kernel to each channel."""

    channels = field.shape[1]
    padding_mode = _normalise_padding_mode(derivative_padding)
    padded = F.pad(field, (1, 1, 1, 1), mode=padding_mode)
    weight = field.new_tensor(kernel).view(1, 1, 3, 3).repeat(channels, 1, 1, 1)
    return F.conv2d(padded, weight, groups=channels)


def _finite_difference_gradients(field, derivative_padding="replicate"):
    """Compute centered finite-difference dx and dy on the output grid."""

    dx_kernel = (
        (0.0, 0.0, 0.0),
        (-0.5, 0.0, 0.5),
        (0.0, 0.0, 0.0),
    )
    dy_kernel = (
        (0.0, -0.5, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.5, 0.0),
    )
    dx = _fixed_kernel_conv2d(field, dx_kernel, derivative_padding=derivative_padding)
    dy = _fixed_kernel_conv2d(field, dy_kernel, derivative_padding=derivative_padding)
    return dx, dy


def _finite_difference_laplacian(field, derivative_padding="replicate"):
    """Compute the five-point finite-difference Laplacian on the output grid."""

    laplacian_kernel = (
        (0.0, 1.0, 0.0),
        (1.0, -4.0, 1.0),
        (0.0, 1.0, 0.0),
    )
    return _fixed_kernel_conv2d(field, laplacian_kernel, derivative_padding=derivative_padding)


def frequency_weighted_mse(
    pred,
    target,
    high_frequency_weight=0.0,
    gradient_weight=0.1,
    laplacian_weight=0.02,
    derivative_padding="replicate",
    eps=1.0e-8,
):
    """Composite Experiment 031 objective for supervised operator training.

    Despite the legacy function name expected by the benchmark harness, the
    base loss is relative L2 rather than MSE.  The returned objective is

        L_rel_l2 + gradient_weight * L_grad + laplacian_weight * L_lap,

    where ``L_grad`` compares centered finite-difference spatial gradients
    and ``L_lap`` compares five-point finite-difference Laplacians.  Padding
    is ``replicate`` for non-periodic data or ``circular``/``periodic`` for
    periodic data.  ``high_frequency_weight`` is retained as a backward
    compatible optional spectral penalty and should remain zero for this
    experiment.
    """

    field_loss = _relative_l2_error(pred, target, eps=eps)

    pred_dx, pred_dy = _finite_difference_gradients(pred, derivative_padding=derivative_padding)
    target_dx, target_dy = _finite_difference_gradients(target, derivative_padding=derivative_padding)
    pred_grad = torch.cat([pred_dx, pred_dy], dim=1)
    target_grad = torch.cat([target_dx, target_dy], dim=1)
    grad_loss = _relative_l2_error(pred_grad, target_grad, eps=eps)

    pred_lap = _finite_difference_laplacian(pred, derivative_padding=derivative_padding)
    target_lap = _finite_difference_laplacian(target, derivative_padding=derivative_padding)
    lap_loss = _relative_l2_error(pred_lap, target_lap, eps=eps)

    loss = field_loss + float(gradient_weight) * grad_loss + float(laplacian_weight) * lap_loss

    if high_frequency_weight > 0:
        pred_fft = torch.fft.rfft2(pred, norm="ortho")
        target_fft = torch.fft.rfft2(target, norm="ortho")
        height = pred.shape[-2]
        width = pred.shape[-1]
        width_half = pred_fft.shape[-1]
        fy = torch.fft.fftfreq(height, device=pred.device).abs().view(1, 1, height, 1)
        fx = torch.fft.rfftfreq(width, device=pred.device).abs().view(1, 1, 1, width_half)
        frequency_radius = fx + fy
        spectral_error = torch.abs(pred_fft - target_fft) ** 2
        spectral_penalty = torch.mean(spectral_error * frequency_radius)
        loss = loss + float(high_frequency_weight) * spectral_penalty.real

    return loss


def build_model(config):
    hidden_channels = int(config.get("hidden_channels", 32))
    return HighFrequencyResidualFNO(
        n_modes=tuple(config.get("n_modes", [16, 16])),
        in_channels=int(config.get("in_channels", 3)),
        out_channels=int(config.get("out_channels", 1)),
        hidden_channels=hidden_channels,
        n_layers=int(config.get("n_layers", 3)),
        residual_weight=float(config.get("residual_weight", 0.0)),
        min_modes=int(config.get("min_modes", 4)),
        blocks_per_scale=int(config.get("blocks_per_scale", 1)),
        max_channels=int(config.get("max_channels", hidden_channels * 4)),
    )

