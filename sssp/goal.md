# Research Goal

Goal: minimize wall-clock runtime of SSSP on the DIMACS CAL (California) road network
      while producing bitwise-correct shortest-path distances

Metric: runtime_ms (milliseconds, lower is better)
        measured as median over 5 fixed source nodes from data/cal.ss

Baseline: 141.0 ms (measured 2026-04-21T17:50:10Z)

Graph: 1,890,815 nodes, 4,657,742 edges (USA-road-d.CAL)
       ~6× larger than BAY, giving ~6× better measurement resolution

Prior work: 81 experiments on DIMACS BAY (321K nodes) reduced runtime from
            23ms → 5ms. Current sssp.cpp reflects those gains. CAL benchmark
            starts fresh to establish real-scale performance.

Constraint: all shortest-path distances must match reference Dijkstra exactly
  - reference Dijkstra is baked into the verifier Docker image
  - checked on all 5 benchmark source nodes after every experiment
  - integer edge weights (DIMACS road-d format) — exact integer comparison

Files in scope:
  sssp.cpp    — main algorithm under optimization (start: faithful BMSSP from arXiv:2504.17033)
  params.json — algorithm parameters: k, t, base_case_n, pivot_rule, pq_type

Off limits:
  dijkstra_ref.cpp  — reference implementation used by verifier (do not touch)
  data/             — DIMACS benchmark files, read-only inputs
  verify.py         — verifier logic baked into Docker image, not in workspace

Secondary metrics to monitor:
  relaxations  — edge relaxation count (alert if > 3x Dijkstra baseline)
  memory_mb    — peak RSS in MB (alert if > 2x Dijkstra baseline)

Session config:
  explore_every_N: 5
  explore_budget:  2
  max_debate_rounds: 2
  scout_every_M:   3

Notes:
  - The paper (arXiv:2504.17033) provides no implementation or empirical results.
    The algorithm may be slower than Dijkstra in practice on DIMACS-scale graphs
    due to constant-factor overhead. This is a valid finding, not a failure.
  - If BMSSP never beats Dijkstra on BAY (321K nodes), switch primary benchmark
    to a larger graph: COL (436K), CAL (1.9M), or E (3.6M). The crossover point
    where the new algorithm wins is itself a research output.
  - radix heap Dijkstra is a strong alternative baseline — add it as a second
    reference in the first experiment batch.
