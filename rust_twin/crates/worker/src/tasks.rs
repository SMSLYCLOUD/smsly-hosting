use crate::WorkerState;
use anyhow::{Context, Result};
use cn_core::entities::{deployment, service};
use infrastructure::{builder::NixpacksBuilder, docker::DockerClient};
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tracing::{error, info, instrument};

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

#[instrument(skip(state))]
async fn handle_smart_deploy(
    state: Arc<WorkerState>,
    project_id: uuid::Uuid,
    deployment_id: uuid::Uuid,
    commit_hash: String,
) -> Result<()> {
    info!(
        "Starting smart_deploy for project {} (Deployment: {}) @ commit {}",
        project_id, deployment_id, commit_hash
    );

    // 1. Mark deployment as building
    let mut deploy_active: deployment::ActiveModel = deployment::Entity::find_by_id(deployment_id)
        .one(&state.db)
        .await?
        .context("Deployment not found in DB")?
        .into();

    deploy_active.status = Set("BUILDING".to_string());
    deploy_active.save(&state.db).await?;

    // 2. Lookup Service
    let svc = service::Entity::find()
        .filter(service::Column::ProjectId.eq(project_id))
        .one(&state.db)
        .await?
        .context("No service associated with this project")?;

    // For demonstration, we assume the code is already cloned to `/tmp/repo`
    // In a full implementation, `git2` would be used to clone `svc.repo_url` @ `svc.branch`.
    let source_dir = "/tmp/repo";
    let image_name = format!("registry.smsly.cloud/project-{}:{}", project_id, commit_hash);

    // 3. Build image using Nixpacks
    let builder = NixpacksBuilder::new().await?;
    match builder.build_image(source_dir, &image_name, vec![]).await {
        Ok(_) => info!("Nixpacks build completed."),
        Err(e) => {
            error!("Build failed: {}", e);

            // Mark as Failed
            let mut deploy_failed: deployment::ActiveModel = deployment::Entity::find_by_id(deployment_id)
                .one(&state.db)
                .await?
                .unwrap()
                .into();
            deploy_failed.status = Set("FAILED".to_string());
            deploy_failed.message = Set(Some(e.to_string()));
            deploy_failed.save(&state.db).await?;

            return Err(e);
        }
    }

    // 4. Run the container via DockerClient
    let docker = DockerClient::new()?;
    let network_name = "smsly-net"; // Matches DOCKER_NETWORK from .env

    // Ensure network exists
    docker.ensure_network(network_name).await?;

    // Run container
    let container_name = format!("svc-{}-{}", svc.slug, commit_hash);
    let container_id = docker.run_container(&image_name, &container_name, vec![], network_name).await?;
    info!("Container {} started successfully.", container_id);

    // 5. Mark Deployment as Running
    let mut deploy_success: deployment::ActiveModel = deployment::Entity::find_by_id(deployment_id)
        .one(&state.db)
        .await?
        .unwrap()
        .into();

    deploy_success.status = Set("RUNNING".to_string());
    deploy_success.docker_image = Set(Some(image_name));
    deploy_success.finished_at = Set(Some(chrono::Utc::now().into()));
    deploy_success.save(&state.db).await?;

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
