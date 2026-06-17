use anyhow::{Context, Result};
use cn_core::config::Config;
use cn_core::db;
use cn_core::telemetry;
use redis::AsyncCommands;
use sea_orm::DatabaseConnection;
use std::sync::Arc;
use tracing::{error, info};

pub mod celery_bridge;
pub mod rollback;
pub mod tasks;

pub struct WorkerState {
    pub db: DatabaseConnection,
    pub config: Config,
    pub redis: redis::Client,
}

const NATIVE_QUEUE: &str = "grid:tasks:default";
const CELERY_QUEUE: &str = "celery";

#[tokio::main]
async fn main() -> Result<()> {
    // 1. Initialize telemetry
    telemetry::init()?;

    // 2. Load config
    info!("Loading worker configuration...");
    let config = Config::load().context("Failed to load environment configuration")?;

    // 3. Connect to database
    info!("Connecting to database at {}", &config.database_url);
    let db = db::establish_connection(&config.database_url).await?;

    // 4. Connect to Redis
    let redis_url = config.get_redis_url();
    info!("Connecting to Redis broker at {}", &redis_url);
    let redis_client =
        redis::Client::open(redis_url.clone()).context("Failed to create Redis client")?;

    // 5. Setup Worker State
    let state = Arc::new(WorkerState {
        db,
        config: config.clone(),
        redis: redis_client,
    });

    info!("Worker successfully initialized and ready to process jobs.");

    // 6. Start the Polling Loop
    // We run the scheduler and the polling loop concurrently
    tokio::try_join!(
        start_polling_loop(Arc::clone(&state)),
        start_scheduler(Arc::clone(&state))
    )?;

    Ok(())
}

/// Mimics Celery Beat: Periodically pushes scheduled tasks (e.g. CollectUsage) to the Redis queue.
async fn start_scheduler(state: Arc<WorkerState>) -> Result<()> {
    info!("Starting background cron scheduler...");
    let mut interval = tokio::time::interval(std::time::Duration::from_secs(60)); // Run every 60s

    loop {
        interval.tick().await;
        info!("Scheduler tick: triggering routine tasks.");

        let mut conn = state.redis.get_multiplexed_async_connection().await?;

        // Example: Trigger global CollectUsage for all active owners
        let task_payload = serde_json::json!({
            "type": "CollectUsage",
            "payload": {
                "owner_id": 1 // In reality, we'd query all active users from DB
            }
        });

        let _: () = conn.lpush(NATIVE_QUEUE, task_payload.to_string()).await.unwrap_or_else(|e| {
            error!("Scheduler failed to push task: {}", e);
        });
    }
}

async fn start_polling_loop(state: Arc<WorkerState>) -> Result<()> {
    // Get a multiplexed async connection to Redis
    let mut conn = state
        .redis
        .get_multiplexed_async_connection()
        .await
        .context("Failed to acquire multiplexed Redis connection")?;

    loop {
        // Blockingly pop from the end of either list (BRPOP supports multiple keys).
        // Celery queue is checked first; native queue is the fallback.
        let result: redis::RedisResult<Option<(String, String)>> = redis::cmd("BRPOP")
            .arg(CELERY_QUEUE)
            .arg(NATIVE_QUEUE)
            .arg(5.0_f64)
            .query_async(&mut conn)
            .await;

        match result {
            Ok(Some((queue, payload))) => {
                info!("Received raw task payload from queue '{}'", queue);

                // Spawn a new task to handle the payload concurrently so we don't block the loop
                let state_clone = Arc::clone(&state);
                tokio::spawn(async move {
                    if let Err(e) = tasks::process_payload(state_clone, payload).await {
                        error!("Task processing failed: {:#}", e);
                    }
                });
            }
            Ok(None) => {
                // Timeout reached, all queues empty. Loop continues.
            }
            Err(e) => {
                error!("Redis BRPOP error: {:#}", e);
                tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
            }
        }
    }
}
