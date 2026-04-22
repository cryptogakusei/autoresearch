#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# 1. Read params.json and export each param as an env var
# ---------------------------------------------------------------------------
PARAMS_FILE="$SCRIPT_DIR/params.json"

eval "$(python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    p = json.load(f)
mapping = {
    'k':           'SSSP_K',
    't':           'SSSP_T',
    'base_case_n': 'SSSP_BASE_CASE_N',
    'pivot_rule':  'SSSP_PIVOT_RULE',
    'pq_type':     'SSSP_PQ_TYPE',
}
for key, envvar in mapping.items():
    if key in p:
        print(f'export {envvar}={repr(str(p[key]))}')
" "$PARAMS_FILE")"

# ---------------------------------------------------------------------------
# 2. Compile
# ---------------------------------------------------------------------------
SRC="$SCRIPT_DIR/src/sssp.cpp"
BIN="/tmp/sssp_bin"

if ! g++ -O2 -std=c++17 -o "$BIN" "$SRC" 2>&1 >&2; then
    echo "Compilation failed." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Check data files
# ---------------------------------------------------------------------------
GRAPH="$SCRIPT_DIR/data/cal.gr"
SOURCES_FILE="$SCRIPT_DIR/data/cal.ss"

if [[ ! -f "$GRAPH" ]]; then
    echo "ERROR: data/cal.gr not found." >&2
    echo "Please download the DIMACS CAL road network from:" >&2
    echo "  http://users.diag.uniroma1.it/challenge9/download.shtml" >&2
    echo "Place the uncompressed .gr file at: $GRAPH" >&2
    exit 1
fi

if [[ ! -f "$SOURCES_FILE" ]]; then
    echo "ERROR: data/cal.ss not found." >&2
    echo "Please download the DIMACS CAL source file from:" >&2
    echo "  http://users.diag.uniroma1.it/challenge9/download.shtml" >&2
    echo "Place the .ss file at: $SOURCES_FILE" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Cache the first 5 source node IDs (fixed for reproducibility)
# ---------------------------------------------------------------------------
SOURCES=()
while IFS= read -r line; do
    [[ -n "$line" ]] && SOURCES+=("$line")
done < <(python3 -c "
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
for s in sources:
    print(s)
" "$SOURCES_FILE")

if [[ ${#SOURCES[@]} -lt 5 ]]; then
    echo "ERROR: data/cal.ss has fewer than 5 source lines." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 4 & 5. Run benchmark on each source, collect times and relaxations
# ---------------------------------------------------------------------------
declare -a TIMES_MS
TOTAL_RELAXATIONS=0

for SOURCE in "${SOURCES[@]}"; do
    OUTPUT=$("$BIN" "$GRAPH" "$SOURCE" 2>/dev/null)

    # Parse time_ms
    TIME_MS=$(printf '%s\n' "$OUTPUT" | python3 -c "
import sys, re
for line in sys.stdin:
    m = re.search(r'time_ms=([0-9]+(?:\.[0-9]+)?)', line)
    if m:
        print(m.group(1))
        break
else:
    print('0')
")
    TIMES_MS+=("$TIME_MS")

    # Parse relaxations
    RELAXATIONS=$(printf '%s\n' "$OUTPUT" | python3 -c "
import sys, re
for line in sys.stdin:
    m = re.search(r'relaxations=([0-9]+)', line)
    if m:
        print(m.group(1))
        break
else:
    print('0')
")
    TOTAL_RELAXATIONS=$(( TOTAL_RELAXATIONS + RELAXATIONS ))

    # Save distance lines for verifier
    printf '%s\n' "$OUTPUT" | grep '^d ' > "/tmp/sssp_distances_${SOURCE}.txt" || true
done

# ---------------------------------------------------------------------------
# 4. Compute median of the 5 runtimes
# ---------------------------------------------------------------------------
MEDIAN_MS=$(python3 - <<PYEOF "${TIMES_MS[@]}"
import sys, statistics
vals = [float(x) for x in sys.argv[1:]]
print(statistics.median(vals))
PYEOF
)

# ---------------------------------------------------------------------------
# 6. Memory: /usr/bin/time -v on Linux, else 0
# ---------------------------------------------------------------------------
PEAK_RSS_MB=0
if [[ "$(uname -s)" == "Linux" ]] && [[ -x /usr/bin/time ]]; then
    TIME_OUT=$( { /usr/bin/time -v "$BIN" "$GRAPH" "${SOURCES[0]}" 2>&1 >/dev/null; } 2>&1 || true )
    PEAK_RSS_MB=$(printf '%s\n' "$TIME_OUT" | python3 -c "
import sys, re
for line in sys.stdin:
    m = re.search(r'Maximum resident set size \(kbytes\):\s*([0-9]+)', line)
    if m:
        kb = int(m.group(1))
        print(round(kb / 1024, 2))
        break
else:
    print(0)
")
fi

# ---------------------------------------------------------------------------
# Output METRIC lines
# ---------------------------------------------------------------------------
echo "METRIC runtime_ms=${MEDIAN_MS}"
echo "METRIC relaxations=${TOTAL_RELAXATIONS}"
echo "METRIC memory_mb=${PEAK_RSS_MB}"
