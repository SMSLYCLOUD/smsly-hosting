# Ecosystem Environment Resolver

Grid uses a strict environment resolver (`EcosystemEnvResolver`) to compile the final set of environment variables for every service in an ecosystem before deployment begins.

## Key Features

1. **Weak Value Rejection**: In production mode, weak placeholders (e.g., `changeme`, `secret`, `test`) are explicitly rejected.
2. **Missing Required Value Guard**: If a required value (such as an external API key) is missing, the resolver flags an error and the deployment is blocked.
3. **Shared Environment Groups**: Generates and syncs shared secrets across multiple services uniformly.
4. **Service Graph Awareness**: The resolver uses the ecosystem graph to inject dependent service URLs and managed addon URIs correctly.
