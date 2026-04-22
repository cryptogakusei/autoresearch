#!/usr/bin/env bash
# Verifier script — implements the framework verifier contract (§5.2)
#
# Output contract:
#   - Write verdict.json to the path specified by VERDICT_OUTPUT_PATH
#     (which is instance_dir/verifier/verdict.json by default)
#   - verdict.json schema:
#       {
#         "status": "PASS" | "FAIL",
#         "detail": "human-readable explanation",
#         "failures": ["list of specific failure descriptions"]
#       }
#   - Exit 0 (even on FAIL verdict) unless you cannot produce a verdict at all.
#     The framework reads the status field, not the exit code.
#
# The framework deletes verdict.json before calling this script.
# If this script exits without writing verdict.json → "verifier produced no output" FAIL.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_FILE="$SCRIPT_DIR/verdict.json"

# TODO: implement your correctness check
# Compare your artifact's output against a reference implementation.
# Write the result to $OUTPUT_FILE in the schema above.

# Placeholder: always PASS
cat > "$OUTPUT_FILE" << 'EOF'
{
  "status": "PASS",
  "detail": "placeholder verifier — replace with real correctness check",
  "failures": []
}
EOF
