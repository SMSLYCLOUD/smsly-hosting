# SMSLY Hosting Rules (Deployment / Orchestration)
# These rules apply to the `smsly-hosting` repository.

---

## §1. TECH STACK (CRITICAL)

- **Framework:** Django 5.x (Orchestrator)
- **Engine:** Docker & Docker Compose
- **Builder:** Nixpacks
- **Target:** Ubuntu VPS
- **Protocol:** SSH / Websockets

---

## §2. ORCHESTRATION SAFETY

1. **Idempotency:** Deployment scripts must be re-runnable without side effects.
2. **Rollbacks:** Every deployment must have a rollback plan (e.g., previous image tag).
3. **Timeouts:** All remote commands (SSH) must have explicit timeouts.
4. **Concurrency:** Use atomic DB locks when modifying deployment state.

---

## §3. DOCKER & CONTAINERS

1. **Socket Security:** NEVER mount `/var/run/docker.sock` directly. Use `tecnativa/docker-socket-proxy`.
2. **User:** Run containers as non-root (`USER 1000`) where possible.
3. **Ports:** Never expose internal ports (DB, Redis) to the host public IP.
4. **Healthchecks:** Every service must have a Docker `HEALTHCHECK`.
5. **Volumes:** Use named volumes for persistent data.

---

## §4. LOGGING & OBSERVABILITY

1. **Real-time:** Stream build logs via WebSockets (Channels).
2. **Persistence:** Store logs in file/DB for audit.
3. **Format:** Use `%s` lazy formatting for Python logs.
4. **Sensitive Data:** Mask secrets in logs (API keys, passwords).

---

## §5. TESTING

1. **Integration:** Test the full `clone -> build -> push -> deploy` pipeline in staging.
2. **Mocking:** Mock Docker/SSH calls in unit tests.
3. **Failure:** Test failure scenarios (build fail, SSH timeout, container crash).

---

## §6. VERIFICATION

Before pushing:
- [ ] `python manage.py check`
- [ ] `python manage.py test`
- [ ] Verify `docker-compose.yml` config
- [ ] Check `install.sh` against a fresh VM if modified
