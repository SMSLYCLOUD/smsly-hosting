# Media Node + Attestation — Design v3: Portability & Scalability

## 1. Design Goals

| Goal | What it means |
|------|---------------|
| **Portable** | Attestation engine works on TPM, Secure Enclave, SE050, software fallback — same binary, same API, zero code changes |
| **Scalable** | Edge-local verification, agent-initiated health, stateless control plane — 10 nodes or 10,000 nodes |
| **Pluggable** | C++ management daemon removable. Attestation backends swappable at config time. No hard dependencies between layers |
| **Open-source clean** | PaaS (`smsly-hosting`) stays a generic OS — zero trust-authority logic. Federation, verification, and audit live in SMSLYCLOUD. |

---

## 2. Layered Architecture

### 2.0 Control Plane Tiers (Reporting Hierarchy)

```
┌─────────────────────────────────────────────────────────────────┐
│  TIER 2: SMSLYCLOUD — Backend / Controller / Ecosystem           │
│  ─────────────────────────────────────────────────────────────  │
│  OFFLOADS THE WORK. Trust authority. Decentralized state.        │
│  • Federation verifiers (cross-node trust consensus)             │
│  • Cross-verification authority (proves MIPStampV2 at scale)     │
│  • smsly-audit-log-service (event sourcing for attest events)    │
│  • Capacity + routing across ALL hosting platforms               │
│  Receives: aggregated telemetry + audit from smsly-hosting       │
└───────────────────────────┬─────────────────────────────────────┘
                            │ Aggregated sync (REST/HMAC)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  TIER 1: smsly-hosting — Hosting Platform / Local Control Plane  │
│  ─────────────────────────────────────────────────────────────  │
│  Manages baremetal nodes directly. Owns node-local state.        │
│  • apps.media — node profiles, capacity, provisioning, telemetry │
│  • AttestationProfile (local copy of edge key + status)          │
│  • Redis cache (telemetry snapshots, capacity scores)            │
│  • PostgreSQL (profiles, room metadata, local audit)             │
│  Communicates with nodes via:                                    │
│  ├─ SSH (provisioning)                                           │
│  ├─ HMAC-signed HTTP (management daemon, attestation engine)     │
│  └─ WebSocket (live telemetry, attestation events)               │
└───────────────────────────┬─────────────────────────────────────┘
                            │ WireGuard mesh
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  BAREMETAL MEDIA NODE — EDGE                     │
│                                                                  │
│  ┌─── Layer 1: Data Plane (systemd, native) ────────────────┐  │
│  │  postgresql, redis, nats, minio, wireguard                │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─── Layer 2: Media Plane (systemd, native) ───────────────┐  │
│  │  kamailio, freeswitch, rtpengine, coturn, livekit         │  │
│  │  smsly-voice-api, smsly-video                             │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─── Layer 3: Trust Plane (systemd, native) ───────────────┐  │
│  │                                                            │  │
│  │  smsly-attestation-engine  :9091                           │  │
│  │  ├─ Pluggable signing backend (config-selected)           │  │
│  │  │   ├─ TPM 2.0 (Linux baremetal)                         │  │
│  │  │   ├─ Secure Enclave (macOS/iOS)                        │  │
│  │  │   ├─ SE050 (IoT/SoM)                                   │  │
│  │  │   └─ Software fallback (dev/testing)                    │  │
│  │  ├─ TrustScoreEngine v2 (7-pillar, stateless)             │  │
│  │  ├─ Merkle tree builder (stateless, per-batch)            │  │
│  │  └─ Federation client (reports to verifiers)              │  │
│  │                                                            │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─── Layer 4: Management Plane (systemd, optional) ────────┐  │
│  │                                                            │  │
│  │  smsly-media-mgmt  :9090  (C++ Drogon, pluggable)        │  │
│  │  ├─ Health aggregator (parallel coroutine checks)         │  │
│  │  ├─ WebSocket telemetry push (5s intervals)               │  │
│  │  ├─ Service lifecycle (start/stop/restart via DBus)       │  │
│  │  └─ Config hot-reload (Kamailio, OpenResty)              │  │
│  │                                                            │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─── Layer 5: Edge Proxy (systemd) ────────────────────────┐  │
│  │  openresty — TLS, routing, HMAC auth                      │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**Key principle:** Each layer is independently removable. Remove Layer 4 (mgmt) → node still works, just no aggregated health or telemetry push. Remove Layer 3 (attestation) → media works but no trust scoring. Layers 1-2 are the minimum viable media node.

### 2.1 Attestation vs. Chain — Same Goal, Different Tier

Attestation and Chain both prove media integrity, but they are **distinct mechanisms at distinct tiers**:

| | **Attestation (Edge)** | **Chain (SMSLYCLOUD)** |
|---|------------------------|------------------------|
| **Tier** | Media node (Tier 0), deployed by PaaS | Ecosystem (Tier 2) |
| **Timing** | **Real-time**, as media flows | Asynchronous, historical |
| **Function** | **Encrypt/decrypt media** + stamp each frame with device-bound key | Merkle transparency log + federation + audit |
| **Keys** | Hardware-bound (TPM/SE), never leaves device | Verification keys (public), federation DIDs |
| **Output** | `MIPStampV2` per frame/batch | Anchored Merkle roots, cross-verifications |
| **Failure mode** | Media stream rejected at edge | Stamp disputed during audit/verification |

```
Media frame ──► [Edge Attestation Engine] ──► encrypted + stamped
                    │  real-time crypto
                    │  device-bound key (TPM)
                    ▼
              MIPStampV2 (per frame)
                    │  heartbeat / telemetry (HMAC)
                    ▼
              smsly-hosting (forwards)
                    │  aggregated sync
                    ▼
              SMSLYCLOUD Chain
                    │  Merkle root anchor
                    │  federation cross-verify
                    │  smsly-audit-log-service
                    ▼
              Verifiable transparency log (20-year proof)
```

**Boundary:** The PaaS (`smsly-hosting`, OSS) runs the edge attestation engine because it needs raw hardware (TPM, systemd, WireGuard). The *authority* — proving chains, federation, audit — is **offloaded to SMSLYCLOUD**. The PaaS never holds the trust brain; it only operates the crypto at the edge and forwards results.

### 2.2 Chain Integration — Edge Stamps → SMSLYCLOUD Chain

The SMSLYCLOUD **Transaction Chain Service** (`CHAIN/smsly-transaction-chain`) is the immutable transparency log. It is blockchain-inspired: SHA-256 hash-chain of blocks, each block a Merkle tree of transactions, append-only (DB triggers block UPDATE/DELETE), block finalized every `BLOCK_INTERVAL_SECONDS` (default 10s), `MAX_TXS_PER_BLOCK` (default 1000).

**How edge stamps become chain transactions:**

```
Edge:  smsly-attestation-engine
         └─ POST /attest → MIPStampV2 (real-time, per frame/batch)
              │
              │  HTTP (HMAC) — attestation event
              ▼
smsly-hosting (apps.media)
         └─ forwards stamp as chain transaction
              │
              │  POST /v1/transactions  (aggregated sync to SMSLYCLOUD)
              ▼
SMSLYCLOUD Transaction Chain Service  (port 8010)
         └─ TransactionCreate {
              correlation_id: <session_id>,
              service:        "attestation",
              tx_type:        "media_stamp",
              actor_id:       <node_id>,
              actor_type:     "media_node",
              resource_id:    <room_id>,
              payload:        { MIPStampV2, trust_score, merkle_root }
            }
              │  (pending pool)
              ▼  every ~10s
         Block created:  merkle_root = SHA-256(tx_hashes)
                        previous_hash → links to prior block
                        proof_path + proof_index assigned per tx
              ▼
         Immutable, verifiable transparency log (20-year proof)
```

**Transaction schema mapping (attestation → chain):**

| Chain field | Attestation source |
|-------------|-------------------|
| `service` | `"attestation"` (NEW — add to `TxService` enum) |
| `tx_type` | `"media_stamp"` (NEW — add to `TxType` enum) |
| `correlation_id` | media session UUID (trace across chain) |
| `parent_tx_id` | previous frame-batch stamp tx_id (within-session chain) |
| `actor_id` | media node ID (`ManagedServer.node_id`) |
| `actor_type` | `"media_node"` |
| `resource_id` | room/session ID |
| `payload` | serialized `MIPStampV2` + `TrustScore` |
| `tx_metadata` | `{ platform_attester, algorithm_suite, merkle_root }` |

**Verification paths (all in SMSLYCLOUD):**
- `GET /v1/transactions/{id}/verify` — verify a single stamp's Merkle proof
- `GET /v1/chain/verify` — verify entire chain integrity (hash-chain linkage)
- `GET /v1/trace/{correlation_id}` — follow a media session's stamp history

**Required changes in SMSLYCLOUD Chain service:**
1. Add `"attestation"` to `TxService` enum (Python + Rust)
2. Add `"media_stamp"` (and `"media_verify"`) to `TxType` enum
3. Accept `actor_type = "media_node"` (already free-form string)

No change to block/merkle logic — attestation stamps are just another `service` stream.

---

## 3. Attestation Engine — Portable Design

### 3.1 Plugin Architecture

The attestation engine uses a **backend trait pattern** — the signing hardware is a config-selected plugin, not a compile-time dependency.

```
smsly-attestation-engine
    │
    ├─ src/main.rs          — Axum server, CLI dispatch
    ├─ src/server.rs        — HTTP handlers (/health, /attest, /verify, /keys)
    ├─ src/trust.rs         — TrustScoreEngine wrapper
    ├─ src/merkle.rs        — Merkle tree builder (stateless)
    ├─ src/federation.rs    — Federation client
    │
    └─ src/backends/        — PLUGGABLE SIGNING BACKENDS
        ├─ mod.rs           — Backend trait definition
        ├─ tpm2.rs          — TPM 2.0 backend (tpm2-tools)
        ├─ software.rs      — Software CSPRNG backend
        └─ (future: se050.rs, keymint.rs, enclave.rs)
```

### 3.2 Backend Trait

```rust
// src/backends/mod.rs

use async_trait::async_trait;

/// The signing backend trait. Each hardware platform implements this.
/// The engine never touches private keys directly — only the backend does.
#[async_trait]
pub trait SigningBackend: Send + Sync {
    /// Backend identifier (matches config key)
    fn name(&self) -> &str;

    /// Security level (1-5, 5 = dedicated secure element)
    fn security_level(&self) -> u8;

    /// Whether this is hardware-backed (false = software fallback)
    fn is_hardware_backed(&self) -> bool;

    /// Generate a new keypair. Returns (public_key_bytes, key_handle).
    /// Private key stays inside the backend — never returned.
    async fn generate_keypair(&self) -> Result<(Vec<u8>, String), BackendError>;

    /// Sign a message. Returns signature bytes.
    /// For hybrid mode, signs with both classical + PQC internally.
    async fn sign(&self, key_handle: &str, message: &[u8]) -> Result<Vec<u8>, BackendError>;

    /// Verify a signature against a public key.
    async fn verify(
        &self,
        public_key: &[u8],
        message: &[u8],
        signature: &[u8],
    ) -> Result<bool, BackendError>;

    /// Read a hardware monotonic counter (anti-replay).
    /// Returns current counter value. None if backend doesn't support counters.
    async fn read_counter(&self, counter_id: &str) -> Result<Option<u64>, BackendError>;

    /// Increment the monotonic counter. Returns new value.
    async fn increment_counter(&self, counter_id: &str) -> Result<u64, BackendError>;

    /// Read platform configuration registers (TPM PCR values).
    /// Returns map of index → hash. Empty if not applicable.
    async fn read_pcrs(&self) -> Result<Vec<PcrValue>, BackendError>;

    /// Generate hardware entropy (TRNG). Used for nonces and challenges.
    async fn generate_entropy(&self, byte_count: usize) -> Result<Vec<u8>, BackendError>;
}

#[derive(Debug, Clone)]
pub struct PcrValue {
    pub index: u8,
    pub value: Vec<u8>,
    pub description: String,
}

#[derive(Debug, thiserror::Error)]
pub enum BackendError {
    #[error("Backend not available: {0}")]
    NotAvailable(String),
    #[error("Key generation failed: {0}")]
    KeyGenFailed(String),
    #[error("Signing failed: {0}")]
    SignFailed(String),
    #[error("Verification failed: {0}")]
    VerifyFailed(String),
    #[error("Hardware error: {0}")]
    HardwareError(String),
}
```

### 3.3 Config-Selected Backend

```json
// /etc/smsly/attestation.json

{
    "node_id": "media-3.iad1.smsly.com",
    "port": 9091,

    "backend": {
        "type": "tpm2",
        "tpm2_device": "/dev/tpm0",
        "key_hierarchy": "owner",
        "counter_id": "mip-stamp-counter"
    },

    "algorithm_suite": "hybrid",
    "trust_engine": {
        "entropy_z_threshold": 2.5,
        "baseline_noise_mean": 0.0,
        "baseline_noise_stddev": 1.0
    },

    "db_url": "postgresql://smsly:***@127.0.0.1:5432/smsly_media",
    "redis_url": "redis://:***@127.0.0.1:6379",
    "master_api_url": "https://master.smsly.com/api/v1",
    "gateway_secret": "***"
}
```

**Backend selection at startup:**
```rust
// src/main.rs

fn load_backend(config: &Config) -> Box<dyn SigningBackend> {
    match config.backend.r#type.as_str() {
        "tpm2" => Box::new(Tpm2Backend::new(&config.backend)),
        "software" => Box::new(SoftwareBackend::new()),
        _ => panic!("Unknown backend type: {}", config.backend.r#type),
    }
}
```

### 3.4 Portable Across Deployments

| Deployment | Backend | Notes |
|------------|---------|-------|
| Linux baremetal + TPM | `tpm2` | Full hardware root of trust |
| Linux baremetal, no TPM | `software` | CSPRNG + file-based keys |
| macOS/iOS | `secure_enclave` | Future: Apple CryptoKit |
| Android | `keymint` | Future: Android Keystore |
| IoT/SoM | `se050` | Future: NXP SE050 |
| Docker (dev) | `software` | No hardware access |
| CI/CD | `software` | Testing only |

**Same binary. Same config schema. Same API. Different backend.**

---

## 4. Scalable Health & Telemetry

### 4.1 Agent-Initiated, Not Master-Polled

The current system polls servers sequentially. For 100+ media nodes, this doesn't scale. Switch to **agent-initiated** for everything:

```
Current (doesn't scale):
  smsly-hosting → SSH → Node A → wait → SSH → Node B → wait → ...

Better (scales to N nodes):
  Node A ──heartbeat──→ smsly-hosting (Redis)
  Node B ──heartbeat──→ smsly-hosting (Redis)
  Node C ──heartbeat──→ smsly-hosting (Redis)
  ...
  smsly-hosting reads Redis (O(1) lookup)
```

### 4.2 Three-Channel Telemetry

Each media node maintains three outbound channels to the master:

| Channel | Transport | Frequency | Data | Direction |
|---------|-----------|-----------|------|-----------|
| **Heartbeat** | HMAC-signed HTTP POST | 10s | Node health, service status, attestation status | Node → Master |
| **Telemetry** | WebSocket (master opens) | 5s push | CPU, memory, disk, calls, rooms, trust scores | Node → Master |
| **Audit** | HMAC-signed HTTP POST | On-event | Stamp generated, tamper detected, key rotated | Node → Master |

**Heartbeat payload (lightweight, every 10s):**
```json
{
    "node_id": "uuid-abc-123",
    "timestamp": "2026-07-08T14:23:01Z",
    "uptime_seconds": 120451,
    "services": {
        "postgresql": "healthy",
        "redis": "healthy",
        "nats": "healthy",
        "minio": "healthy",
        "kamailio": "healthy",
        "freeswitch": "healthy",
        "rtpengine": "healthy",
        "livekit": "healthy",
        "coturn": "healthy",
        "voice_api": "healthy",
        "video": "healthy",
        "attestation": "healthy",
        "mgmt": "healthy"
    },
    "attestation": {
        "engine_healthy": true,
        "platform_attester": "tpm2",
        "algorithm_suite": "hybrid",
        "monotonic_counter": 42,
        "last_attestation_at": "2026-07-08T14:22:55Z",
        "stamps_generated_total": 15420,
        "stamps_verified_total": 15418,
        "tamper_detections_total": 2
    },
    "capacity": {
        "score": 0.62,
        "active_calls": 1243,
        "active_rooms": 87,
        "active_participants": 512
    }
}
```

**Why this scales:**
- smsly-hosting does zero polling — it just receives heartbeats
- If a node goes silent for 60s, smsly-hosting marks it degraded
- Telemetry WebSocket is opened by smsly-hosting once per node — push, not poll
- Audit events are fire-and-forget POSTs — no persistent connection

### 4.3 Master-Side Processing

```python
# backend/apps/media/services/telemetry.py

class TelemetryService:
    """Processes incoming heartbeats and telemetry from media nodes."""

    def __init__(self):
        self.cache = cache  # Redis

    def process_heartbeat(self, node_id: str, payload: dict):
        """Called by webhook when node sends heartbeat."""
        # 1. Update Redis cache (instant)
        cache.set(
            f"media:heartbeat:{node_id}",
            payload,
            timeout=120,  # expires in 2 min if no new heartbeat
        )

        # 2. Check if node went silent
        was_healthy = cache.get(f"media:status:{node_id}") == "online"
        cache.set(f"media:status:{node_id}", "online", timeout=120)

        if not was_healthy:
            # Node recovered — trigger audit log
            self._log_recovery(node_id)

        # 3. Update DB (batched, async)
        self._queue_db_update(node_id, payload)

    def process_telemetry(self, node_id: str, payload: dict):
        """Called by WebSocket handler when node pushes telemetry."""
        # 1. Update Redis (instant dashboard)
        cache.set(f"media:telemetry:{node_id}", payload, timeout=30)

        # 2. Update DB fields (async, can lag)
        MediaNodeProfile.objects.filter(
            server_id=node_id
        ).update(
            cpu_percent=payload["system"]["cpu_percent"],
            memory_percent=payload["system"]["memory_used_mb"] / payload["system"]["memory_total_mb"] * 100,
            active_calls=payload["voice"]["active_calls"],
            active_rooms=payload["video"]["active_rooms"],
            capacity_score=payload["capacity"]["score"],
            last_telemetry_at=timezone.now(),
        )

    def process_audit_event(self, node_id: str, event: dict):
        """Called by webhook when node reports attestation event."""
        # 1. Local cache (authoritative copy lives in SMSLYCLOUD Chain)
        AttestationAuditLog.objects.create(
            server_id=node_id,
            event_type=event["event_type"],
            trust_score=event.get("trust_score"),
            metadata=event.get("metadata", {}),
        )

        # 2. FORWARD to SMSLYCLOUD Transaction Chain (the work is offloaded)
        #    smsly-hosting is OSS — it does NOT hold the trust brain.
        if event.get("stamp"):
            ChainReporter.submit_stamp(node_id, event["stamp"])

    def get_all_node_status(self) -> dict:
        """O(1) lookup of all node statuses from Redis."""
        # Keys: media:heartbeat:{node_id}
        # Returns: { node_id: { ... heartbeat payload ... } }
        keys = cache._client.keys("media:heartbeat:*")
        # Pipeline mget for efficiency
        return {k: cache.get(k) for k in keys}


class ChainReporter:
    """Forwards edge attestation stamps to SMSLYCLOUD Transaction Chain.

    smsly-hosting (OSS PaaS) only operates the edge + forwards.
    The immutable transparency log and verification authority live in
    SMSLYCLOUD — see §2.2.
    """

    @staticmethod
    def submit_stamp(node_id: str, stamp: dict):
        """POST /v1/transactions to SMSLYCLOUD Chain service."""
        import requests  # or httpx; async in production
        payload = {
            "correlation_id": stamp.get("session_id", node_id),
            "service": "attestation",
            "tx_type": "media_stamp",
            "actor_id": node_id,
            "actor_type": "media_node",
            "resource_id": stamp.get("session_id"),
            "payload": stamp,
            "metadata": {
                "platform_attester": stamp.get("algorithm_suite"),
                "merkle_root": stamp.get("merkle_root"),
            },
        }
        # HMAC-signed request to SMSLYCLOUD_CHAIN_URL (env)
        requests.post(
            f"{settings.SMSLYCLOUD_CHAIN_URL}/v1/transactions",
            json=payload,
            headers={"Authorization": f"Bearer {settings.SMSLYCLOUD_API_TOKEN}"},
            timeout=5,
        )
```

### 4.4 Celery Task Scalability

```
Current queues:
  celery (default)  — general tasks
  deploy            — long-running provisioning
  fast              — quick tasks

New media-specific queues:
  media-telemetry   — processing telemetry batches
  media-audit       — writing attestation audit logs
```

**Periodic tasks (media-specific):**

| Task | Queue | Schedule | Purpose |
|------|-------|----------|---------|
| `check_stale_nodes` | media-telemetry | 30s | Detect nodes silent > 60s |
| `aggregate_capacity` | media-telemetry | 60s | Recompute global capacity from Redis |
| `flush_telemetry_to_db` | media-telemetry | 5 min | Batch-write Redis telemetry to PostgreSQL |
| `rotate_node_keys` | deploy | Daily | Trigger key rotation on all media nodes |
| `verify_federation_chains` | media-audit | Hourly | Verify cross-verifier trust chains |

---

## 5. Scalable Federation

> **Boundary:** Per the OSS principle, federation / cross-verification authority does
> **NOT** live in `smsly-hosting`. The PaaS only *forwards* verification requests to
> SMSLYCLOUD. The verifier registry, trust-chain scoring, and consensus live in the
> ecosystem (SMSLYCLOUD), built on top of the Transaction Chain (§2.2).

### 5.1 Decentralized Trust, No Single Bottleneck (SMSLYCLOUD)

```
                    ┌──────────────────┐
                    │  SMSLY Root      │
                    │  Verifier        │
                    │  (did:smsly:...  │
                    │   :root)         │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │ Telco A    │  │ Enterprise │  │ Government │
     │ Verifier   │  │ Verifier   │  │ Verifier   │
     │ (tier 2)   │  │ (tier 2)   │  │ (tier 2)   │
     └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
           │                │                │
     ┌─────┴──────┐  ┌─────┴──────┐  ┌─────┴──────┐
     │ Community  │  │ Community  │  │ Community  │
     │ Verifier   │  │ Verifier   │  │ Verifier   │
     │ (tier 3)   │  │ (tier 3)   │  │ (tier 3)   │
     └────────────┘  └────────────┘  └────────────┘
```

**Each verifier (SMSLYCLOUD service):**
- Has its own keypair (hardware-backed)
- Can independently verify any MIP stamp against the Chain
- Cross-signs trust assessments

**No central bottleneck.** Verifiers are independent SMSLYCLOUD processes. `smsly-hosting` (PaaS) only forwards verification requests — it does not verify or aggregate trust itself.

### 5.2 Verifier Registry (SMSLYCLOUD-Side)

> These models live in **SMSLYCLOUD** (not the OSS `smsly-hosting` PaaS). Listed here
> for completeness of the federation design.

```python
# SMSLYCLOUD — federation models (NOT in smsly-hosting)

class FederationVerifier(models.Model):
    """Registered verifier in the federated trust network."""
    did = models.CharField(max_length=255, unique=True)  # did:smsly:verifier:*
    name = models.CharField(max_length=255)
    org_type = models.CharField(
        max_length=20,
        choices=[
            ("platform", "SMSLY Root"),
            ("telco", "Telco Provider"),
            ("enterprise", "Enterprise"),
            ("government", "Government"),
            ("academic", "Academic"),
            ("community", "Community"),
        ],
    )
    public_key = models.TextField()  # base64
    trust_tier = models.PositiveSmallIntegerField(default=3)
    jurisdictions = models.JSONField(default=list)  # ["US", "GB", "EU"]
    endpoint_url = models.CharField(max_length=255)  # verifier's API endpoint
    is_active = models.BooleanField(default=True)
    registered_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["trust_tier", "is_active"]),
        ]


class FederationTrustChain(models.Model):
    """Aggregated trust chain for a media session/stamp."""
    subject_id = models.CharField(max_length=255, db_index=True)
    consensus_score = models.FloatField(default=0.0)
    verifier_count = models.PositiveSmallIntegerField(default=0)
    verifications = models.JSONField(default=list)  # [{verifier_did, score, signature, ...}]
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["subject_id", "-created_at"]),
        ]
```

### 5.3 Federation Scoring (SMSLYCLOUD)

> Scoring runs in **SMSLYCLOUD**, not the PaaS. `smsly-hosting` only forwards
> stamp/verification events; the ecosystem computes consensus.

The federation score is computed incrementally, not by replaying all verifiers:

```python
# SMSLYCLOUD — federation service (NOT in smsly-hosting)

    def add_verification(
        self,
        chain_id: str,
        verifier_did: str,
        assessed_score: float,
        signature: str,
    ):
        """Add a single verification and update consensus incrementally."""
        chain = FederationTrustChain.objects.get(id=chain_id)

        # Append verification
        verifications = chain.verifications
        verifications.append({
            "verifier_did": verifier_did,
            "assessed_score": assessed_score,
            "signature": signature,
            "endorsed_at": timezone.now().isoformat(),
        })

        # Incremental consensus update (no full recomputation)
        scores = [v["assessed_score"] for v in verifications]
        chain.consensus_score = sum(scores) / len(scores)
        chain.verifier_count = len(set(v["verifier_did"] for v in verifications))
        chain.verifications = verifications
        chain.save(update_fields=["consensus_score", "verifier_count", "verifications", "updated_at"])
```

---

## 6. Scalable Provisioning

### 6.1 Parallel Provisioning

```python
# backend/apps/media/provisioner.py

from celery import group

def provision_media_nodes_parallel(server_specs: list[dict]) -> str:
    """Provision multiple media nodes in parallel.
    Each node gets its own Celery task. No sequencing between nodes.
    """
    tasks = group(
        provision_media_node.s(spec["server_id"])
        for spec in server_specs
    )
    result = tasks.apply_async()
    return result.id  # Group ID for tracking


def provision_media_node(server_id: str):
    """Single node provisioning — same as before but with attestation."""
    # ... SSH, install, register ...
    # Attestation engine is installed as part of the standard flow
```

### 6.2 Node Auto-Registration

Instead of Django creating records and then provisioning, nodes can self-register:

```
1. Operator runs install.sh on baremetal
2. install.sh provisions all services + attestation engine
3. attestation engine starts, generates keys
4. attestation engine calls smsly-hosting: POST /api/v1/media-nodes/register/
   { hostname, ip, public_key, platform_attester, ... }
5. Master creates ManagedServer + MediaNodeProfile + AttestationProfile
6. Master opens WebSocket to node for telemetry
```

This eliminates the need for Django to SSH into nodes — the node pushes itself to the master. Only credentials exchange requires bidirectional communication.

---

## 7. File Manifest (Final)

### 7.1 New Files

| # | File | Purpose |
|---|------|---------|
| 1 | `scripts/systemd/smsly-attestation.service` | Attestation engine systemd unit |
| 2 | `scripts/systemd/smsly-media-mgmt.service` | Management daemon systemd unit (optional) |
| 3 | `scripts/systemd/smsly-voice-api.service` | Voice API systemd unit |
| 4 | `scripts/systemd/smsly-video.service` | Video SFU systemd unit |
| 5 | `scripts/systemd/kamailio.service` | Kamailio systemd unit |
| 6 | `scripts/systemd/freeswitch.service` | FreeSWITCH systemd unit |
| 7 | `scripts/systemd/rtpengine.service` | RTPEngine systemd unit |
| 8 | `scripts/systemd/livekit-server.service` | LiveKit systemd unit |
| 9 | `scripts/systemd/coturn.service` | coturn systemd unit |
| 10 | `infrastructure/media/openresty/nginx.conf` | OpenResty config |
| 11 | `infrastructure/media/kamailio/kamailio.cfg` | Kamailio config |
| 12 | `infrastructure/media/freeswitch/freeswitch.xml` | FreeSWITCH config |
| 13 | `infrastructure/media/rtpengine/rtpengine.conf` | RTPEngine config |
| 14 | `infrastructure/media/livekit/livekit.yaml` | LiveKit config |
| 15 | `infrastructure/media/coturn/turnserver.conf` | coturn config |
| 16 | `infrastructure/media/attestation/attestation.json` | Attestation config template |
| 17 | `lib/media-node.sh` | Media node provisioning functions |
| 18 | `backend/apps/media/__init__.py` | App package |
| 19 | `backend/apps/media/models.py` | MediaNodeProfile, MediaRoom, MediaParticipant |
| 20 | `backend/apps/media/models_attestation.py` | AttestationProfile, AttestationAuditLog (LOCAL cache) |
| 21 | `backend/apps/media/views.py` | REST API views |
| 22 | `backend/apps/media/urls.py` | URL routing |
| 23 | `backend/apps/media/tasks.py` | Celery tasks |
| 24 | `backend/apps/media/services/__init__.py` | Services package |
| 25 | `backend/apps/media/services/livekit_admin.py` | LiveKit API client |
| 26 | `backend/apps/media/services/capacity.py` | Node capacity routing (local) |
| 27 | `backend/apps/media/services/attestation_admin.py` | Attestation engine client |
| 28 | `backend/apps/media/services/telemetry.py` | Heartbeat/telemetry + Chain forwarding |
| 29 | `backend/apps/media/services/chain_reporter.py` | Forwards stamps to SMSLYCLOUD Chain |
| 30 | `backend/apps/media/provisioner.py` | Media node provisioning logic |
| 31 | `backend/apps/media/admin.py` | Django admin |
| 32 | `backend/apps/media/migrations/0001_initial.py` | Initial migration |

> **Federation authority is OFFLOADED to SMSLYCLOUD** — `FederationVerifier` and
> `FederationTrustChain` models + scoring service live in the ecosystem (§5), NOT in
> this OSS PaaS. `smsly-hosting` only forwards verification requests.

### 7.2 Modified Files

| # | File | Change |
|---|------|--------|
| 34 | `backend/apps/deployments/models_core.py` | Add `node_type` field to ManagedServer |
| 35 | `backend/config/settings.py` | Add `apps.media` to INSTALLED_APPS |
| 36 | `backend/config/urls.py` | Add media API routes |
| 37 | `backend/config/celery.py` | Add media queues + periodic tasks |
| 38 | `install.sh` | Add `--mode=media-node` dispatch |
| 39 | `lib/fresh.sh` | Add media node branch |
| 40 | `lib/update.sh` | Add media node update branch |
| 41 | `lib/ops.sh` | Add media node ops functions |

### 7.3 Attestation Engine Repo (separate)

```
smsly-attestation-engine/
├─ Cargo.toml
├─ src/
│   ├─ main.rs           — Axum server + CLI
│   ├─ server.rs         — HTTP handlers
│   ├─ trust.rs          — TrustScoreEngine wrapper
│   ├─ merkle.rs         — Merkle tree builder
│   ├─ federation.rs     — Federation client
│   └─ backends/
│       ├─ mod.rs        — Backend trait
│       ├─ tpm2.rs       — TPM 2.0 backend
│       └─ software.rs   — Software fallback
├─ infrastructure/
│   └─ systemd/
│       └─ smsly-attestation.service
├─ .github/
│   └─ workflows/
│       └─ build.yml     — CI + Cosign signing
└─ tests/
```

---

## 8. Implementation Phases

### Phase 1: Attestation Engine (separate repo)
**Effort:** 7-10 days
**Depends on:** smsly-attestation crate (exists)

Build the Rust binary with pluggable backends, Axum HTTP server, CLI key generation. TPM backend first, software fallback second. Cosign signing in CI.

### Phase 2: Infrastructure Templates
**Files:** 1-16
**Effort:** 2-3 days
**Depends on:** Nothing

Systemd units, OpenResty config, Kamailio/FreeSWITCH/RTPEngine/coturn/LiveKit configs, attestation config template.

### Phase 3: Shell Scripts
**Files:** 17, 38-41
**Effort:** 3-4 days
**Depends on:** Phase 1, Phase 2

`lib/media-node.sh` with provisioning functions. Add `--mode=media-node` to `install.sh`. TPM detection, attestation engine installation, key generation during provisioning.

### Phase 4: Django Backend
**Files:** 18-33, 34-37
**Effort:** 7-10 days
**Depends on:** Phase 3

`apps/media/` — models, views, URLs, tasks, services, admin. Telemetry service (agent-initiated). Federation service. Capacity routing. ManagedServer node_type extension.

### Phase 5: Integration Tests
**Files:** Test files
**Effort:** 3-5 days
**Depends on:** Phase 4

E2E provision test, attestation engine tests, TPM mock tests, federation scoring tests, telemetry processing tests, capacity routing tests.

---

## 9. Scalability Numbers

| Metric | Current Design | v3 Design |
|--------|---------------|-----------|
| Nodes master can manage | ~10 (sequential polling) | ~1000+ (agent-initiated) |
| Health check latency | O(n) per node | O(1) heartbeat lookup |
| Provisioning | Sequential | Parallel (Celery group) |
| Attestation verification | Centralized | Edge-local (zero latency) |
| Federation verifiers | Single bottleneck | Decentralized, independent |
| Key management | Per-node, manual rotation | Auto-rotation via provisioning |
| DB write pattern | Per-heartbeat poll | Batch flush (5 min intervals) |
| Cache hit rate | N/A | Redis-first, DB as backup |

---

*Document version: 3.0 — 2026-07-08*
*Focus: Portability (pluggable backends), Scalability (agent-initiated, edge-local, decentralized)*
