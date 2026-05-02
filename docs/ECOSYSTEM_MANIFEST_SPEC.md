# Ecosystem Manifest Specification

The `cloudneuron.ecosystem.yml` manifest allows CloudNeuron to deploy complex interconnected services reliably by defining all dependencies, addons, and environment variables upfront.

## Format Reference

```yaml
version: "1"
name: "generic-ecosystem"
mode: "production"

addons:
  postgres:
    type: postgres
    required: true
  redis:
    type: redis
    required: false
  rabbitmq:
    type: rabbitmq
    required: false

shared_env:
  groups:
    core:
      vars:
        JWT_SECRET:
          secret: true
          source: generated
          min_length: 48
        INTERNAL_SERVICE_TOKEN:
          secret: true
          source: generated
          min_length: 48

services:
  api:
    type: backend_api
    runtime: python
    framework: django
    public: true
    build:
      command: ""
    start:
      command: ""
    health:
      path: /health
    dependencies:
      - postgres
      - redis
    env:
      DATABASE_URL:
        source: addon
        addon: postgres
        required: true
        secret: true
      REDIS_URL:
        source: addon
        addon: redis
        required: false
        secret: true

  web:
    type: frontend
    runtime: node
    framework: nextjs
    public: true
    dependencies:
      - api
    env:
      NEXT_PUBLIC_API_URL:
        source: service_public_url
        service: api
        required: true
        public: true
```

## Schema Details

- `addons`: Managed services that CloudNeuron provisions.
- `shared_env`: Environment variables that are synced identically across dependent services.
- `services`: The components of the ecosystem.
  - `dependencies`: Defines the deployment order and linkage.
  - `env`: Service-specific environment contracts, defining where the value comes from (`addon`, `service_public_url`, `generated`, `external_required`, etc.).
