import logging
import shlex

from apps.deployments.models import (
    Service,
)

from ..ssh_client import SSHClient

logger = logging.getLogger(__name__)


class DeletionMixin:
    def delete_service(
        self,
        remote_service_id: str,
        *,
        force: bool = False,
        not_found_ok: bool = True,
    ) -> bool:
        path = f"/api/v1/services/{remote_service_id}/"
        params = {"force": "true"} if force else None

        try:
            resp = self._request("DELETE", path, params=params, timeout=20)
            if resp and resp.status_code in (202, 204, 200):
                return True
            if resp and resp.status_code == 404 and not_found_ok:
                return True
            if resp is not None:
                self._set_last_error("Failed to delete service on remote.", response=resp)
        except Exception as e:
            self._set_last_error(f"Error deleting service on remote: {e}")
            logger.error(self.last_error)
        return False

    def delete_service_for_local(self, service: Service, *, force: bool = False) -> bool:
        remote_service_id = self._search_remote_service(service, "/api/v1/services/")
        api_deleted = False

        if remote_service_id:
            api_deleted = self.delete_service(
                remote_service_id,
                force=force,
                not_found_ok=True,
            )
        elif remote_service_id == "":
            api_deleted = self.delete_service(
                str(service.id),
                force=force,
                not_found_ok=False,
            )

        ssh_deleted = False
        if not api_deleted or getattr(service, "active_runtime_id", None):
            ssh_deleted = self.delete_service_runtime_via_ssh(service)

        return bool(api_deleted or ssh_deleted or force)

    def delete_service_runtime_via_ssh(self, service: Service) -> bool:
        if not (self.server.ssh_key or self.server.ssh_password):
            self._set_last_error(
                "Remote service API deletion did not complete and no SSH credentials are stored for fallback cleanup."
            )
            return False

        identifiers = []
        for raw in (
            getattr(service, "active_runtime_id", None),
            getattr(service, "name", None),
            getattr(service, "slug", None),
        ):
            value = str(raw or "").strip()
            if value and value not in identifiers:
                identifiers.append(value)

        service_id = str(getattr(service, "id", "") or "").strip()
        service_name = str(getattr(service, "name", "") or "").strip()
        service_slug = str(getattr(service, "slug", "") or "").strip()
        label_filters = []
        if service_id:
            label_filters.append(f"smsly.service_id={service_id}")
        if service_name:
            label_filters.append(f"smsly.blue_green.canonical_name={service_name}")
        if service_slug:
            label_filters.append(f"com.docker.compose.project={service_slug}")

        remove_exact = " ".join(shlex.quote(value) for value in identifiers)
        label_args = " ".join(shlex.quote(value) for value in label_filters)
        green_prefixes = " ".join(
            shlex.quote(f"{value}-green-")
            for value in (service_name, service_slug)
            if value
        )
        volume_label_args = label_args

        script = f"""
set +e
removed=0
failed=0
for ref in {remove_exact}; do
  [ -n "$ref" ] || continue
  if docker inspect "$ref" >/dev/null 2>&1; then
    docker rm -f "$ref" >/dev/null 2>&1 && removed=1 || failed=1
  fi
done
for label in {label_args}; do
  [ -n "$label" ] || continue
  for cid in $(docker ps -aq --filter "label=$label"); do
    docker rm -f "$cid" >/dev/null 2>&1 && removed=1 || failed=1
  done
done
for prefix in {green_prefixes}; do
  [ -n "$prefix" ] || continue
  for cid in $(docker ps -aq --filter "name=^/${{prefix}}"); do
    docker rm -f "$cid" >/dev/null 2>&1 && removed=1 || failed=1
  done
done
for label in {volume_label_args}; do
  [ -n "$label" ] || continue
  for vid in $(docker volume ls -q --filter "label=$label"); do
    docker volume rm -f "$vid" >/dev/null 2>&1 || true
  done
done
if [ "$failed" -eq 1 ]; then
  echo SMSLY_DELETE_FAILED
  exit 1
fi
if [ "$removed" -eq 1 ]; then
  echo SMSLY_DELETE_REMOVED
else
  echo SMSLY_DELETE_NOT_FOUND
fi
exit 0
""".strip()

        ssh = SSHClient(
            ip=self.server.host,
            key_content=self.server.ssh_key,
            password=self.server.ssh_password,
            user=self.server.ssh_user,
            port=self.server.ssh_port,
            wg_address=self.server.wg_address,
        )
        try:
            ssh.connect()
            argv = shlex.split(f"sh -lc {shlex.quote(script)}")
            out, err, code = ssh.exec_command(
                argv,
                timeout=60,
                raise_on_error=False,
            )
            output = f"{out}\n{err}".strip()
            if code == 0:
                if "SMSLY_DELETE_REMOVED" not in output:
                    self._set_last_error(
                        f"SSH runtime cleanup found no matching containers on {self.server.host}: {output[:500]}"
                    )
                    logger.warning(self.last_error)
                    return False
                logger.info(
                    "SSH runtime cleanup for service %s on %s completed: %s",
                    service.name, self.server.host, output[:500],
                )
                return True
            self._set_last_error(
                f"SSH runtime cleanup failed on {self.server.host}: {output[:500]}"
            )
            logger.error(self.last_error)
            return False
        except Exception as exc:
            self._set_last_error(
                f"SSH runtime cleanup failed on {self.server.host}: {exc}"
            )
            logger.error(self.last_error)
            return False
        finally:
            ssh.close()
