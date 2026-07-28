# SPIFFE mTLS for smsly-hosting

## Overview

smsly-hosting provides automatic **mTLS (mutual TLS)** for all deployed services using [SPIFFE](https://spiffe.io/) and [SPIRE](https://github.com/spiffe/spire). This means every service deployed on the platform automatically gets:

- **A cryptographic identity** (X.509 certificate with SPIFFE ID)
- **Automatic encryption** (TLS 1.2+ on all inter-service communication)
- **Automatic rotation** (certificates rotate every hour, no manual intervention)
- **Zero shared secrets** (no HMAC keys, no API keys for service-to-service auth)

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│                    smsly-hosting Platform                    │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              SPIRE Server (Infrastructure)           │   │
│  │  • Issues X.509 certificates to services            │   │
│  │  • Manages trust bundle (root CA)                   │   │
│  │  • Configurable trust domain                        │   │
│  └───────────────────────┬─────────────────────────────┘   │
│                          │                                  │
│  ┌───────────────────────┴─────────────────────────────┐   │
│  │              SPIRE Agent (Per Node)                  │   │
│  │  • Attests workloads via Docker labels              │   │
│  │  • Delivers SVIDs via Unix socket                   │   │
│  │  • Auto-rotates certificates                        │   │
│  └───────────────────────┬─────────────────────────────┘   │
│                          │                                  │
│         ┌────────────────┼────────────────┐                │
│         ▼                ▼                ▼                 │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐           │
│  │ Service A│     │ Service B│     │ Service C│           │
│  │ (your    │◄───►│ (other   │◄───►│ (any     │           │
│  │  app)    │ mTLS│  tenant) │ mTLS│  container│          │
│  └──────────┘     └──────────┘     └──────────┘           │
│                                                             │
│  Each service has:                                          │
│  • /opt/spire/svids/cert.pem   (X.509 certificate)        │
│  • /opt/spire/svids/key.pem    (private key)               │
│  • /opt/spire/svids/bundle.pem (trust bundle)              │
└─────────────────────────────────────────────────────────────┘
```

## Getting Started

### Automatic (Default)

Every service deployed on smsly-hosting automatically gets mTLS enabled. No configuration needed.

Your service receives:
- **Environment variables**: `SPIFFE_TRUST_DOMAIN`, `SPIFFE_SVID_CERT_PATH`, etc.
- **Volume mounts**: SVID certificates at `/opt/spire/svids/`
- **Unix socket**: SPIRE agent at `/opt/spire/run/agent.sock`

### Using mTLS in Your Application

**Python (requests)**:
```python
import os
import ssl

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.load_cert_chain(
    certfile=os.getenv("SPIFFE_SVID_CERT_PATH"),
    keyfile=os.getenv("SPIFFE_SVID_KEY_PATH"),
)
ctx.load_verify_locations(cafile=os.getenv("SPIFFE_BUNDLE_PATH"))
ctx.check_hostname = False

import requests
resp = requests.get("https://other-service:8000/api/data", verify=ctx)
```

**Python (httpx)**:
```python
from spiffe_mtls import create_mtls_client

client = create_mtls_client()
resp = await client.get("https://other-service:8000/api/data")
```

**Node.js**:
```javascript
const fs = require('fs');
const https = require('https');

const options = {
  cert: fs.readFileSync(process.env.SPIFFE_SVID_CERT_PATH),
  key: fs.readFileSync(process.env.SPIFFE_SVID_KEY_PATH),
  ca: fs.readFileSync(process.env.SPIFFE_BUNDLE_PATH),
};

https.get('https://other-service:8000/api/data', options, (res) => {
  // handle response
});
```

**Go**:
```go
import "crypto/tls"

cert, _ := tls.LoadX509KeyPair(
    os.Getenv("SPIFFE_SVID_CERT_PATH"),
    os.Getenv("SPIFFE_SVID_KEY_PATH"),
)
caCert, _ := os.ReadFile(os.Getenv("SPIFFE_BUNDLE_PATH"))
caCertPool := x509.NewCertPool()
caCertPool.AppendCertsFromPEM(caCert)

client := &http.Client{
    Transport: &http.Transport{
        TLSClientConfig: &tls.Config{
            Certificates: []tls.Certificate{cert},
            RootCAs:      caCertPool,
        },
    },
}
```

**Any Language**: Just use the file-based SVIDs (`cert.pem`, `key.pem`, `bundle.pem`) with your language's TLS/SSL library.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MTLS_ENABLED` | `true` | Enable/disable mTLS for this service |
| `SPIFFE_TRUST_DOMAIN` | `platform.local` | Trust domain for SPIFFE IDs |
| `SPIFFE_SVID_CERT_PATH` | `/opt/spire/svids/cert.pem` | Path to X.509 certificate |
| `SPIFFE_SVID_KEY_PATH` | `/opt/spire/svids/key.pem` | Path to private key |
| `SPIFFE_BUNDLE_PATH` | `/opt/spire/svids/bundle.pem` | Path to trust bundle |
| `SPIFFE_ENDPOINT_SOCKET` | `/opt/spire/run/agent.sock` | SPIRE agent socket |

### Disabling mTLS

Set `MTLS_ENABLED=false` in your service's environment variables. The SPIRE socket will still be mounted but your service won't use it.

## SPIFFE IDs

Each service gets a SPIFFE ID in the format:
```
spiffe://<trust-domain>/service/<app-name>
```

Example: `spiffe://platform.local/service/my-api`

## Troubleshooting

See [troubleshooting.md](./troubleshooting.md).
