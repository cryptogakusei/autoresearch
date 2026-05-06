#!/usr/bin/env python3
"""Download Navier-Stokes data via neuraloperator and convert to .pt format.

Run on the Lambda GPU box:
    pip install neuraloperator
    python3 prep_navier_stokes.py --out-dir /home/ubuntu/data/navier_stokes

Creates:
    ns_train_128.pt  — {"x": (N, 1, 128, 128), "y": (N, 1, 128, 128)}
    ns_test_128.pt   — {"x": (M, 1, 128, 128), "y": (M, 1, 128, 128)}

Input x = vorticity at time t, output y = vorticity at time t+1.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("/home/ubuntu/data/navier_stokes"))
    parser.add_argument("--resolution", type=int, default=128)
    parser.add_argument("--n-train", type=int, default=1000)
    parser.add_argument("--n-test", type=int, default=200)
    args = parser.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    res = args.resolution

    print(f"Downloading Navier-Stokes at resolution {res} via neuraloperator...")
    from neuralop.data.datasets import NavierStokesDataset

    ds = NavierStokesDataset(
        root_dir=str(out_dir / "_raw"),
        n_train=args.n_train,
        n_tests=[args.n_test],
        batch_size=16,
        test_batch_sizes=[16],
        train_resolution=res,
        test_resolutions=[res],
        download=True,
    )

    print("Extracting tensors from data loaders...")
    train_x, train_y = _collect_from_loader(ds.train_loader)
    test_x, test_y = _collect_from_loader(ds.test_loaders[res])

    print(f"Train: x={train_x.shape}, y={train_y.shape}")
    print(f"Test:  x={test_x.shape}, y={test_y.shape}")

    train_path = out_dir / f"ns_train_{res}.pt"
    test_path = out_dir / f"ns_test_{res}.pt"
    torch.save({"x": train_x, "y": train_y}, train_path)
    torch.save({"x": test_x, "y": test_y}, test_path)
    print(f"Saved {train_path} ({train_path.stat().st_size / 1e6:.1f} MB)")
    print(f"Saved {test_path} ({test_path.stat().st_size / 1e6:.1f} MB)")


def _collect_from_loader(loader):
    xs, ys = [], []
    for batch in loader:
        x = batch["x"] if isinstance(batch, dict) else batch[0]
        y = batch["y"] if isinstance(batch, dict) else batch[1]
        if x.dim() == 3:
            x = x.unsqueeze(1)
        if y.dim() == 3:
            y = y.unsqueeze(1)
        xs.append(x.cpu())
        ys.append(y.cpu())
    return torch.cat(xs, dim=0), torch.cat(ys, dim=0)


if __name__ == "__main__":
    main()
