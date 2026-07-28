import logging

from apps.deployments.models import (
    EnvironmentVariable,
    Service,
)

logger = logging.getLogger(__name__)


class ServiceSyncMixin:
    def _search_remote_service(self, service: Service, path: str) -> str | None:
        page = 1
        while True:
            resp = self._request("GET", path, params={"search": service.name, "page": page}, timeout=15)
            if resp is None:
                logger.error(
                    "Failed to search service %s on remote %s: %s",
                    service.name,
                    self.server.host,
                    self.describe_last_error(),
                )
                return None

            if resp.status_code != 200:
                logger.error(
                    "Failed to search service %s on remote %s: %s",
                    service.name,
                    self.server.host,
                    self._response_error("service search failed", resp),
                )
                return None

            data = self._parse_json_response(resp, "searching remote services")
            if data is None:
                return None

            if isinstance(data, dict):
                results = data.get("results", [])
            else:
                results = data

            if not isinstance(results, list):
                self._set_last_error("Remote API returned an invalid services list.")
                return None

            for remote_svc in results:
                if not isinstance(remote_svc, dict):
                    continue
                if remote_svc.get("name") == service.name:
                    logger.info(
                        "Found existing service %s on remote %s",
                        service.name,
                        self.server.host,
                    )
                    return remote_svc.get("id") or ""

            if isinstance(data, dict):
                next_url = data.get("next")
                if not next_url:
                    break
            else:
                break
            page += 1

        return ""

    def sync_service(self, service: Service) -> str | None:
        path = "/api/v1/services/"

        try:
            existing_id = self._search_remote_service(service, path)
            if existing_id:
                if not self._sync_remote_service_config(service, existing_id):
                    return None
                self.sync_env_vars(service, existing_id)
                return existing_id
            if existing_id is None:
                return None
        except Exception as e:
            self._set_last_error(f"Failed to search service on remote {self.server.host}: {e}")
            logger.warning(self.last_error)
            return None

        logger.info("Creating service %s on remote %s", service.name, self.server.host)
        payload = self._service_sync_payload(service)

        try:
            resp = self._request("POST", path, payload=payload, timeout=30)
            if resp and resp.status_code in (201, 200):
                data = self._parse_json_response(resp, "creating remote service")
                if not isinstance(data, dict) or not data.get("id"):
                    self._set_last_error(
                        "Remote service create response did not include an id.",
                        response=resp,
                    )
                    return None
                remote_id = data["id"]

                self.sync_env_vars(service, remote_id)
                return remote_id

            if resp is not None:
                self._set_last_error("Failed to create service on remote.", response=resp)
            logger.error(self.last_error)
        except Exception as e:
            self._set_last_error(f"Error creating service on remote: {e}")
            logger.error(self.last_error)

        return None

    def _service_sync_payload(self, service: Service) -> dict:
        payload = {
            "name": service.name,
            "deploy_type": service.deploy_type,
            "repository_url": service.repository_url,
            "branch": service.branch,
            "docker_image": service.docker_image,
            "internal_port": service.internal_port,
            "is_public": service.is_public,
            "buildpack": service.buildpack,
            "public_domain": service.public_domain,
            "public_domain_hidden": service.public_domain_hidden,
            "custom_domains": service.custom_domains or [],
            "build_command": service.build_command,
            "start_command": service.start_command,
            "root_directory": service.root_directory,
            "deploy_mode": service.deploy_mode,
            "compose_file": service.compose_file,
            "compose_main_service": service.compose_main_service,
            "health_check_path": service.health_check_path,
            "health_check_port": service.health_check_port,
            "health_check_interval": service.health_check_interval,
            "health_check_timeout": service.health_check_timeout,
            "health_check_retries": service.health_check_retries,
            "restart_policy": service.restart_policy,
            "cpu_cores": str(service.cpu_cores),
            "memory_mb": service.memory_mb,
            "min_replicas": service.min_replicas,
            "max_replicas": service.max_replicas,
            "vpa_enabled": service.vpa_enabled,
        }
        return payload

    def _sync_remote_service_config(self, service: Service, remote_service_id: str) -> bool:
        path = f"/api/v1/services/{remote_service_id}/"
        try:
            resp = self._request(
                "PATCH",
                path,
                payload=self._service_sync_payload(service),
                timeout=15,
            )
            if resp and resp.status_code in (200, 202):
                return True

            if resp is not None:
                self._set_last_error("Failed to update service on remote.", response=resp)
            logger.error(self.last_error)
        except Exception as e:
            self._set_last_error(f"Error updating service on remote: {e}")
            logger.error(self.last_error)

        return False

    def sync_env_vars(self, service: Service, remote_service_id: str):
        path = f"/api/v1/services/{remote_service_id}/env_vars/"

        def _is_ciphertext(val: str) -> bool:
            if not val or not isinstance(val, str):
                return False
            if val.startswith("gAAAA"):
                return True
            if len(val) > 100 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in val):
                try:
                    import base64
                    padded = val + '=' * (-len(val) % 4)
                    decoded = base64.urlsafe_b64decode(padded)
                    if len(decoded) >= 57 and decoded[0] == 0x80:
                        return True
                except Exception as exc:
                    logger.debug("Fernet token detection failed: %s", exc)
            return False

        env_vars = EnvironmentVariable.objects.filter(service=service)

        safe_vars = []
        skipped_count = 0
        for var in env_vars:
            raw_value = var.value
            if _is_ciphertext(raw_value):
                logger.warning(
                    "[DB-ENCRYPT] Skipping env var %s for service %s — "
                    "value is ciphertext (decryption failed or double-encrypted).",
                    var.key, service.name,
                )
                skipped_count += 1
                continue
            safe_vars.append({
                "key": var.key,
                "value": raw_value,
                "is_secret": var.is_secret,
                "source": var.source,
            })

        if skipped_count > 0:
            logger.warning(
                "[DB-ENCRYPT] Skipped %d environment variables for service %s due to decryption failure/ciphertext value.",
                skipped_count, service.name,
            )

        if not safe_vars:
            logger.info(
                "No safe env vars to sync for service %s (all were ciphertext).",
                service.name,
            )
            return

        payload = {"vars": safe_vars}

        try:
            resp = self._request("POST", path, payload=payload, timeout=20)
            if resp is not None and resp.status_code < 400:
                return
            if resp is not None:
                logger.warning(
                    "Bulk env sync failed for remote service %s: %s",
                    remote_service_id,
                    self._response_error("bulk env sync failed", resp),
                )
        except Exception as exc:
            logger.warning(
                "Bulk env sync failed for remote service %s: %s",
                remote_service_id,
                exc,
            )

        for var in safe_vars:
            try:
                resp = self._request("POST", path, payload=var, timeout=10)
                if resp is not None and resp.status_code >= 400:
                    logger.warning(
                        "Failed to sync env var %s to remote service %s: %s",
                        var["key"],
                        remote_service_id,
                        self._response_error("env var sync failed", resp),
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to sync env var %s to remote service %s: %s",
                    var["key"],
                    remote_service_id,
                    exc,
                )
