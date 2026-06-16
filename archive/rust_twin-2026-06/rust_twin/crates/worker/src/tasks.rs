use crate::WorkerState;
use anyhow::{Context, Result};
use cn_core::deployment_status::DeploymentStatus;
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
    ProvisionAddon {
        addon_id: uuid::Uuid,
        addon_type: String,
    },
    CollectUsage {
        owner_id: i32,
    },
}

#[instrument(skip(state, raw_payload))]
pub async fn process_payload(state: Arc<WorkerState>, raw_payload: String) -> Result<()> {
    info!("Parsing task payload");

    // Try the Celery bridge first (handles both Celery and native formats).
    let task = match crate::celery_bridge::parse_celery_message(&raw_payload) {
        Ok(Some(task)) => task,
        Ok(None) => {
            info!("Unknown task, acknowledged");
            return Ok(());
        }
        Err(e) => {
            error!("Failed to parse payload: {}", e);
            return Err(anyhow::anyhow!(e));
        }
    };

    info!("Processing task: {:?}", task);
    match task {
        Task::SmartDeploy {
            project_id,
            deployment_id,
            commit_hash,
        } => handle_smart_deploy(state, project_id, deployment_id, commit_hash).await?,
        Task::ProvisionAddon {
            addon_id,
            addon_type,
        } => handle_provision_addon(state, addon_id, addon_type).await?,
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

    deploy_active.status = Set(DeploymentStatus::Building.as_str().to_string());
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
            deploy_failed.status = Set(DeploymentStatus::DeployFailed.as_str().to_string());
            deploy_failed.save(&state.db).await?;

            return Err(e);
        }
    }

    // 4. Mark deployment as deploying (state machine: BUILDING -> DEPLOYING -> RUNNING)
    let mut deploy_deploying: deployment::ActiveModel = deployment::Entity::find_by_id(deployment_id)
        .one(&state.db)
        .await?
        .context("Deployment not found in DB")?
        .into();
    deploy_deploying.status = Set(DeploymentStatus::Deploying.as_str().to_string());
    deploy_deploying.save(&state.db).await?;

    // 5. Run the container via DockerClient
    let docker = DockerClient::new()?;
    let network_name = "smsly-net"; // Matches DOCKER_NETWORK from .env

    // Ensure network exists
    docker.ensure_network(network_name).await?;

    // Run container
    let container_name = format!("svc-{}-{}", svc.slug, commit_hash);
    let container_id = match docker.run_container(&image_name, &container_name, vec![], network_name).await {
        Ok(id) => id,
        Err(e) => {
            error!("Deploy failed: {}", e);
            let mut deploy_failed: deployment::ActiveModel = deployment::Entity::find_by_id(deployment_id)
                .one(&state.db)
                .await?
                .unwrap()
                .into();
            deploy_failed.status = Set(DeploymentStatus::DeployFailed.as_str().to_string());
            deploy_failed.save(&state.db).await?;
            return Err(e);
        }
    };
    info!("Container {} started successfully.", container_id);

    // 6. Mark Deployment as Running
    let mut deploy_success: deployment::ActiveModel = deployment::Entity::find_by_id(deployment_id)
        .one(&state.db)
        .await?
        .unwrap()
        .into();

    deploy_success.status = Set(DeploymentStatus::Running.as_str().to_string());
    deploy_success.finished_at = Set(Some(chrono::Utc::now().into()));
    deploy_success.save(&state.db).await?;

    info!("smart_deploy task completed successfully");
    Ok(())
}

use cn_core::entities::addon;

#[instrument(skip(state))]
async fn handle_provision_addon(state: Arc<WorkerState>, addon_id: uuid::Uuid, addon_type: String) -> Result<()> {
    info!("Provisioning Addon: {} (Type: {})", addon_id, addon_type);

    let mut addon_active: addon::ActiveModel = addon::Entity::find_by_id(addon_id)
        .one(&state.db)
        .await?
        .context("Addon not found in DB")?
        .into();

    let docker = DockerClient::new()?;
    let network_name = "smsly-net";
    docker.ensure_network(network_name).await?;

    let (image, env, port) = match addon_type.as_str() {
        "POSTGRES" => {
            let pass = uuid::Uuid::new_v4().to_string().replace("-", "")[..16].to_string();
            let env = vec![
                format!("POSTGRES_PASSWORD={}", pass),
                "POSTGRES_USER=postgres".to_string(),
                "POSTGRES_DB=main".to_string()
            ];
            ("postgres:16-alpine", env, 5432)
        },
        "REDIS" => {
            let pass = uuid::Uuid::new_v4().to_string().replace("-", "")[..16].to_string();
            let env = vec![format!("REDIS_PASSWORD={}", pass)];
            ("redis:7-alpine", env, 6379)
        },
        _ => return Err(anyhow::anyhow!("Unsupported addon type")),
    };

    let container_name = format!("addon-{}-{}", addon_type.to_lowercase(), addon_id);
    let container_id = docker.run_container(image, &container_name, env, network_name).await?;

    // Simulate extracting connection string from env context
    let uri = format!("{}://user:pass@{}:{}", addon_type.to_lowercase(), container_name, port);

    addon_active.status = Set("RUNNING".to_string());
    addon_active.container_id = Set(Some(container_id));
    addon_active.connection_url = Set(Some(uri));
    addon_active.save(&state.db).await?;

    info!("Addon provisioned successfully");
    Ok(())
}

use cn_core::entities::usage;
use rand::Rng; // Simulating Docker Stats parsing

#[instrument(skip(state))]
async fn handle_collect_usage(state: Arc<WorkerState>, owner_id: i32) -> Result<()> {
    info!("Collecting usage metrics for owner {}", owner_id);

    // Fetch the first service from the database to align with the foreign key constraint
    let service_opt = service::Entity::find().one(&state.db).await?;
    let service_id = match service_opt {
        Some(s) => s.id,
        None => {
            info!("No service found in DB, skipping usage logging.");
            return Ok(());
        }
    };

    // In a real environment, we would use `bollard` to stream container stats
    // Example: docker.stats(container_id).next().await;
    // Here we simulate capturing running container stats.
    // NOTE: We scope the RNG generation so `ThreadRng` (which is not Send) is dropped before `await`.
    let (cpu_used, mem_used) = {
        let mut rng = rand::thread_rng();
        let cpu = rng.gen_range(0.01..2.0); // 0.01 to 2 CPU Cores
        let mem = rng.gen_range(50.0..1024.0); // 50MB to 1GB
        (cpu, mem)
    };

    let new_usage = usage::ActiveModel {
        service_id: Set(service_id),
        cpu_cores: Set(cpu_used),
        memory_mb: Set(mem_used as i32),
        duration_seconds: Set(3600),
        cost: Set(0.01),
        timestamp: Set(chrono::Utc::now().into()),
        ..Default::default()
    };

    new_usage.insert(&state.db).await?;

    info!("Usage logged: {:.2} Cores, {:.2} MB", cpu_used, mem_used);
    Ok(())
}
