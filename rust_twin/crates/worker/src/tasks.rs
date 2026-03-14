use crate::WorkerState;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tracing::{info, instrument};

#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "type", content = "payload")]
pub enum Task {
    SmartDeploy {
        project_id: uuid::Uuid,
        deployment_id: uuid::Uuid,
        commit_hash: String,
    },
    CollectUsage {
        owner_id: i32,
    },
}

#[instrument(skip(state, raw_payload))]
pub async fn process_payload(state: Arc<WorkerState>, raw_payload: String) -> Result<()> {
    info!("Parsing task payload");

    let task: Task = serde_json::from_str(&raw_payload)
        .context("Failed to deserialize JSON task payload")?;

    match task {
        Task::SmartDeploy {
            project_id,
            deployment_id,
            commit_hash,
        } => handle_smart_deploy(state, project_id, deployment_id, commit_hash).await?,
        Task::CollectUsage { owner_id } => handle_collect_usage(state, owner_id).await?,
    }

    Ok(())
}

#[instrument(skip(_state))]
async fn handle_smart_deploy(
    _state: Arc<WorkerState>,
    project_id: uuid::Uuid,
    deployment_id: uuid::Uuid,
    commit_hash: String,
) -> Result<()> {
    info!(
        "Starting smart_deploy for project {} (Deployment: {}) @ commit {}",
        project_id, deployment_id, commit_hash
    );

    // TODO: (Phase 4.2)
    // 1. Fetch project from DB (`state.db`)
    // 2. Clone Repository (`git2` / Command)
    // 3. Analyze Dockerfile/Nixpacks
    // 4. Build image via `bollard` (Docker SDK)
    // 5. Push to registry
    // 6. Deploy stack

    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    info!("smart_deploy task completed successfully");

    Ok(())
}

#[instrument(skip(_state))]
async fn handle_collect_usage(_state: Arc<WorkerState>, owner_id: i32) -> Result<()> {
    info!("Collecting usage metrics for owner {}", owner_id);

    // TODO: Phase (4.3) Billing aggregation

    tokio::time::sleep(std::time::Duration::from_millis(500)).await;
    info!("collect_usage task completed successfully");

    Ok(())
}
