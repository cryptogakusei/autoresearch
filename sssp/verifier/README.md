# SSSP Verifier

Docker container that checks correctness of SSSP experiments by running a
baked-in reference Dijkstra and comparing its output against the experiment's
distance files.

## Build

Run from the `sssp/` directory (that directory is the Docker build context):

```bash
docker build -t sssp-verifier -f verifier/Dockerfile .
```

## How control_plane.py calls it

```bash
docker run --network none --rm \
  -v $(pwd)/data/bay.gr:/data/graph.gr:ro \
  -v /tmp/sources.txt:/data/sources.txt:ro \
  -v /tmp/sssp_distances:/data/distances:ro \
  -v /tmp/verdict_output:/output \
  sssp-verifier
```

- `--network none` — the verifier has no outbound network access.
- `/data/graph.gr` — the graph under test (DIMACS .gr format).
- `/data/sources.txt` — one source node ID per line.
- `/data/distances/` — experiment output files named `distances_<source>.txt`,
  each containing lines of the form `d <node> <dist|INF>`.
- `/output/` — writable directory where `verdict.json` is written.

The container always exits 0. Read `/output/verdict.json` for the result.

## verdict.json format

### PASS

```json
{
  "status": "PASS",
  "constraint": "all shortest-path distances match reference Dijkstra exactly",
  "sources_checked": 5,
  "errors": []
}
```

### FAIL

```json
{
  "status": "FAIL",
  "constraint": "all shortest-path distances match reference Dijkstra exactly",
  "sources_checked": 5,
  "errors": [
    "Source 12345: 3 mismatches. First: node 8901 ref=1450 got=1449"
  ]
}
```

Possible error strings:
- `"Missing distances file for source N"` — experiment did not produce output for that source.
- `"Reference Dijkstra failed for source N: <detail>"` — the reference binary crashed or timed out.
- `"Source N: K mismatches. First: node M ref=R got=G"` — distance mismatch (up to 5 mismatches captured per source).
