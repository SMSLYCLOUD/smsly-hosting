"""Tasks package — re-exports all deployment tasks for backward compatibility."""

# ── New submodules (split from tasks.py) ──────────────────────────
# ── Re-exports from tasks_ecosystem ───────────────────────────────
from apps.deployments.tasks.ecosystem import (
    ecosystem_deferred_build_task,
    ecosystem_deploy_task,
    ecosystem_release_wave_task,
    ecosystem_scan_task,
)

# ── Re-exports from tasks_safedeploy ──────────────────────────────
from apps.deployments.tasks.deployment.tasks_safedeploy import (
    create_database_clone_job,
    create_preview_environment_job,
    destroy_preview_environment_job,
    expire_stale_previews_job,
    provision_preview_service_job,
    run_migration_validation_job,
    run_preview_health_check_job,
    run_preview_tests_job,
)

from .deploy import *  # noqa: F401, F403
from .ecosystem import *  # noqa: F401, F403
from .ai.ollama import (
    _cleanup_shared_ollama_if_unused,
    _detect_safe_ollama_cpu,
    _detect_safe_ollama_ram_mb,
    _ensure_shared_ollama_cpp,
    _pull_ollama_models_into_shared,
)
from .remote import *  # noqa: F401, F403

# ── Re-exports from tasks_addons ──────────────────────────────────
from apps.addons.tasks.crud import (
    backup_addon_task,
    delete_addon_task,
    deprovision_addon_task,
    provision_addon_task,
    restore_addon_task,
)

# ── Re-exports from tasks_backup ──────────────────────────────────
from apps.deployments.tasks.data.tasks_backup import (  # noqa: F401
    cleanup_old_backups_task,
    create_server_backup_task,
    create_service_backup_task,
    purge_user_backups_task,
    restore_server_backup_task,
    restore_service_backup_task,
)

# ── Re-exports from tasks_health ──────────────────────────────────
from apps.deployments.tasks.infra.tasks_health import (
    auto_authenticate_nodes_task,
    check_managed_servers_health_task,
    node_watchdog_task,
    refresh_managed_server_health,
    sync_master_db_to_agents_task,
)

# ── Re-exports from tasks_maintenance ─────────────────────────────
from apps.deployments.tasks.infra.tasks_maintenance import (
    registry_garbage_collection_task,
    run_maintenance_task,
)

# ── Re-exports from tasks_platform_update ─────────────────────────
from apps.deployments.tasks.infra.tasks_platform_update import (
    platform_rollback_task,
    platform_update_task,
)

# ── Re-exports from tasks_server_update ───────────────────────────
from apps.deployments.tasks.infra.tasks_server_update import update_remote_server_task

# ── Re-exports from tasks_templates ───────────────────────────────
from apps.deployments.tasks.deployment.tasks_templates import one_click_deploy_template_task  # noqa: F401

# ── Re-exports from tasks_transfer ────────────────────────────────
from apps.deployments.tasks.infra.tasks_transfer import (
    execute_server_transfer_task,
    rollback_transfer_task,
)

# ── Re-exports from tasks_bundles ─────────────────────────────────
from apps.deployments.tasks.deployment.tasks_bundles import (
    backup_bundle_component_task,
    deprovision_bundle_task,
    delete_bundle_task,
    provision_bundle_task,
    reprovision_bundle_task,
    restore_bundle_component_task,
)

# ── Re-exports from tasks_replication ─────────────────────────────
from apps.deployments.tasks.data.tasks_replication import (
    check_replication_health_task,
    deploy_replication_task,
    manual_failover_task,
)

# ── Re-exports from tasks_election ────────────────────────────────
from apps.deployments.tasks.infra.tasks_election import (
    cleanup_heartbeat_logs_task,
    force_election_task,
    heartbeat_task,
)

# ── Re-exports from tasks_mesh ────────────────────────────────────
from apps.deployments.tasks.infra.tasks_mesh import (
    check_mesh_health_task,
    deploy_mesh_task,
)


# ── Lazy re-exports from tasks_deploy (circular import avoidance) ─
def __getattr__(name):
    import sys

    if name in ('smart_deploy_task', 'resume_deploy_task', '_post_deploy_monitor'):
        from apps.deployments.tasks.deployment.tasks_deploy import _post_deploy_monitor, resume_deploy_task, smart_deploy_task
        module = sys.modules[__name__]
        module.smart_deploy_task = smart_deploy_task
        module.resume_deploy_task = resume_deploy_task
        module._post_deploy_monitor = _post_deploy_monitor
        return module.__dict__[name]
    if name == 'enqueue_smart_deploy_task':
        from apps.deployments.tasks.deploy.helpers import enqueue_smart_deploy_task
        module = sys.modules[__name__]
        module.enqueue_smart_deploy_task = enqueue_smart_deploy_task
        return module.enqueue_smart_deploy_task
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# ── Re-exports for the old tasks_deploy_remote inline import ──────
from .deployment.tasks_deploy_remote import self_heal_remote_deployment  # noqa: F401
