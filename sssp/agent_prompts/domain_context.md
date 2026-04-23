## Domain: Single-Source Shortest Path on DIMACS Road Networks

The artifact under optimization is {{ARTIFACT_NAME}} — a C++17 implementation of SSSP. The current baseline is a standard Dijkstra with `std::priority_queue` and a CSR adjacency list. This is intentionally simple — the goal is to discover algorithmic and data-structure improvements that beat this baseline.

The verifier checks that all shortest-path distances exactly match reference Dijkstra — correctness is non-negotiable.

### Benchmark structure

{{BENCHMARK_DESCRIPTION}}

The graph is sparse (avg degree ~2.5), planar-ish, large diameter (~10k hops), and has weight distributions that are NOT uniform. These structural properties are critical: many algorithms that work well on random graphs perform poorly here. Road networks favor algorithms that exploit locality and degree structure over those that assume dense or high-degree graphs.

### Known idea categories (draw from but don't limit to these)

- **Priority queue swap**: bucket queue (Dial's algorithm) tuned to integer weight range; two-level bucket queue for large weight ranges; radix heap; pairing heap
- **Preprocessing / graph contraction**: degree-1 leaf removal, degree-2 chain contraction, MIS-based contraction of higher-degree nodes — all reduce effective graph size before Dijkstra
- **Contraction Hierarchies**: full CH preprocessing (node ordering by importance, upward Dijkstra, shortcut edges) — very effective on road networks but complex to implement correctly
- **Memory layout**: SoA (separate target/weight arrays) vs AoS (interleaved) for adjacency list; CSR vs vector-of-vectors; packing target+weight into a single 64-bit word
- **Code-level micro-optimization**: software prefetch hints for dist[] lookups during edge relaxation; `__builtin_expect` on stale-node check and relaxation branch; uint32_t dist[] for cache efficiency
- **Lazy deletion**: avoid decrease-key entirely — insert a new entry on relaxation and skip stale entries at pop; eliminates the need for an indexed heap
- **Algorithmic structural change**: bidirectional Dijkstra; A* with landmark heuristics; parallel relaxation stubs

### params.json

The current params.json is a simple opaque blob you can extend freely. The baseline value is:
```json
{"algorithm": "dijkstra"}
```
You may add any keys to document the algorithm variant implemented (e.g. `"pq_type"`, `"coarse_bucket_width"`, `"preprocessing"`). The framework passes params.json to agents as context — its contents do not drive compilation directly. Build logic lives in {{ARTIFACT_NAME}}.

### Conditions that must hold for most improvements

- Change must not affect correctness: all output distances must match reference Dijkstra exactly
- Road network weight distribution: integer weights, relatively small range (max weight ~100k for CAL), clustered near small values — this makes bucket queues competitive
- Memory hierarchy: L1 ~32KB, L2 ~256KB, L3 ~8-32MB. Cache pressure on the frontier and adjacency list often dominates
- The adjacency list must be traversed for every settled node — changes that reduce this traversal (preprocessing, contraction) have the highest ceiling
- Correctness invariant: final dist[] must be exactly shortest-path distances for all reachable nodes. Optimizations like early termination are only valid for point-to-point queries, not SSSP.

### Papers in literature (for cross-referencing)

- Dial (1969) — original bucket queue (Dial's algorithm)
- Goldberg & Tarjan (1996) — radix heap for SSSP
- Geisberger et al. (2008) — Contraction Hierarchies for road networks
- Route Planning in Road Networks (Bast et al., 2016) — survey of CH and related techniques
- arXiv:2504.17033 — Breaking the Sorting Barrier for Straight-Line Planar Graphs
