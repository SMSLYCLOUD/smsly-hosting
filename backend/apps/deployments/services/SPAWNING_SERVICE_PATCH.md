# mTLS Integration Patch for spawning_service.py
# =================================================
# Apply these changes to `backend/apps/deployments/services/spawning_service.py`
# to enable automatic mTLS for all tenant services.
#
# Changes are minimal — just import the helper and add it to the container config.

## Step 1: Add import at the top of the file (after existing imports)

```python
# Add after line 10 (from .ssh_client import SSHClient)
from .mtls_integration import (
    get_mtls_labels,
    get_mtls_env_vars,
    get_mtls_docker_run_args,
    get_mtls_docker_run_volumes,
)
```

## Step 2: In spawn() method — add mTLS labels and env vars

### After the labels list is built (around line 137, after `label_args`):

```python
        # --- mTLS integration ---
        mtls_labels = get_mtls_labels(service)
        for k, v in mtls_labels.items():
            labels.append(f"{k}={v}")
        label_args = " ".join(f"-l {shlex.quote(label)}" for label in labels)
```

### After env_args is built (around line 145):

```python
        # --- mTLS SPIFFE env vars ---
        mtls_env = get_mtls_env_vars(service)
        for k, v in mtls_env.items():
            env_args += f" -e {shlex.quote(k)}={shlex.quote(v)}"
```

### In the docker run command (around line 200), add volume mounts and network:

```python
        # --- mTLS volume mounts ---
        mtls_volumes = get_mtls_docker_run_args(service)

        cmd = (
            f"{login_cmd}"
            f"docker pull {shlex.quote(image)} 2>/dev/null; "
            f"docker rm -f {shlex.quote(name)} 2>/dev/null; "
            f"docker run -d --name {shlex.quote(name)} "
            f"{sec_flags}"
            f"{runtime_flag} "
            f"--restart unless-stopped --network {shlex.quote(net)} "
            f"{mtls_volumes} "  # <-- ADD THIS LINE
            f"{label_args} {env_args} "
            f"{shlex.quote(image)}; "
        )
```

## Step 3: In spawn_local() method — add mTLS config

### After labels dict is built (around line 280):

```python
        # --- mTLS integration ---
        labels.update(get_mtls_labels(service))
```

### After env_vars is built (around line 295):

```python
        # --- mTLS SPIFFE env vars ---
        env_vars.update(get_mtls_env_vars(service))
```

### In the client.containers.run() call (around line 300):

```python
        # --- mTLS volume mounts ---
        mtls_volumes = get_mtls_docker_run_volumes(service)

        container = client.containers.run(
            image=image,
            name=name,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            network=net,
            labels=labels,
            environment=env_vars,
            volumes=mtls_volumes,  # <-- ADD THIS LINE
            security_opt=["no-new-privileges:true", "apparmor=docker-default"],
            # ... rest of args
        )
```

## Step 4: Add smsly-net as secondary network

After the container is created, add it to smsly-net so it can reach the SPIRE agent:

```python
        # --- mTLS: connect to smsly-net for SPIRE agent access ---
        if is_mtls_enabled(service):
            try:
                smsly_net = client.networks.get("smsly-net")
                smsly_net.connect(container)
            except Exception as e:
                logger.warning("Failed to connect container to smsly-net for mTLS: %s", e)
```

## Summary of Changes

| Location | Change | Purpose |
|----------|--------|---------|
| Top of file | Import mtls_integration | Access mTLS helpers |
| spawn() labels | Add SPIRE Docker labels | Workload attestation |
| spawn() env_args | Add SPIFFE env vars | SVID path config |
| spawn() docker run | Add -v mounts | Mount SPIRE socket + SVIDs |
| spawn_local() labels | Add SPIRE Docker labels | Workload attestation |
| spawn_local() env_vars | Add SPIFFE env vars | SVID path config |
| spawn_local() volumes | Add volume mounts | Mount SPIRE socket + SVIDs |
| spawn_local() post-create | Connect to smsly-net | Reach SPIRE agent |

Total lines added: ~20
No existing behavior changed — mTLS is additive.
