from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import time
import traceback


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("experiment_id")
    parser.add_argument("--data-dir", type=Path, default=Path("/home/ubuntu/data/darcy"))
    parser.add_argument("--experiment-type", default="neuraloperator_fno_gpu")
    args = parser.parse_args(argv)

    artifact_dir = args.artifact_dir
    experiment_id = args.experiment_id
    data_dir = args.data_dir
    experiment_type = args.experiment_type
    root = Path.cwd()
    started = datetime.now(timezone.utc).isoformat()

    model_files = list(artifact_dir.rglob("high_frequency_residual_fno.py"))
    config_files = list(artifact_dir.rglob("smoke_config.json"))
    if not model_files or not config_files:
        print(json.dumps(_failure(experiment_type, experiment_id, started, "missing required artifact files")))
        return 0

    config = json.loads(config_files[0].read_text(encoding="utf-8"))

    try:
        result = _run_darcy_gpu(root, artifact_dir, experiment_type, experiment_id, started, model_files[0], config, data_dir)
    except Exception:
        print(json.dumps(_failure(experiment_type, experiment_id, started, traceback.format_exc(limit=5))))
        return 0

    print(json.dumps(result))
    return 0


def _run_darcy_gpu(
    root: Path,
    artifact_dir: Path,
    experiment_type: str,
    experiment_id: str,
    started: str,
    model_file: Path,
    config: dict,
    data_dir: Path,
) -> dict:
    neuraloperator_path = root / "external" / "neuraloperator"
    if neuraloperator_path.exists():
        sys.path.insert(0, str(neuraloperator_path))

    import torch
    from torch.utils.data import DataLoader, TensorDataset
    from neuralop.models import FNO

    device = "cuda" if torch.cuda.is_available() else "cpu"
    _log(f"device={device}, data_dir={data_dir}")

    module = _load_module(model_file)
    build_model = getattr(module, "build_model")
    frequency_weighted_mse = getattr(module, "frequency_weighted_mse")

    resolution = int(config.get("resolution", 128))
    n_train = int(config.get("train_samples", 1000))
    n_val = int(config.get("val_samples", 100))
    epochs = int(config.get("epochs", 50))
    batch_size = int(config.get("batch_size", 32))
    lr = float(config.get("learning_rate", 0.001))
    in_channels = int(config.get("in_channels", 3))

    torch.manual_seed(7)
    train_x, train_y, val_x, val_y = _load_darcy_data(
        data_dir, resolution, n_train, n_val, in_channels, torch, device,
    )
    _log(f"data loaded: train={train_x.shape}, val={val_x.shape}")

    n_modes = tuple(config.get("n_modes", [16, 16]))
    out_channels = int(config.get("out_channels", 1))
    hidden_channels = int(config.get("hidden_channels", 32))
    n_layers = int(config.get("n_layers", 4))

    baseline = FNO(
        n_modes=n_modes,
        in_channels=in_channels,
        out_channels=out_channels,
        hidden_channels=hidden_channels,
        n_layers=n_layers,
        lifting_channel_ratio=1,
        projection_channel_ratio=1,
        use_channel_mlp=False,
    ).to(device)

    variant = build_model(config).to(device)

    torch.cuda.reset_peak_memory_stats() if torch.cuda.is_available() else None

    _log("training baseline...")
    baseline_metrics = _train_and_eval(
        baseline, train_x, train_y, val_x, val_y, torch,
        loss_fn=lambda pred, y: torch.mean((pred - y) ** 2),
        lr=lr, epochs=epochs, batch_size=batch_size, device=device,
    )
    _log(f"baseline done: rel_l2={baseline_metrics['relative_l2']:.6f}")

    _log("training variant...")
    variant_metrics = _train_and_eval(
        variant, train_x, train_y, val_x, val_y, torch,
        loss_fn=lambda pred, y: frequency_weighted_mse(
            pred, y, float(config.get("frequency_loss_weight", 0.25)),
        ),
        lr=lr, epochs=epochs, batch_size=batch_size, device=device,
    )
    _log(f"variant done: rel_l2={variant_metrics['relative_l2']:.6f}")

    peak_memory_gb = 0.0
    if torch.cuda.is_available():
        peak_memory_gb = torch.cuda.max_memory_allocated() / 1e9

    total_train_time = baseline_metrics["train_time_sec"] + variant_metrics["train_time_sec"]
    size_label = f"darcy_{resolution}"

    return {
        "type": experiment_type,
        "artifactId": experiment_id,
        "passed": True,
        "metrics": {
            size_label: {
                "relative_l2": variant_metrics["relative_l2"],
                "baseline_relative_l2": baseline_metrics["relative_l2"],
                "spectral_hf_error": variant_metrics["spectral_hf_error"],
                "baseline_spectral_hf_error": baseline_metrics["spectral_hf_error"],
                "train_time_sec": round(total_train_time, 2),
                "peak_memory_gb": round(peak_memory_gb, 2),
                "valid": True,
            }
        },
        "validation": {
            "passed": True,
            "failedConstraints": [],
            "validatorVersion": "neuraloperator-fno-darcy-gpu-v1",
        },
        "metadata": {
            "benchmarkVersion": "neuraloperator-fno-darcy-gpu-v1",
            "mode": f"darcy_{resolution}_gpu",
            "device": device,
            "resolution": resolution,
            "epochs": epochs,
            "n_train": n_train,
            "startedAt": started,
            "completedAt": datetime.now(timezone.utc).isoformat(),
        },
    }


def _load_darcy_data(data_dir, resolution, n_train, n_val, in_channels, torch, device):
    train_path = data_dir / f"darcy_train_{resolution}.pt"
    test_path = data_dir / f"darcy_test_{resolution}.pt"

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

    return {
        "relative_l2": round(rel_l2, 6),
        "spectral_hf_error": round(hf, 6),
        "train_time_sec": round(elapsed, 2),
    }


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


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location("hf_residual_fno_artifact", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _failure(experiment_type: str, experiment_id: str, started: str, reason: str) -> dict:
    return {
        "type": experiment_type,
        "artifactId": experiment_id,
        "passed": False,
        "metrics": {
            "darcy_128": {
                "relative_l2": 999.0,
                "spectral_hf_error": 999.0,
                "train_time_sec": 0.0,
                "peak_memory_gb": 0.0,
                "valid": False,
            }
        },
        "validation": {
            "passed": False,
            "failedConstraints": [{"reason": reason}],
            "validatorVersion": "neuraloperator-fno-darcy-gpu-v1",
        },
        "metadata": {
            "benchmarkVersion": "neuraloperator-fno-darcy-gpu-v1",
            "startedAt": started,
            "completedAt": datetime.now(timezone.utc).isoformat(),
        },
    }


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
