use anyhow::{Context, Result};
use cn_core::config::Config;
use cn_core::db;
use cn_core::telemetry;
use redis::AsyncCommands;
use sea_orm::DatabaseConnection;
use std::sync::Arc;
use tracing::{error, info};

pub mod tasks;

pub struct WorkerState {
    pub db: DatabaseConnection,
    pub config: Config,
    pub redis: redis::Client,
}

const QUEUE_NAME: &str = "cloudneuron:tasks:default";

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
    start_polling_loop(state).await
}

async fn start_polling_loop(state: Arc<WorkerState>) -> Result<()> {
    // Get a multiplexed async connection to Redis
    let mut conn = state
        .redis
        .get_multiplexed_async_connection()
        .await
        .context("Failed to acquire multiplexed Redis connection")?;

    loop {
        // Blockingly pop from the end of the list (BRPOP), timeout 5 seconds
        let result: redis::RedisResult<Option<(String, String)>> =
            conn.brpop(QUEUE_NAME, 5.0).await;

        match result {
            Ok(Some((_queue, payload))) => {
                info!("Received raw task payload");

                // Spawn a new task to handle the payload concurrently so we don't block the loop
                let state_clone = Arc::clone(&state);
                tokio::spawn(async move {
                    if let Err(e) = tasks::process_payload(state_clone, payload).await {
                        error!("Task processing failed: {:#}", e);
                    }
                });
            }
            Ok(None) => {
                // Timeout reached, queue is empty. Loop continues.
            }
            Err(e) => {
                error!("Redis BRPOP error: {:#}", e);
                tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
            }
        }
    }
}
