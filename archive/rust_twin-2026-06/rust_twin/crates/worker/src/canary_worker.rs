//! Periodic canary health-check tick.
//!
//! Runs every 30 seconds (driven from `main.rs`). For each deployment whose
//! status is `CANARY10` or `CANARY50`:
//! 1. Load the persisted [`CanaryRun`] from Redis (or initialize a new one).
//! 2. Issue an HTTP GET to the service's health endpoint.
//! 3. Record the outcome and check for abort / advancement.
//! 4. Persist the updated [`CanaryRun`] back to Redis.
//!
//! The tick is idempotent: running it twice in a row produces the same
//! deployment state as running it once, provided the underlying health
//! endpoint is deterministic.

use crate::WorkerState;
use anyhow::{Context, Result};
use chrono::Utc;
use cn_core::canary::{
    CanaryConfig, CanaryRun, CanaryStage, CANARY_STATUS_10, CANARY_STATUS_50,
    CANARY_STATUS_ABORTED,
};
use cn_core::entities::{deployment, service};
use redis::AsyncCommands;
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use std::sync::Arc;
use std::time::Duration;
use tracing::{error, info, warn};
use uuid::Uuid;

const REDIS_KEY_PREFIX: &str = "canary:run:";
const REDIS_TTL_SECS: u64 = 86_400;
const RUNNING_STATUS: &str = "RUNNING";

/// One tick of the canary health-check loop.
pub async fn tick(state: Arc<WorkerState>) -> Result<()> {
    let cfg = CanaryConfig::default();
    let http_client = reqwest::Client::builder()
        .timeout(Duration::from_secs(cfg.health_check_timeout_secs))
        .build()
        .context("failed to build reqwest client")?;

    let canary_statuses = [CANARY_STATUS_10, CANARY_STATUS_50];
    let deployments = deployment::Entity::find()
        .filter(deployment::Column::Status.is_in(canary_statuses.to_vec()))
        .all(&state.db)
        .await
        .context("failed to query canary deployments")?;

    for dep in deployments {
        if let Err(e) = process_deployment(&state, &http_client, &cfg, dep).await {
            error!("canary tick failed for deployment: {:#}", e);
        }
    }

    Ok(())
}

async fn process_deployment(
    state: &Arc<WorkerState>,
    http_client: &reqwest::Client,
    cfg: &CanaryConfig,
    dep: deployment::Model,
) -> Result<()> {
    let now = Utc::now();
    let dep_id = dep.id;
    let service_id = dep.service_id;
    let current_status = dep.status.clone();

    let mut run = load_canary_run(state, dep_id)
        .await
        .context("failed to load canary run from redis")?
        .unwrap_or_else(|| CanaryRun::new(dep_id, now));

    if let Some(stage) = CanaryStage::from_status(&current_status) {
        run.stage = stage;
    }

    let service_slug = match service::Entity::find_by_id(service_id)
        .one(&state.db)
        .await
        .context("failed to query service for canary health check")?
    {
        Some(s) => s.slug,
        None => {
            warn!(
                "no service found for deployment {} (service_id={}), using placeholder URL",
                dep_id, service_id
            );
            "unknown".to_string()
        }
    };

    let url = format!("http://{}.localhost{}", service_slug, cfg.health_check_path);
    let passed = do_health_check(http_client, &url).await;
    run.record_health_check(passed);

    if !passed {
        warn!(
            "health check failed for deployment {} (url={}, consecutive_failures={})",
            dep_id, url, run.health_failures
        );
    }

    if run.should_abort(cfg) {
        warn!(
            "canary aborted for deployment {} after {} consecutive failures",
            dep_id, run.health_failures
        );
        let mut active: deployment::ActiveModel = dep.into();
        active.status = Set(CANARY_STATUS_ABORTED.to_string());
        active.finished_at = Set(Some(now.into()));
        active
            .update(&state.db)
            .await
            .context("failed to persist canary abort")?;
        delete_canary_run(state, dep_id).await?;
        return Ok(());
    }

    if let Some(next_stage) = run.advance_if_ready(cfg, now) {
        info!(
            "canary advancing deployment {} to stage {:?}",
            dep_id, next_stage
        );
        let mut active: deployment::ActiveModel = dep.into();
        if next_stage == CanaryStage::Canary100 {
            active.status = Set(RUNNING_STATUS.to_string());
            active.finished_at = Set(Some(now.into()));
        } else {
            active.status = Set(next_stage.as_str().to_string());
        }
        active
            .update(&state.db)
            .await
            .context("failed to persist canary stage transition")?;
    } else {
        save_canary_run(state, &run)
            .await
            .context("failed to persist canary run to redis")?;
    }

    Ok(())
}

async fn do_health_check(client: &reqwest::Client, url: &str) -> bool {
    match client.get(url).send().await {
        Ok(resp) => resp.status().is_success(),
        Err(_) => false,
    }
}

async fn load_canary_run(
    state: &Arc<WorkerState>,
    deployment_id: Uuid,
) -> Result<Option<CanaryRun>> {
    let mut conn = state
        .redis
        .get_multiplexed_async_connection()
        .await
        .context("failed to acquire redis connection")?;
    let key = redis_key(deployment_id);
    let val: Option<String> = conn
        .get(&key)
        .await
        .context("failed to read canary run from redis")?;
    match val {
        Some(s) => Ok(Some(
            serde_json::from_str(&s).context("failed to deserialize canary run")?,
        )),
        None => Ok(None),
    }
}

async fn save_canary_run(state: &Arc<WorkerState>, run: &CanaryRun) -> Result<()> {
    let mut conn = state
        .redis
        .get_multiplexed_async_connection()
        .await
        .context("failed to acquire redis connection")?;
    let key = redis_key(run.deployment_id);
    let val = serde_json::to_string(run).context("failed to serialize canary run")?;
    let _: () = conn
        .set_ex(&key, val, REDIS_TTL_SECS)
        .await
        .context("failed to write canary run to redis")?;
    Ok(())
}

async fn delete_canary_run(state: &Arc<WorkerState>, deployment_id: Uuid) -> Result<()> {
    let mut conn = state
        .redis
        .get_multiplexed_async_connection()
        .await
        .context("failed to acquire redis connection")?;
    let key = redis_key(deployment_id);
    let _: () = conn
        .del(&key)
        .await
        .context("failed to delete canary run from redis")?;
    Ok(())
}

fn redis_key(deployment_id: Uuid) -> String {
    format!("{}{}", REDIS_KEY_PREFIX, deployment_id)
}
