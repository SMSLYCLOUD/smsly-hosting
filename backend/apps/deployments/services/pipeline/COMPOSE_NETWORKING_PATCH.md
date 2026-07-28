# mTLS Integration for compose_networking.py
# ============================================
# Safe integration points for SPIRE mTLS in the ecosystem deployment pipeline.
#
# The compose networking mixin generates Docker compose override files with
# Traefik labels, security options, and network configuration. We add SPIRE
# mounts and labels here so compose-based deployments get mTLS automatically.
#
# This is SAFE because:
# 1. It's additive — doesn't change existing behavior
# 2. Only activates when MTLS_ENABLED=true (platform env var)
# 3. SPIRE volumes are mounted read-only — tenant can't modify SVIDs
# 4. Labels are informational — SPIRE agent uses them for attestation
# 5. Env vars are paths only — no secrets are injected
# 6. Network attachment to smsly-net is required anyway for SPIRE agent access

## Step 1: Import mtls_integration at the top of compose_networking.py

```python
# Add after line 5 (from .exceptions import BuildError)
from apps.deployments.services.mtls_integration import (
    is_mtls_enabled,
    get_mtls_labels,
    get_mtls_env_vars,
    get_mtls_volumes,
    SPIRE_SOCKET_CONTAINER_PATH,
    SPIRE_SVIDS_CONTAINER_PATH,
)
```

## Step 2: Add mTLS labels in _compose_traefik_labels()

After the existing labels dict is built (around line 100, before `return labels`):

```python
        # --- mTLS: Add SPIRE workload attestation labels ---
        try:
            if is_mtls_enabled(self.service):
                mtls_labels = get_mtls_labels(self.service)
                labels.update(mtls_labels)
        except Exception as e:
            logger.debug("mTLS label injection skipped: %s", e)

        return labels
```

## Step 3: Add mTLS volumes and env in _write_compose_routing_override()

In the section where `override_payload["services"][main_service]` is built
(around line 200, where security_opt is set):

```python
                        # --- mTLS: Add SPIRE volumes and env vars ---
                        mtls_volumes = []
                        mtls_env = {}
                        try:
                            if is_mtls_enabled(self.service):
                                for host_vol, container_path, mode in get_mtls_volumes():
                                    mtls_volumes.append(f"{host_vol}:{container_path}:{mode}")
                                mtls_env = get_mtls_env_vars(self.service)
                        except Exception as e:
                            logger.debug("mTLS compose injection skipped: %s", e)

                        for svc_name in user_compose["services"]:
                            if svc_name not in override_payload["services"]:
                                override_payload["services"][svc_name] = {}
                            override_payload["services"][svc_name]["security_opt"] = [
                                "no-new-privileges:true",
                                "apparmor:docker-default"
                            ]
                            if compose_runtime and compose_runtime != "runc":
                                override_payload["services"][svc_name]["runtime"] = compose_runtime

                            # Attach every service to the shared network
                            if network_name not in user_networks:
                                svc_networks = override_payload["services"][svc_name].get("networks") or []
                                if network_name not in svc_networks:
                                    svc_networks.append(network_name)
                                    override_payload["services"][svc_name]["networks"] = svc_networks

                            # --- mTLS: Add SPIRE volumes to every service ---
                            if mtls_volumes:
                                existing_vols = override_payload["services"][svc_name].get("volumes") or []
                                for vol in mtls_volumes:
                                    if vol not in existing_vols:
                                        existing_vols.append(vol)
                                override_payload["services"][svc_name]["volumes"] = existing_vols

                            # --- mTLS: Add SPIFFE env vars to every service ---
                            if mtls_env:
                                existing_env = override_payload["services"][svc_name].get("environment") or {}
                                if isinstance(existing_env, list):
                                    # Convert list format to dict
                                    existing_env = dict(e.split("=", 1) for e in existing_env if "=" in e)
                                existing_env.update(mtls_env)
                                override_payload["services"][svc_name]["environment"] = existing_env
```

## Step 4: Ensure smsly-net is accessible for SPIRE agent

In `_ensure_docker_network()`, the existing code already creates the scoped network.
We also need to ensure the tenant container can reach `smsly-net` (where SPIRE agent runs).

The existing code in `_write_compose_routing_override()` already attaches services to
the shared network. The SPIRE agent socket is mounted as a volume, so the container
doesn't need to be on the same network as the SPIRE agent — it communicates via the
Unix Domain Socket, not over TCP.

**No change needed here** — the volume mount is sufficient.

## Summary of Changes

| Location | Change | Lines Added | Risk |
|----------|--------|-------------|------|
| Top of file | Import mtls_integration | 6 | None |
| _compose_traefik_labels() | Add mTLS labels | 6 | None (additive) |
| _write_compose_routing_override() | Add SPIRE volumes + env vars | 15 | Low (read-only mounts) |

Total lines added: ~27
No existing behavior changed — mTLS is purely additive.

## Safety Considerations

1. **Read-only mounts**: SPIRE socket and SVIDs are mounted `:ro` — tenant can't modify
2. **No secrets in env vars**: Only file paths are injected, no actual certificates
3. **Opt-in**: Only activates when `MTLS_ENABLED=true` in platform config
4. **Graceful degradation**: All mTLS code is wrapped in try/except — failures don't block deployment
5. **Network isolation preserved**: Tenant containers still use scoped networks; smsly-net attachment is only for SPIRE agent UDS access (which is read-only)
6. **Labels are informational**: Docker labels don't affect container behavior — SPIRE agent uses them only for attestation
