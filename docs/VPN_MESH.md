# VPN Mesh

WireGuard peer-to-peer networks are established via `MeshNetwork` and `WireGuardPeer`.

## Security
- Configuration strings are base64-encoded to prevent shell injection via the CLI.
- Docker and SSH CLI invocations use strict argument quoting (`shlex`) and `shell=False`.
- The system automatically allocates unused IP subsets and synchronizes keys remotely.
