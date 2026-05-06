from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import inspect
import json
import math
from pathlib import Path
import sys
import time
import traceback


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("experiment_id")
    parser.add_argument("--data-dir", type=Path, default=Path("/home/ubuntu/data"))
    parser.add_argument("--datasets", default="darcy_128", help="Comma-separated dataset names")
    parser.add_argument("--experiment-type", default="neuraloperator_fno_gpu")
    args = parser.parse_args(argv)

    artifact_dir = args.artifact_dir
    experiment_id = args.experiment_id
    data_dir = args.data_dir
    experiment_type = args.experiment_type
    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    root = Path.cwd()
    started = datetime.now(timezone.utc).isoformat()

    model_files = list(artifact_dir.rglob("high_frequency_residual_fno.py"))
    config_files = list(artifact_dir.rglob("smoke_config.json"))
    if not model_files or not config_files:
        print(json.dumps(_failure(experiment_type, experiment_id, started, datasets, "missing required artifact files")))
        return 0

    config = json.loads(config_files[0].read_text(encoding="utf-8"))

    try:
        result = _run_multi_pde(
            root, artifact_dir, experiment_type, experiment_id,
            started, model_files[0], config, data_dir, datasets,
        )
    except Exception:
        print(json.dumps(_failure(experiment_type, experiment_id, started, datasets, traceback.format_exc(limit=5))))
        return 0

    print(json.dumps(result))
    return 0


def _run_multi_pde(
    root: Path,
    artifact_dir: Path,
    experiment_type: str,
    experiment_id: str,
    started: str,
    model_file: Path,
    config: dict,
    data_dir: Path,
    datasets: list[str],
) -> dict:
    neuraloperator_path = root / "external" / "neuraloperator"
    if neuraloperator_path.exists():
        sys.path.insert(0, str(neuraloperator_path))

    import torch
    from neuralop.models import FNO

    device = "cuda" if torch.cuda.is_available() else "cpu"
    _log(f"device={device}, data_dir={data_dir}, datasets={datasets}")

    module = _load_module(model_file)
    build_model = getattr(module, "build_model")
    frequency_weighted_mse = getattr(module, "frequency_weighted_mse")

    shared = config.get("shared", {})
    dataset_configs = config.get("datasets", {})

    all_metrics: dict[str, dict] = {}
    all_valid = True

    torch.cuda.reset_peak_memory_stats() if torch.cuda.is_available() else None

    for ds_name in datasets:
        _log(f"\n{'='*40} {ds_name} {'='*40}")

        spec = _get_dataset_spec(ds_name)
        in_channels = spec["in_channels"]
        out_channels = spec["out_channels"]
        loader = globals()[spec["loader"]]

        ds_config = {**shared, **dataset_configs.get(ds_name, {})}
        if not ds_config:
            ds_config = {k: v for k, v in config.items() if k not in ("shared", "datasets")}
        ds_config["in_channels"] = in_channels
        ds_config["out_channels"] = out_channels

        resolution = int(ds_config.get("resolution", 128))
        n_train = int(ds_config.get("train_samples", 1000))
        n_val = int(ds_config.get("val_samples", 100))
        epochs = int(ds_config.get("epochs", 50))
        batch_size = int(ds_config.get("batch_size", 32))
        lr = float(ds_config.get("learning_rate", 0.001))

        torch.manual_seed(7)

        train_x, train_y, val_x, val_y = loader(
            data_dir, resolution, n_train, n_val, in_channels, torch, device,
        )
        _log(f"data loaded: train={train_x.shape}, val={val_x.shape}")

        n_modes = tuple(ds_config.get("n_modes", [16, 16]))
        hidden_channels = int(ds_config.get("hidden_channels", 32))
        n_layers = int(ds_config.get("n_layers", 4))
        baseline_n_modes = tuple(ds_config.get("baseline_n_modes", n_modes))
        baseline_hidden_channels = int(
            ds_config.get("baseline_hidden_channels", hidden_channels)
        )
        baseline_n_layers = int(ds_config.get("baseline_n_layers", n_layers))

        baseline = FNO(
            n_modes=baseline_n_modes,
            in_channels=in_channels,
            out_channels=out_channels,
            hidden_channels=baseline_hidden_channels,
            n_layers=baseline_n_layers,
            lifting_channel_ratio=1,
            projection_channel_ratio=1,
            use_channel_mlp=False,
        ).to(device)

        variant = build_model(ds_config).to(device)

        baseline_params = sum(p.numel() for p in baseline.parameters())
        variant_params = sum(p.numel() for p in variant.parameters())

        _log(f"baseline params={baseline_params:,}, variant params={variant_params:,}")

        _log(f"training baseline on {ds_name}...")
        baseline_metrics = _train_and_eval(
            baseline, train_x, train_y, val_x, val_y, torch,
            loss_fn=lambda pred, y: torch.mean((pred - y) ** 2),
            lr=lr, epochs=epochs, batch_size=batch_size, device=device,
        )
        _log(f"baseline done: rel_l2={baseline_metrics['relative_l2']:.6f}, h1={baseline_metrics['h1_error']:.6f}")

        _log(f"training variant on {ds_name}...")
        freq_weight = float(ds_config.get("frequency_loss_weight", 0.25))
        variant_metrics = _train_and_eval(
            variant, train_x, train_y, val_x, val_y, torch,
            loss_fn=lambda pred, y, cfg=ds_config: _call_artifact_loss(
                frequency_weighted_mse, pred, y, cfg
            ),
            lr=lr, epochs=epochs, batch_size=batch_size, device=device,
        )
        _log(f"variant done: rel_l2={variant_metrics['relative_l2']:.6f}, h1={variant_metrics['h1_error']:.6f}")

        total_train_time = baseline_metrics["train_time_sec"] + variant_metrics["train_time_sec"]

        all_metrics[ds_name] = {
            "relative_l2": variant_metrics["relative_l2"],
            "baseline_relative_l2": baseline_metrics["relative_l2"],
            "spectral_hf_error": variant_metrics["spectral_hf_error"],
            "baseline_spectral_hf_error": baseline_metrics["spectral_hf_error"],
            "h1_error": variant_metrics["h1_error"],
            "baseline_h1_error": baseline_metrics["h1_error"],
            "train_time_sec": round(total_train_time, 2),
            "param_count": variant_params,
            "baseline_param_count": baseline_params,
            "valid": True,
        }

    peak_memory_gb = 0.0
    if torch.cuda.is_available():
        peak_memory_gb = torch.cuda.max_memory_allocated() / 1e9

    for ds_name in all_metrics:
        all_metrics[ds_name]["peak_memory_gb"] = round(peak_memory_gb, 2)

    passed = all(m.get("valid", False) for m in all_metrics.values())

    return {
        "type": experiment_type,
        "artifactId": experiment_id,
        "passed": passed,
        "metrics": all_metrics,
        "validation": {
            "passed": passed,
            "failedConstraints": [],
            "validatorVersion": "neuraloperator-fno-multi-pde-gpu-v1",
        },
        "metadata": {
            "benchmarkVersion": "neuraloperator-fno-multi-pde-gpu-v1",
            "mode": "multi_pde_gpu",
            "device": device,
            "datasets": datasets,
            "startedAt": started,
            "completedAt": datetime.now(timezone.utc).isoformat(),
        },
    }


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

DATASET_SPEC: dict[str, dict] = {
    "darcy_128": {
        "loader": "_load_darcy_data",
        "in_channels": 3,
        "out_channels": 1,
    },
    "navier_stokes_128": {
        "loader": "_load_navier_stokes_data",
        "in_channels": 3,
        "out_channels": 1,
    },
}


def _get_dataset_spec(ds_name: str) -> dict:
    spec = DATASET_SPEC.get(ds_name)
    if spec is None:
        raise ValueError(f"Unknown dataset: {ds_name}. Supported: {list(DATASET_SPEC)}")
    return spec


def _load_darcy_data(data_dir, resolution, n_train, n_val, in_channels, torch, device):
    darcy_dir = data_dir / "darcy"
    train_path = darcy_dir / f"darcy_train_{resolution}.pt"
    test_path = darcy_dir / f"darcy_test_{resolution}.pt"

    if not train_path.exists():
        raise FileNotFoundError(f"Darcy training data not found at {train_path}")
    if not test_path.exists():
        raise FileNotFoundError(f"Darcy test data not found at {test_path}")

    train_data = torch.load(train_path, map_location=device, weights_only=True)
    test_data = torch.load(test_path, map_location=device, weights_only=True)

    x_train = train_data["x"][:n_train].float()
    y_train = train_data["y"][:n_train].float()
    x_val = test_data["x"][:n_val].float()
    y_val = test_data["y"][:n_val].float()

    if x_train.dim() == 3:
        x_train = x_train.unsqueeze(1)
        y_train = y_train.unsqueeze(1)
        x_val = x_val.unsqueeze(1)
        y_val = y_val.unsqueeze(1)

    x_train = _add_grid_channels(x_train, in_channels, torch, device)
    x_val = _add_grid_channels(x_val, in_channels, torch, device)

    return x_train, y_train, x_val, y_val


def _load_navier_stokes_data(data_dir, resolution, n_train, n_val, in_channels, torch, device):
    ns_dir = data_dir / "navier_stokes"
    train_path = ns_dir / f"ns_train_{resolution}.pt"
    test_path = ns_dir / f"ns_test_{resolution}.pt"

    if not train_path.exists():
        raise FileNotFoundError(
            f"Navier-Stokes training data not found at {train_path}. "
            f"Run data_prep/prep_navier_stokes.py on the GPU box first."
        )
    if not test_path.exists():
        raise FileNotFoundError(f"Navier-Stokes test data not found at {test_path}")

    train_data = torch.load(train_path, map_location=device, weights_only=True)
    test_data = torch.load(test_path, map_location=device, weights_only=True)

    x_train = train_data["x"][:n_train].float()
    y_train = train_data["y"][:n_train].float()
    x_val = test_data["x"][:n_val].float()
    y_val = test_data["y"][:n_val].float()

    if x_train.dim() == 3:
        x_train = x_train.unsqueeze(1)
        y_train = y_train.unsqueeze(1)
        x_val = x_val.unsqueeze(1)
        y_val = y_val.unsqueeze(1)

    x_train = _add_grid_channels(x_train, in_channels, torch, device)
    x_val = _add_grid_channels(x_val, in_channels, torch, device)

    return x_train, y_train, x_val, y_val


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _add_grid_channels(x, target_channels, torch, device):
    current_channels = x.shape[1]
    if current_channels >= target_channels:
        return x[:, :target_channels]

    resolution = x.shape[-1]
    grid = torch.linspace(0, 1, resolution, device=device)
    yy, xx = torch.meshgrid(grid, grid, indexing="ij")
    coords = torch.stack([xx, yy], dim=0).unsqueeze(0).expand(x.shape[0], -1, -1, -1)

    needed = target_channels - current_channels
    x = torch.cat([x, coords[:, :needed]], dim=1)
    return x


def _train_and_eval(model, train_x, train_y, val_x, val_y, torch,
                    loss_fn, lr, epochs, batch_size, device):
    from torch.utils.data import DataLoader, TensorDataset

    train_ds = TensorDataset(train_x, train_y)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    start = time.perf_counter()
    model.train()
    for epoch in range(epochs):
        for xb, yb in train_loader:
            optimizer.zero_grad(set_to_none=True)
            pred = model(xb)
            loss = loss_fn(pred, yb)
            loss.backward()
            optimizer.step()
        scheduler.step()
        if (epoch + 1) % 10 == 0:
            _log(f"  epoch {epoch+1}/{epochs} loss={loss.item():.6f}")
    elapsed = time.perf_counter() - start

    model.eval()
    with torch.no_grad():
        pred = model(val_x)
        rel_l2 = float((torch.linalg.vector_norm(pred - val_y) / torch.linalg.vector_norm(val_y)).cpu())
        hf = float(_spectral_high_frequency_error(pred, val_y, torch).cpu())
        h1 = float(_h1_sobolev_error(pred, val_y, torch).cpu())

    return {
        "relative_l2": round(rel_l2, 6),
        "spectral_hf_error": round(hf, 6),
        "h1_error": round(h1, 6),
        "train_time_sec": round(elapsed, 2),
    }


def _call_artifact_loss(loss_fn, pred, target, config):
    signature = inspect.signature(loss_fn)
    accepted = set(signature.parameters)
    kwargs = {}
    loss_config = {
        "high_frequency_weight": float(config.get("frequency_loss_weight", 0.25)),
        "gradient_weight": float(config.get("gradient_loss_weight", 0.1)),
        "laplacian_weight": float(config.get("laplacian_loss_weight", 0.02)),
        "derivative_padding": config.get("derivative_padding", "replicate"),
    }
    for key, value in loss_config.items():
        if key in accepted:
            kwargs[key] = value

    if kwargs:
        return loss_fn(pred, target, **kwargs)
    return loss_fn(pred, target, loss_config["high_frequency_weight"])


def _spectral_high_frequency_error(pred, target, torch):
    pred_fft = torch.fft.rfft2(pred, norm="ortho")
    target_fft = torch.fft.rfft2(target, norm="ortho")
    height = pred.shape[-2]
    width_half = pred_fft.shape[-1]
    fy = torch.fft.fftfreq(height, device=pred.device).abs().view(1, 1, height, 1)
    fx = torch.fft.rfftfreq((width_half - 1) * 2, device=pred.device).abs().view(
        1, 1, 1, width_half
    )
    mask = (fx + fy) >= 0.25
    numerator = torch.linalg.vector_norm((pred_fft - target_fft) * mask)
    denominator = torch.linalg.vector_norm(target_fft * mask).clamp_min(1e-8)
    return numerator / denominator


def _h1_sobolev_error(pred, target, torch):
    diff = pred - target

    l2_num_sq = torch.linalg.vector_norm(diff) ** 2
    l2_den_sq = torch.linalg.vector_norm(target) ** 2

    dx_diff = diff[:, :, 1:, :] - diff[:, :, :-1, :]
    dx_target = target[:, :, 1:, :] - target[:, :, :-1, :]
    dy_diff = diff[:, :, :, 1:] - diff[:, :, :, :-1]
    dy_target = target[:, :, :, 1:] - target[:, :, :, :-1]

    grad_num_sq = torch.linalg.vector_norm(dx_diff) ** 2 + torch.linalg.vector_norm(dy_diff) ** 2
    grad_den_sq = torch.linalg.vector_norm(dx_target) ** 2 + torch.linalg.vector_norm(dy_target) ** 2

    numerator = l2_num_sq + grad_num_sq
    denominator = (l2_den_sq + grad_den_sq).clamp_min(1e-8)
    return torch.sqrt(numerator / denominator)


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location("hf_residual_fno_artifact", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _failure(experiment_type: str, experiment_id: str, started: str, datasets: list[str], reason: str) -> dict:
    metrics = {}
    for ds in datasets:
        metrics[ds] = {
            "relative_l2": 999.0,
            "spectral_hf_error": 999.0,
            "h1_error": 999.0,
            "train_time_sec": 0.0,
            "peak_memory_gb": 0.0,
            "param_count": 0,
            "valid": False,
        }
    return {
        "type": experiment_type,
        "artifactId": experiment_id,
        "passed": False,
        "metrics": metrics,
        "validation": {
            "passed": False,
            "failedConstraints": [{"reason": reason}],
            "validatorVersion": "neuraloperator-fno-multi-pde-gpu-v1",
        },
        "metadata": {
            "benchmarkVersion": "neuraloperator-fno-multi-pde-gpu-v1",
            "startedAt": started,
            "completedAt": datetime.now(timezone.utc).isoformat(),
        },
    }


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
