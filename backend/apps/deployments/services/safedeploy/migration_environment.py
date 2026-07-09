import logging
import os
import re
import shutil
import subprocess
import venv
from typing import NamedTuple

logger = logging.getLogger(__name__)

DEPENDENCY_FILES = [
    ("requirements.txt", "pip install -r {path}"),
    ("pyproject.toml", "pip install -e ."),
    ("setup.py", "pip install -e ."),
    ("Pipfile", "pipenv install --deploy --ignore-pipfile"),
    ("poetry.lock", "poetry install --no-dev"),
]
"""Sorted by priority: requirements.txt is checked first, pyproject.toml second, etc."""

SETTINGS_MODULE_RE = re.compile(
    r"os\.environ\.setdefault\s*\(\s*['\"]DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)"
)
MANAGE_PY_CALL_RE = re.compile(r"DJANGO_SETTINGS_MODULE\s*=\s*['\"]([^'\"]+)['\"]")


class MigrationEnvironmentResult(NamedTuple):
    ok: bool
    python_bin: str
    env: dict[str, str]
    error: str


def _safe_shell_split(cmd: str):
    import shlex
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _run_pip_install(python_bin: str, dep_file_abs: str, cwd: str, timeout: int = 600):
    install_cmd = f"{python_bin} -m pip install -r {dep_file_abs}"
    logger.info("Installing dependencies: %s in %s", install_cmd, cwd)
    try:
        result = subprocess.run(
            _safe_shell_split(install_cmd),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            stderr_tail = (result.stderr or "").strip().splitlines()
            last_lines = stderr_tail[-5:] if len(stderr_tail) > 5 else stderr_tail
            return False, "\n".join(last_lines)
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "pip install timed out after {timeout}s"
    except FileNotFoundError:
        return False, f"python binary not found: {python_bin}"
    except Exception as e:
        return False, f"pip install error: {e}"


def _invoke_install(python_bin: str, dep_file: str, project_path: str, timeout: int = 600):
    dep_path = os.path.join(project_path, dep_file)
    if not os.path.isfile(dep_path):
        return False, f"Dependency file {dep_file} not found"

    if dep_file == "requirements.txt":
        return _run_pip_install(python_bin, dep_path, project_path, timeout)

    if dep_file == "pyproject.toml" or dep_file == "setup.py":
        install_cmd = f"{python_bin} -m pip install -e {project_path}"
        logger.info("Installing project (editable): %s", install_cmd)
        try:
            result = subprocess.run(
                _safe_shell_split(install_cmd),
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                return False, (result.stderr or "pip install -e failed")[:800]
            return True, ""
        except subprocess.TimeoutExpired:
            return False, "pip install -e timed out"
        except Exception as e:
            return False, f"pip install -e error: {e}"

    if dep_file == "Pipfile":
        install_cmd = f"{python_bin} -m pipenv install --deploy --ignore-pipfile"
    elif dep_file == "poetry.lock":
        install_cmd = f"{python_bin} -m poetry install --no-dev"
    else:
        return False, f"Unsupported dependency file: {dep_file}"

    logger.info("Installing dependencies: %s in %s", install_cmd, project_path)
    try:
        result = subprocess.run(
            _safe_shell_split(install_cmd),
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "install failed")[:800]
        return True, ""
    except subprocess.TimeoutExpired:
        return False, f"Dependency install timed out after {timeout}s"
    except FileNotFoundError:
        return False, f"Tool not available: {install_cmd.split()[0]}"
    except Exception as e:
        return False, f"Dependency install error: {e}"


def _discover_django_settings_module(project_path: str) -> str | None:
    manage_py = os.path.join(project_path, "manage.py")
    if not os.path.isfile(manage_py):
        return None

    try:
        with open(manage_py, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None

    match = SETTINGS_MODULE_RE.search(content)
    if match:
        return match.group(1)

    match = MANAGE_PY_CALL_RE.search(content)
    if match:
        return match.group(1)

    try:
        for root, dirs, files in os.walk(project_path):
            for d in list(dirs):
                if d.startswith(".") or d in ("node_modules", "__pycache__", "venv", ".venv", ".git"):
                    dirs.remove(d)
            dir_name = os.path.basename(root)
            settings_file = os.path.join(root, "settings.py")
            if os.path.isfile(settings_file) and dir_name not in ("django", "rest_framework"):
                rel = os.path.relpath(root, project_path).replace(os.sep, ".")
                return f"{rel}.settings"
    except OSError:
        pass
    return None


def build_migration_environment(
    project_path: str,
    db_url: str,
    service_env_vars: dict[str, str] | None = None,
    timeout: int = 600,
    block_addon_urls: bool = True,
) -> MigrationEnvironmentResult:
    if not project_path or not os.path.isdir(project_path):
        return MigrationEnvironmentResult(ok=False, python_bin="", env={}, error=f"Project path not found: {project_path}")

    manage_py = os.path.join(project_path, "manage.py")
    if not os.path.isfile(manage_py):
        return MigrationEnvironmentResult(ok=False, python_bin="", env={}, error="manage.py not found in project root")

    venv_dir = os.path.join(project_path, ".smsly_venv")
    if os.path.exists(venv_dir):
        shutil.rmtree(venv_dir, ignore_errors=True)

    try:
        venv.create(venv_dir, with_pip=True, clear=True)
    except Exception as e:
        return MigrationEnvironmentResult(ok=False, python_bin="", env={}, error=f"Failed to create venv: {e}")

    if os.name == "nt":
        python_bin = os.path.join(venv_dir, "Scripts", "python.exe")
    else:
        python_bin = os.path.join(venv_dir, "bin", "python")

    if not os.path.isfile(python_bin):
        python_bin = os.path.join(venv_dir, "bin", "python3")
    if not os.path.isfile(python_bin):
        shutil.rmtree(venv_dir, ignore_errors=True)
        return MigrationEnvironmentResult(ok=False, python_bin="", env={}, error="python binary not found in created venv")

    logger.info("Upgrading pip in venv...")
    try:
        subprocess.run(
            [python_bin, "-m", "pip", "install", "--upgrade", "pip"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception:
        pass

    installed = False
    install_error = ""
    for dep_file, _ in DEPENDENCY_FILES:
        dep_path = os.path.join(project_path, dep_file)
        if os.path.isfile(dep_path):
            ok, err = _invoke_install(python_bin, dep_file, project_path, timeout)
            if ok:
                installed = True
                break
            install_error = err
            logger.warning("Dependency install via %s failed: %s", dep_file, err)

    if not installed:
        shutil.rmtree(venv_dir, ignore_errors=True)
        return MigrationEnvironmentResult(
            ok=False, python_bin="", env={},
            error=f"No dependency file found or all installs failed. Last error: {install_error or 'no dependency files detected'}"
        )

    django_settings = _discover_django_settings_module(project_path)
    logger.info("Discovered DJANGO_SETTINGS_MODULE=%s", django_settings)

    env: dict[str, str] = {}
    env["DATABASE_URL"] = db_url
    env["PYTHONUNBUFFERED"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

    if django_settings:
        env["DJANGO_SETTINGS_MODULE"] = django_settings

    env_vars_blacklist = {
        "PATH", "HOME", "USER", "PWD", "OLDPWD", "SHELL", "TERM", "LOGNAME",
        "LANG", "LC_ALL", "TZ", "HOSTNAME", "DEBIAN_FRONTEND",
        "SMSLY_RUN_ENTRYPOINT_TASKS", "CELERY_TASK_ALWAYS_EAGER",
        "SAFEDEPLOY_RUN_EAGER_DEPLOY", "SMSLY_GIT_PASSWORD", "GIT_ASKPASS",
        "GIT_TERMINAL_PROMPT",
    }

    # Addon connection URLs that must NOT be forwarded from the parent service
    # during migration validation. At this stage preview addons haven't been
    # provisioned yet; forwarding the parent's production addon URLs would
    # either leak to production or fail on unreachable hosts.
    # DATABASE_URL is already set above from the clone URL (safe).
    # These keys will be set to their real preview values later by
    # provision_preview_service_job when the container actually starts.
    addon_env_keys_blocklist = {
        "POSTGRES_URL", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRESQL_URL",
        "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE",
        "DB_URL", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME",
        "DATABASE_HOST", "DATABASE_PORT", "DATABASE_USER", "DATABASE_PASSWORD", "DATABASE_NAME",
        "DIRECT_URL", "DIRECT_DATABASE_URL", "SHADOW_DATABASE_URL", "SQLALCHEMY_DATABASE_URI",
        "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE",
        "MONGO_HOST", "MONGO_PORT", "MONGO_USER", "MONGO_PASSWORD", "MONGO_DB",
        "REDIS_URI", "RABBITMQ_PORT", "RABBITMQ_USER", "RABBITMQ_PASSWORD",
        "REDIS_URL", "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD",
        "RABBITMQ_URL", "RABBITMQ_HOST", "AMQP_URL",
        "MYSQL_URL", "MONGODB_URI", "MONGODB_URL",
        "ELASTICSEARCH_URL", "QDRANT_URL",
        "MINIO_URL", "S3_ENDPOINT", "S3_BUCKET",
        "KAFKA_URL", "KAFKA_BOOTSTRAP_SERVERS",
        "NATS_URL", "PULSAR_URL",
        "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND",
        "CACHE_URL", "CACHE_HOST",
        "SESSION_REDIS_URL",
        "MEILISEARCH_URL", "TYPESENSE_URL",
        "INFLUXDB_URL", "TIMESCALEDB_URL",
        "CLICKHOUSE_URL", "CASSANDRA_URL",
        "NEO4J_URL", "WEAVIATE_URL", "MILVUS_URL",
        "CHROMADB_URL", "OPENSEARCH_URL",
        "KEYDB_URL", "VALKEY_URL", "DRAGONFLY_URL",
        "ETCD_URL", "CONSUL_URL", "VAULT_URL",
        "KEYCLOAK_URL", "TEMPORAL_URL",
        "PROMETHEUS_URL", "VICTORIAMETRICS_URL",
        "GRAFANA_URL", "JAEGER_URL",
    }

    if service_env_vars:
        for key, value in service_env_vars.items():
            upper_key = key.upper().replace("-", "_")
            if upper_key in env_vars_blacklist:
                continue
            if block_addon_urls and upper_key in addon_env_keys_blocklist:
                continue
            if key not in env:
                env[key] = str(value) if value else ""

    # Set dummy values for commonly-required addon env vars that Django
    # settings.py may reference with os.environ['KEY']. This prevents
    # KeyError crashes during manage.py bootstrap without connecting to
    # production infrastructure. Real values get injected later when the
    # preview container starts.
    if block_addon_urls:
        for placeholder_key, placeholder_val in [
            ("REDIS_URL", "redis://localhost:6379/0"),
            ("CACHE_URL", "redis://localhost:6379/1"),
            ("CELERY_BROKER_URL", "redis://localhost:6379/2"),
            ("CELERY_RESULT_BACKEND", "redis://localhost:6379/3"),
        ]:
            if placeholder_key not in env:
                env[placeholder_key] = placeholder_val

    if not django_settings:
        for key in env:
            upper = key.upper()
            if "SETTINGS" in upper and ("DJANGO" in upper or "MODULE" in upper):
                env["DJANGO_SETTINGS_MODULE"] = str(env[key])
                break

    return MigrationEnvironmentResult(ok=True, python_bin=python_bin, env=env, error="")
