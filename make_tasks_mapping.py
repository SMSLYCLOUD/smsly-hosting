mapping = {
    'tasks_utils': [
        '_env_bool',
        '_env_int',
        'should_skip_review_for_commit_message',
        '_current_agent_node_queue'
    ],
    'tasks_deploy': [
        'smart_deploy_task',
        'resume_deploy_task',
        'enqueue_smart_deploy_task',
        'recover_stalled_queued_deployments',
        '_resolve_provider_for_service',
        '_deployment_effective_server',
        '_is_local_deployment_server',
        'fleet_build_lock',
        '_run_managed_image_post_deploy_hooks',
        '_do_promote',
        '_deploy_container',
        '_post_deploy_monitor',
        '_handle_failure',
        'delete_service_task'
    ],
    'tasks_deploy_local': [
        '_docker_safe_segment',
        '_detect_exposed_port',
        '_coerce_int',
        '_is_legacy_default_healthcheck',
        '_build_platform_healthcheck',
        '_build_runtime_env',
        '_smart_derive_database_vars',
        '_smart_derive_redis_vars',
        '_infer_database_name',
        '_ensure_database_exists',
        '_is_low_resource_service',
        '_local_route_timeout_seconds',
        '_local_container_timeout_seconds',
        '_wait_for_local_container_healthy',
        '_wait_for_local_route_ready',
        '_link_ecosystem'
    ],
    'tasks_deploy_remote': [
        '_handle_remote_deployment_legacy',
        '_remote_failure_message',
        '_stop_local_service_container',
        '_remote_deploy_failed',
        '_handle_remote_deployment',
        '_resume_remote_deployment',
        '_copy_remote_deployment_fields',
        '_poll_remote_deployment',
        '_is_traefik_not_ready',
        '_route_misroute_reason',
        'self_heal_remote_deployment'
    ],
    'tasks_build': [
        '_build_function',
        '_build_uploaded_source',
        '_resolve_upload_zip_path',
        '_safe_extract_zip'
    ],
    'tasks_ai_router': [
        '_escalate_to_ai',
        '_detect_safe_ollama_ram_mb',
        '_detect_safe_ollama_cpu',
        '_ensure_shared_ollama_cpp',
        '_pull_ollama_models_into_shared',
        '_cleanup_shared_ollama_if_unused'
    ],
    'tasks_templates': [
        'one_click_deploy_template_task'
    ],
    'tasks_addons': [
        'provision_addon_task',
        'deprovision_addon_task',
        'backup_addon_task',
        'restore_addon_task',
        'delete_addon_task'
    ],
    'tasks_backup': [
        'create_service_backup_task',
        'create_server_backup_task',
        'restore_service_backup_task',
        'restore_server_backup_task',
        'purge_user_backups_task',
        'cleanup_old_backups_task',
        'run_scheduled_backups_task'
    ],
    'tasks_transfer': [
        'execute_server_transfer_task',
        'rollback_transfer_task'
    ],
    'tasks_platform_update': [
        'platform_update_task',
        'platform_rollback_task',
        '_clear_directory_contents'
    ],
    'tasks_maintenance': [
        '_extract_addon_id_from_name',
        '_is_stale_maintenance_container',
        '_clear_orphaned_runtime_resources',
        'run_maintenance_task',
        'ThrottledLogAppender',
        'registry_garbage_collection_task'
    ],
    'tasks_server_update': [
        'update_remote_server_task',
        '_redact_remote_update_log',
        '_append_remote_update_log',
        '_remote_update_preflight_script',
        '_remote_update_postflight_script',
        '_run_ssh_command'
    ],
    'tasks_health': [
        'auto_authenticate_nodes_task',
        'check_managed_servers_health_task',
        'node_watchdog_task',
        'refresh_managed_server_health',
        'sync_master_db_to_agents_task'
    ],
    'tasks_caddy': [
        '_regenerate_caddyfile'
    ]
}
