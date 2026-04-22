#!/usr/bin/env bash
# Benchmark script — implements the framework benchmark contract (§5.1)
#
# Output contract: print "METRIC key=value" lines to stdout, one per metric.
#   - METRIC lines must appear on stdout (not stderr)
#   - metric_name from instance.json MUST appear exactly once
#   - value must be a finite number (no NaN, no empty)
#   - exit 0 on success, non-zero on failure
#
# Example output:
#   METRIC your_metric=42.5
#   METRIC secondary_metric=128

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# TODO: compile your artifact
# g++ -O2 -o /tmp/your_binary "$SCRIPT_DIR/src/YOUR_ARTIFACT.ext"

# TODO: run your benchmark
# RESULT=$(your_binary your_input)

# TODO: parse result and emit METRIC lines
# echo "METRIC your_metric=${RESULT}"
echo "METRIC your_metric=0"
