from __future__ import annotations

import json
from pathlib import Path
import sys
from datetime import UTC, datetime


def main(argv: list[str]) -> int:
    artifact_dir = Path(argv[0])
    experiment_id = argv[1]
    started = datetime.now(UTC).isoformat()
    files = list(artifact_dir.rglob("solution.txt"))
    if not files:
        result = {
            "type": "example",
            "artifactId": experiment_id,
            "passed": False,
            "metrics": {"main": {"score": 0, "length": 0, "valid": False}},
            "validation": {
                "passed": False,
                "failedConstraints": [{"reason": "missing solution.txt"}],
                "validatorVersion": "example-1",
            },
            "metadata": {
                "benchmarkVersion": "example-1",
                "startedAt": started,
                "completedAt": datetime.now(UTC).isoformat(),
            },
        }
        print(json.dumps(result))
        return 0

    text = files[0].read_text(encoding="utf-8")
    valid = "INVALID" not in text
    score = len([line for line in text.splitlines() if line.strip()])
    result = {
        "type": "example",
        "artifactId": experiment_id,
        "passed": valid,
        "metrics": {
            "main": {
                "score": score,
                "length": len(text),
                "valid": valid,
            }
        },
        "validation": {
            "passed": valid,
            "failedConstraints": [] if valid else [{"reason": "artifact contains INVALID"}],
            "validatorVersion": "example-1",
        },
        "metadata": {
            "benchmarkVersion": "example-1",
            "startedAt": started,
            "completedAt": datetime.now(UTC).isoformat(),
        },
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

