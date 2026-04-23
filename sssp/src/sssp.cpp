// SSSP — Delta-stepping with light/heavy edge separation
// Uses flat array of band worklists with DELTA=4096
// Light edges (weight < DELTA) are relaxed within current band worklist
// Heavy edges (weight >= DELTA) are deferred to target band
#include <cstdio>
#include <cstdint>
#include <vector>
#include <chrono>
#include <limits>
#include <cstring>
#include <cstdlib>
#include <algorithm>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
using namespace std;

static const uint32_t INF = numeric_limits<uint32_t>::max();

static inline const char* skip_to_digit(const char* p) {
    while (*p < '0' || *p > '9') ++p;
    return p;
}

static inline const char* parse_uint(const char* p, uint32_t& val) {
    val = 0;
    while (*p >= '0' && *p <= '9') {
        val = val * 10 + (*p - '0');
        ++p;
    }
    return p;
}

static char outbuf[1 << 22];
static int outpos = 0;

static inline void flush_out() {
    if (outpos > 0) {
        fwrite(outbuf, 1, outpos, stdout);
        outpos = 0;
    }
}

static inline void out_char(char c) {
    outbuf[outpos++] = c;
    if (outpos >= (1 << 22) - 64) flush_out();
}

static inline void out_uint(uint32_t v) {
    char tmp[12];
    int len = 0;
    if (v == 0) {
        out_char('0');
        return;
    }
    while (v > 0) {
        tmp[len++] = '0' + (v % 10);
        v /= 10;
    }
    for (int i = len - 1; i >= 0; --i) out_char(tmp[i]);
}

static inline void out_int(int v) {
    if (v < 0) {
        out_char('-');
        out_uint((uint32_t)(-v));
    } else {
        out_uint((uint32_t)v);
    }
}

int main(int argc, char* argv[]) {
    if (argc < 3) {
        fprintf(stderr, "usage: sssp <graph.gr> <source>\n");
        return 1;
    }

    int fd = open(argv[1], O_RDONLY);
    if (fd < 0) { perror("open"); return 1; }
    struct stat st;
    fstat(fd, &st);
    size_t filesize = st.st_size;
    const char* data = (const char*)mmap(nullptr, filesize, PROT_READ, MAP_PRIVATE, fd, 0);
    if (data == MAP_FAILED) { perror("mmap"); return 1; }
    close(fd);
    madvise((void*)data, filesize, MADV_SEQUENTIAL);

    uint32_t n = 0, m = 0;
    const char* p = data;
    const char* end_data = data + filesize;
    const char* first_arc = nullptr;

    // Parse header
    while (p < end_data) {
        if (*p == 'c') {
            while (p < end_data && *p != '\n') ++p;
            if (p < end_data) ++p;
        } else if (*p == 'p') {
            p += 2;
            while (p < end_data && (*p < '0' || *p > '9')) ++p;
            uint32_t tmp;
            p = parse_uint(p, tmp); n = tmp;
            p = skip_to_digit(p);
            p = parse_uint(p, tmp); m = tmp;
            while (p < end_data && *p != '\n') ++p;
            if (p < end_data) ++p;
        } else if (*p == 'a') {
            first_arc = p;
            break;
        } else {
            while (p < end_data && *p != '\n') ++p;
            if (p < end_data) ++p;
        }
    }

    // Nodes are 1-based: 1..n
    vector<uint32_t> degree(n + 2, 0);

    // Temporary edge storage
    vector<uint32_t> edge_u(m), edge_v(m), edge_w(m);

    {
        uint32_t ei = 0;
        p = first_arc;
        while (p < end_data) {
            if (*p == 'a') {
                p++;
                p = skip_to_digit(p);
                uint32_t u;
                p = parse_uint(p, u);
                p = skip_to_digit(p);
                uint32_t v;
                p = parse_uint(p, v);
                p = skip_to_digit(p);
                uint32_t w;
                p = parse_uint(p, w);
                edge_u[ei] = u;
                edge_v[ei] = v;
                edge_w[ei] = w;
                degree[u]++;
                ei++;
                while (p < end_data && *p != '\n') ++p;
                if (p < end_data) ++p;
            } else {
                while (p < end_data && *p != '\n') ++p;
                if (p < end_data) ++p;
            }
        }
    }

    munmap((void*)data, filesize);

    // Build CSR offset array
    vector<uint32_t> offset(n + 2, 0);
    for (uint32_t i = 1; i <= n; i++) {
        offset[i + 1] = offset[i] + degree[i];
    }

    // Build CSR adjacency arrays
    vector<uint32_t> csr_to(m);
    vector<uint32_t> csr_w(m);

    memset(degree.data(), 0, (n + 2) * sizeof(uint32_t));
    for (uint32_t i = 0; i < m; i++) {
        uint32_t u = edge_u[i];
        uint32_t idx = offset[u] + degree[u];
        csr_to[idx] = edge_v[i];
        csr_w[idx] = edge_w[i];
        degree[u]++;
    }

    edge_u.clear(); edge_u.shrink_to_fit();
    edge_v.clear(); edge_v.shrink_to_fit();
    edge_w.clear(); edge_w.shrink_to_fit();
    degree.clear(); degree.shrink_to_fit();

    // Source node (1-based)
    uint32_t src = (uint32_t)atoi(argv[2]);

    // Delta-stepping parameter
    static const uint32_t DELTA = 4096;

    // dist array: indices 0..n, use indices 1..n (1-based)
    vector<uint32_t> dist(n + 1, INF);
    dist[src] = 0;

    // Band buckets for delta-stepping
    // For road networks max distance is typically < 40M, so max band ~ 40M/4096 ~ 9766.
    // Allocate 131072 bands to be safe (covers distances up to ~537M).
    static const uint32_t NUM_BAND_BUCKETS = 131072;
    vector<vector<uint32_t>> band_bucket(NUM_BAND_BUCKETS);

    // Worklist for light-edge relaxations within current band
    vector<uint32_t> light_worklist;
    // Temporary collection for heavy-edge targets
    vector<uint32_t> heavy_targets;

    // Insert source into band 0
    band_bucket[0].push_back(src);
    uint32_t max_band = 0;

    for (uint32_t band = 0; band <= max_band + 1 && band < NUM_BAND_BUCKETS; ) {
        if (band_bucket[band].empty()) {
            band++;
            continue;
        }

        // Phase 1: Process light edges within this band repeatedly until no more
        // light-edge relaxations produce new nodes in this band.
        // We process the band bucket, relaxing only light edges (weight < DELTA).
        // Nodes whose light-edge relaxation lands in the same band are re-added
        // to the band bucket for reprocessing.
        while (!band_bucket[band].empty()) {
            light_worklist.swap(band_bucket[band]);
            band_bucket[band].clear();

            for (uint32_t idx = 0; idx < light_worklist.size(); idx++) {
                uint32_t u = light_worklist[idx];
                uint32_t du = dist[u];
                // Skip stale entries: if node's distance no longer maps to this band
                if (du == INF || du / DELTA != band) continue;

                uint32_t eStart = offset[u];
                uint32_t eEnd = offset[u + 1];
                for (uint32_t ei = eStart; ei < eEnd; ei++) {
                    uint32_t w = csr_w[ei];
                    if (w < DELTA) {
                        // Light edge: relax and possibly re-add to same band
                        uint32_t to = csr_to[ei];
                        uint32_t nd = du + w;
                        if (nd < dist[to]) {
                            dist[to] = nd;
                            uint32_t target_band = nd / DELTA;
                            // Light edges with du in current band: nd = du + w where w < DELTA
                            // target_band is either == band or == band+1
                            // (since du/DELTA == band and w < DELTA, nd < (band+1)*DELTA + DELTA)
                            if (target_band == band) {
                                // Same band: add to current band bucket for reprocessing
                                band_bucket[band].push_back(to);
                            } else {
                                // Different band (band+1 typically): defer
                                if (target_band < NUM_BAND_BUCKETS) {
                                    band_bucket[target_band].push_back(to);
                                    if (target_band > max_band) max_band = target_band;
                                }
                            }
                        }
                    }
                }
            }
            // Don't clear light_worklist yet — we need it for Phase 2
            // Actually we need to accumulate all settled nodes for heavy-edge phase.
            // We'll collect them in heavy_targets temporarily.
            // But it's simpler to just do Phase 2 after each light pass.
            // Actually, the textbook algorithm says: do all light relaxations until
            // the band is empty, collecting all settled nodes, then do heavy relaxations once.
            // Let's collect settled nodes from this light pass.
            for (uint32_t idx = 0; idx < light_worklist.size(); idx++) {
                uint32_t u = light_worklist[idx];
                uint32_t du = dist[u];
                if (du != INF && du / DELTA == band) {
                    heavy_targets.push_back(u);
                }
            }
            light_worklist.clear();
        }

        // Phase 2: Process heavy edges for all nodes settled in this band
        for (uint32_t u : heavy_targets) {
            uint32_t du = dist[u];
            // Verify node is still in this band (it must be, since light phase is done)
            if (du == INF || du / DELTA != band) continue;

            uint32_t eStart = offset[u];
            uint32_t eEnd = offset[u + 1];
            for (uint32_t ei = eStart; ei < eEnd; ei++) {
                uint32_t w = csr_w[ei];
                if (w >= DELTA) {
                    // Heavy edge: relax into target band
                    uint32_t to = csr_to[ei];
                    uint32_t nd = du + w;
                    if (nd < dist[to]) {
                        dist[to] = nd;
                        uint32_t target_band = nd / DELTA;
                        if (target_band < NUM_BAND_BUCKETS) {
                            band_bucket[target_band].push_back(to);
                            if (target_band > max_band) max_band = target_band;
                        }
                    }
                }
            }
        }
        heavy_targets.clear();

        band++;
    }

    // Output using original 1-based node IDs
    for (uint32_t i = 1; i <= n; i++) {
        uint32_t d = dist[i];
        if (d < INF) {
            out_char('d');
            out_char(' ');
            out_uint(i);
            out_char(' ');
            out_uint(d);
            out_char('\n');
        }
    }

    flush_out();

    return 0;
}