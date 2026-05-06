# Ecosystem Deploy Repair Runbook

If an ecosystem deployment fails (e.g. due to node capacity constraints or transient env resolver issues), Grid provides a Django management command to repair the ecosystem from its known Grid-side state.

## The Command

`python manage.py repair_ecosystem_deploy --project <project_id> [--dry-run | --apply]`

### What It Does
1. **Scans Project Services**: Loads all services belonging to the given ecosystem/project.
2. **Assigns Missing Nodes**: If a service was created but not assigned to a node, it queries the `node_selector` to assign an eligible `ManagedServer`.
3. **Persists Env Vars**: Uses the `bulk_persist_and_verify_ecosystem_env` logic to securely resolve, sync, and persist missing or failed environment variables.
4. **Re-queues Deployments**: Locates `FAILED` deployment records and resets them to `QUEUED`.

### Usage example

**Dry Run:**
```bash
python manage.py repair_ecosystem_deploy --project "b2f6..." --dry-run
```

**Apply:**
```bash
python manage.py repair_ecosystem_deploy --project "b2f6..." --apply
```
