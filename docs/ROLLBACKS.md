# Rollbacks

## Initiating a Rollback
A rollback acts as a completely new deployment pointing to the previously successful commit hash or image tag.

- Rollbacks enforce explicit confirmation via API (`confirm=True`).
- A rollback tracks its source deployment via the `rollback_from` foreign key.
- Rollbacks bypass review and are marked `is_rollback=True`.
