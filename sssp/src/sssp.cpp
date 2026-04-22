// SSSP — Dijkstra with two-level bucket queue + lazy deletion
// SoA adjacency layout + software prefetching for dist[] during edge relaxation
#include <cstdio>
#include <vector>
#include <chrono>
#include <limits>
#include <algorithm>
#include <cassert>
using namespace std;

using ll = long long;
static const ll INF = numeric_limits<ll>::max();

int main(int argc, char* argv[]) {
    if (argc < 3) {
        fprintf(stderr, "usage: sssp <graph.gr> <source>\n");
        return 1;
    }

    FILE* f = fopen(argv[1], "r");
    if (!f) { perror("open"); return 1; }

    int n = 0, m = 0;
    char line[256];
    int max_weight = 0;

    struct RawEdge { int u, v, w; };
    vector<RawEdge> raw_edges;

    while (fgets(line, sizeof(line), f)) {
        if (line[0] == 'c') continue;
        if (line[0] == 'p') {
            int nn, mm;
            sscanf(line, "p sp %d %d", &nn, &mm);
            n = nn; m = mm;
            raw_edges.reserve(m);
        } else if (line[0] == 'a') {
            int u, v, w;
            sscanf(line, "a %d %d %d", &u, &v, &w);
            raw_edges.push_back({u, v, w});
            if (w > max_weight) max_weight = w;
        }
    }
    fclose(f);

    // Build CSR-style SoA adjacency
    vector<int> degree(n + 2, 0);
    for (auto& e : raw_edges) {
        degree[e.u]++;
    }

    vector<int> offsets(n + 2, 0);
    for (int i = 1; i <= n; i++) {
        offsets[i + 1] = offsets[i] + degree[i];
    }
    int total_edges = offsets[n + 1];

    vector<int> targets(total_edges);
    vector<int> weights(total_edges);

    vector<int> cursor(n + 2, 0);
    for (int i = 1; i <= n + 1; i++) {
        cursor[i] = offsets[i];
    }

    for (auto& e : raw_edges) {
        int pos = cursor[e.u]++;
        targets[pos] = e.v;
        weights[pos] = e.w;
    }

    { vector<RawEdge>().swap(raw_edges); }
    { vector<int>().swap(degree); }
    { vector<int>().swap(cursor); }

    int src = atoi(argv[2]);

    // -----------------------------------------------------------------------
    // Two-level bucket queue with lazy deletion
    // -----------------------------------------------------------------------
    static const int W = 32; // coarse bucket width

    // Circular coarse array size: need to cover range of active distances
    // Active range is at most max_weight ahead of current minimum
    int num_active_coarse = max_weight / W + 2;
    int fine_total = num_active_coarse * W;

    // Each fine bucket is a singly-linked list. Since we use lazy deletion,
    // a node may appear in multiple buckets. We use a pool-based linked list.
    // Pool entries: each entry has (node_id, next_in_bucket).
    // We pre-allocate for worst case: n initial + m relaxation insertions.
    int pool_cap = n + m + 2;
    vector<int> pool_node(pool_cap);
    vector<int> pool_next(pool_cap, -1);
    int pool_size = 0;

    vector<int> fine_head(fine_total, -1);   // head of linked list for each fine bucket
    vector<int> coarse_count(num_active_coarse, 0); // count of entries (including stale) per coarse bucket

    auto get_coarse = [&](ll d) -> int {
        return (int)((d / W) % num_active_coarse);
    };
    auto get_fine_offset = [&](ll d) -> int {
        return (int)(d % W);
    };

    auto bucket_insert = [&](int v, ll d) {
        int ci = get_coarse(d);
        int fo = get_fine_offset(d);
        int fi = ci * W + fo;
        int idx = pool_size++;
        pool_node[idx] = v;
        pool_next[idx] = fine_head[fi];
        fine_head[fi] = idx;
        coarse_count[ci]++;
    };

    // -----------------------------------------------------------------------
    // Dijkstra with two-level bucket queue + lazy deletion + prefetching
    // -----------------------------------------------------------------------
    vector<ll> dist(n + 1, INF);
    dist[src] = 0;

    bucket_insert(src, 0);

    long long relaxations = 0;
    int nodes_settled = 0;

    const ll* dist_ptr = dist.data();
    const int* targets_ptr = targets.data();
    const int* weights_ptr = weights.data();

    static const int PREFETCH_AHEAD = 6;

    // Current scan position
    ll cur_coarse_abs = 0;
    int cur_fine_offset = 0; // fine offset within current coarse bucket being scanned

    auto t0 = chrono::steady_clock::now();

    while (nodes_settled < n) {
        // Find next non-empty entry
        // First, find a non-empty coarse bucket starting from current position
        {
            int scanned = 0;
            while (true) {
                int ci = (int)(cur_coarse_abs % num_active_coarse);
                if (coarse_count[ci] > 0) break;
                cur_coarse_abs++;
                cur_fine_offset = 0;
                scanned++;
                if (scanned >= num_active_coarse) goto done;
            }
        }

        // Process current coarse bucket
        {
            int ci = (int)(cur_coarse_abs % num_active_coarse);
            int fine_base = ci * W;

            while (coarse_count[ci] > 0 && cur_fine_offset < W) {
                int fi = fine_base + cur_fine_offset;
                
                while (fine_head[fi] != -1) {
                    // Pop entry from the bucket
                    int idx = fine_head[fi];
                    int u = pool_node[idx];
                    fine_head[fi] = pool_next[idx];
                    coarse_count[ci]--;

                    // Expected distance for this bucket position
                    ll expected_dist = cur_coarse_abs * W + cur_fine_offset;

                    // Lazy deletion: skip stale entries
                    if (dist[u] != expected_dist) {
                        continue;
                    }

                    nodes_settled++;

                    int edge_start = offsets[u];
                    int edge_end = offsets[u + 1];
                    int num_edges = edge_end - edge_start;

                    for (int k = 0; k < PREFETCH_AHEAD && k < num_edges; k++) {
                        __builtin_prefetch(&dist_ptr[targets_ptr[edge_start + k]], 0, 1);
                    }

                    // Track if we need to reset scan pointer
                    bool need_reset = false;
                    ll reset_coarse_abs = cur_coarse_abs;
                    int reset_fine_offset = cur_fine_offset;

                    for (int i = edge_start; i < edge_end; i++) {
                        if (i + PREFETCH_AHEAD < edge_end) {
                            __builtin_prefetch(&dist_ptr[targets_ptr[i + PREFETCH_AHEAD]], 0, 1);
                        }

                        int v = targets_ptr[i];
                        int w = weights_ptr[i];
                        ll nd = expected_dist + w;
                        if (nd < dist[v]) {
                            dist[v] = nd;
                            bucket_insert(v, nd);
                            relaxations++;

                            // Check if inserted behind current scan position
                            ll new_coarse_abs = nd / W;
                            int new_fine_off = (int)(nd % W);
                            if (new_coarse_abs < cur_coarse_abs ||
                                (new_coarse_abs == cur_coarse_abs && new_fine_off < cur_fine_offset)) {
                                // Need to reset scan pointer backward
                                if (!need_reset || new_coarse_abs < reset_coarse_abs ||
                                    (new_coarse_abs == reset_coarse_abs && new_fine_off < reset_fine_offset)) {
                                    reset_coarse_abs = new_coarse_abs;
                                    reset_fine_offset = new_fine_off;
                                    need_reset = true;
                                }
                            }
                        }
                    }

                    if (need_reset) {
                        cur_coarse_abs = reset_coarse_abs;
                        cur_fine_offset = reset_fine_offset;
                        ci = (int)(cur_coarse_abs % num_active_coarse);
                        fine_base = ci * W;
                        fi = fine_base + cur_fine_offset;
                        // Continue processing from new position - the outer while loops will handle it
                        // We need to break out and let the outer loop re-enter
                        // Actually, we can just continue the while loop since fi is now updated
                        // But we need to also update the ci tracking
                        // Let's break and let the outer loops handle it
                        goto restart_coarse;
                    }
                }
                cur_fine_offset++;
            }

            // Done with this coarse bucket (or it's empty now)
            // If coarse_count[ci] > 0, it means stale entries remain; 
            // they'll never be valid since we've scanned all fine offsets.
            // Clear them out to avoid blocking future circular reuse.
            if (coarse_count[ci] > 0) {
                // Drain remaining stale entries
                for (int fo2 = 0; fo2 < W; fo2++) {
                    int fi2 = fine_base + fo2;
                    while (fine_head[fi2] != -1) {
                        int idx = fine_head[fi2];
                        fine_head[fi2] = pool_next[idx];
                        coarse_count[ci]--;
                    }
                }
            }
        }

        cur_coarse_abs++;
        cur_fine_offset = 0;
        continue;

    restart_coarse:
        // Scan pointer was reset; continue from new position
        // The outer while loop will pick up from cur_coarse_abs, cur_fine_offset
        continue;
    }

done:

    auto t1 = chrono::steady_clock::now();
    double ms = chrono::duration<double, milli>(t1 - t0).count();

    for (int i = 1; i <= n; i++) {
        if (dist[i] < INF)
            printf("d %d %lld\n", i, dist[i]);
    }

    printf("time_ms=%.3f\n", ms);
    printf("relaxations=%lld\n", relaxations);

    return 0;
}