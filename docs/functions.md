# Serverless Functions

Grid's "serverless" surface is a thin serverless-style runtime on top of the standard service pipeline. Every function is a real Docker container built from a hand-rolled HTTP wrapper, hardened for untrusted code, and constrained to the public network by an SSRF guard. Functions flow through the same build / deploy / health-check pipeline as any other service — there is no separate FaaS substrate.

## Reality Check: Not a True FaaS

Grid functions are **not** AWS Lambda. They are full Docker containers wrapped in a small HTTP shim. Concretely:

- A function is a `Service` row with `deploy_type='FUNCTION'`, `function_code`, `function_runtime`, and `function_handler` set.
- The build phase emits a Dockerfile from a static template (`Node 18` or `Python 3.9`), drops the user code into `/app/`, and produces a `smsly/function-<id>` image.
- The container listens on port 8000. The HTTP shim parses the incoming request, runs the user code, captures the response, and returns it. There is no V8 isolate, no micro-VM, no warm pool.
- Cold start = container startup time. The first request to a brand-new function pays a 200–2000 ms container boot cost; subsequent requests on the same container are microseconds.
- Functions are bounded by the same `Service` resource fields (`memory_mb`, `cpu_shares`, `min_replicas`, `max_replicas`).
- Triggers are HTTP only. There is no cron, no queue, no event source.

Treat the feature as "inline code with a streamlined UI", not as a competitor to dedicated FaaS runtimes. If you need bursty scale-to-zero and sub-100 ms cold starts, run a regular service with `min_replicas=0` and let the autoscaler handle it.

## Runtimes

| Runtime | Image | Notes |
| --- | --- | --- |
| `node18` | `node:18-alpine` | Hand-rolled wrapper at `/app/smsly-function-runner.cjs` invokes the user's `handler(event, context)` and returns a 200 / 4xx / 5xx. |
| `python3.9` | `python:3.9-slim` | Wrapper at `/app/smsly_function_runner.py` invokes `handler(event, context)` and returns the response. |

The wrappers are intentionally minimal: no built-in HTTP client, no third-party packages, no env var templating. Anything the function needs must be present in the code itself (or installed at build time — see [Custom Dependencies](#custom-dependencies)). The platform's outbound `fetch` / `urllib` / `requests` / `http.client` calls are monkey-patched at startup to enforce the SSRF guard; see [Security](#security).

## Hardening

### Non-Root User

The function Dockerfile emits:

```dockerfile
RUN addgroup -S function_user && adduser -S function_user -G function_user
USER function_user
```

This means the user code runs as UID 1000-ish, not as root. A code-execution vulnerability inside the function cannot `mount`, `iptables`, or write to `/proc/sys`. It also cannot bind to port 80 (the wrapper is hard-coded to 8000). The `node18` image additionally uses `node` (UID 1000) as the runtime user.

### SSRF Guard (Outbound Network Policy)

The function runner is sandboxed against outbound network calls to internal infrastructure. The guard runs at runtime, in the same process, by monkey-patching the standard library HTTP client. It applies to:

- **Node.js** — `fetch`, `http.request`, `https.request`, and the global `http` / `https` modules.
- **Python** — `urllib.request`, `http.client`, and the `requests` library (when imported).

The blocked ranges are:

| Range | Reason |
| --- | --- |
| `127.0.0.0/8` | Loopback (Docker socket, metadata, etc.) |
| `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` | RFC 1918 private |
| `169.254.0.0/16` | Link-local / cloud metadata (`169.254.169.254` is the AWS / GCP metadata IP) |
| `100.64.0.0/10` | Carrier-grade NAT (RFC 6598) |
| `0.0.0.0/8`, `255.255.255.255/32` | Unspecified / broadcast |
| `224.0.0.0/4` | Multicast |
| `fc00::/7` | IPv6 unique-local (ULA) |
| `fe80::/10` | IPv6 link-local |
| `::1/128` | IPv6 loopback |

The check runs against the **resolved IP**, not the hostname. A function that calls `http://10-0-0-1.xip.io/` is still blocked (the resolver maps that hostname to `10.0.0.1`). The check is DNS-rebinding-aware: the resolver result is captured at request time and compared against the guard, not deferred to a separate background lookup.

The guard returns HTTP 452 (a non-standard "blocked by policy" code) for any outbound request that fails the check. The platform's `AuditLog` is **not** written for these — the function simply sees a thrown error.

### DNS Rebinding Mitigation

A naive SSRF guard that only checks the URL is bypassable via DNS rebinding: the attacker controls a DNS record that initially resolves to a public IP, then flips to `10.0.0.1` after the guard's check. Grid's guard is **resolve-then-check** (not **check-then-resolve**) and re-checks at connect time. The patched `urllib.request` and `http.client` call `socket.getaddrinfo()` first, validate every IP in the result against the blocklist, and refuse the request if any IP matches. For `fetch` (Node 18), the underlying `Agent` is monkey-patched to do the same.

### Container Security Directives

The generated Dockerfile includes:

| Directive | Value | Purpose |
| --- | --- | --- |
| `USER` | `function_user` / `node` | Non-root execution. |
| `EXPOSE` | `8000` | Single-port surface; no host networking. |
| `HEALTHCHECK` | `curl -fsS http://127.0.0.1:8000/health` every 30s | Detects wedged wrappers and triggers an auto-restart via Docker. |
| `WORKDIR` | `/app` | Read-only by default; function code is copied in via `COPY --chown=function_user:function_user`. |
| `ENV PATH` | System default | No extra `LD_PRELOAD`, no extra `PYTHONPATH` from the host. |

The platform's network policy is a **commented template** in the generated Dockerfile: `NETWORK_MODE=bridge` with all outbound blocked except the configured egress allowlist. The default egress allowlist is empty (everything blocked). Operators who want to permit specific destinations add them to the `FUNCTION_EGRESS_ALLOWLIST` env var (comma-separated hostnames); everything else remains blocked. The allowlist is applied at the `docker network` / iptables layer, not inside the container.

## Limits

| Limit | Default | Source |
| --- | --- | --- |
| Code size | 256 KB | `MAX_FUNCTION_CODE_BYTES` (env, default `262144`) |
| Request body size | 1 MB | `MAX_FUNCTION_BODY_BYTES` (env, default `1048576`) |
| Execution time | 30 s | `FUNCTION_TIMEOUT_SECONDS` (env, default `30`) |
| Memory | inherits `Service.memory_mb` | same field as a regular service |
| Concurrency | inherits `Service.min_replicas`/`max_replicas` | one request per container, no in-process queue |

A function that exceeds the execution timeout returns HTTP 504 (`Gateway Timeout`) and the container is recycled. A function that exceeds the body limit returns HTTP 413 (`Payload Too Large`) without invoking the user code.

## Triggers

The only trigger is HTTP. There is **no** built-in cron, **no** queue subscriber, **no** event source. The endpoint URL is:

```
https://<service.public_domain>/fn/<function_name>
```

For example, a service with `function_name='hello'` exposes:

```
POST https://hello.example.com/fn/hello
```

The HTTP method on the request becomes the HTTP method on the wrapper. The body is JSON-decoded and passed as `event.body`; query string is `event.queryStringParameters`; headers are `event.headers` (with `Host` and `Content-Length` removed for size). The function's return value is JSON-serialized with status code 200 by default; the user can override by returning `{statusCode, headers, body}`.

If you need scheduled invocation, point an external cron (GitHub Actions, system cron, Cloudflare Workers cron) at the function URL with an empty POST body. The platform does not provide a built-in scheduler for functions.

## Custom Dependencies

The standard buildpacks (`npm install`, `pip install`) are **not** used. If the function needs third-party packages, the user is expected to inline them in the function code (e.g. `node_modules` checked in for Node, vendored `.whl` for Python). This is intentional: the function surface is supposed to be small and audit-friendly.

If you need `npm` dependencies, the recommended pattern is to ship a tarball with `package.json` + `node_modules/` pre-installed. The wrapper invokes `node` directly with `--no-deprecation` to suppress noise.

## API Reference

Function endpoints live under `/api/v1/services/`. Functions are created, updated, and deployed via the same endpoints as a regular service, with `deploy_type='FUNCTION'`.

### `POST /api/v1/services/` (create function)

Create a new service with `deploy_type='FUNCTION'`. The first deployment is auto-triggered on create.

**Request body (FUNCTION-specific fields):**

| Field | Type | Notes |
| --- | --- | --- |
| `name` | string | Required. The service name; also used as the function's `function_name`. |
| `function_runtime` | `node18` \| `python3.9` | Required when `deploy_type=FUNCTION`. |
| `function_code` | string | Required. Inline source. Must be ≤ 256 KB. |
| `function_handler` | string | Optional. Defaults to `handler`. |
| `public_domain` | string | Optional. The function's public hostname. |
| `memory_mb` | int | Optional. Default 256. |

**Example request:**

```bash
curl -sS -X POST http://localhost:8000/api/v1/services/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hello",
    "deploy_type": "FUNCTION",
    "function_runtime": "node18",
    "function_code": "module.exports.handler = async (event) => ({ statusCode: 200, body: \"hello, \" + (event.queryStringParameters?.name || \"world\") });",
    "public_domain": "hello.example.com"
  }'
```

**Example response (HTTP 201):**

```json
{
  "id": "9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21",
  "name": "hello",
  "deploy_type": "FUNCTION",
  "function_runtime": "node18",
  "public_domain": "hello.example.com",
  "status": "QUEUED",
  "deployment_id": "2d3e4f5a-6b7c-8d9e-0f1a-2b3c4d5e6f7a"
}
```

**Error responses:**

| Status | Cause |
| --- | --- |
| 400 | `function_code` > 256 KB, invalid `function_runtime`, missing required field. |
| 403 | `deploy_type=FUNCTION` requires `FUNCTIONS_ENABLED=True` in platform `.env`. |

### `PATCH /api/v1/services/{id}/`

Update the function code or runtime. The patch auto-triggers a fresh deployment.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `function_code` | string | New source. |
| `function_runtime` | string | Switch between `node18` and `python3.9`. |
| `function_handler` | string | New handler name. |

```bash
curl -sS -X PATCH http://localhost:8000/api/v1/services/9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"function_code": "module.exports.handler = async () => ({ statusCode: 200, body: \"v2\" });"}'
```

### `POST /api/v1/services/{id}/invoke/`

Synchronous invocation endpoint. The function runs **on the controller**, not on the deployed container — useful for testing, cron-style invocation, or admin operations. The response is the function's return value, exactly as the deployed container would have produced it.

**Request body:**

| Field | Type | Notes |
| --- | --- | --- |
| `event` | object | The event payload. JSON-decoded and passed as `event`. |
| `timeout_seconds` | int | Optional. Default 30, max 300. |

```bash
curl -sS -X POST http://localhost:8000/api/v1/services/9c8b4b1a-7d1c-4a2b-9a55-2e8c3d4f9b21/invoke/ \
  -H "Authorization: Token $SMSLY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"event": {"queryStringParameters": {"name": "alice"}}}'
```

**Example response:**

```json
{
  "statusCode": 200,
  "headers": {"content-type": "application/json"},
  "body": "hello, alice"
}
```

This endpoint is throttled to 60 calls/minute per user (`FunctionInvokeRateThrottle`).

## Security

### Outbound Network Policy

The SSRF guard described above is the only egress control. There is no separate firewall. Operators who want a stronger guarantee should add a network policy at the cluster level (Calico, Cilium) and refuse to run Grid on a network where the SSRF guard's blocklist is insufficient.

The blocklist is **not** configurable. If you need to allow `10.0.0.0/8` (e.g. internal services), the function surface is not the right tool — deploy a regular service and use a private add-on instead.

### Encrypted Code at Rest

`Service.function_code` is stored as a regular `TextField` (not encrypted). The reasoning: the code is runnable, so the operator can read it. There is no secret material in the function code by policy. If the code includes secrets (which it should not), use a regular service with `EnvironmentVariable` and `is_secret=True` instead.

### API Key Management

Functions inherit `Service.env_vars` (with the same `is_secret` masking and Fernet encryption). The standard precedence rules apply — see [docs/deployments.md](deployments.md#environment-variables).

### No Cross-Tenant Data

The function runs in its own container with no shared filesystem. It cannot read other services' volumes, addons, or backups. The platform's database connection is also inaccessible.

## Troubleshooting

### "Function code exceeds 256 KB"

The `function_code` field is capped at 256 KB. Move large assets out of the function (use a static service, or fetch them at runtime from a CDN).

### "SSRF guard blocked outbound request to 10.x.x.x"

The function tried to call an internal IP. The guard is intentional and not configurable. If you need to call internal services, deploy a regular service and put it on the same Docker network as the target.

### "Execution timed out after 30s"

Raise `FUNCTION_TIMEOUT_SECONDS` in the platform `.env` (max 300), or refactor the function to return early. Long-running tasks belong in a worker service, not a function.

### "Function runs in `invoke/` but returns 504 in production"

The deployed container is OOM-killed or CPU-throttled. Check `Service.memory_mb` and `cpu_shares`. The function's `HEALTHCHECK` will also have flipped to `unhealthy` — check the deployment's health-check phase.

### "Health check returns 200 but the function returns 502"

The wrapper's `/health` endpoint does not invoke the user code. A 200 on `/health` only means the wrapper is alive. Test the function with `POST /api/v1/services/{id}/invoke/` to see the actual error.

### "Function works on one replica but not another"

`Service.min_replicas > 1` and a stale container is serving the request. Roll the deployment, or set `min_replicas=1` and let the autoscaler scale up.

## Limitations

- **No warm pool.** Cold start is container startup. Expect 200–2000 ms for the first request.
- **No triggers beyond HTTP.** No cron, no queue, no event source.
- **No native package management.** Third-party deps must be vendored.
- **256 KB code cap.** Large functions must be split.
- **30s default timeout.** Long tasks belong in a worker.
- **No VPC / private network access.** Egress is restricted to public internet only.
- **No cold-start pricing.** Functions consume the same per-container resources as a regular service. They are not billed per-invocation.
- **No streaming responses.** The wrapper buffers the full response and returns it as a single body. SSE / chunked responses are not supported.
- **No durable state.** Containers can be recycled at any time. Persistent state must live in an add-on.
- **No native observability.** Function logs are visible in `Deployment.build_logs` / runtime logs (the same as a regular service). There is no per-invocation trace ID.
- **No local file system durability.** Writes inside the container are lost on restart. Use a volume or an add-on.
