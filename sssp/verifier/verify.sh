#!/usr/bin/env bash
# SSSP verifier — implements the framework verifier contract (§5.2)
#
# Input:  /tmp/sssp_distances_{source}.txt written by autoresearch.sh
# Output: verifier/verdict.json (this file's directory / verdict.json)
#
# Exit codes: 0 = verdict.json written (inspect status field); non-zero = unrecoverable setup error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTANCE_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_FILE="$SCRIPT_DIR/verdict.json"

SOURCES_FILE="$INSTANCE_DIR/data/cal.ss"
GRAPH_FILE="$INSTANCE_DIR/data/cal.gr"

if [[ ! -f "$SOURCES_FILE" ]]; then
    printf '{"status":"FAIL","detail":"cal.ss not found","failures":[]}\n' > "$OUTPUT_FILE"
    exit 0
fi

# Collect first 5 source IDs into a temp file
SOURCES_TXT="/tmp/sssp_verify_sources.txt"
python3 -c "
import sys
sources = []
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if line.startswith('s '):
            parts = line.split()
            if len(parts) >= 2:
                sources.append(parts[1])
        if len(sources) == 5:
            break
print('\n'.join(sources))
" "$SOURCES_FILE" > "$SOURCES_TXT"

if [[ ! -s "$SOURCES_TXT" ]]; then
    printf '{"status":"FAIL","detail":"no source IDs found in cal.ss","failures":[]}\n' > "$OUTPUT_FILE"
    exit 0
fi

# Assemble distances directory from files written by autoresearch.sh
DISTANCES_DIR="/tmp/sssp_verify_distances"
rm -rf "$DISTANCES_DIR"
mkdir -p "$DISTANCES_DIR"
while IFS= read -r src; do
    src_file="/tmp/sssp_distances_${src}.txt"
    [[ -f "$src_file" ]] && cp "$src_file" "$DISTANCES_DIR/distances_${src}.txt"
done < "$SOURCES_TXT"

# Compile reference binary if not cached
REF_BIN="/tmp/dijkstra_ref_verifier"
if [[ ! -x "$REF_BIN" ]]; then
    ref_src="$INSTANCE_DIR/src/dijkstra_ref.cpp"
    if [[ -f "$ref_src" ]]; then
        g++ -O2 -std=c++17 -o "$REF_BIN" "$ref_src" 2>/dev/null || REF_BIN=""
    else
        REF_BIN=""
    fi
fi

# Run verify.py via env-var interface
GRAPH_FILE="$GRAPH_FILE" \
SOURCES_FILE="$SOURCES_TXT" \
DISTANCES_DIR="$DISTANCES_DIR" \
OUTPUT_FILE="$OUTPUT_FILE" \
REF_BINARY="${REF_BIN:-}" \
python3 "$SCRIPT_DIR/verify.py"
