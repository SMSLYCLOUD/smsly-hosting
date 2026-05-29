# Firecracker MicroVM (FVM) Migration Guide

This guide explains how to migrate existing Docker-based service workloads to Firecracker MicroVMs.

## Prerequisites
- The host must have the `smsly-fvm` bridge network created (handled automatically by docker-compose).
- KVM must be enabled on the host machine.
- The `FVM_KERNEL_PATH` must point to a valid uncompressed linux kernel (e.g., `vmlinux`), or auto-download will be used.

## Enabling FVM for a Service

The runtime selection is controlled per-service in the database. By default, all services run on `docker`.

To migrate a service to Firecracker, update the `runtime` field on the `Service` model:

1. **Django Shell / Admin**
   - Locate the service in the admin panel or Django shell.
   - Change `runtime` from `"docker"` to `"firecracker"`.

2. **Trigger a Redeployment**
   - The change takes effect on the next deployment.
   - Navigate to the service dashboard and click **Deploy**.
   - The pipeline will automatically build the ext4 rootfs instead of an OCI container image.
   - The service will boot as a Firecracker VM.
   - Traefik routing will seamlessly redirect traffic to the new VM IP.

## Rollback

If a workload misbehaves in the MicroVM environment, you can instantly rollback:

1. Change `runtime` back to `"docker"`.
2. Trigger a redeployment.
3. The platform will pull the standard OCI image and launch it as a Docker container.
