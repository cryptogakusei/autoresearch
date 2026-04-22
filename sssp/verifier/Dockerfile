FROM ubuntu:22.04

# Install build tools and Python, then clean up apt lists to keep image lean
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        g++ \
        python3 \
    && rm -rf /var/lib/apt/lists/*

# Copy reference Dijkstra source (build context is the sssp/ directory)
COPY src/dijkstra_ref.cpp /verifier/dijkstra_ref.cpp

# Compile the reference binary
RUN g++ -O2 -std=c++17 -o /verifier/dijkstra_ref /verifier/dijkstra_ref.cpp

# Copy verifier script
COPY verifier/verify.py /verifier/verify.py

# Pre-create expected mount points so permissions are predictable
RUN mkdir -p /data/distances /output

CMD ["python3", "/verifier/verify.py"]
