## Domain: Single-Source Shortest Path on DIMACS Road Networks

The artifact under optimization is {{ARTIFACT_NAME}} — a C++17 implementation of SSSP. The current best approach uses Contraction Hierarchies (CH) preprocessing with an upward Dijkstra phase followed by a downward sweep over rank-ordered CSR.

The algorithm draws on techniques from arXiv:2504.17033 ("Breaking the Sorting Barrier for Straight-Line Planar Graphs"). The verifier checks that all shortest-path distances exactly match reference Dijkstra — correctness is non-negotiable.

### Benchmark structure

{{BENCHMARK_DESCRIPTION}}

The graph is sparse (avg degree ~2.5), planar-ish, large diameter (~10k hops), and has weight distributions that are NOT uniform. These structural properties are critical: many algorithms that work well on random graphs perform poorly here. Road networks favor algorithms that exploit locality and degree structure over those that assume dense or high-diameter graphs.

### Known idea categories (draw from but don't limit to these)

- **params.json tuning**: adjusting `k` (recursion fan-out), `t` (threshold), `base_case_n` (switch to base case), or `pq_type`
- **Priority queue swap**: radix heap, pairing heap, or Fibonacci heap implementations; bucket-based queues tuned to road network weight distributions
- **Pivot rule change**: how the pivot element is selected in recursive SSSP partition
- **BMSSP stub**: beginning an implementation of the Bucketed Multi-Source SSSP sub-routine from the paper
- **CH preprocessing optimizations**: witness search parameters, node ordering heuristics, shortcut minimization
- **Code-level micro-optimization**: memory layout (SoA vs AoS), branch reduction, loop unrolling, prefetch hints, avoiding unnecessary copies, SIMD opportunities
- **Algorithmic structural change**: early termination, lazy deletion, bidirectional relaxation, parallel Dijkstra stubs

### Conditions that must hold for most improvements

- Change must not affect correctness: all output distances must match reference Dijkstra exactly
- Road network weight distribution: integer weights, small range relative to n, clustered near small values
- Memory hierarchy: L1 ~32KB, L2 ~256KB, L3 ~8-32MB. Cache pressure on frontier often dominates
- The CH structure (contraction rank ordering) is the load-bearing data structure — changes that break the rank invariant will produce wrong answers

### Papers in literature (for cross-referencing)

- arXiv:2504.17033 — Breaking the Sorting Barrier for Straight-Line Planar Graphs
- POLCA (Ren et al., 2026) — priority queue + epsilon-greedy for LLM optimization
- Route Planning in Road Networks (Bast et al., 2016) — survey of CH and related techniques
