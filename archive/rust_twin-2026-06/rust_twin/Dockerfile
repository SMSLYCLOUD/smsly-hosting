# syntax=docker/dockerfile:1.7
#
# Multi-stage build for the rust_twin.
# Builds two binaries: api (Axum HTTP server) and worker (Redis queue processor).
#
# Usage:
#   docker build -t rust_twin-api:latest -f Dockerfile --target api .
#   docker build -t rust_twin-worker:latest -f Dockerfile --target worker .
#
# The frontend crate is excluded (it's a separate WASM build).

# ============== Stage 1: builder ==============
FROM rust:1.82-slim-bookworm AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy the workspace manifests first to cache deps
COPY Cargo.toml Cargo.lock ./
COPY crates/api/Cargo.toml ./crates/api/
COPY crates/worker/Cargo.toml ./crates/worker/
COPY crates/core/Cargo.toml ./crates/core/
COPY crates/cli/Cargo.toml ./crates/cli/
COPY crates/infrastructure/Cargo.toml ./crates/infrastructure/
COPY crates/intelligence/Cargo.toml ./crates/intelligence/

# Dummy build to cache dependencies (will be overwritten below)
RUN mkdir -p \
    crates/api/src crates/worker/src crates/core/src crates/cli/src \
    crates/infrastructure/src crates/intelligence/src \
    && echo 'fn main() {}' > crates/api/src/main.rs \
    && echo 'fn main() {}' > crates/worker/src/main.rs \
    && echo 'fn main() {}' > crates/cli/src/main.rs \
    && touch crates/core/src/lib.rs \
    && touch crates/infrastructure/src/lib.rs \
    && touch crates/intelligence/src/lib.rs \
    && cargo build --release --bin api --bin worker --bin cli 2>&1 \
    || true

# Copy the actual source
COPY crates/ ./crates/

# Build with the real source
RUN cargo build --release --bin api --bin worker --bin cli \
    && strip target/release/api target/release/worker target/release/cli

# ============== Stage 2: api runtime ==============
FROM gcr.io/distroless/cc-debian12:nonroot AS api

# Copy the compiled binary
COPY --from=builder /build/target/release/api /usr/local/bin/api

# distroless already has a nonroot user (UID 65532)
USER nonroot:nonroot

# Default port (Axum reads $PORT, default 8080)
ENV PORT=8080
ENV HOST=0.0.0.0
EXPOSE 8080

# HEALTHCHECK is not available in distroless; use a sidecar or the
# application's own /health endpoint. Document this in the README.

ENTRYPOINT ["/usr/local/bin/api"]

# ============== Stage 3: worker runtime ==============
FROM gcr.io/distroless/cc-debian12:nonroot AS worker

COPY --from=builder /build/target/release/worker /usr/local/bin/worker

USER nonroot:nonroot

ENTRYPOINT ["/usr/local/bin/worker"]
