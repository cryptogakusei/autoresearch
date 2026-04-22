// dijkstra_ref.cpp — Reference Dijkstra with binary heap.
// Oracle used by the verifier AND the baseline to beat.
//
// Build:  g++ -O2 -std=c++17 dijkstra_ref.cpp -o dijkstra_ref
// Usage:  ./dijkstra_ref <graph_file.gr> <source_node>
//
// Input format: DIMACS .gr (single-source shortest-path variant)
//   c  comment line (ignored)
//   p sp <n> <m>    — problem line: n nodes, m arcs
//   a <u> <v> <w>   — arc u->v with integer weight w (nodes are 1-indexed)
//
// Output:
//   d <node_id> <distance>   for every node 1..N  (INF if unreachable)
//   time_ms=<N>
//   relaxations=<N>

#include <algorithm>
#include <chrono>
#include <climits>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <queue>
#include <string>
#include <utility>
#include <vector>
using namespace std;

using ll = long long;
static constexpr ll INF = numeric_limits<ll>::max();

// ---------- graph -------------------------------------------------------

struct Graph {
    int n;                            // number of nodes (1-indexed: 1..n)
    long long m;                      // number of arcs
    vector<vector<pair<int, int>>> adj; // adj[u] = {(v, w), ...}
};

Graph read_dimacs(const char* path) {
    FILE* f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "Cannot open graph file: %s\n", path);
        exit(1);
    }

    Graph g;
    g.n = 0;
    g.m = 0;
    bool header_seen = false;

    char line[256];
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == 'c') continue;          // comment
        if (line[0] == 'p') {
            // p sp <n> <m>
            long long mm;
            sscanf(line, "p sp %d %lld", &g.n, &mm);
            g.m = mm;
            g.adj.assign(g.n + 1, {});
            header_seen = true;
        } else if (line[0] == 'a') {
            if (!header_seen) {
                fprintf(stderr, "Arc line before problem line in %s\n", path);
                exit(1);
            }
            int u, v, w;
            sscanf(line, "a %d %d %d", &u, &v, &w);
            g.adj[u].emplace_back(v, w);
        }
    }
    fclose(f);
    return g;
}

// ---------- Dijkstra ----------------------------------------------------

struct Result {
    vector<ll> dist;
    long long relaxations;
    double time_ms;
};

Result dijkstra(const Graph& g, int src) {
    vector<ll> dist(g.n + 1, INF);
    dist[src] = 0;
    long long relaxations = 0;

    // min-heap: (tentative_dist, node)
    priority_queue<pair<ll, int>,
                   vector<pair<ll, int>>,
                   greater<pair<ll, int>>> pq;
    pq.emplace(0LL, src);

    auto t0 = chrono::high_resolution_clock::now();

    while (!pq.empty()) {
        auto [d, u] = pq.top();
        pq.pop();

        if (d > dist[u]) continue;  // stale entry

        for (auto [v, w] : g.adj[u]) {
            ++relaxations;
            ll nd = dist[u] + (ll)w;
            if (nd < dist[v]) {
                dist[v] = nd;
                pq.emplace(nd, v);
            }
        }
    }

    auto t1 = chrono::high_resolution_clock::now();
    double time_ms =
        chrono::duration<double, milli>(t1 - t0).count();

    return {dist, relaxations, time_ms};
}

// ---------- main --------------------------------------------------------

int main(int argc, char* argv[]) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s <graph_file.gr> <source_node>\n", argv[0]);
        return 1;
    }
    const char* graph_file = argv[1];
    int src = atoi(argv[2]);

    Graph g = read_dimacs(graph_file);

    if (src < 1 || src > g.n) {
        fprintf(stderr, "Source node %d out of range [1, %d]\n", src, g.n);
        return 1;
    }

    Result r = dijkstra(g, src);

    // --- output distances ---
    for (int i = 1; i <= g.n; ++i) {
        if (r.dist[i] == INF)
            printf("d %d INF\n", i);
        else
            printf("d %d %lld\n", i, r.dist[i]);
    }

    // --- output stats ---
    // time_ms rounded to nearest integer
    printf("time_ms=%lld\n", llround(r.time_ms));
    printf("relaxations=%lld\n", r.relaxations);

    return 0;
}
