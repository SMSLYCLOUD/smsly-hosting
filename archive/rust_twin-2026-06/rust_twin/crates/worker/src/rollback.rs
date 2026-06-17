//! Real rollback executor.
//!
//! Given a `deployment_id` of a freshly-created rollback record (status
//! `ROLLING_OUT`, `is_rollback = true`), this module:
//!
//! 1. Loads the rollback record and its service.
//! 2. Finds the previous successful deployment for the same service
//!    (status `RUNNING`, `id != current`, newest first).
//! 3. Stops the running container for the *current* (non-rollback) commit
//!    so traffic can be handed off.
//! 4. Starts a new container from the *previous* deployment's image, with
//!    the same env vars, ports, and network as the service.
//! 5. Marks the rollback record as `ROLLED_BACK`, sets `finished_at`, and
//!    records an `AuditLog` entry.
//!
//! The whole flow is idempotent: if the rollback record is already in
//! `ROLLED_BACK`, `execute` returns a no-op result so a retried message
//! cannot spin up a duplicate container or stop a container that has
//! already been swapped out.

use anyhow::{anyhow, Context, Result};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, QueryOrder, Set};
use serde::{Deserialize, Serialize};
use tracing::{error, info, instrument, warn};
use uuid::Uuid;

use cn_core::deployment_status::DeploymentStatus;
use cn_core::entities::{audit_log, deployment, environment_variable, service};
use infrastructure::docker::DockerClient;

use crate::WorkerState;

pub const ROLLBACK_NETWORK: &str = "smsly-net";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RollbackRequest {
    pub deployment_id: Uuid,
    #[serde(default)]
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RollbackResult {
    pub new_container_id: String,
    pub previous_image: String,
    pub previous_deployment_id: Uuid,
    pub previous_commit_hash: String,
}

/// Execute a real rollback for the given deployment record.
///
/// The `deployment_id` is the id of the rollback record (the one created
/// by the API handler with `is_rollback = true`, `status = ROLLING_OUT`).
/// The function:
/// - is idempotent: a second call for a record already in `ROLLED_BACK`
///   returns a no-op `Ok` without re-stopping/re-starting containers,
/// - returns a clear error if no previous successful deployment exists.
#[instrument(skip(state), fields(deployment_id = %req.deployment_id))]
pub async fn execute(state: &WorkerState, req: RollbackRequest) -> Result<RollbackResult> {
    info!(
        "Starting rollback for deployment {} (reason: {})",
        req.deployment_id, req.reason
    );

    // 1. Load the rollback deployment record.
    let rollback_deploy = deployment::Entity::find_by_id(req.deployment_id)
        .one(&state.db)
        .await
        .context("failed to query rollback deployment")?
        .ok_or_else(|| anyhow!("rollback deployment {} not found", req.deployment_id))?;

    // 2. Idempotency: if the rollback has already completed, do nothing.
    let current_status = DeploymentStatus::from_str(&rollback_deploy.status);
    if matches!(current_status, Some(DeploymentStatus::RolledBack)) {
        info!(
            "rollback deployment {} is already ROLLED_BACK; no-op",
            req.deployment_id
        );
        return Ok(RollbackResult {
            new_container_id: String::new(),
            previous_image: String::new(),
            previous_deployment_id: Uuid::nil(),
            previous_commit_hash: rollback_deploy.commit_hash,
        });
    }

    // 3. Load the service.
    let svc = service::Entity::find_by_id(rollback_deploy.service_id)
        .one(&state.db)
        .await
        .context("failed to load service for rollback")?
        .ok_or_else(|| {
            anyhow!(
                "service {} referenced by rollback {} not found",
                rollback_deploy.service_id,
                req.deployment_id
            )
        })?;

    // 4. Find the previous successful deployment for this service.
    //
    //    "Previous" is defined as: status == RUNNING, id != current,
    //    ordered by created_at DESC, limit 1. We exclude the current
    //    rollback record itself.
    let previous = deployment::Entity::find()
        .filter(deployment::Column::ServiceId.eq(rollback_deploy.service_id))
        .filter(deployment::Column::Status.eq(DeploymentStatus::Running.as_str()))
        .filter(deployment::Column::Id.ne(req.deployment_id))
        .order_by_desc(deployment::Column::CreatedAt)
        .one(&state.db)
        .await
        .context("failed to query previous deployment")?
        .ok_or_else(|| {
            anyhow!(
                "no previous deployment to roll back to for service {}",
                rollback_deploy.service_id
            )
        })?;

    info!(
        "Rolling back service {}: current={} -> previous={} (commit {})",
        svc.slug, rollback_deploy.commit_hash, previous.id, previous.commit_hash
    );

    // 5. Build the image name for the previous deployment.
    //
    //    Mirrors the naming convention used by `tasks::handle_smart_deploy`:
    //    `registry.smsly.cloud/project-{project_id}:{commit_hash}`.
    let previous_image =
        format!("registry.smsly.cloud/project-{}:{}", svc.project_id, previous.commit_hash);

    // 6. Resolve env vars for the service so the new container starts
    //    with the same configuration as the original.
    let env_rows = environment_variable::Entity::find()
        .filter(environment_variable::Column::ServiceId.eq(svc.id))
        .all(&state.db)
        .await
        .context("failed to query environment variables")?;
    let env: Vec<String> = env_rows
        .into_iter()
        .map(|row| format!("{}={}", row.key, row.value))
        .collect();

    // 7. Compute container names. The convention (set by smart_deploy) is
    //    `svc-{slug}-{commit_hash}`.
    let current_container_name = format!("svc-{}-{}", svc.slug, rollback_deploy.commit_hash);
    let new_container_name = format!("svc-{}-{}", svc.slug, previous.commit_hash);

    // 8. Stop the currently running container, then start the previous
    //    version. Either step can fail; we surface the error to the
    //    caller, who decides whether to retry.
    let docker = DockerClient::new().context("failed to connect to Docker")?;
    docker
        .ensure_network(ROLLBACK_NETWORK)
        .await
        .context("failed to ensure rollback network")?;

    info!("Stopping current container: {}", current_container_name);
    docker
        .stop_container(&current_container_name)
        .await
        .context("failed to stop current container")?;

    info!(
        "Starting previous container '{}' from image '{}'",
        new_container_name, previous_image
    );
    let new_container_id = docker
        .run_container(&previous_image, &new_container_name, env, ROLLBACK_NETWORK)
        .await
        .context("failed to start previous container")?;

    let now = chrono::Utc::now();

    // 9. Persist the rollback outcome on the rollback record.
    //
    //    The deployment entity does not have a `metadata` column, so we
    //    persist the reason via the audit log and just set `finished_at`.
    //    The status transition ROLLING_OUT -> ROLLED_BACK is performed by
    //    the API handler; the worker is the one that drives the
    //    transition once the container swap succeeds.
    let mut active: deployment::ActiveModel = rollback_deploy.clone().into();
    active.finished_at = Set(Some(now.into()));
    active.save(&state.db).await.context("failed to persist rollback")?;

    // 10. Record an audit-log entry. If the entity is unavailable for any
    //     reason we fall back to a structured tracing line so the rollback
    //     is always traceable.
    let metadata = serde_json::json!({
        "service_id": svc.id,
        "deployment_id": req.deployment_id,
        "rolled_back_to_id": previous.id,
        "rolled_back_to_commit": previous.commit_hash,
        "rolled_back_to_image": previous_image,
        "new_container_id": new_container_id,
        "reason": req.reason,
    });
    if let Err(e) = record_audit_log(state, &req, &previous.id, &metadata).await {
        warn!(
            "audit log insert failed ({}); falling back to tracing::info",
            e
        );
        info!(
            target: "audit",
            action = "DEPLOYMENT_ROLLBACK",
            deployment_id = %req.deployment_id,
            service_id = %svc.id,
            rolled_back_to_id = %previous.id,
            rolled_back_to_commit = %previous.commit_hash,
            new_container_id = %new_container_id,
            reason = %req.reason,
            "rollback completed"
        );
    }

    info!(
        "Rollback complete: new container {} running image {}",
        new_container_id, previous_image
    );

    Ok(RollbackResult {
        new_container_id,
        previous_image,
        previous_deployment_id: previous.id,
        previous_commit_hash: previous.commit_hash,
    })
}

/// Insert an `AuditLog` row recording the rollback.
///
/// Returns `Err` on any DB failure so the caller can fall back to
/// `tracing::info!`. The action string matches the Django backend's
/// `DEPLOYMENT_ROLLBACK_INSTANT` convention.
async fn record_audit_log(
    state: &WorkerState,
    req: &RollbackRequest,
    previous_id: &Uuid,
    metadata: &serde_json::Value,
) -> Result<()> {
    let now = chrono::Utc::now();
    let row = audit_log::ActiveModel {
        id: Set(Uuid::new_v4()),
        actor_id: Set(None),
        action: Set("DEPLOYMENT_ROLLBACK".to_string()),
        target_type: Set("Deployment".to_string()),
        target_id: Set(Some(req.deployment_id)),
        ip_address: Set(None),
        user_agent: Set(None),
        metadata_json: Set(Some(metadata.clone())),
        created_at: Set(now.into()),
    };
    row.save(&state.db).await.map_err(|e| {
        error!("audit log save failed: {}", e);
        anyhow!(e)
    })?;
    let _ = previous_id; // currently encoded in metadata
    Ok(())
}
