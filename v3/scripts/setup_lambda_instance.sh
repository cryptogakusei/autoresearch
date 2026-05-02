#!/usr/bin/env bash
set -euo pipefail

HOST="${1:?Usage: setup_lambda_instance.sh <host> [key_path] [user]}"
KEY="${2:-gigachad.pem}"
USER="${3:-ubuntu}"
REMOTE_ROOT="/home/${USER}/autoresearch"
DATA_DIR="/home/${USER}/data/darcy"

SSH_CMD="ssh -i ${KEY} -o StrictHostKeyChecking=accept-new ${USER}@${HOST}"

echo "=== Setting up Lambda instance ${HOST} ==="

echo "--- Creating directories ---"
$SSH_CMD "mkdir -p ${REMOTE_ROOT}/benchmarks ${REMOTE_ROOT}/external ${REMOTE_ROOT}/workspace/baselines ${REMOTE_ROOT}/.autoresearch/artifacts ${DATA_DIR}"

echo "--- Syncing neuraloperator repo ---"
rsync -az --delete -e "ssh -i ${KEY}" \
    external/neuraloperator/ ${USER}@${HOST}:${REMOTE_ROOT}/external/neuraloperator/

echo "--- Syncing benchmark scripts ---"
rsync -az -e "ssh -i ${KEY}" \
    benchmarks/ ${USER}@${HOST}:${REMOTE_ROOT}/benchmarks/

echo "--- Syncing workspace baselines ---"
rsync -az -e "ssh -i ${KEY}" \
    workspace/baselines/ ${USER}@${HOST}:${REMOTE_ROOT}/workspace/baselines/

echo "--- Installing Python dependencies ---"
$SSH_CMD "pip install -q neuraloperator 2>&1 | tail -3"

echo "--- Downloading Darcy flow data (resolution 128) ---"
$SSH_CMD "python3 -c \"
import sys
sys.path.insert(0, '${REMOTE_ROOT}/external/neuraloperator')
from neuralop.data.datasets.darcy import DarcyDataset
DarcyDataset(root_dir='${DATA_DIR}', n_train=1000, n_tests=[100], batch_size=32,
             test_batch_sizes=[32], train_resolution=128, test_resolutions=[128], download=True)
print('Darcy 128 data downloaded successfully')
\""

echo "--- Verifying GPU ---"
$SSH_CMD "python3 -c 'import torch; print(f\"GPU: {torch.cuda.get_device_name(0)}, VRAM: {torch.cuda.get_device_properties(0).total_mem / 1e9:.1f} GB\")'"

echo "--- Listing data files ---"
$SSH_CMD "ls -lh ${DATA_DIR}/"

echo "=== Setup complete ==="
