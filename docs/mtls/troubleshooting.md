# mTLS Troubleshooting

## Common Issues

### 1. "SPIFFE cert not found" Error

**Symptom**: Application fails to start with `FileNotFoundError: SPIFFE cert not found at /opt/spire/svids/cert.pem`

**Cause**: SPIRE agent hasn't issued SVIDs yet, or the volume mount is missing.

**Fix**:
```bash
# Check if SPIRE agent is running
docker ps | grep spire-agent

# Check if SVIDs exist in the container
docker exec <container-name> ls -la /opt/spire/svids/

# Check SPIRE agent logs
docker logs smsly-spire-agent
```

### 2. "Invalid SPIFFE trust domain" Error

**Symptom**: Service rejects requests with "Invalid SPIFFE trust domain"

**Cause**: The calling service has a SPIFFE ID from a different trust domain than expected.

**Fix**: Ensure all services use the same `SPIFFE_TRUST_DOMAIN` value.

### 3. "Caller not authorized" Error

**Symptom**: Service rejects requests with "Caller not authorized"

**Cause**: The calling service's SPIFFE ID is not in the allowed callers list.

**Fix**: Add the caller's SPIFFE path to the allowed callers configuration.

### 4. Certificate Expired

**Symptom**: TLS handshake fails with certificate expired error

**Cause**: SPIRE agent failed to rotate the certificate.

**Fix**:
```bash
# Check SPIRE agent health
docker exec smsly-spire-agent /opt/spire/bin/spire-agent healthcheck \
  -socketPath /opt/spire/run/agent.sock

# Force certificate rotation by restarting the agent
docker restart smsly-spire-agent
```

### 5. SPIRE Server Not Reachable

**Symptom**: SPIRE agent can't connect to server

**Fix**:
```bash
# Check SPIRE server health
docker exec smsly-spire-server /opt/spire/bin/spire-server healthcheck \
  -socketPath /opt/spire/data/server.sock

# Check network connectivity
docker exec smsly-spire-agent ping spire-server
```

## Health Checks

### SPIRE Server
```bash
curl http://localhost:8080/live   # Liveness
curl http://localhost:8080/ready  # Readiness
```

### SPIRE Agent
```bash
docker exec smsly-spire-agent /opt/spire/bin/spire-agent healthcheck \
  -socketPath /opt/spire/run/agent.sock
```

### Verify mTLS is Working
```bash
# From inside a service container:
curl --cert /opt/spire/svids/cert.pem \
     --key /opt/spire/svids/key.pem \
     --cacert /opt/spire/svids/bundle.pem \
     https://other-service:8000/health
```
