#!/usr/bin/env python3
from __future__ import annotations
"""
SSSP Verifier — runs inside Docker container.

Compares experiment SSSP output against reference Dijkstra for every source
listed in /data/sources.txt and writes a verdict to /output/verdict.json.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Paths default to Docker mount points but can be overridden via env vars
# for running verify.py directly (no Docker) — used by control_plane.py fallback.
GRAPH_FILE   = Path(os.environ.get("GRAPH_FILE",    "/data/graph.gr"))
SOURCES_FILE = Path(os.environ.get("SOURCES_FILE",  "/data/sources.txt"))
DISTANCES_DIR= Path(os.environ.get("DISTANCES_DIR", "/data/distances"))
_output_file = os.environ.get("OUTPUT_FILE")
OUTPUT_DIR   = Path(_output_file).parent if _output_file else Path("/output")
_verdict_override = _output_file  # full path override for verdict.json, or None

# Reference binary: Docker image has it at /verifier/dijkstra_ref.
# Fallback: compile from src/ next to this script if not present.
_default_ref = "/verifier/dijkstra_ref"
REF_BINARY = os.environ.get("REF_BINARY", _default_ref)
CONSTRAINT = "all shortest-path distances match reference Dijkstra exactly"
MAX_MISMATCHES_PER_SOURCE = 5
REF_TIMEOUT_SECONDS = 120


def parse_distances(lines: list[str]) -> dict[int, int | None]:
    """Parse lines of the form 'd <node> <dist|INF>' into {node: dist_or_None}."""
    result: dict[int, int | None] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 3 or parts[0] != "d":
            continue
        node = int(parts[1])
        raw = parts[2]
        result[node] = None if raw == "INF" else int(raw)
    return result


def run_reference(source: int) -> tuple[dict[int, int | None] | None, str]:
    """
    Run the reference Dijkstra binary for a single source.
    Returns (distances_dict, error_message). On success error_message is "".
    """
    try:
        proc = subprocess.run(
            [REF_BINARY, str(GRAPH_FILE), str(source)],
            capture_output=True,
            text=True,
            timeout=REF_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {REF_TIMEOUT_SECONDS}s"
    except Exception as exc:
        return None, str(exc)

    if proc.returncode != 0:
        stderr_snippet = proc.stderr.strip()[:200]
        return None, f"exited with code {proc.returncode}: {stderr_snippet}"

    return parse_distances(proc.stdout.splitlines()), ""


def load_experiment(source: int) -> tuple[dict[int, int | None] | None, str]:
    """
    Load experiment distances for a single source from the mounted distances dir.
    Returns (distances_dict, error_message). On success error_message is "".
    """
    path = DISTANCES_DIR / f"distances_{source}.txt"
    if not path.exists():
        return None, f"Missing distances file for source {source}"
    try:
        return parse_distances(path.read_text().splitlines()), ""
    except Exception as exc:
        return None, f"Failed to read distances file for source {source}: {exc}"


def compare(
    source: int,
    ref: dict[int, int | None],
    exp: dict[int, int | None],
) -> list[str]:
    """
    Return a list of mismatch descriptions (capped at MAX_MISMATCHES_PER_SOURCE).
    """
    mismatches: list[str] = []
    all_nodes = ref.keys() | exp.keys()
    for node in sorted(all_nodes):
        if len(mismatches) >= MAX_MISMATCHES_PER_SOURCE:
            break
        r = ref.get(node)
        e = exp.get(node)
        if r != e:
            r_str = "INF" if r is None else str(r)
            e_str = "INF" if e is None else str(e)
            mismatches.append(f"node {node} ref={r_str} got={e_str}")
    return mismatches


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Read source list
    try:
        raw_sources = SOURCES_FILE.read_text().splitlines()
    except Exception as exc:
        verdict = {
            "status": "FAIL",
            "constraint": CONSTRAINT,
            "sources_checked": 0,
            "errors": [f"Could not read sources file: {exc}"],
        }
        _write_verdict(verdict)
        return

    sources = [int(s.strip()) for s in raw_sources if s.strip()]
    errors: list[str] = []

    for source in sources:
        # Run reference Dijkstra
        ref_distances, ref_err = run_reference(source)
        if ref_err:
            errors.append(f"Reference Dijkstra failed for source {source}: {ref_err}")
            continue

        # Load experiment output
        exp_distances, exp_err = load_experiment(source)
        if exp_err:
            errors.append(exp_err)
            continue

        # Compare
        mismatches = compare(source, ref_distances, exp_distances)
        if mismatches:
            first = mismatches[0]
            errors.append(
                f"Source {source}: {len(mismatches)} mismatches. First: {first}"
            )

    status = "PASS" if not errors else "FAIL"
    verdict = {
        "status": status,
        "constraint": CONSTRAINT,
        "sources_checked": len(sources),
        "errors": errors,
    }
    _write_verdict(verdict)

    if status == "PASS":
        print("PASS")
    else:
        print(f"FAIL: {errors[0]}")


def _write_verdict(verdict: dict) -> None:
    if _verdict_override:
        out_path = Path(_verdict_override)
    else:
        out_path = OUTPUT_DIR / "verdict.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(verdict, indent=2) + "\n")


if __name__ == "__main__":
    main()
