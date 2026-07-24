import logging
import os

from django.db.models.signals import post_save
from django.dispatch import receiver

from ..models import PlatformConfig
from ..utils import log_event
from ..patching import patch_runtime_settings
from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile

logger = logging.getLogger(__name__)


@receiver(post_save, sender=PlatformConfig)
def sync_infrastructure_on_config_change(sender, instance, **kwargs):
    logger = logging.getLogger(__name__)
    try:
        patch_runtime_settings()

        _new_domain = (instance.domain or "").strip()
        _new_ssl = instance.use_ssl
        _new_scheme = 'https' if _new_ssl else 'http'
        _new_origin = f'{_new_scheme}://{_new_domain}' if _new_domain else ''
        _new_grafana_url = f'{_new_origin}/grafana' if _new_domain else None

        _env_sync_map = {
            'DOMAIN=': _new_domain,
            'USE_SSL=': 'true' if _new_ssl else 'false',
            'SITE_URL=': _new_origin or None,
            'GRAFANA_EXTERNAL_URL=': _new_grafana_url,
            'ALLOWED_HOSTS=': None,
            'CSRF_TRUSTED_ORIGINS=': None,
            'CORS_ALLOWED_ORIGINS=': None,
        }

        for _env_path in ("/app/.env", "/caddy-config/.env"):
            if not (_new_domain and os.path.isfile(_env_path)):
                continue
            if not os.access(_env_path, os.W_OK):
                logger.debug("Skipping .env sync: %s is not writable", _env_path)
                continue
            try:
                _updated = False
                _lines = []
                with open(_env_path, encoding="utf-8") as _fh:
                    for _line in _fh:
                        _matched = False
                        for _key, _val in _env_sync_map.items():
                            if _line.startswith(_key):
                                if _val is not None:
                                    _lines.append(f"{_key}{_val}\n")
                                    _updated = True
                                else:
                                    _lines.append(_line)
                                _matched = True
                                break
                        if not _matched:
                            _lines.append(_line)
                for _key, _val in _env_sync_map.items():
                    if _val is not None and not any(line.startswith(_key) for line in _lines):
                        _lines.append(f"{_key}{_val}\n")
                        _updated = True
                if _updated:
                    with open(_env_path, "w", encoding="utf-8") as _fh:
                        _fh.writelines(_lines)
                    logger.info(
                        "Synced %s: DOMAIN=%s, USE_SSL=%s", _env_path, _new_domain, _new_ssl
                    )
            except PermissionError:
                logger.warning(
                    "Cannot write to %s (Permission denied). "
                    "Fix with: sudo chown 1000:1000 %s && sudo chmod 664 %s",
                    _env_path, _env_path, _env_path,
                )
            except OSError as _exc:
                logger.error("Failed to sync %s: %s", _env_path, _exc)

        _reg_user = (instance.registry_user or "").strip()
        _reg_pass = (instance.registry_password or "").strip()
        if _reg_user and _reg_pass:
            _cred_key = f"{_reg_user}:{_reg_pass}"
            if not getattr(sync_infrastructure_on_config_change, '_last_reg_creds', None) or \
               sync_infrastructure_on_config_change._last_reg_creds != _cred_key:
                _htpasswd_paths = ["/auth/htpasswd", "/app/auth/htpasswd"]
                _htpasswd_written = False
                for _hp in _htpasswd_paths:
                    _hp_dir = os.path.dirname(_hp)
                    if not os.path.isdir(_hp_dir):
                        try:
                            os.makedirs(_hp_dir, mode=0o755, exist_ok=True)
                        except OSError as _mk_exc:
                            logger.debug("Cannot create auth dir %s: %s — skipping", _hp_dir, _mk_exc)
                            continue
                    try:
                        _htpasswd_content = None
                        try:
                            import bcrypt
                            _hashed = bcrypt.hashpw(_reg_pass.encode(), bcrypt.gensalt(10))
                            _htpasswd_content = f"{_reg_user}:{_hashed.decode()}\n"
                        except ImportError:
                            pass

                        try:
                            import subprocess as _sp
                            _result = _sp.run(
                                ["htpasswd", "-Bbn", _reg_user, _reg_pass],
                                capture_output=True, text=True, timeout=10,
                            )
                            if _result.returncode == 0 and _result.stdout.strip():
                                _htpasswd_content = _result.stdout.strip() + "\n"
                        except (FileNotFoundError, OSError):
                            pass
                        except Exception as _cli_exc:
                            logger.debug("htpasswd CLI error: %s — using bcrypt fallback", _cli_exc)

                        if _htpasswd_content is None:
                            logger.warning(
                                "Cannot generate htpasswd for %s: neither bcrypt nor htpasswd CLI available",
                                _hp,
                            )
                            continue

                        with open(_hp, "w", encoding="utf-8") as _fh:
                            _fh.write(_htpasswd_content)
                        os.chmod(_hp, 0o644)
                        _htpasswd_written = True
                        logger.info("Synced htpasswd to %s for user %s", _hp, _reg_user)
                        break
                    except Exception as _exc:
                        logger.warning("Failed to write htpasswd to %s: %s", _hp, _exc)

                for _env_path in ("/app/.env", "/caddy-config/.env"):
                    if not os.path.isfile(_env_path) or not os.access(_env_path, os.W_OK):
                        continue
                    try:
                        _lines = []
                        _updated = False
                        with open(_env_path, encoding="utf-8") as _fh:
                            for _line in _fh:
                                if _line.startswith("REGISTRY_USER="):
                                    _lines.append(f"REGISTRY_USER={_reg_user}\n")
                                    _updated = True
                                elif _line.startswith("REGISTRY_PASSWORD="):
                                    _lines.append(f"REGISTRY_PASSWORD={_reg_pass}\n")
                                    _updated = True
                                else:
                                    _lines.append(_line)
                        if _updated:
                            with open(_env_path, "w", encoding="utf-8") as _fh:
                                _fh.writelines(_lines)
                    except Exception:
                        pass

                if _htpasswd_written:
                    try:
                        import subprocess
                        subprocess.run(
                            ["docker", "restart", "smsly-hosting-registry-1"],
                            capture_output=True, timeout=30,
                        )
                        logger.info("Restarted registry container after htpasswd update")
                    except Exception:
                        pass

                sync_infrastructure_on_config_change._last_reg_creds = _cred_key

        logger.info("Signal: Re-generating Caddyfile for domain %s", instance.domain)
        content = generate_caddyfile(instance)
        apply_caddyfile(
            content,
            cloudflare_token=instance.cloudflare_api_token,
            preserve_existing_token=True
        )

        log_event(
            actor='system',
            action='INFRA_SYNC',
            target='Caddyfile',
            metadata={
                'domain': instance.domain,
                'use_ssl': instance.use_ssl,
                'wildcard': instance.wildcard_subdomains,
            }
        )
    except Exception as e:
        logger.error("Failed to sync infrastructure from signal: %s", e)
