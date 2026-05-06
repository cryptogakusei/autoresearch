from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


class PreflightError(RuntimeError):
    pass


def run_neuraloperator_fno_gpu_preflight(root: Path, workspace: Path) -> dict[str, Any]:
    model_file = workspace / "neuraloperator_variant" / "high_frequency_residual_fno.py"
    config_file = workspace / "benchmarks" / "smoke_config.json"
    if not model_file.exists():
        raise PreflightError(f"missing model artifact: {model_file.relative_to(root)}")
    if not config_file.exists():
        raise PreflightError(f"missing config artifact: {config_file.relative_to(root)}")

    external = root / "external" / "neuraloperator"
    if external.exists():
        sys.path.insert(0, str(external))

    try:
        import torch
    except Exception as exc:
        raise PreflightError(f"preflight could not import torch: {exc}") from exc

    module = _load_module(model_file)
    build_model = getattr(module, "build_model", None)
    loss_fn = getattr(module, "frequency_weighted_mse", None)
    if build_model is None or loss_fn is None:
        raise PreflightError("artifact must define build_model(config) and frequency_weighted_mse")

    config = json.loads(config_file.read_text(encoding="utf-8"))
    shared = config.get("shared", {})
    datasets = config.get("datasets", {})
    if not isinstance(datasets, dict) or not datasets:
        datasets = {"default": {}}

    results: dict[str, Any] = {}
    for dataset_name, dataset_config in datasets.items():
        if not isinstance(dataset_config, dict):
            raise PreflightError(f"dataset config for {dataset_name} must be an object")
        merged = {**shared, **dataset_config}
        merged.setdefault("dataset", dataset_name)
        result = _preflight_one_dataset(build_model, loss_fn, merged, torch, dataset_name)
        results[dataset_name] = result

    return {"passed": True, "datasets": results}


def _preflight_one_dataset(build_model, loss_fn, config: dict[str, Any], torch, dataset_name: str):
    height = width = int(config.get("preflight_resolution", 16))
    batch = int(config.get("preflight_batch_size", 2))
    in_channels = int(config.get("in_channels", 3))
    out_channels = int(config.get("out_channels", 1))

    try:
        model = build_model(config)
    except Exception as exc:
        raise PreflightError(f"{dataset_name}: build_model failed: {exc}") from exc
    param_count = sum(p.numel() for p in model.parameters())
    if param_count <= 0:
        raise PreflightError(f"{dataset_name}: model has no trainable parameters")

    model.train()
    x = torch.randn(batch, in_channels, height, width)
    target = torch.randn(batch, out_channels, height, width)
    try:
        pred = model(x)
    except Exception as exc:
        raise PreflightError(f"{dataset_name}: forward failed: {exc}") from exc

    expected_shape = tuple(target.shape)
    actual_shape = tuple(pred.shape)
    if actual_shape != expected_shape:
        raise PreflightError(
            f"{dataset_name}: expected output shape {expected_shape}, got {actual_shape}"
        )

    try:
        loss = loss_fn(pred, target, float(config.get("frequency_loss_weight", 0.0)))
    except Exception as exc:
        raise PreflightError(f"{dataset_name}: loss failed: {exc}") from exc
    if not torch.is_tensor(loss):
        raise PreflightError(f"{dataset_name}: loss function returned non-tensor {type(loss).__name__}")
    if not torch.isfinite(loss.detach()).all():
        raise PreflightError(f"{dataset_name}: loss is not finite")

    try:
        loss.backward()
    except Exception as exc:
        raise PreflightError(f"{dataset_name}: backward failed: {exc}") from exc

    return {
        "outputShape": list(actual_shape),
        "paramCount": int(param_count),
        "loss": float(loss.detach().cpu()),
    }


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location("autoresearch_preflight_artifact", path)
    if spec is None or spec.loader is None:
        raise PreflightError(f"could not load artifact module from {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise PreflightError(f"artifact import failed: {exc}") from exc
    return module
